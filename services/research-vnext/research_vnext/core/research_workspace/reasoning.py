"""RV18 · Research Agent 的真实推理路径（推导校验 / 公式检查 / LaTeX 错误解释）。

背景（**这是本轮最大的产品缺口**）
----------------------------------
`agent._run_math` 原先只有**确定性符号校验**一条路：`context` 里带
`lhs/rhs/points` 或 `checks[]` 才动，否则一律回 `needs_conditions`（"请提供更多信息"）。
而黄金图承诺的是：科研人员在 PDF 里**选中一段数学陈述**（如 Céa 引理）点「推导校验」，
拿到的是 **结论 + 使用的条件 + 推导逻辑 + 真实文献证据 + 置信状态**（宪法 §33/§34/§35/§58）。

本模块补上这条路：把
  1. 选中的数学陈述（可能是 LaTeX）；
  2. 当前论文上下文（真实 `research_paper` 行的 title/authors/year + 阅读位置）；
  3. 项目真实文献（`knowledge.search` 的全文检索命中 + 项目文献兜底）
组成 prompt 交给模型，产出**结构化 Research Result**（宪法 §58）。

四条不可越过的红线
------------------
- §37/§90 **证据不得伪造**：模型只能从我们给出的候选来源里挑 `source_ids`，
  且 `evidence[]` 的 title/authors/year/page/section **一律由数据库行的真实字段回填**，
  模型输出的文献字段一概丢弃。挑不到任何真实来源 ⇒ `evidence=[]` 且状态不得是
  `verified`/`supported`。
- §90 **不冒充确定性**：本路径的结论上限是 `likely`（黄金图徽标「概率较高」即此义）。
  只有符号校验那条确定性路径才配 `verified`。
- §56/§90 **诚实降级**：模型不可用 / 超时 / 输出不可解析 ⇒ `insufficient` +
  **人类可读中文**，绝不伪造结论，绝不出现错误码 / traceback / 裸 JSON。
- §27/§85 **统一业务语言**：面向用户的文案里不得出现 RAG / embedding / top-k /
  chunk / tool call / vector DB / 模型名 / temperature / 提示词 等实现术语；
  `sanitize_user_text()` 是这条红线的**最后一道**（不是唯一一道）保险。
- §68 **日志脱敏**：本模块**不打印**论文正文或选中文本（只打印长度/来源数）。

LaTeX 错误解释（§39）另见 `explain_latex_errors()`：允许模型增强"人类可读解释 /
可能原因"，但 **file / line / Diff 的 before-after 必须来自真实编译日志与真实源码行**。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import uuid

from sqlalchemy.orm import Session

from ..llm import gateway as llm
from . import knowledge as knowledge_svc
from .models import ResearchPaperRow
from .papers import list_papers

#: 本路径允许出现的状态（`failed` 留给"能力本身坏了"，此处不用）。
_LLM_STATUSES = frozenset(
    {"verified", "supported", "likely", "needs_conditions", "insufficient", "conflict"}
)

#: 模型输出里**永远**不允许出现的实现术语（宪法 §27/§85）。
#: 命中即替换为统一业务语言，避免"内部实现泄漏到科研用户面前"。
_FORBIDDEN_TERMS: tuple[tuple[str, str], ...] = (
    ("retrieval-augmented generation", "项目知识"),
    ("retrieval augmented generation", "项目知识"),
    ("vector database", "项目知识"),
    ("vector db", "项目知识"),
    ("context window", "当前上下文"),
    ("function call", "项目能力"),
    ("tool call", "项目能力"),
    ("embedding", "项目知识"),
    ("向量数据库", "项目知识"),
    ("向量库", "项目知识"),
    ("上下文窗口", "当前上下文"),
    ("工具调用", "项目能力"),
    ("函数调用", "项目能力"),
    ("提示词", "指令"),
    ("temperature", "参数"),
    ("top-k", "相关文献"),
    ("topk", "相关文献"),
    ("chunk", "片段"),
    ("rag", "项目知识"),
)

#: 模型/供应商标识（**任何情况下**都不能出现在用户可见文案里）。
_MODEL_NAME_RE = re.compile(
    r"\b(?:doubao|spark|ark|gpt|claude|qwen|deepseek|glm|llama|moonshot|"
    r"xop3qwen80bnext|xophunyuanocr|litellm)[\w.\-]*\b",
    re.IGNORECASE,
)

#: 用户可见文案的长度上限（防模型长篇大论撑爆卡片）。
_MAX_SUMMARY = 400
_MAX_FINDING = 300
_MAX_FINDINGS = 6
_MAX_SOURCES = 6
_MAX_EVIDENCE = 4


def sanitize_user_text(text: object, *, limit: int = _MAX_FINDING) -> str:
    """把模型输出收敛成**面向科研用户**的文案（宪法 §27/§85/§68）。

    做三件事，且只做这三件：
    1. 折叠空白（模型爱输出换行/多空格）；
    2. 替换实现术语与模型标识为统一业务语言；
    3. 截断到上限。

    不做"语义改写"——那会引入新的幻觉面。
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return ""
    low = s.lower()
    for bad, good in _FORBIDDEN_TERMS:
        if bad in low:
            s = re.sub(re.escape(bad), good, s, flags=re.IGNORECASE)
            low = s.lower()
    s = _MODEL_NAME_RE.sub("服务", s)
    return s[:limit].strip()


# --------------------------------------------------------------------------
# 真实来源（候选证据）——**证据只能来自这里**
# --------------------------------------------------------------------------


def _authors_text(row: ResearchPaperRow) -> str:
    authors = row.authors
    if isinstance(authors, list):
        return ", ".join(str(a) for a in authors if str(a).strip())
    return str(authors or "")


#: `locate_bbox` 分级匹配的**前缀长度**（由长到短，取能命中的最长前缀）。
#:
#: 为什么必须分级：`needle` 常常是「标题 + 正文」拼起来的串（用户跨块选区
#: 或 `snippet` 由首行标题接正文构成）。只拿整串前 60 字符去**单个 block** 里
#: 匹配时，60 字符前缀必然跨过 block 边界 → 永远匹配不到 → 返回 None → 只跳页。
#: 按 60→40→24→16→8 逐级截短，短前缀就能落回**单个真实 block** 内。
_BBOX_PROBE_LENGTHS: tuple[int, ...] = (60, 40, 24, 16, 8)

#: 最小有效探测长度：短于它一律不匹配。两三字的前缀会在整页里到处命中，
#: 那等于**假高亮**（宪法 §14/§33 禁止伪造位置）。
_BBOX_MIN_PROBE = 8


def _block_bbox_at(target: dict, probe: str) -> list[float] | None:
    """在 `target` 页的**真实 block 文本**里找 `probe`，命中即返回该 block 的真实 bbox。

    `probe` 需为小写、已折叠空白。找不到（或 block 无合法 bbox）返回 `None`。
    """
    for block in target.get("blocks") or []:
        block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
        if not block_text:
            continue
        if probe and probe in block_text.lower():
            bbox = block.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                try:
                    return [float(v) for v in bbox]
                except (TypeError, ValueError):
                    return None
    return None


def locate_bbox(row: ResearchPaperRow, *, page: int, needle: str) -> list[float] | None:
    """在**已解析的真实页块**里定位 `needle`，返回该行 bbox；定位不到返回 `None`。

    匹配策略（**分级**）：把 `needle` 折叠空白后，按 `_BBOX_PROBE_LENGTHS`
    （60→40→24→16→8）**由长到短**逐级取前缀，返回**首个能命中某个真实 block**
    的（即最长可命中前缀）对应 block 的 bbox。这样「标题 + 正文」拼串也能落到
    标题所在的单块；同时用 `_BBOX_MIN_PROBE`（8 字符）挡掉过短的假命中。

    为什么必须返回 `None` 而不是"给个大概位置"：前端「定位原文」在只有
    page/section 时会退化成"只跳页不高亮"，那是**可接受的诚实行为**；
    而伪造一个 bbox 会让高亮框落在错误的位置上——那是在骗用户（宪法 §14/§33）。
    """
    text = re.sub(r"\s+", " ", str(needle or "")).strip()
    if not text or page < 1:
        return None
    pages = list(row.pages or [])
    target = next((p for p in pages if int(p.get("page") or 0) == page), None)
    if target is None:
        return None
    # 待尝试的前缀长度：固定档位（≤ 串长）+ 整串（串长 ≥ 最小有效长度时）。
    lengths = {n for n in _BBOX_PROBE_LENGTHS if n <= len(text)}
    if len(text) >= _BBOX_MIN_PROBE:
        lengths.add(len(text))
    for n in sorted(lengths, reverse=True):
        if n < _BBOX_MIN_PROBE:
            continue
        bbox = _block_bbox_at(target, text[:n].lower())
        if bbox is not None:
            return bbox
    return None


def _source_dict(
    row: ResearchPaperRow, *, page: int, section: str, snippet: str
) -> dict:
    """把真实文献行渲染成候选来源（**所有文献字段都来自数据库行**）。

    `locator.selected_text` 必须带上**真实 snippet**（前端「定位原文」在拿不到
    bbox 时，用它在已渲染的真实文本块里回退定位并高亮；此前恒为空串，导致
    回退分支永不触发——只能跳页、高亮不出来）。这里复用下面 `snippet` 同一次
    `sanitize_user_text` 结果，保证 locator 与卡片显示的是同一段真值。
    """
    snippet_text = sanitize_user_text(snippet, limit=200)
    bbox = locate_bbox(row, page=page, needle=snippet)
    locator: dict = {
        "paper_id": str(row.id),
        "page": int(page or 0),
        "section": str(section or ""),
        "selected_text": snippet_text,
        "bbox": bbox,
    }
    return {
        "id": str(row.id),
        "paper_id": str(row.id),
        "title": row.title or "",
        "authors": _authors_text(row),
        "year": int(row.year or 0),
        "page": int(page or 0),
        "section": str(section or ""),
        "locator": locator,
        "snippet": snippet_text,
    }


def section_for_page(row: ResearchPaperRow, page: int) -> str:
    """该页所属章节（取 `page_sections` 里最后一个 `page <= page` 的标题）。

    章节来自**真实解析结果**；没有就返回空串，不猜（宪法 §33）。
    """
    best_page, best_title = 0, ""
    for s in row.page_sections or []:
        try:
            pno = int(s.get("page") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < pno <= int(page or 0) and pno >= best_page:
            best_page, best_title = pno, str(s.get("title") or "")
    return best_title


def _anchor_source(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    context: dict,
    selected: str,
) -> dict | None:
    """把**用户当前正在读的那篇论文那一页**做成第一候选来源。

    为什么必须单独做：`knowledge.search` 对 LaTeX 片段（如 `\\nabla`）常常切不出词元，
    检索无命中就会退化成"项目文献列表"，于是证据只剩标题、没有页码/章节/bbox，
    前端「定位原文」只能跳页。而"用户选中的文字来自哪一篇哪一页"是**确定事实**，
    直接用它，就能给出真实 page + section + bbox（定位到真实行框）。
    """
    raw = context.get("paper_id")
    if not raw:
        return None
    try:
        row = db.get(ResearchPaperRow, uuid.UUID(str(raw)))
    except (ValueError, TypeError):
        return None
    if row is None or row.tenant_id != tenant_id or row.deleted_at is not None:
        return None
    try:
        page = int(context.get("page") or 0)
    except (TypeError, ValueError):
        page = 0
    if page < 1:
        page = int(row.last_page or 1)
    section = str(context.get("section") or "") or section_for_page(row, page)
    return _source_dict(row, page=page, section=section, snippet=selected)


def gather_sources(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    query: str,
    limit: int = _MAX_SOURCES,
) -> list[dict]:
    """挑出**本项目真实存在**的候选来源（先全文检索，再用项目文献兜底）。

    兜底很关键：中文/短查询常常检索不到命中，但"项目里到底有哪些文献"是
    确定事实——有它才能让模型在"确实有依据"时给出可核验的证据，
    而不是因为检索没命中就一律 `insufficient`。
    """
    out: list[dict] = []
    seen: set[str] = set()

    hits = knowledge_svc.search(
        db, tenant_id=tenant_id, project_id=project_id, query=query, limit=limit
    )
    for it in hits.get("items", []):
        if it.get("kind") != "paper":
            continue
        pid = str(it.get("paper_id") or "")
        if not pid or pid in seen:
            continue
        try:
            row = db.get(ResearchPaperRow, uuid.UUID(pid))
        except (ValueError, TypeError):
            continue
        if row is None or row.tenant_id != tenant_id or row.deleted_at is not None:
            continue
        seen.add(pid)
        out.append(
            _source_dict(
                row,
                page=int(it.get("page") or 0),
                section=str(it.get("section") or ""),
                snippet=str(it.get("snippet") or ""),
            )
        )

    if len(out) < limit:
        for row in list_papers(db, tenant_id=tenant_id, project_id=project_id):
            pid = str(row.id)
            if pid in seen:
                continue
            seen.add(pid)
            out.append(
                _source_dict(
                    row,
                    page=int(row.last_page or 1),
                    section="",
                    snippet="",
                )
            )
            if len(out) >= limit:
                break

    return out[:limit]


def _paper_context(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, context: dict
) -> dict:
    """当前论文上下文（来自真实项目数据；取不到就如实留空）。"""
    out = {
        "title": "",
        "authors": "",
        "year": 0,
        "section": str(context.get("section") or ""),
        "page": int(context.get("page") or 0) if str(context.get("page") or "").isdigit() else 0,
        "file": str(context.get("file") or ""),
        "line": int(context.get("line") or 0) if str(context.get("line") or "").isdigit() else 0,
    }
    raw = context.get("paper_id")
    if not raw:
        return out
    try:
        row = db.get(ResearchPaperRow, uuid.UUID(str(raw)))
    except (ValueError, TypeError):
        return out
    if row is None or row.tenant_id != tenant_id or row.deleted_at is not None:
        return out
    out["title"] = row.title or ""
    out["authors"] = _authors_text(row)
    out["year"] = int(row.year or 0)
    return out


# --------------------------------------------------------------------------
# LLM 调用 + 输出解析
# --------------------------------------------------------------------------


_HEX4_RE = re.compile(r"[0-9a-fA-F]{4}")

#: `\n`/`\t`/`\b`/`\f`/`\r` 与 LaTeX 命令名的**真实冲突**（实测踩到）：
#: JSON 里 `"\nabla"` 会被解析成「换行 + abla」，公式直接坏掉（宪法 §76 要求公式
#: 交给 KaTeX 正确渲染）。因此遇到"反斜杠 + 转义字母 + 后续字母"时，先判断后续字母
#: 是否构成一个**已知 LaTeX 命令**的尾部；是则按字面反斜杠处理。
_TEX_ESCAPE_COLLISIONS: dict[str, tuple[str, ...]] = {
    "n": ("nabla", "neq", "newline", "newcommand", "nonumber", "nolimits", "not", "notin", "norm", "natural", "nu", "nmid", "nsubseteq", "nparallel"),
    "t": ("times", "theta", "tau", "top", "to", "text", "textbf", "textit", "textrm", "textsf", "texttt", "tfrac", "tbinom", "tilde", "triangle", "tan", "tanh", "tag", "tiny", "thanks", "thickapprox"),
    "b": ("beta", "begin", "big", "bigg", "bigl", "bigr", "bmod", "bar", "boldsymbol", "bmatrix", "boxed", "bullet", "because", "bottom", "backslash", "bigcup", "bigcap", "biguplus"),
    "f": ("frac", "forall", "flat", "fbox", "footnotesize", "footnote"),
    "r": ("rho", "right", "rangle", "rVert", "rvert", "rceil", "rfloor", "ref", "rule", "raisebox", "rm", "rightarrow", "rightharpoonup", "rightharpoondown"),
}


def _is_tex_command_tail(escape: str, letters: str) -> bool:
    """`\\<escape><letters>` 是否其实是某个 LaTeX 命令（如 `\\nabla`）。"""
    return any(letters.startswith(cmd[1:]) for cmd in _TEX_ESCAPE_COLLISIONS.get(escape, ()))


def _repair_json_text(s: str) -> str:
    """修复模型**几乎总是**会犯的两类 JSON 错误，让结果可用而不是白跑一次调用。

    实测（真实模型 + 数学 prompt）：
    1. **LaTeX 反斜杠未转义** —— `{"math": "\\alpha^{1/2}"}` 里的 `\\a` 不是合法
       JSON 转义，`json.loads` 直接抛 `Invalid \\escape`。数学场景里这几乎是必然事件。
       更隐蔽的是 `\\nabla`：`\\n` 是**合法**转义，于是 `\\nabla` 被解析成
       「换行 + abla」——公式静默损坏（靠 `_is_tex_command_tail` 拦下）。
    2. **字符串里出现裸换行/制表符** —— 同样让 `json.loads` 失败。

    只做这两件事（外加截断补救），**不做**语义层面的猜测；修不好就如实降级。
    """
    out: list[str] = []
    i, n = 0, len(s)
    in_str = False
    while i < n:
        c = s[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
            i += 1
            continue
        if c == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt in "ntbfr":
                j = i + 2
                while j < n and s[j].isalpha():
                    j += 1
                if _is_tex_command_tail(nxt, s[i + 2 : j]):
                    out.append("\\\\")  # 其实是 LaTeX 命令 ⇒ 当字面反斜杠
                    i += 1
                    continue
            if nxt in '"\\/bfnrt':
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            if nxt == "u" and _HEX4_RE.fullmatch(s[i + 2 : i + 6]):
                out.append(s[i : i + 6])
                i += 6
                continue
            out.append("\\\\")  # 非法转义 ⇒ 当字面反斜杠
            i += 1
            continue
        if c == '"':
            in_str = False
            out.append(c)
            i += 1
            continue
        if c == "\n":
            out.append("\\n")
            i += 1
            continue
        if c == "\r":
            out.append("\\r")
            i += 1
            continue
        if c == "\t":
            out.append("\\t")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _loads(text: str) -> dict | None:
    """先按"LaTeX 安全"的方式解析，再退回原生解析。

    顺序很关键：`{"math": "\\beta"}` 用原生 `json.loads` **能解析成功**（`\\b` 是合法
    转义），但结果是「退格 + eta」——公式被静默改坏。所以必须先跑 `_repair_json_text`。
    """
    for candidate in (_repair_json_text(text), text):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    # 截断补救：从最后一个 `}` 往前找能解析的最长前缀。
    end = text.rfind("}")
    while end > 0:
        try:
            obj = json.loads(_repair_json_text(text[: end + 1]))
        except ValueError:
            end = text.rfind("}", 0, end)
            continue
        if isinstance(obj, dict):
            return obj
        end = text.rfind("}", 0, end)
    return None


def _extract_json(text: str) -> dict | None:
    """从模型输出里取第一个 JSON 对象（容忍 ```json 围栏与前后废话）。"""
    s = (text or "").strip()
    if not s:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    obj = _loads(s)
    if obj is not None:
        return obj
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        return _loads(s[start : end + 1])
    return None


async def _complete(messages: list[dict]) -> tuple[str | None, str]:
    """调用模型。返回 `(文本, 失败原因)`；失败原因 ∈ {"", "unavailable", "timeout", "error"}。

    超时是**硬上界**（`config.llm_total_timeout_s("short")`，默认 90s）：
    后台任务不该因为一次模型抖动而无限期停在 `running`（宪法 §54/§56）。
    """
    from ..config import llm_total_timeout_s

    backend = llm.get_backend()
    if not backend.available:
        return None, "unavailable"
    try:
        completion = await asyncio.wait_for(
            backend.acompletion(model=llm.llm_model_long(), messages=messages),
            timeout=llm_total_timeout_s("short"),
        )
    except (asyncio.TimeoutError, TimeoutError):
        return None, "timeout"
    except Exception:  # noqa: BLE001 - 失败必须降级成人话，不吞不炸
        return None, "error"
    return (completion.text or ""), ""


#: 降级文案（宪法 §56/§90）：说清"发生了什么 / 数据仍安全 / 可以做什么"，
#: **禁止**出现错误码、traceback、裸 JSON。
_DEGRADED_ZH = {
    "unavailable": "AI 服务暂时不可用，本次没能给出结论。你的论文内容没有改动，可以稍后重试。",
    "timeout": "本次校验等待超时，暂时没能给出结论。你的论文内容没有改动，可以稍后重试。",
    "error": "AI 服务本次没有正常返回，暂时没能给出结论。你的论文内容没有改动，可以稍后重试。",
    "unparsed": "本次没能形成可核验的结论。可以稍后重试，或先在输入框中写出待验证的等式。",
}

_DEGRADED_DETAIL = {
    "unavailable": "服务恢复后重新点击即可，已选中的内容不会丢失。",
    "timeout": "服务恢复后重新点击即可，已选中的内容不会丢失。",
    "error": "服务恢复后重新点击即可，已选中的内容不会丢失。",
    "unparsed": "如果陈述里有明确的等式与取值条件，写出来会更容易给出确定结论。",
}


def degraded_math_result(kind: str, reason: str, selected: str) -> dict:
    """LLM 路径失败时的**诚实**结果（宪法 §56/§90）。"""
    key = reason if reason in _DEGRADED_ZH else "error"
    return {
        "kind": kind,
        "status": "insufficient",
        "summary": _DEGRADED_ZH[key],
        "findings": [
            {
                "label": "本次没有形成结论",
                "detail": _DEGRADED_DETAIL[key],
                "math": sanitize_user_text(selected, limit=200),
            }
        ],
        "evidence": [],
        "source_locations": [],
        "patches": [],
        "actions": ["locate", "add_note"],
        "confidence": 0.0,
    }


# --------------------------------------------------------------------------
# 推导校验 / 公式检查
# --------------------------------------------------------------------------

_SYSTEM_MATH = (
    "你是资深应用数学研究者，为科研人员核验论文中的数学推导。\n"
    "只输出一个 JSON 对象，不要任何解释文字，不要 Markdown 代码围栏。\n\n"
    "JSON 结构：\n"
    "{\n"
    '  "status": "verified|supported|likely|needs_conditions|insufficient|conflict",\n'
    '  "summary": "2~3 句中文结论",\n'
    '  "findings": [{"label": "简短要点（≤24 字，可含 $行内公式$）", "detail": "必要时的一句补充，可为空", "math": ""}],\n'
    '  "missing_conditions": ["具体缺什么，例如：缺少 α>0 的 V-椭圆条件声明"],\n'
    '  "source_ids": ["候选来源的 id"],\n'
    '  "confidence": 0.0\n'
    "}\n\n"
    "判定规则（必须严格遵守）：\n"
    "1. 只有每一步都能由已声明的假设直接推出时，status 才能是 verified 或 supported。\n"
    "2. 结论本身可能成立、但论文里没有声明必要假设时，status 必须是 needs_conditions，"
    "并在 missing_conditions 里逐条写清**缺哪一个假设**；"
    "禁止写成笼统的「需要更多信息」。\n"
    "3. 选中内容不含可核验的数学陈述、或依据不足时，status 必须是 insufficient。\n"
    "4. source_ids 只能从下面给出的候选来源里选 id；"
    "不得编造论文、作者、年份、页码、DOI，也不要在 summary 里写来源信息。\n"
    "5. findings 用 3~5 条：前几条写「使用的条件」，后面写「推导逻辑」。"
    "**每条 label 必须是 ≤24 字的要点短语**（形如「$V_h \\subset V$，双线性形式连续且强制」"
    "「由 Galerkin 正交性得到误差上界」），**不要写成长句**；"
    "确需展开说明时放进 detail（一句话以内），否则 detail 留空。公式写在 $...$ 里。\n"
    "6. 面向科研人员的文字里禁止出现这些词：RAG、embedding、向量、top-k、chunk、"
    "工具调用、函数调用、模型名称、temperature、提示词。\n"
    "7. confidence 是你对自己结论的把握，取 0~1 之间的小数。"
)


def _math_user_prompt(
    *,
    preset: str,
    selected: str,
    paper: dict,
    sources: list[dict],
    extra: str,
) -> str:
    label = "推导校验" if preset == "derivation_check" else "公式检查"
    parts = [f"【任务】{label}：核验下面这段数学陈述是否成立、依据什么条件、推导逻辑是什么。", ""]
    parts += ["【待核验的数学陈述】", selected.strip(), ""]
    ctx = [
        f"标题：{paper['title']}" if paper["title"] else "",
        f"作者：{paper['authors']}" if paper["authors"] else "",
        f"年份：{paper['year']}" if paper["year"] else "",
        f"章节：{paper['section']}" if paper["section"] else "",
        f"页码：第 {paper['page']} 页" if paper["page"] else "",
        f"草稿位置：{paper['file']} 第 {paper['line']} 行" if paper["file"] and paper["line"] else "",
    ]
    ctx = [c for c in ctx if c]
    parts += ["【当前上下文】", ("；".join(ctx) if ctx else "（无）"), ""]
    if extra.strip():
        parts += ["【用户补充说明】", extra.strip(), ""]
    if sources:
        parts.append("【候选来源（source_ids 只能从这里选）】")
        for s in sources:
            head = " ".join(
                x for x in (s["authors"], f"({s['year']})" if s["year"] else "", s["title"]) if x
            ).strip()
            tail = []
            if s["page"]:
                tail.append(f"第 {s['page']} 页")
            if s["section"]:
                tail.append(f"章节 {s['section']}")
            if s["snippet"]:
                tail.append(f"摘录：{s['snippet']}")
            parts.append(f"- id={s['id']}｜{head}" + (f"｜{'｜'.join(tail)}" if tail else ""))
        parts.append("")
    else:
        parts += ["【候选来源】", "（当前项目没有可用文献，source_ids 必须为空数组）", ""]
    parts.append("现在只输出 JSON。")
    return "\n".join(parts)


#: 置信度基线（与 `agent._confidence` 同口径；此处内联避免反向 import）。
_STATUS_BASE = {
    "verified": 0.9,
    "supported": 0.75,
    "likely": 0.6,
    "conflict": 0.5,
    "needs_conditions": 0.4,
    "insufficient": 0.2,
}


def _clamp_confidence(raw: object, status: str) -> float:
    """模型给的把握度，收敛到该状态的合理区间。

    模型偶尔返回 `0` 或空值——那在界面上表现为"0% 把握"，读起来像坏了。
    因此低于该状态基线时抬到基线（**不**越过上限）。
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = 0.0
    base = _STATUS_BASE.get(status, 0.3)
    cap = {
        "verified": 0.95,
        "supported": 0.9,
        "likely": 0.85,
        "conflict": 0.7,
        "needs_conditions": 0.6,
        "insufficient": 0.3,
    }.get(status, 0.3)
    return round(max(base, min(value, cap)), 2)


def _norm_findings(raw: object) -> list[dict]:
    out: list[dict] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = sanitize_user_text(item.get("label"), limit=_MAX_FINDING)
            detail = sanitize_user_text(item.get("detail"), limit=_MAX_FINDING)
            math = str(item.get("math") or "").strip()[:400]
            if not (label or detail or math):
                continue
            out.append({"label": label, "detail": detail, "math": math})
            if len(out) >= _MAX_FINDINGS:
                break
    return out


def _norm_missing(raw: object) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            text = sanitize_user_text(item, limit=120)
            if text:
                out.append(text)
            if len(out) >= 5:
                break
    return out


def assemble_math_result(
    *,
    kind: str,
    parsed: dict,
    sources: list[dict],
    selected: str,
    anchor: dict | None = None,
) -> dict:
    """把模型 JSON + **真实**候选来源组装成结构化 Research Result（宪法 §58/§90）。"""
    status = str(parsed.get("status") or "").strip().lower()
    if status not in _LLM_STATUSES:
        status = "insufficient"

    findings = _norm_findings(parsed.get("findings"))
    missing = _norm_missing(parsed.get("missing_conditions"))
    summary = sanitize_user_text(parsed.get("summary"), limit=_MAX_SUMMARY)

    by_id = {s["id"]: s for s in sources}
    raw_ids = parsed.get("source_ids")
    cited: list[dict] = []
    if isinstance(raw_ids, list):
        for sid in raw_ids:
            key = str(sid or "").strip()
            if key in by_id and key not in {e["paper_id"] for e in cited}:
                cited.append(by_id[key])
            if len(cited) >= _MAX_EVIDENCE:
                break

    # §90：没有**模型自己核验过**的真实来源，就不许自称"已验证/证据充分"。
    if status in ("verified", "supported") and not cited:
        status = "likely"

    # §90/§35：本路径**不冒充确定性**——符号校验那条路才配 verified。
    if status == "verified":
        status = "likely"

    # 当前正在读的那篇论文那一页是**确定事实**（用户就是从那选的），
    # 无论模型有没有提到它，都作为第一条证据返回 —— 否则「定位原文」只能跳页。
    evidence: list[dict] = []
    if anchor is not None:
        evidence.append(anchor)
    for item in cited:
        if item["paper_id"] not in {e["paper_id"] for e in evidence}:
            evidence.append(item)
    evidence = evidence[: _MAX_EVIDENCE + 1]

    # §34：条件缺失必须点名缺什么，而不是"需要更多信息"。
    if missing:
        status = "needs_conditions"
        joined = "；".join(missing)
        if not any(m in summary for m in missing):
            summary = (summary.rstrip("。") + "。" if summary else "") + f"当前缺少的条件：{joined}。"
        findings.append(
            {
                "label": "当前论文中缺少的假设",
                "detail": joined,
                "math": "",
            }
        )

    if not summary:
        summary = "本次没能形成可核验的结论，可以补充取值条件后重试。"

    if not findings:
        findings = [
            {
                "label": "本次没有可复述的推导步骤",
                "detail": "选中内容里没有识别出可逐步核对的数学陈述。",
                "math": sanitize_user_text(selected, limit=200),
            }
        ]

    return {
        "kind": kind,
        "status": status,
        "summary": summary,
        "findings": findings[:_MAX_FINDINGS],
        "evidence": evidence,
        "source_locations": [],
        "patches": [],
        "actions": ["locate", "view_evidence", "add_note"] if evidence else ["locate", "add_note"],
        "confidence": _clamp_confidence(parsed.get("confidence"), status),
    }


async def reason_math(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    preset: str,
    kind: str,
    context: dict,
    selected: str,
) -> dict:
    """无结构化规格时的真实推理路径（推导校验 / 公式检查）。"""
    sources = gather_sources(
        db, tenant_id=tenant_id, project_id=project_id, query=selected, limit=_MAX_SOURCES
    )
    anchor = _anchor_source(
        db, tenant_id=tenant_id, project_id=project_id, context=context, selected=selected
    )
    if anchor is not None:
        # 当前论文那一页永远排第一：用户选中的文字就来自它。
        sources = [anchor] + [s for s in sources if s["id"] != anchor["id"]]
        sources = sources[:_MAX_SOURCES]
    paper = _paper_context(db, tenant_id=tenant_id, project_id=project_id, context=context)
    extra = str(context.get("query") or context.get("question") or "")

    messages = [
        {"role": "system", "content": _SYSTEM_MATH},
        {
            "role": "user",
            "content": _math_user_prompt(
                preset=preset, selected=selected, paper=paper, sources=sources, extra=extra
            ),
        },
    ]
    text, reason = await _complete(messages)
    if text is None:
        return degraded_math_result(kind, reason, selected)
    parsed = _extract_json(text)
    if parsed is None:
        return degraded_math_result(kind, "unparsed", selected)
    return assemble_math_result(
        kind=kind, parsed=parsed, sources=sources, selected=selected, anchor=anchor
    )


# --------------------------------------------------------------------------
# LaTeX 错误解释（宪法 §39）
# --------------------------------------------------------------------------

#: 编译器信息 → （中文错误类型, 内部类别）。顺序即优先级。
#:
#: ⚠️ 引用的**真实**日志形态是
#: ``LaTeX Warning: Citation `Cea1971' on page 1 undefined on input line 20.``
#: —— 标识符与 `undefined` **之间还夹着 "on page N"**，不能写成相邻匹配。
_ERROR_KINDS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"Citation\s+[`'\"]([^`'\"]+)[`'\"].{0,40}?\bundefined", re.I), "未定义的引用标识符", "citation"),
    (re.compile(r"Reference\s+[`'\"]([^`'\"]+)[`'\"].{0,40}?\bundefined", re.I), "未定义的交叉引用", "reference"),
    (re.compile(r"I can't find file|File\s+[`'\"]([^`'\"]+)[`'\"]\s+not found", re.I), "找不到文件", "file"),
    (re.compile(r"Undefined control sequence", re.I), "未定义的控制序列", "control"),
    (re.compile(r"Missing\s+\$\s+inserted", re.I), "数学模式定界符缺失", "mathshift"),
    (re.compile(r"Environment\s+[`'\"]?(\S+?)[`'\"]?\s+undefined", re.I), "未定义的环境", "environment"),
    (re.compile(r"Missing\s+\\begin\{document\}|\\begin\{document\}\s+ended by", re.I), "文档结构不完整", "document"),
    (re.compile(r"Too many \}'s|Missing \}\s+inserted", re.I), "花括号不匹配", "brace"),
    (re.compile(r"Runaway argument", re.I), "参数没有正常结束", "runaway"),
    (re.compile(r"Emergency stop", re.I), "编译被中止", "emergency"),
    (re.compile(r"Package\s+\S+\s+Error", re.I), "宏包错误", "package"),
    (re.compile(r"Undefined\s+font", re.I), "字体未定义", "font"),
    (re.compile(r"LaTeX Error", re.I), "LaTeX 排版错误", "latex"),
)

_UNDEFINED_RE = re.compile(
    r"(?:Citation|Reference)\s+[`'\"]([^`'\"]+)[`'\"].{0,40}?\bundefined", re.I
)
_CITE_KEY_RE = re.compile(r"\\(?:cite[pt]?|textcite|parencite|autocite)\s*(?:\[[^\]]*\])*\s*\{([^{}]*)\}")
_REF_KEY_RE = re.compile(r"\\(?:ref|eqref|autoref|pageref|cref|Cref)\s*\{([^{}]*)\}")
_BIB_KEY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
_CONTROL_SEQ_RE = re.compile(r"\\([A-Za-z@]+)")
_UNDEFINED_CS_NAME_RE = re.compile(r"^\s*\\([A-Za-z@]+)")


def classify_error(text: str) -> tuple[str, str]:
    """编译器原始信息 → `(中文错误类型, 内部类别)`（确定性，不调用模型）。"""
    raw = str(text or "")
    for pattern, zh, kind in _ERROR_KINDS:
        if pattern.search(raw):
            return zh, kind
    return "编译错误", "unknown"


def _rule_explanation(zh: str, kind: str, raw: str, key: str, line: int) -> str:
    """规则版人话解释（模型不可用时的**兜底**，也是 §90 的诚实下限）。"""
    where = f"第 {line} 行" if line else "相关位置"
    if kind == "citation":
        return (
            f"编译日志显示引用「{key}」未定义。这通常意味着参考文献文件里没有这个条目，"
            f"或标识符拼写与条目不一致。请检查 {where} 的引用键，确认它已在参考文献文件中登记。"
        )
    if kind == "reference":
        return (
            f"编译日志显示交叉引用「{key}」未定义。通常是标签拼写不一致，"
            f"或对应的 \\label 尚未插入。请核对 {where} 附近的标签与引用是否配对。"
        )
    if kind == "file":
        return (
            f"编译器找不到所需的文件。请确认文件路径拼写正确、文件已加入项目，"
            f"并注意大小写是否一致（{where}）。"
        )
    if kind == "control":
        return (
            f"编译器遇到它不认识的控制序列（命令），通常来自拼写错误、"
            f"缺少对应的宏包，或命令放错了位置。请检查 {where} 附近的命令拼写与所需宏包。"
        )
    if kind == "mathshift":
        return (
            "正文与数学模式之间的切换没有配对：可能有 $ 落单，"
            "或某个命令只能在数学模式里使用。请检查公式定界符是否成对。"
        )
    if kind == "environment":
        return "使用了未定义或未正确配对的环境。请检查 \\begin/\\end 的名称与配对情况。"
    if kind == "document":
        return "文档结构不完整：\\begin{document} 与 \\end{document} 不匹配，或导言区有语法错误。"
    if kind == "brace":
        return "花括号不匹配：某个命令的参数多写或少写了 }。请检查报错位置附近的花括号。"
    if kind == "runaway":
        return "某个命令的参数没有正常结束，通常是漏了右花括号。请检查报错位置附近。"
    if kind == "package":
        return "某个宏包在处理时出错。请检查报错位置附近该宏包相关命令的用法。"
    if kind == "emergency":
        return "编译器已中止：前面出现了无法恢复的错误，请先修复最早出现的那条错误。"
    if kind == "font":
        return "字体未定义或缺失。请确认所用字体已安装，或改用可用字体。"
    return "编译日志给出了一条错误，请检查报错位置附近的 LaTeX 语法。"


def _norm_rel_path(path: str) -> str:
    p = str(path or "").strip().replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def _basename(path: str) -> str:
    """相对路径的最后一段（`a/b/c.tex` → `c.tex`）。"""
    return _norm_rel_path(path).split("/")[-1]


def _stem(path: str) -> str:
    """相对路径最后一段**去掉扩展名**（`a/intro.tex` → `intro`；`a/intro` → `intro`）。"""
    b = _basename(path)
    return b.rsplit(".", 1)[0] if "." in b else b


def _dir_of(path: str) -> str:
    """相对路径的目录部分（无目录返回空串）。"""
    p = _norm_rel_path(path)
    return p.rsplit("/", 1)[0] if "/" in p else ""


def _strip_ext(path: str) -> str:
    """去掉**最后一段**的扩展名（保留目录）：`a/intro.tex` → `a/intro`。"""
    p = _norm_rel_path(path)
    if p.rfind(".") > p.rfind("/"):
        return p[: p.rfind(".")]
    return p


#: 从"找不到文件"的编译器信息里取文件名的**定向**模式（顺序即优先级）。
#:
#: 为什么不能用"第一个引号对"的通用匹配：真实信息是
#: ``! I can't find file `intro'.`` —— 里面先出现的是 **can't 的撇号**，通用
#: `[`'"]([^`'"]+)[`'"]` 会从那个撇号一直匹配到 `intro` 前的反引号，取到
#: 「t find file 」这种垃圾目标，于是永远匹配不到真实文件。必须锚定消息措辞。
_FILE_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"I can't find file\s*[`'\"]([^`'\"]+)[`'\"]", re.I),
    re.compile(r"File\s*[`'\"]([^`'\"]+)[`'\"]\s*not found", re.I),
    re.compile(
        r"[`'\"]([^`'\"\s]+\.(?:tex|bib|sty|cls|cfg|def|eps|pdf|png|jpe?g))[`'\"]",
        re.I,
    ),
)


def _file_target_from_raw(raw: str) -> str:
    """从编译器信息里取出"找不到的文件"名；取不到返回空串（绝不复用错误的引号对）。"""
    text = str(raw or "")
    for pat in _FILE_TARGET_PATTERNS:
        m = pat.search(text)
        if m:
            return _norm_rel_path(m.group(1))
    return ""


# --------------------------------------------------------------------------
# LaTeX 常用命令白名单（**受控**：`control` 类缺陷的最近邻只在其中匹配）
#
# 为什么必须白名单：编译器只告诉我们"有个未定义的控制序列"，命令名要落回
# **真实源码行**去认。若拿"任意 `\foo`"去猜，就会把用户自定义宏也误当成笔误
# 而给出假 Diff（宪法 §14/§33 禁止）。只在**受控白名单**里做最近邻，才既可能
# 抓到 `\\tabel`→`\\table` 这类拼写/手误，又不会凭空发明命令。
# --------------------------------------------------------------------------
_KNOWN_TEX_COMMANDS: tuple[str, ...] = (
    # 文档结构
    "documentclass", "usepackage", "RequirePackage", "begin", "end", "title",
    "author", "date", "maketitle", "thanks", "and", "part", "chapter", "section",
    "subsection", "subsubsection", "paragraph", "subparagraph", "appendix",
    "tableofcontents", "listoffigures", "listoftables", "newpage", "clearpage",
    "cleardoublepage", "pagestyle", "thispagestyle", "pagenumbering", "frontmatter",
    "mainmatter", "backmatter", "item", "itemize", "enumerate", "description",
    # 引用 / 文献
    "label", "ref", "eqref", "pageref", "autoref", "cref", "Cref", "cite", "citep",
    "citet", "citealp", "citeauthor", "citeyear", "textcite", "parencite",
    "autocite", "nocite", "bibliography", "bibliographystyle", "addbibresource",
    "printbibliography", "footcite", "footnote", "url", "href", "hyperref",
    "input", "include", "includeonly", "subfile", "inputenc",
    # 文本 / 字体 / 间距
    "text", "textrm", "textsf", "texttt", "textbf", "textit", "textsc", "textsl",
    "emph", "underline", "mbox", "hbox", "vbox", "parbox", "fbox", "framebox",
    "colorbox", "fcolorbox", "raisebox", "rule", "centering", "raggedright",
    "raggedleft", "noindent", "indent", "par", "newline", "linebreak", "hfill",
    "vfill", "hspace", "vspace", "smallskip", "medskip", "bigskip", "quad",
    "qquad", "kill", "baselineskip", "footnotesize", "scriptsize", "tiny",
    "small", "normalsize", "large", "Large", "LARGE", "huge", "Huge",
    # 表格 / 图 / 浮动体
    "table", "tabular", "tabularx", "tabbing", "multicolumn", "multirow",
    "hline", "cline", "toprule", "midrule", "bottomrule", "cmidrule", "caption",
    "figure", "includegraphics", "subfigure", "subcaption", "subfloat",
    "float", "rowcolors", "cellcolor", "resizebox", "scalebox", "adjustbox",
    # 数学
    "frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom", "sqrt",
    "sum", "prod", "coprod", "int", "iint", "iiint", "oint", "partial", "nabla",
    "infty", "lim", "limsup", "liminf", "log", "ln", "lg", "exp", "sin", "cos",
    "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh",
    "tanh", "min", "max", "sup", "inf", "argmin", "argmax", "gcd", "operatorname",
    "left", "right", "middle", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
    "langle", "rangle", "lvert", "rvert", "lVert", "rVert", "vert", "Vert",
    "mathbf", "mathrm", "mathit", "mathcal", "mathbb", "mathfrak", "mathsf",
    "mathtt", "boldsymbol", "bm", "hat", "widehat", "bar", "overline", "tilde",
    "widetilde", "vec", "dot", "ddot", "overbrace", "underbrace", "overset",
    "underset", "stackrel", "begin", "end", "displaystyle", "textstyle",
    "scriptstyle", "limits", "nolimits", "not", "neq", "leq", "geq", "approx",
    "equiv", "sim", "simeq", "propto", "in", "notin", "subset", "subseteq",
    "supset", "supseteq", "cup", "cap", "setminus", "emptyset", "forall",
    "exists", "neg", "land", "lor", "to", "rightarrow", "leftarrow", "mapsto",
    "Rightarrow", "Leftarrow", "Leftrightarrow", "times", "cdot", "pm", "mp",
    "div", "ast", "circ", "oplus", "otimes", "alpha", "beta", "gamma", "delta",
    "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta", "iota", "kappa",
    "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi",
    "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi",
    "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
    # 定义 / 宏 / 常见
    "newcommand", "renewcommand", "providecommand", "DeclareMathOperator", "def",
    "let", "newenvironment", "renewenvironment", "if", "else", "fi", "ifx",
    "newif", "ensuremath", "protect", "leavevmode", "phantom", "vphantom",
    "hphantom", "makebox", "mspace", "textwidth", "linewidth", "columnwidth",
    "textheight", "baselinestretch", "setlength", "addtolength", "vskip",
    "hskip", "line", "underline", "framebox", "parbox", "settowidth",
)
_KNOWN_TEX_COMMAND_SET: frozenset[str] = frozenset(_KNOWN_TEX_COMMANDS)

#: `control` 类最近邻的相似度下限。取 0.7：能抓到 `\\tabel`→`\\table`（0.8），
#: 又挡掉 `\\subref`→`\\ref`（0.67）这类"合法但不在白名单"的误替换。
_CONTROL_MATCH_CUTOFF = 0.7


def _common_prefix_len(a: str, b: str) -> int:
    """两串的公共前缀长度（用于打破打字错误的相似度并列）。"""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _control_candidate_score(name: str, cand: str) -> tuple[float, int, int]:
    """可疑命令 → 白名单候选的可比分数（越大越好）。

    主分是相似度；并列时**公共前缀更长者优先**——打字错误（尤其字母转置，
    如 `tabel`↔`table`）几乎不改首字母，而相似度对 `tabel`→`table` 与
    `tabel`→`label` 会并列（都是 0.8）。用公共前缀打破并列，既确定性又更贴近
    真实手误；末位再看长度差（更接近者优先）。
    """
    ratio = difflib.SequenceMatcher(None, name, cand).ratio()
    return (round(ratio, 4), _common_prefix_len(name, cand), -abs(len(name) - len(cand)))


def _undefined_control_patch(
    *, source_line: str, file_: str, line: int
) -> dict | None:
    """`Undefined control sequence` 的**真实可核对**建议修改（拿不到就返回 None）。

    思路：编译器只说"有个未定义命令"，命令名从**真实源码行**里认——取该行所有
    `\\cmd`，其中**不在受控白名单**里的就是可疑手误；在**白名单**内做最近邻
    （如 `\\tabel`→`\\table`）。只有（a）可疑命令唯一且能命中白名单、
    （b）替换后**源码行真的变了**时才产出 patch；否则返回 None。

    绝不发明命令、绝不给"看起来像修复"的假 Diff（本文件 §39/§40 的原则）。
    """
    names: list[str] = []
    for m in _CONTROL_SEQ_RE.finditer(source_line):
        name = m.group(1)
        if name and name not in names:
            names.append(name)
    unknown = [n for n in names if n not in _KNOWN_TEX_COMMAND_SET]
    if not unknown:
        return None

    scored: list[tuple[tuple[float, int, int], str, str]] = []
    for name in unknown:
        best: tuple[tuple[float, int, int], str] | None = None
        for cand in _KNOWN_TEX_COMMANDS:
            if cand == name:
                continue
            # 长度守卫：打字错误通常只差 0~1 个字符；差 2 个以上更像**另一个命令**
            # （如自定义宏 `\myvec` 与 `\vec`），不能当成笔误（否则就是假 Diff）。
            if abs(len(name) - len(cand)) > 1:
                continue
            score = _control_candidate_score(name, cand)
            if score[0] < _CONTROL_MATCH_CUTOFF:
                continue
            if best is None or score > best[0]:
                best = (score, cand)
        if best is not None:
            scored.append((best[0], name, best[1]))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    # 并列第一 ⇒ 歧义（改哪个都可能是错的）⇒ 宁可不给。
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    _, name, replacement = scored[0]
    if replacement == name:
        return None
    after = re.sub(
        r"\\" + re.escape(name) + r"(?![A-Za-z@])",
        lambda _m: "\\" + replacement,
        source_line,
        count=1,
    )
    if after == source_line:
        return None
    return {
        "file": file_,
        "line": line,
        "before": source_line,
        "after": after,
        "reason": (
            f"编译器不认识命令「\\{name}」；项目中常用的同名命令是「\\{replacement}」，"
            f"请确认是否为笔误。"
        ),
    }


def _match_project_file(target: str, pool: list[str]) -> str:
    """在项目**真实文件**里找出 `target` 指向的文件；找不到或**有歧义**返回空串。

    - 带扩展名：先按完整相对路径做最近邻，再按 basename 做最近邻；
    - **不带扩展名**（如 ``\\input{intro}``）：按"去扩展名的 basename（stem）"匹配，
      **仅当唯一命中**时才返回；多个候选（歧义）就返回空串——宁可不出补丁，
      也不改错文件（宪法 §14/§33）。
    """
    t_base = _basename(target)
    if not t_base:
        return ""
    if "." in t_base:
        hit = difflib.get_close_matches(target, pool, n=1, cutoff=0.6)
        if hit:
            return hit[0]
        by_base: dict[str, str] = {}
        for p in pool:
            by_base.setdefault(_basename(p), p)
        hit2 = difflib.get_close_matches(t_base, list(by_base), n=1, cutoff=0.6)
        return by_base[hit2[0]] if hit2 else ""
    # 无扩展名 —— 按 stem 匹配
    t_stem = t_base
    by_stem: dict[str, list[str]] = {}
    for p in pool:
        by_stem.setdefault(_stem(p), []).append(p)
    exact = by_stem.get(t_stem, [])
    if "/" in target and exact:
        same_dir = [p for p in exact if _dir_of(p) == _dir_of(target)]
        if same_dir:
            exact = same_dir
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return ""
    close = difflib.get_close_matches(t_stem, list(by_stem), n=2, cutoff=0.5)
    if not close:
        return ""
    if len(close) > 1:
        r0 = difflib.SequenceMatcher(None, t_stem, close[0]).ratio()
        r1 = difflib.SequenceMatcher(None, t_stem, close[1]).ratio()
        if abs(r0 - r1) < 1e-9:
            return ""
    cands = by_stem[close[0]]
    if "/" in target:
        same_dir = [p for p in cands if _dir_of(p) == _dir_of(target)]
        if same_dir:
            cands = same_dir
    return cands[0] if len(cands) == 1 else ""


def read_source_line(files: list, path: str, line: int) -> str:
    """取**真实**源码行（取不到返回空串——绝不编造 before）。"""
    if line < 1:
        return ""
    want = _norm_rel_path(path)
    for f in files:
        if _norm_rel_path(f.path) != want:
            continue
        lines = str(f.content or "").splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return ""
    return ""


def _bib_keys(files: list) -> list[str]:
    keys: list[str] = []
    for f in files:
        if f.kind != "bib":
            continue
        for m in _BIB_KEY_RE.finditer(str(f.content or "")):
            key = m.group(1).strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def _label_keys(files: list) -> list[str]:
    keys: list[str] = []
    for f in files:
        if f.kind != "tex":
            continue
        for m in re.finditer(r"\\label\s*\{([^{}]*)\}", str(f.content or "")):
            key = m.group(1).strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def _nearest(candidate: str, pool: list[str]) -> str:
    if not pool:
        return ""
    hit = difflib.get_close_matches(candidate, pool, n=1, cutoff=0.45)
    return hit[0] if hit else pool[0]


def undefined_key(raw: str) -> str:
    """从"引用/交叉引用未定义"的编译器信息里取出那个标识符（取不到返回空串）。"""
    m = _UNDEFINED_RE.search(str(raw or ""))
    return (m.group(1) if m else "").strip()


def build_error_item(*, log_line: dict, entry: str, files: list) -> dict:
    """把**一条真实日志行** + **真实源码行**组装成待解释的问题项。

    `file` / `line` / `source_line` 全部来自真实数据；模型只被允许填
    "人话解释 / 可能原因 / 建议"（见 `explain_latex_errors`）。
    """
    raw = str(log_line.get("text") or "")
    file_ = _norm_rel_path(str(log_line.get("file") or entry)) or entry
    line = int(log_line.get("line") or 0)
    title, kind = classify_error(raw)
    return {
        "title": title,
        "kind": kind,
        "raw": raw,
        "file": file_,
        "line": line,
        "source_line": read_source_line(files, file_, line),
        "key": undefined_key(raw),
        "bib_keys": _bib_keys(files),
        "label_keys": _label_keys(files),
    }


#: 源码里"引用外部文件"的命令（用于把**找不到的文件**回定位到真实引用行）。
_FILE_REF_RE = re.compile(
    r"\\(?:input|include|includegraphics|bibliography|addbibresource|subfile)"
    r"\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}"
)


def _locate_reference_line(files: list, target: str) -> tuple[str, int, str] | None:
    """在**项目真实源码**里找引用 `target` 的那一行（**唯一命中**才返回）。

    为什么需要它：真实 xelatex 对"找不到文件"报的是
    ``! LaTeX Error: File `intro.tex' not found.``，**没有行号**（后面的
    `l.<n>` 行不存在）。只靠日志行号就永远拿不到 before/after。这里退一步，
    在项目真实文件里搜"引用了这个名字"的行；命中唯一才采用——**不是猜行号**，
    是读真实源码；歧义（多处引用）就返回 None，宁可不给（§14/§33）。
    """
    t_base = _basename(target)
    t_stem = _stem(target)
    if not t_base:
        return None
    hits: list[tuple[str, int, str]] = []
    for f in files:
        for i, line in enumerate(str(f.content or "").splitlines(), start=1):
            for m in _FILE_REF_RE.finditer(line):
                arg = _norm_rel_path(m.group(1).strip())
                if _basename(arg) == t_base or _stem(arg) == t_stem:
                    hits.append((_norm_rel_path(f.path), i, line))
    if len(hits) == 1:
        return hits[0]
    return None


def _file_patch(
    *, raw: str, source_line: str, file_: str, line: int, files: list
) -> dict | None:
    """`找不到文件` 类缺陷的真实建议修改（带/不带扩展名都支持；拿不到就 None）。"""
    target = _file_target_from_raw(raw)
    if not target:
        return None
    pool = [_norm_rel_path(f.path) for f in files if f.path]
    # 目标文件确实存在 ⇒ 不是"找不到文件"，无需修改。
    if not pool or target in pool:
        return None
    replacement = _match_project_file(target, pool)
    if not replacement or replacement == target:
        return None

    # 日志没给行号（真实 xelatex 的 "File ... not found" 就没有）⇒ 回真实源码找引用行。
    if not source_line.strip() or line < 1:
        located = _locate_reference_line(files, target)
        if located is None:
            return None
        file_, line, source_line = located

    t_base = _basename(target)
    t_stem = _stem(target)
    if not t_base:
        return None
    # 源码里可能写的是"带扩展名"或"不带扩展名"两种形式，二者取其一命中即可。
    tokens = [t_base] if t_base == t_stem else [t_base, t_stem]
    match: re.Match[str] | None = None
    matched_token = ""
    for token in tokens:
        candidate = re.search(
            r"(?<![A-Za-z0-9_.\-])" + re.escape(token) + r"(?![A-Za-z0-9_.\-])",
            source_line,
        )
        if candidate is not None:
            match, matched_token = candidate, token
            break
    if match is None:
        return None
    # 源码里已有目录（如 `\input{sections/intro}`）⇒ 只替换文件名词干；
    # 源码里没有目录 ⇒ 带上 replacement 的**项目相对路径**，否则跨目录引用仍会找不到。
    had_dir = match.start() > 0 and source_line[match.start() - 1] in "/\\"
    if had_dir:
        new_ref = _basename(replacement)
    else:
        new_ref = replacement if "." in matched_token else _strip_ext(replacement)
    after = source_line[: match.start()] + new_ref + source_line[match.end() :]
    if after == source_line:
        return None
    return {
        "file": file_,
        "line": line,
        "before": source_line,
        "after": after,
        "reason": f"项目中已存在的文件是「{replacement}」，请确认是否为想要引用的文件。",
    }


def build_patch(
    *, kind: str, raw: str, source_line: str, file_: str, line: int, files: list
) -> dict | None:
    """生成**真实可核对**的建议修改；拿不到真实 before/after 就不产出 patch。

    §39 要求"建议修改 + 代码 Diff"，§40 要求改动必须经用户确认。
    这里的原则是：**宁可没有 Diff，也不给一个看起来像修复的假 Diff**。
    """
    if kind == "file":
        # 找不到文件类：真实日志**常常没有行号**，故先于"行号/源码行"守卫处理。
        return _file_patch(
            raw=raw, source_line=source_line, file_=file_, line=line, files=files
        )

    if not source_line.strip() or line < 1:
        return None

    if kind in ("citation", "reference"):
        m = _UNDEFINED_RE.search(raw)
        key = (m.group(1) if m else "").strip()
        if not key:
            return None
        if kind == "citation":
            pool = _bib_keys(files)
            pattern = _CITE_KEY_RE
            missing_hint = f"请在参考文献文件中补充条目「{key}」"
        else:
            pool = _label_keys(files)
            pattern = _REF_KEY_RE
            missing_hint = f"请确认标签「{key}」对应的 \\label 已插入"
        if not pool:
            return {
                "file": file_,
                "line": line,
                "before": source_line,
                "after": source_line,
                "reason": f"{missing_hint}；项目里目前没有可替换的标识符。",
            }
        # 引用键必须**真实存在于源码行**，否则说明行号对不上，不能改。
        if not pattern.search(source_line):
            return None
        replacement = _nearest(key, pool)
        if not replacement or replacement == key:
            return None
        after = pattern.sub(
            lambda mm: mm.group(0).replace(key, replacement, 1), source_line, count=1
        )
        if after == source_line:
            return None
        return {
            "file": file_,
            "line": line,
            "before": source_line,
            "after": after,
            "reason": f"{missing_hint}；也可以改用项目中已存在的标识符「{replacement}」。",
        }

    if kind == "control":
        # 未定义的控制序列（LaTeX 最常见错误之一）：命令名从**真实源码行**认，
        # 最近邻只在**受控白名单**里做（详见 `_undefined_control_patch`）。
        return _undefined_control_patch(source_line=source_line, file_=file_, line=line)

    return None


_SYSTEM_TEX = (
    "你是 LaTeX 排版专家，为科研人员解释真实编译日志中的问题。\n"
    "只输出一个 JSON 对象，不要解释文字，不要 Markdown 代码围栏：\n"
    '{"items":[{"index":0,"explanation":"1~3 句中文解释","causes":["可能原因"],'
    '"suggestion":"一句中文建议"}]}\n'
    "要求：\n"
    "1. explanation 面向科研人员，说清哪里错了、为什么；\n"
    "2. causes 给 1~3 条最可能的原因；suggestion 给 1 句可执行的修改建议；\n"
    "3. 只能依据给出的信息作答，**不得编造**文件名、行号、命令、文献标识符；\n"
    "4. 禁止出现实现术语：模型、提示词、temperature、embedding、向量、RAG、chunk、工具调用。"
)


def _tex_user_prompt(items: list[dict]) -> str:
    parts = ["【真实编译问题清单】"]
    for i, it in enumerate(items):
        parts.append(f"--- index={i} ---")
        parts.append(f"错误类型：{it['title']}")
        parts.append(f"文件与行号：{it['file']} 第 {it['line']} 行" if it["line"] else f"文件：{it['file']}")
        parts.append(f"编译器原始信息：{it['raw']}")
        if it["source_line"]:
            parts.append(f"该行真实源码：{it['source_line']}")
        if it["bib_keys"]:
            parts.append(f"项目参考文献中已有的标识符：{', '.join(it['bib_keys'][:12])}")
        if it["label_keys"]:
            parts.append(f"项目源码中已有的标签：{', '.join(it['label_keys'][:12])}")
    parts.append("")
    parts.append("现在只输出 JSON，items 的 index 必须与上面一致。")
    return "\n".join(parts)


def extract_json(text: str) -> dict | None:
    """公开入口：把模型返回的文本（可能带围栏 / LaTeX 反斜杠 / 裸换行）解析成 dict。

    `project memory summary` 也要走同一套解析，避免两处各写一份 JSON 修复逻辑。
    """
    return _extract_json(text)


def _degrade_item_explanation(it: dict) -> dict:
    return {
        "title": it["title"],
        "explanation": _rule_explanation(it["title"], it["kind"], it["raw"], it["key"], it["line"]),
        "causes": [],
        "suggestion": "",
    }


def _apply_llm_explanations(items: list[dict], parsed: dict) -> list[dict]:
    """把模型的解释合并进**确定性骨架**（file/line/patch 一律不受模型影响）。"""
    out = [_degrade_item_explanation(it) for it in items]
    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        return out
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(out)):
            continue
        explanation = sanitize_user_text(entry.get("explanation"), limit=_MAX_SUMMARY)
        if explanation:
            out[idx]["explanation"] = explanation
        causes = entry.get("causes")
        if isinstance(causes, list):
            cleaned = [sanitize_user_text(c, limit=160) for c in causes]
            out[idx]["causes"] = [c for c in cleaned if c][:3]
        suggestion = sanitize_user_text(entry.get("suggestion"), limit=200)
        if suggestion:
            out[idx]["suggestion"] = suggestion
    return out


def compose_summary(explanation: str, causes: list[str], suggestion: str) -> str:
    """解释 + 可能原因 + 建议 → 卡片正文（黄金图的解释正文就是这个语感）。"""
    parts = [explanation.strip()]
    if causes:
        joined = "；".join(c.strip().rstrip("。.;；") for c in causes if c.strip())
        if joined:
            parts.append("可能原因：" + joined + "。")
    if suggestion:
        parts.append(suggestion.strip())
    text = " ".join(p for p in parts if p)
    # 折叠重复句读（模型常写 "…错。。"）——只做标点规范化，不改语义。
    text = re.sub(r"([。！？；])\1+", r"\1", text)
    return sanitize_user_text(text, limit=_MAX_SUMMARY) or "编译器报告了一个问题，请检查对应位置。"


async def explain_latex_errors(
    *, items: list[dict], files: list
) -> list[dict]:
    """对真实编译问题清单产出解释（模型增强，失败自动退化为规则版）。

    `items` 的 `file`/`line`/`source_line`/`raw` **必须**由调用方从真实日志与
    真实源码行填好；本函数**只**负责"人话解释 / 可能原因 / 建议"这三项。
    """
    if not items:
        return []
    messages = [
        {"role": "system", "content": _SYSTEM_TEX},
        {"role": "user", "content": _tex_user_prompt(items)},
    ]
    text, reason = await _complete(messages)
    if text is None:
        # §56/§90：模型不可用时仍然给出**规则版**人话解释（不是空卡、不是错误码）。
        return [_degrade_item_explanation(it) for it in items]
    parsed = _extract_json(text)
    if parsed is None:
        return [_degrade_item_explanation(it) for it in items]
    return _apply_llm_explanations(items, parsed)


__all__ = [
    "assemble_math_result",
    "build_error_item",
    "build_patch",
    "classify_error",
    "compose_summary",
    "degraded_math_result",
    "explain_latex_errors",
    "extract_json",
    "gather_sources",
    "locate_bbox",
    "read_source_line",
    "reason_math",
    "sanitize_user_text",
    "section_for_page",
    "undefined_key",
]
