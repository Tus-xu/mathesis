"""RV18 · 项目记忆摘要（宪法 §8 Project Memory Summary）。

`GET /projects/{id}/overview` 的 `summary` 不再是"项目名 + 关键词碎片 + 计数"的机械模板，
而是**基于项目真实数据**的一段中文研究主题摘要：

1. **模型生成**：把项目**真实**的文献标题/年份/作者、笔记摘录、草稿文件名交给模型，
   产出 2~4 句中文研究主题摘要；
2. **缓存**：按 `(tenant_id, project_id)` 缓存，并用**数据指纹**失效
   —— 项目数据一变（加文献/改笔记/加草稿）指纹就变，缓存自然作废；
3. **规则降级**：模型不可用 / 超时 / 返回不可用 → 由**同一份真实数据**拼出可读中文摘要
   （§56/§90：给不出 AI 摘要时也不能给一坨碎片）。

红线
----
- §70 **禁止固定字符串**：规则版里每一个字都由项目数据决定，没有写死的主题词；
- §85 不把英文小写关键词碎片（`finite、element、error`）塞进中文 UI；
- §37/§90 不得编造项目里没有的文献、作者、年份：事实清单由后端给定，
  模型只负责**措辞**，输出还要过 `reasoning.sanitize_user_text`。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm import gateway as llm
from . import reasoning as reasoning_svc
from .models import (
    DRAFT_FILE_KINDS,
    ResearchNoteRow,
    ResearchPaperRow,
    ResearchProjectFileRow,
)

#: 摘要生成的总时长上限（秒）。比 Agent 任务的 90s 短得多 —— 概览是打开项目就要看的，
#: 不能让用户等。超时就退回规则版，并**缓存**规则版一小段时间，避免反复付超时。
LLM_TIMEOUT_S = 12.0
#: 模型版摘要的缓存时长。
CACHE_TTL_S = 30 * 60
#: 规则版摘要的缓存时长（短一些，好让模型恢复后尽快换上 AI 版）。
FALLBACK_TTL_S = 5 * 60

#: 摘要正文长度上限（避免模型话痨）。
MAX_SUMMARY_CHARS = 600
#: 参与摘要的原始素材条数上限。
MAX_PAPERS = 8
MAX_SECTIONS = 10
MAX_NOTES = 6
MAX_FILES = 10
NOTE_EXCERPT_CHARS = 120

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{3,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
#: 标题里常见的无信息量词（大小写无关比较）。
_TITLE_STOPWORDS = frozenset(
    {
        "finite", "element", "elements", "method", "methods", "approach", "analysis",
        "error", "estimates", "estimate", "estimation", "problem", "problems",
        "numerical", "study", "paper", "results", "result", "based", "using",
        "this", "that", "with", "from", "into", "over", "under", "between",
        "研究", "方法", "结果", "分析", "问题", "本文", "我们", "基于",
        # 文档类型词：出现在标题里但**不是**研究主题（如「…（示例）」）。
        "示例", "教程", "讲义",
    }
)
#: 摘要用的 CJK 功能词（与 `overview.py` 的展示切分口径一致）。
_CJK_FUNCTION_CHARS = "的与和及或在对其该这那之而则并等以为所"

_CACHE: dict[tuple[str, str], dict] = {}


# --------------------------------------------------------------------------
# 真实素材
# --------------------------------------------------------------------------


def _cjk_words(run: str) -> list[str]:
    if not run:
        return []
    parts = re.split(f"[{re.escape(_CJK_FUNCTION_CHARS)}]", run)
    return [p for p in parts if len(p) >= 2]


def _shorten(text: str, limit: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _authors_text(raw: object) -> str:
    if isinstance(raw, list):
        names = [str(a).strip() for a in raw if str(a).strip()]
    else:
        names = [str(raw).strip()] if str(raw or "").strip() else []
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return names[0] + " 等" if len(names) > 2 else "、".join(names)


def gather_grounding(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, project
) -> dict:
    """把项目的**真实**素材收成一份清单（摘要的事实来源，模型只能基于它措辞）。"""
    papers = (
        db.execute(
            select(ResearchPaperRow)
            .where(
                ResearchPaperRow.tenant_id == tenant_id,
                ResearchPaperRow.project_id == project_id,
                ResearchPaperRow.deleted_at.is_(None),
            )
            .order_by(ResearchPaperRow.created_at.desc())
            .limit(MAX_PAPERS)
        )
        .scalars()
        .all()
    )
    notes = (
        db.execute(
            select(ResearchNoteRow)
            .where(
                ResearchNoteRow.tenant_id == tenant_id,
                ResearchNoteRow.project_id == project_id,
                ResearchNoteRow.deleted_at.is_(None),
            )
            .order_by(ResearchNoteRow.updated_at.desc())
            .limit(MAX_NOTES)
        )
        .scalars()
        .all()
    )
    files = (
        db.execute(
            select(ResearchProjectFileRow)
            .where(
                ResearchProjectFileRow.tenant_id == tenant_id,
                ResearchProjectFileRow.project_id == project_id,
            )
            .order_by(ResearchProjectFileRow.updated_at.desc())
        )
        .scalars()
        .all()
    )

    sections: list[str] = []
    for p in papers:
        for s in list(p.page_sections or [])[:3]:
            title = str((s or {}).get("title") or "").strip()
            if title and title not in sections:
                sections.append(title)
    sections = sections[:MAX_SECTIONS]

    note_excerpts: list[str] = []
    for n in notes:
        text = " ".join(str(n.body or "").split()) or " ".join(str(n.title or "").split())
        if text:
            note_excerpts.append(_shorten(text, NOTE_EXCERPT_CHARS))

    draft_paths = [f.path for f in files if f.kind in DRAFT_FILE_KINDS]

    return {
        "name": str(getattr(project, "name", "") or "").strip(),
        "description": str(getattr(project, "description", "") or "").strip(),
        "papers": [
            {
                "title": _shorten(p.title, 160),
                "year": p.year or 0,
                "authors": _authors_text(p.authors),
            }
            for p in papers
            if p.title
        ],
        "sections": sections,
        "notes": note_excerpts,
        "files": draft_paths[:MAX_FILES],
    }


def fingerprint(grounding: dict, stats: dict) -> str:
    """素材指纹：数据一变就变，用来让缓存自然失效。"""
    blob = json.dumps(
        {"g": grounding, "s": stats}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    return hashlib.md5(blob).hexdigest()


# --------------------------------------------------------------------------
# 规则降级版（无模型也要可读、无英文碎片）
# --------------------------------------------------------------------------


def theme_terms(grounding: dict, top: int = 4) -> list[str]:
    """从真实标题里提取主题词。

    §85：优先用**中文**主题词；没有中文时，只保留**保留原大小写**的拉丁术语
    （`Stokes`/`Galerkin`/`Sobolev` 这类），**不**把 `finite`/`element`/`error`
    这种小写碎片丢进中文句子。
    """
    cjk: Counter[str] = Counter()
    latin: Counter[str] = Counter()
    sources = [p["title"] for p in grounding.get("papers") or []]
    sources += [str(s) for s in grounding.get("sections") or []]
    for text in sources:
        for run in _CJK_RE.findall(text or ""):
            for word in _cjk_words(run):
                if word not in _TITLE_STOPWORDS:
                    cjk[word] += 1
        for word in _WORD_RE.findall(text or ""):
            if word.lower() in _TITLE_STOPWORDS:
                continue
            if word[:1].isupper() or any(c.isupper() for c in word[1:]):
                latin[word] += 1
    if cjk:
        return [w for w, _n in cjk.most_common(top)]
    return [w for w, _n in latin.most_common(top)]


def rule_summary(grounding: dict, stats: dict) -> str:
    """规则版摘要：每个字都由项目数据决定（§70 无固定串）。"""
    name = grounding.get("name") or ""
    desc = grounding.get("description") or ""
    papers = grounding.get("papers") or []
    parts: list[str] = []

    head = f"《{name}》" if name else "本项目"
    if desc:
        parts.append(f"{head}：{desc.rstrip('。.')}。")
    else:
        parts.append(f"{head}。")

    if papers:
        shown = [f"《{p['title']}》" for p in papers[:2] if p.get("title")]
        who = papers[0].get("authors") or ""
        year = papers[0].get("year") or 0
        cite = who + (f"，{year}" if year else "")
        recent = f"{shown[0]}（{cite}）" if (shown and cite) else (shown[0] if shown else "")
        if len(papers) > 2 and recent:
            parts.append(f"目前收录 {stats['papers']} 篇文献，最近在读{recent}等。")
        else:
            joined = "、".join(shown)
            tail = f"：{joined}" if joined else ""
            parts.append(f"目前收录 {stats['papers']} 篇文献{tail}。")
    else:
        parts.append("目前还没有收录文献，导入 PDF 后这里会给出研究主题概览。")

    themes = theme_terms(grounding)
    if themes:
        parts.append("主题集中在" + "、".join(themes) + "。")

    if stats.get("drafts") or stats.get("notes") or stats.get("data"):
        parts.append(
            f"写作侧有 {stats['drafts']} 份草稿、{stats['notes']} 条笔记、"
            f"{stats['data']} 项数据。"
        )
    return "".join(parts)[:MAX_SUMMARY_CHARS]


# --------------------------------------------------------------------------
# 模型版
# --------------------------------------------------------------------------


_SYSTEM = (
    "你在帮一位科研人员维护他的项目记忆。请根据下面给出的**真实项目数据**，"
    "写一段 2~4 句的中文研究主题摘要，让作者一眼看懂这个项目在做什么。\n"
    "要求：\n"
    "1. 只说数据里出现过的事实，**不得**编造文献、作者、年份、结论；\n"
    "2. 用通顺的中文书面语，不要罗列英文关键词，不要出现小写英文碎片；\n"
    "3. 不要写「收录了几篇文献」这类计数流水账，计数会另行展示；\n"
    "4. 禁止出现实现术语：模型、提示词、temperature、embedding、向量、检索、chunk、工具调用；\n"
    "5. 只输出一个 JSON 对象，不要解释文字，不要 Markdown 代码围栏："
    '{"summary":"……"}'
)


def _user_prompt(grounding: dict, stats: dict) -> str:
    lines = ["【项目真实数据】"]
    lines.append(f"项目名：{grounding.get('name') or '（未命名）'}")
    if grounding.get("description"):
        lines.append(f"项目描述：{grounding['description']}")
    papers = grounding.get("papers") or []
    if papers:
        lines.append(f"已收录文献（共 {stats.get('papers', len(papers))} 篇，列出最近的）：")
        for p in papers:
            bits = [p.get("title") or ""]
            if p.get("authors"):
                bits.append(p["authors"])
            if p.get("year"):
                bits.append(str(p["year"]))
            lines.append("  - " + "；".join(b for b in bits if b))
    else:
        lines.append("已收录文献：无")
    if grounding.get("sections"):
        lines.append("文献里出现的章节标题（真实抽取）：")
        lines.append("  - " + "；".join(grounding["sections"]))
    if grounding.get("notes"):
        lines.append("作者自己的笔记摘录：")
        for n in grounding["notes"]:
            lines.append("  - " + n)
    if grounding.get("files"):
        lines.append("草稿文件：" + "、".join(grounding["files"]))
    lines.append(
        f"统计：{stats.get('papers', 0)} 篇文献、{stats.get('drafts', 0)} 份草稿、"
        f"{stats.get('notes', 0)} 条笔记、{stats.get('data', 0)} 项数据。"
    )
    lines.append("")
    lines.append("现在只输出 JSON。")
    return "\n".join(lines)


async def _llm_summary(grounding: dict, stats: dict) -> str:
    """模型版摘要；任何失败都返回空串（调用方退规则版）。"""
    backend = llm.get_backend()
    if not backend.available:
        return ""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(grounding, stats)},
    ]
    try:
        completion = await asyncio.wait_for(
            backend.acompletion(model=llm.llm_model_long(), messages=messages),
            timeout=LLM_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, TimeoutError):
        return ""
    except Exception:  # noqa: BLE001 - 失败必须降级，不吞不炸
        return ""
    parsed = reasoning_svc.extract_json(completion.text or "")
    raw = parsed.get("summary") if isinstance(parsed, dict) else None
    text = reasoning_svc.sanitize_user_text(raw, limit=MAX_SUMMARY_CHARS)
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    # 模型偶尔会自带「摘要：」前缀或把整段塞进引号，这里统一收口。
    text = re.sub(r"^(摘要|总结|项目摘要)\s*[:：]\s*", "", text).strip()
    if not text.endswith(("。", "！", "？", ".", "!", "?")):
        text += "。"
    return text


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


async def summarize(
    grounding: dict, stats: dict, *, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> tuple[str, str]:
    """返回 `(summary, source)`。`source ∈ {llm:project-memory, rule:project-data}`。

    **不碰数据库**：素材由调用方用 `gather_grounding()` 先取好 ——
    这样模型调用（可能 2~5s）期间**不会占着 DB 连接**（`session_scope` 在退出前
    一直持有事务）。
    """
    fp = fingerprint(grounding, stats)
    key = (str(tenant_id), str(project_id))
    now = time.time()

    hit = _CACHE.get(key)
    if hit and hit["fingerprint"] == fp and hit["expires_at"] > now:
        return hit["summary"], hit["source"]

    text = await _llm_summary(grounding, stats)
    if text:
        _CACHE[key] = {
            "fingerprint": fp,
            "summary": text,
            "source": "llm:project-memory",
            "expires_at": now + CACHE_TTL_S,
        }
        return text, "llm:project-memory"

    fallback = rule_summary(grounding, stats)
    _CACHE[key] = {
        "fingerprint": fp,
        "summary": fallback,
        "source": "rule:project-data",
        "expires_at": now + FALLBACK_TTL_S,
    }
    return fallback, "rule:project-data"


async def summary_for_project(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, project, stats: dict
) -> tuple[str, str]:
    """`overview.overview()` 的入口：**取真实素材 → 生成摘要**，返回 `(summary, source)`。

    与 `summarize()` 的分工：`summarize()` 不碰数据库（模型调用可能 2~5s），
    本函数负责先用 `gather_grounding()` 把项目的真实素材（文献/笔记/文件/统计）取好。

    已知取舍：调用方（路由）用 `session_scope()` 包住整段，因此模型调用期间仍会持有
    DB 连接。若要彻底解耦，应由路由先 `gather_grounding()` 并关闭 session，再调 `summarize()`。
    这是性能优化项，不影响正确性。
    """
    grounding = gather_grounding(
        db, tenant_id=tenant_id, project_id=project_id, project=project
    )
    return await summarize(
        grounding, stats, tenant_id=tenant_id, project_id=project_id
    )


def invalidate(*, tenant_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """数据变更后主动作废缓存（指纹失效是兜底，这里是显式通道）。"""
    _CACHE.pop((str(tenant_id), str(project_id)), None)


__all__ = [
    "CACHE_TTL_S",
    "FALLBACK_TTL_S",
    "LLM_TIMEOUT_S",
    "fingerprint",
    "gather_grounding",
    "invalidate",
    "rule_summary",
    "summarize",
    "summary_for_project",
    "theme_terms",
]
