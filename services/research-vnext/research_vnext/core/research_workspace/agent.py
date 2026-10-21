"""RV17 · Research Agent 统一任务（P0-7）。

统一入口避免"每加一个能力再造一种卡片"：`preset → 真实后端能力`，
产出**结构化 Research Result**（宪法 §58）：

    {kind, status, summary, findings, evidence, source_locations, patches, actions, confidence}

`preset → 能力` 映射（**禁止写死模板**）
--------------------------------------------
| preset | 真实落点 |
|---|---|
| derivation_check | 有结构化规格 → `run_offline_check(MATH_CHECK)`；否则 → 选中文本 + 真实论文上下文 + 项目真实文献 → 模型推理（`reasoning.reason_math`） |
| formula_check    | 同上（确定性符号-数值检查 / 模型推理） |
| citation         | 项目文献 + `literature_record` 的真实匹配；核验不到 → `unverified` |
| latex_compare    | paper 章节结构 vs 当前 `.tex` 大纲（`outline.parse_outline`）的对账 |
| translate        | `writing.service.translate`（保护 `\\cite`/`\\ref`/math） |
| polish           | LLM 网关润色 + 受保护 token 守恒校验 |
| latex_error      | `compile-runs/latest` 的 error 行（无 error 时退到"引用未定义"类警告）→ 真实 file/line/源码行 → 解释 + patch |
| suggestion       | 项目知识（全文检索）+ 项目数据（未读文献等） |

任务生命周期 `queued → running → completed|failed` 落 `research_agent_task`，
关闭 Accordion 不丢（宪法 §54）。`patches` **不自动写入**：前端须用户确认后
走 `PUT /files/{path}`（宪法 §32/§40）。
"""

from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..api.deps import AccessContext
from ..domain.errors import DomainError
from ..literature.models import LiteratureRecordRow
from ..verification.adapters import run_offline_check
from . import knowledge as knowledge_svc
from . import reasoning as reasoning_svc
from . import terminology
from .files import list_files, outline as outline_svc
from .models import (
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_QUEUED,
    TASK_RUNNING,
    ResearchAgentTaskRow,
    ResearchPaperRow,
    utcnow,
)
from .papers import get_paper, list_papers
from .compile import latest_run as latest_compile_run

PRESETS = (
    "derivation_check",
    "citation",
    "latex_compare",
    "formula_check",
    "translate",
    "polish",
    "latex_error",
    "suggestion",
)

#: preset → Research Result `kind`（多数同名；translate/polish 分别成型）。
_KIND = {
    "derivation_check": "derivation_check",
    "citation": "citation",
    "latex_compare": "latex_compare",
    "formula_check": "formula_check",
    "translate": "translate",
    "polish": "polish",
    "latex_error": "latex_error",
    "suggestion": "suggestion",
}

#: Research Result 的封闭 status 词表。
RESULT_STATUSES = frozenset(
    {"verified", "supported", "likely", "needs_conditions", "insufficient", "conflict", "failed"}
)

_PROTECTED_RE = re.compile(r"\\(?:cite[pt]?|ref|eqref|label|autoref|pageref)\s*\{[^{}]*\}")

#: 确定性校验结论 → 科研人员可读的中文（宪法 §35：结论必须带置信状态）。
_VERDICT_ZH = {"PASS": "通过", "FAIL": "未通过", "UNKNOWN": "无法判定"}

#: 校验未决原因码 → 可读标题（**不得**把内部编码暴露给科研用户，宪法 §85）。
_UNKNOWN_LABEL = {
    "NO_CHECKS": "未提供可校验的条件",
    "POINTS_MISSING": "缺少取值条件",
    "UNKNOWN_CHECK_KIND": "暂不支持的校验类型",
    "EXPRESSION_REJECTED": "无法解析的表达式",
    "EVALUATION_FAILED": "取值代入无法计算",
}

#: 缺少可校验输入时，按能力给出**学术化**表述（宪法 §30/§34：不得要求用户填参数）。
_MATH_HINT = {
    "derivation_check": {
        "summary": "当前上下文还缺少可校验的等式或假设。请在论文中选中要校验的推导步骤，"
        "或在输入框里写出待验证的等式。",
        "label": "缺少可校验的条件",
        "detail": "要给出可复现的推导结论，需要明确所依据的假设与等式。"
        "可先选中论文中的相关推导或公式，Agent 会据此逐步核对。",
    },
    "formula_check": {
        "summary": "当前上下文还缺少可校验的公式。请在论文中选中要检查的公式，"
        "或在输入框里写出待验证的等式。",
        "label": "缺少可校验的公式",
        "detail": "公式检查需要明确的等式与取值条件。"
        "可先选中论文中的公式，Agent 会据此代入验证。",
    },
}

#: 目标语言代码 → 中文名（避免向用户展示语言代码）。
_LANG_ZH = {"en": "英文", "zh": "中文", "cn": "中文", "ja": "日文", "de": "德文", "fr": "法文"}


def _lang_zh(code: str) -> str:
    c = (code or "").strip().lower()
    return _LANG_ZH.get(c, code or "目标语言")


def _unknown_finding(raw: object) -> dict:
    """把校验未决原因渲染成科研人员可读的说明（不暴露内部编码）。"""
    code, _, rest = str(raw).partition(":")
    name = rest.split(":", 1)[0]
    label = _UNKNOWN_LABEL.get(code, "暂无法判定")
    if code == "POINTS_MISSING":
        detail = f"校验项「{name}」缺少取值条件，暂时无法代入验证。"
    elif code == "EXPRESSION_REJECTED":
        detail = f"校验项「{name}」的表达式无法解析，本次未参与判定。"
    elif code == "EVALUATION_FAILED":
        detail = f"校验项「{name}」在代入时无法计算，本次未参与判定。"
    elif code == "UNKNOWN_CHECK_KIND":
        detail = "该类型的校验暂不支持，本次未参与判定。"
    elif code == "NO_CHECKS":
        detail = "没有可执行的校验项。"
    else:
        detail = "该项证据不足，暂无法判定。"
    return {"label": label, "detail": detail, "math": ""}


class UnknownPreset(DomainError):
    code = "UNKNOWN_PRESET"
    http_status = 422


def _empty_result(kind: str, status: str, summary: str) -> dict:
    return {
        "kind": kind,
        "status": status if status in RESULT_STATUSES else "insufficient",
        "summary": summary,
        "findings": [],
        "evidence": [],
        "source_locations": [],
        "patches": [],
        "actions": [],
        "confidence": 0.0,
    }


def _confidence(status: str, *, evidence: int = 0, findings: int = 0) -> float:
    base = {
        "verified": 0.9,
        "supported": 0.75,
        "likely": 0.6,
        "needs_conditions": 0.4,
        "insufficient": 0.2,
        "conflict": 0.5,
        "failed": 0.0,
    }.get(status, 0.3)
    boost = min(0.08, 0.02 * evidence + 0.01 * findings)
    return round(min(0.99, base + boost), 2)


def _idempotency_lock_key(tenant_id: uuid.UUID, actor_id: uuid.UUID, cid: str) -> int:
    """`(tenant_id, actor_id, client_request_id)` → 64 位有符号 advisory lock 键。"""
    digest = hashlib.sha256(f"{tenant_id}|{actor_id}|{cid}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def create_task(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID,
    preset: str,
    context: dict | None = None,
    client_request_id: str = "",
) -> tuple[ResearchAgentTaskRow, bool]:
    """创建任务。返回 `(row, created)` —— `created=False` 表示命中幂等、复用既有任务。

    幂等键 `(tenant_id, actor_id, client_request_id)`（宪法 §31/§54）：
    同一键重复提交**只落一条任务**，并返回同一个 `task_id`。
    键由客户端提供（body `client_request_id` 或 `X-Client-Request-Id` 头）；
    没提供就退化为"每次都新建"（与改动前行为一致）。

    实现说明：`research_agent_task` 表**没有** `client_request_id` 列，本函数
    不改 schema —— 键存在 `context` JSONB 里，并用事务级 advisory lock 把并发的
    重复请求串行化，避免两条同时插入。
    """
    p = (preset or "").strip()
    if p not in PRESETS:
        raise UnknownPreset(f"暂不支持该科研能力：{p!r}")
    cid = str(client_request_id or "").strip()[:128]
    ctx_body = dict(context or {})

    if cid:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": _idempotency_lock_key(ctx.tenant_id, ctx.actor_id, cid)},
        )
        existing = (
            db.execute(
                select(ResearchAgentTaskRow)
                .where(
                    ResearchAgentTaskRow.tenant_id == ctx.tenant_id,
                    ResearchAgentTaskRow.actor_id == ctx.actor_id,
                    ResearchAgentTaskRow.context["client_request_id"].astext == cid,
                )
                .order_by(ResearchAgentTaskRow.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return existing, False
        ctx_body["client_request_id"] = cid

    now = utcnow()
    row = ResearchAgentTaskRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        actor_id=ctx.actor_id,
        preset=p,
        kind=_KIND[p],
        context=ctx_body,
        status=TASK_QUEUED,
        result={},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row, True


def task_public_dict(row: ResearchAgentTaskRow) -> dict:
    result = dict(row.result or {})
    if not result:
        result = _empty_result(row.kind or _KIND.get(row.preset, row.preset), "insufficient", "任务尚未完成")
    return {
        **result,
        "task": {
            "id": str(row.id),
            "preset": row.preset,
            "status": row.status,
            "error_code": row.error_code or "",
            "error_message": row.error_message or "",
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        },
    }


def get_task(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, task_id: uuid.UUID
) -> ResearchAgentTaskRow:
    from ..domain.errors import NotFound

    row = db.get(ResearchAgentTaskRow, task_id)
    if row is None or row.tenant_id != tenant_id or row.project_id != project_id:
        raise NotFound(f"任务不存在: {task_id}")
    return row


# --------------------------------------------------------------------------
# preset 执行
# --------------------------------------------------------------------------


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _math_checks_from_context(context: dict) -> list[dict]:
    """从 context 提取确定性数学检查规格（真实输入 → 真实检查）。"""
    checks = context.get("checks")
    if isinstance(checks, list) and checks:
        return [c for c in checks if isinstance(c, dict)]
    lhs = context.get("lhs")
    rhs = context.get("rhs")
    points = context.get("points")
    if lhs is not None and rhs is not None and isinstance(points, list) and points:
        spec: dict = {"name": context.get("name") or "derivation", "kind": "IDENTITY", "lhs": lhs, "rhs": rhs, "points": points}
        if context.get("tolerance") is not None:
            spec["tolerance"] = context["tolerance"]
        return [spec]
    return []


def _map_verdict(result: str) -> str:
    return {"PASS": "verified", "FAIL": "conflict", "UNKNOWN": "needs_conditions"}.get(result, "needs_conditions")


def _selected_text(context: dict) -> str:
    """待核验/待处理的**内容**文本（交给前端 KaTeX 渲染，不暴露分词与内部参数）。

    宪法 §46：Research Context 里 `selected text` / `selected formula` 是**内容**，
    而 `statement` 只是被核验对象的**名称/标识**（如 "Theorem 2.1 (Céa's lemma)"）。
    前端可能同时下发两者，因此**必须先取内容字段、最后才退回名称**：
    否则会把标题当成待核验的数学陈述交给模型，得到"未提供具体陈述"的空结论 ——
    即"表面调了模型、实际永远核验不了"的假能力（§70/§71）。
    """
    return str(
        context.get("selection")
        or context.get("selected_text")
        or context.get("expression")
        or context.get("statement")
        or ""
    )


def _deterministic_math(kind: str, checks: list[dict]) -> dict:
    """**结构化规格路径**（context 带 `lhs/rhs/points` 或 `checks[]`）。

    这是确定性符号校验，结论才配 `verified`/`conflict`（宪法 §35）。
    **既有 contract 与 status 词表保持不变**——本函数只是从 `_run_math` 里原样搬出来。
    """
    outcome = run_offline_check("MATH_CHECK", {"checks": checks})
    status = _map_verdict(outcome.result)
    verdict_zh = _VERDICT_ZH.get(outcome.result, "无法判定")
    res = _empty_result(
        kind, status, f"已完成确定性校验：{verdict_zh}（共 {len(outcome.criteria)} 项判定）。"
    )
    for c in outcome.criteria:
        res["findings"].append(
            {
                "label": str(c.get("code") or "校验项"),
                "detail": str(c.get("detail") or c.get("reason") or ""),
                "math": str(c.get("observed")) if c.get("observed") is not None else "",
            }
        )
    for u in outcome.unknowns:
        res["findings"].append(_unknown_finding(u))
    res["actions"] = ["locate", "view_evidence", "add_note"]
    res["confidence"] = _confidence(status, findings=len(res["findings"]))
    return res


def _math_hint_result(preset: str, kind: str, selected: str) -> dict:
    """既无结构化规格、又无选中文本：按能力给出学术化提示（宪法 §30/§34）。"""
    hint = _MATH_HINT.get(preset, _MATH_HINT["derivation_check"])
    res = _empty_result(kind, "needs_conditions", hint["summary"])
    res["findings"].append({"label": hint["label"], "detail": hint["detail"], "math": selected})
    res["actions"] = ["locate", "add_note"]
    res["confidence"] = _confidence("needs_conditions")
    return res


async def _run_math(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    preset: str,
    context: dict,
) -> dict:
    """推导校验 / 公式检查：**两条真实路径**（宪法 §33/§34/§58）。

    1. `context` 带结构化规格（`checks[]` 或 `lhs/rhs/points`）⇒ 确定性符号校验；
    2. 否则若选中/输入了数学陈述 ⇒ 结合**真实论文上下文 + 项目真实文献**交给模型推理，
       产出结构化结论（结论上限 `likely`，证据只能来自项目真实文献）；
    3. 两者都没有 ⇒ 学术化提示（`needs_conditions`），而不是"请提供更多信息"。
    """
    kind = _KIND[preset]
    checks = _math_checks_from_context(context)
    selected = _selected_text(context)
    if checks:
        return _deterministic_math(kind, checks)
    if not selected.strip():
        return _math_hint_result(preset, kind, selected)
    return await reasoning_svc.reason_math(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        preset=preset,
        kind=kind,
        context=context,
        selected=selected,
    )


def _paper_evidence(row: ResearchPaperRow, *, page: int | None = None, section: str = "", snippet: str = "") -> dict:
    """真实文献 → 证据项（宪法 §33）。

    `locator` 尽量带 bbox：能在**已解析的真实页块**里定位到 `snippet` 就填真实 bbox，
    定位不到就**如实填 `None`**（前端会退化为"只跳页不高亮"）——绝不假造高亮位置
    （宪法 §14）。
    """
    pno = int(page or row.last_page or 1)
    bbox = reasoning_svc.locate_bbox(row, page=pno, needle=snippet) if snippet else None
    return {
        "paper_id": str(row.id),
        "title": row.title or "",
        "authors": ", ".join(row.authors or []) if isinstance(row.authors, list) else str(row.authors or ""),
        "year": row.year or 0,
        "page": pno,
        "section": str(section or ""),
        "locator": {
            "paper_id": str(row.id),
            "page": pno,
            "section": str(section or ""),
            "selected_text": "",
            "bbox": bbox,
        },
    }


def _cite_key(row: ResearchPaperRow) -> str:
    authors = row.authors or []
    first = ""
    if isinstance(authors, list) and authors:
        first = str(authors[0]).split()[-1] if str(authors[0]).split() else ""
    base = re.sub(r"[^A-Za-z]", "", first).lower() or "ref"
    return f"{base}{row.year or ''}"


def _run_citation(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, context: dict) -> dict:
    res = _empty_result("citation", "insufficient", "")
    query = str(context.get("query") or context.get("claim") or "").strip()
    cite_key = str(context.get("cite_key") or "").strip()
    evidence: list[dict] = []

    if cite_key:
        # 在项目 .bib 或文献记录里核验该 key。
        matched = None
        for f in list_files(db, tenant_id=tenant_id, project_id=project_id):
            if f.kind == "bib" and re.search(rf"@\w+\s*\{{\s*{re.escape(cite_key)}\s*,", f.content or ""):
                matched = f.path
                break
        if matched:
            res["status"] = "verified"
            res["summary"] = f"引用「{cite_key}」已在 {matched} 中登记并通过核验。"
            res["source_locations"] = [{"file": matched, "line": 1}]
        else:
            res["status"] = "insufficient"
            res["summary"] = f"未能在项目参考文献中核验引用「{cite_key}」，已标记为未验证引用。"
            res["findings"].append(
                {"label": "未验证引用", "detail": f"项目参考文献中没有与「{cite_key}」对应的条目。", "math": ""}
            )
        res["actions"] = ["locate", "view_evidence", "insert_citation"]
        res["confidence"] = _confidence(res["status"])
        return res

    if not query:
        res["summary"] = "当前上下文缺少要补全的论述，无法完成引用补全。请先选中需要引用的句子。"
        return res

    # 真实检索：项目文献 + 项目内检索
    found = knowledge_svc.search(db, tenant_id=tenant_id, project_id=project_id, query=query, limit=5)
    for it in found.get("items", []):
        page = int(it.get("page") or 0)
        section = str(it.get("section") or "")
        snippet = str(it.get("snippet") or "")
        # 元信息**一律回填 DB 真值**（§36 要求含「作者 / 年份」；§37 禁止伪造）：
        # 检索命中项只带 title/page/section，authors/year 必须从 `research_paper` 行取，
        # 之前这里把 `year` 硬编码为 0、authors 从检索项取空 → 卡片上作者年份永远空白。
        paper_row = None
        try:
            paper_row = db.get(ResearchPaperRow, uuid.UUID(str(it["paper_id"])))
        except (KeyError, ValueError, TypeError):
            paper_row = None
        bbox = (
            reasoning_svc.locate_bbox(paper_row, page=page, needle=snippet)
            if paper_row is not None
            else None
        )
        authors = ""
        year = 0
        title = str(it.get("title") or "")
        if paper_row is not None:
            raw_authors = paper_row.authors
            authors = (
                ", ".join(str(a) for a in raw_authors)
                if isinstance(raw_authors, list)
                else str(raw_authors or "")
            )
            year = int(paper_row.year or 0)
            title = title or str(paper_row.title or "")
        # §36「支持程度」：只做**可核验的客观描述**，不做模型式判断（§37 禁止伪装）。
        # 判据 = 该来源在项目知识检索里的命中位置：有 snippet（命中正文片段）→ 正文引用；
        # 只有标题/元信息命中 → 标题相关。二者都可点「定位原文」去核对。
        support = "正文引用" if snippet else "标题相关"
        if paper_row is None:
            # 查不到文献行（例如来源已删）→ 明确标注未验证，绝不冒充真实来源
            support = "未验证来源"
        evidence.append(
            {
                "paper_id": it["paper_id"],
                "title": title,
                "authors": authors,
                "year": year,
                "page": page,
                "section": section,
                "support": support,
                "locator": {
                    "paper_id": it["paper_id"],
                    "page": page,
                    "section": section,
                    "selected_text": "",
                    "bbox": bbox,
                },
                "snippet": snippet,
            }
        )
    lit_rows = db.execute(
        select(LiteratureRecordRow)
        .where(
            LiteratureRecordRow.tenant_id == tenant_id,
            LiteratureRecordRow.project_id == project_id,
            LiteratureRecordRow.title.ilike(f"%{query}%"),
        )
        .limit(5)
    ).scalars().all()
    for lr in lit_rows:
        evidence.append(
            {
                "paper_id": str(lr.id),
                "title": lr.title or "",
                "authors": ", ".join(lr.authors or []) if isinstance(lr.authors, list) else "",
                "year": int((lr.published_date or "0")[:4]) if (lr.published_date or "")[:4].isdigit() else 0,
                "page": 0,
                "section": "",
                "locator": {"doi": lr.doi or ""},
                "snippet": "",
            }
        )

    res["evidence"] = evidence
    if evidence:
        res["status"] = "supported"
        res["summary"] = f"在项目文献中找到 {len(evidence)} 条与「{query}」相关的候选引用。"
        res["actions"] = ["locate", "view_evidence", "insert_citation"]
    else:
        res["status"] = "insufficient"
        res["summary"] = f"项目内未能核验与「{query}」匹配的引用（标记为未验证引用）。"
        res["findings"].append({"label": "未验证引用", "detail": "项目文献/文献库中无匹配记录", "math": ""})
        res["actions"] = ["locate", "add_note"]
    res["confidence"] = _confidence(res["status"], evidence=len(evidence))
    return res


def _run_latex_compare(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, context: dict) -> dict:
    res = _empty_result("latex_compare", "insufficient", "")
    paper_id_raw = context.get("paper_id")
    file_path = str(context.get("file") or "main.tex")
    if not paper_id_raw:
        res["summary"] = "尚未选择要对照的文献，无法进行 LaTeX 对照。请先在阅读区打开一篇文献。"
        res["actions"] = ["locate", "add_note"]
        return res
    try:
        paper = get_paper(db, tenant_id=tenant_id, project_id=project_id, paper_id=uuid.UUID(str(paper_id_raw)))
    except Exception:  # noqa: BLE001
        res["summary"] = "所选文献不在当前项目中，无法对照。"
        return res

    tex_outline = outline_svc(db, tenant_id=tenant_id, project_id=project_id, entry=file_path)
    tex_titles = {str(i["title"]).strip().lower() for i in tex_outline.get("items", [])}
    paper_sections = [s for s in (paper.page_sections or [])]
    paper_titles = {str(s.get("title") or "").strip().lower() for s in paper_sections if s.get("title")}

    missing_in_tex = sorted(t for t in paper_titles if t and t not in tex_titles)
    extra_in_tex = sorted(t for t in tex_titles if t and t not in paper_titles)

    res["evidence"] = [_paper_evidence(paper)]
    res["source_locations"] = [{"file": file_path, "line": 1}]
    # §58：findings 必须能被前端稳定渲染成「可读条目」。
    # 原实现把所有差异都写成同一个 label「文献有、草稿缺 / 草稿有、文献缺」，
    # 具体是哪一节只藏在 detail 里 → 折叠/截断后用户看不到差异项，等于没给出信息。
    # 现在把**具体章节名放进 label**（截断到可读长度，完整值留在 detail）。
    def _brief(t: str, limit: int = 28) -> str:
        s = str(t).strip()
        return s if len(s) <= limit else s[: limit - 1] + "…"

    for t in missing_in_tex[:10]:
        res["findings"].append(
            {
                "label": f"文献有、草稿缺：{_brief(t)}",
                "detail": f"文献章节「{t}」未在当前 .tex 大纲中出现",
                "math": "",
            }
        )
    for t in extra_in_tex[:10]:
        res["findings"].append(
            {
                "label": f"草稿有、文献缺：{_brief(t)}",
                "detail": f"草稿章节「{t}」在文献中无对应",
                "math": "",
            }
        )
    res["status"] = "conflict" if res["findings"] else "supported"
    res["summary"] = (
        f"对照 {file_path}（{len(tex_titles)} 个章节）与文献《{paper.title}》"
        f"（{len(paper_titles)} 个章节）：{len(res['findings'])} 处差异。"
        + (f"例：{_brief(missing_in_tex[0])}" if missing_in_tex else "")
    )
    res["actions"] = ["locate", "add_note", "recompile"]
    res["confidence"] = _confidence(res["status"], evidence=1, findings=len(res["findings"]))
    return res


def _protected_counts(text: str) -> dict[str, int]:
    return {
        "commands": len(_PROTECTED_RE.findall(text or "")),
        "math": (text or "").count("$"),
    }


async def _run_translate(
    db: Session,
    context: dict,
    *,
    tenant_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> dict:
    """学术翻译（宪法 §42）：**同一 Project 内同一术语必须译法一致**。

    术语一致性靠 `terminology.TerminologyMemory`：
    1. 把该项目**已确立的术语译法**注入 prompt（模型必须沿用，不得另起译名）；
    2. 从模型返回的**段级对照**（`blocks`）里学习本轮新确立的术语；
    3. 在结果里如实标注 `terminology_source`（用了多少条已有术语 / 新学了多少条）。
    """
    from ..writing import service as wb

    text = str(context.get("text") or "")
    target = str(context.get("target_lang") or "en")
    source = str(context.get("source_lang") or "auto")
    if not text.strip():
        res = _empty_result(
            "translate", "insufficient",
            "当前没有可翻译的文本。请先在论文中选中要翻译的段落，或在输入框里输入内容。",
        )
        return res

    # §42：取本项目的术语记忆（无则空记忆，不污染 prompt）
    mem = terminology.for_project(tenant_id, project_id) if (tenant_id and project_id) else None
    known_before = len(mem.snapshot(db)) if mem else 0
    glossary_block = mem.prompt_block(db) if mem else ""

    try:
        out = await wb.translate(
            text, source_lang=source, target_lang=target,
            glossary=glossary_block or None,
        )
    except TypeError:
        # 兜底：wb.translate 尚未接受 glossary 参数时，退化为不带术语记忆的调用
        # （仍然可用，只是首轮无法沿用已确立译法）
        try:
            out = await wb.translate(text, source_lang=source, target_lang=target)
        except Exception:  # noqa: BLE001 - 诚实失败，不伪造译文
            res = _empty_result("translate", "failed", "翻译服务暂时不可用，请稍后重试。")
            res["findings"].append(
                {"label": "翻译服务暂时不可用", "detail": "你的论文内容没有改动，可稍后重新尝试。", "math": ""}
            )
            return res
    except Exception:  # noqa: BLE001 - 诚实失败，不伪造译文
        res = _empty_result("translate", "failed", "翻译服务暂时不可用，请稍后重试。")
        res["findings"].append(
            {"label": "翻译服务暂时不可用", "detail": "你的论文内容没有改动，可稍后重新尝试。", "math": ""}
        )
        return res

    translated = str(out.get("translated_text") or "")
    before = _protected_counts(text)
    after = _protected_counts(translated)
    status = "supported"
    findings: list[dict] = []
    if before["commands"] != after["commands"] or (before["math"] % 2) != (after["math"] % 2):
        status = "conflict"
        findings.append(
            {
                "label": "引用或公式标记可能受影响",
                "detail": f"译文中 \\cite、\\ref 等引用命令数量为 {before['commands']}→{after['commands']}，"
                f"数学公式定界符为 {before['math']}→{after['math']}，请核对后再应用。",
                "math": "",
            }
        )

    # §42：从「原文 + 译文」里提取术语对照并记入项目术语记忆。
    # ⚠️ 不能用长度启发式白名单（我第一版用 `len(s)<=60` 过滤，结果学术句子普遍超长 →
    # 一条都学不到，"记忆"形同不存在）。改为让模型只做**术语抽取**这件事：
    # 输入原文+定稿译文，输出术语对照；术语是否成立由模型判断，比字符串长度靠谱得多。
    learned = 0
    conflicts: list[str] = []
    if mem is not None:
        # 先记下"本轮之前"的记忆，用于反向校验（本轮新学的不是"不一致"，是"新确立"）
        before_terms = dict(mem.snapshot(db))
        learned = mem.remember_many(db, await _extract_glossary_pairs(text, translated))
        low = text.lower()
        for src_term, tgt_term in before_terms.items():
            if src_term in low and len(tgt_term) >= 2 and tgt_term not in translated:
                conflicts.append(f"「{src_term}」此前译为「{tgt_term}」，本次译文中未出现")

    if conflicts:
        status = "conflict" if status == "supported" else status
        findings.append(
            {
                "label": "术语译法可能不一致",
                "detail": "；".join(conflicts[:3]) + "。建议沿用本项目已确立的译法，或确认是否确实需要新译名。",
                "math": "",
            }
        )

    res = _empty_result(
        "translate", status,
        f"已完成翻译（{_lang_zh(target)}；{len(text)} 字符 → {len(translated)} 字符）。",
    )
    res["findings"] = findings
    # §42：如实标注术语记忆的来源与用量（不暴露实现细节，只说业务事实）
    if mem is not None:
        res["terminology"] = {
            "source": "project-terminology-memory",
            "known_terms": known_before,
            "learned_terms": learned,
            "conflicts": len(conflicts),
        }
    res["patches"] = [
        {
            "file": str(context.get("file") or "main.tex"),
            "line": int(context.get("line") or 0),
            "before": text,
            "after": translated,
            "reason": "替换为译文（需用户确认后写入）",
        }
    ]
    res["actions"] = ["apply_patch", "recompile"]
    res["confidence"] = _confidence(status, findings=len(findings))
    return res


async def _extract_glossary_pairs(
    source_text: str, target_text: str, *, limit: int = 12
) -> list[tuple[str, str]]:
    """从「原文 + 定稿译文」里抽取**术语对照**（宪法 §42 Terminology Memory 的学习环节）。

    为什么单独用一次模型调用：术语识别是**语义判断**，字符串长度/频率启发式都不可靠
    （我第一版按"句子 ≤60 字符"过滤，学术句子普遍超长 → 一条都学不到）。
    这里只做一件事、只输出 JSON，成本可控；失败/不可用一律返回空列表 ——
    **学不到术语不影响翻译本身**，只是下一轮没有记忆可用。

    Raises: 不抛（内部吞掉），保证翻译主流程不受影响。
    """
    from ..llm import gateway as llm

    src = (source_text or "").strip()
    tgt = (target_text or "").strip()
    if not src or not tgt:
        return []
    try:
        backend = llm.get_backend()
        if not backend.available:
            return []
        system = (
            "你是科技术语对照提取器。只输出一个 JSON 对象，不要解释、不要代码围栏。\n"
            '结构：{"terms": [{"source": "原文术语（可为英文短语）", '
            '"target": "译文中对应的译名"}]}\n'
            "规则：\n"
            "1. 只提取**专业术语**（学科名词、方法名、性质名），不要提取普通词组或整句；\n"
            "2. 每个 source 必须是原文里**逐字出现**的连续片段；每个 target 必须是译文里"
            "**逐字出现**的连续片段；\n"
            "3. 宁缺勿错：拿不准就不要收录；最多 12 条；没有术语就返回 {\"terms\": []}。"
        )
        user = f"【原文】\n{src}\n\n【译文】\n{tgt}\n\n现在只输出 JSON。"
        completion = await backend.acompletion(
            model=llm.llm_model_long(),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        import json as _json
        import re as _re

        raw = (completion.text or "").strip()
        obj = None
        try:
            obj = _json.loads(raw)
        except Exception:
            # 容错：模型偶尔包了代码围栏或前后有解释文字
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            if m:
                try:
                    obj = _json.loads(m.group(0))
                except Exception:
                    obj = None
        if not isinstance(obj, dict):
            return []
        out: list[tuple[str, str]] = []
        for t in (obj.get("terms") or [])[:limit]:
            if not isinstance(t, dict):
                continue
            s = str(t.get("source") or "").strip()
            g = str(t.get("target") or "").strip()
            # 只收**双向逐字可验证**的对照（§37 精神：不采信无法核对的内容）
            if not s or not g or len(s) < 2 or len(g) < 2:
                continue
            if s.lower() not in src.lower() or g not in tgt:
                continue
            out.append((s, g))
        return out
    except Exception:  # noqa: BLE001 - 术语抽取失败不得影响翻译结果
        return []


async def _run_polish(context: dict) -> dict:
    from ..llm import gateway as llm

    text = str(context.get("text") or "")
    if not text.strip():
        return _empty_result(
            "polish", "insufficient",
            "当前没有可润色的文本。请先在论文中选中要润色的段落，或在输入框里输入内容。",
        )
    backend = llm.get_backend()
    if not backend.available:
        return _empty_result(
            "polish", "failed", "AI 服务暂时不可用，暂时无法润色。你的论文内容没有改动。"
        )
    system = (
        "你是学术写作助手。仅改进表达与语法，**必须原样保留**所有 LaTeX 命令"
        "（如 \\cite{}、\\ref{}、\\label{}）、数学环境与公式。只输出润色后的文本。"
    )
    try:
        completion = await backend.acompletion(
            model=llm.llm_model_long(),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        )
    except Exception:  # noqa: BLE001
        return _empty_result(
            "polish", "failed", "润色服务暂时不可用，请稍后重试。你的论文内容没有改动。"
        )
    polished = completion.text or ""
    before = _protected_counts(text)
    after = _protected_counts(polished)
    status = "supported"
    findings: list[dict] = []
    if before["commands"] != after["commands"] or (before["math"] % 2) != (after["math"] % 2):
        status = "conflict"
        findings.append(
            {
                "label": "引用或公式标记可能受影响",
                "detail": f"润色后 \\cite、\\ref 等引用命令数量为 {before['commands']}→{after['commands']}，"
                f"数学公式定界符为 {before['math']}→{after['math']}，请核对后再应用。",
                "math": "",
            }
        )
    res = _empty_result("polish", status, "已润色（保留 LaTeX 命令与公式）。")
    res["findings"] = findings
    res["patches"] = [
        {
            "file": str(context.get("file") or "main.tex"),
            "line": int(context.get("line") or 0),
            "before": text,
            "after": polished,
            "reason": "应用润色（需用户确认后写入）",
        }
    ]
    res["actions"] = ["apply_patch", "recompile"]
    res["confidence"] = _confidence(status, findings=len(findings))
    return res


#: 单次最多解释几条编译问题（防一次编译刷出上百条把卡片撑爆）。
_LATEX_ERROR_LIMIT = 3


async def _run_latex_error(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, context: dict
) -> dict:
    """LaTeX 错误解释（宪法 §39）：**错误类型 / 文件 / 行号 / 编译器原始摘要 /
    人类可读解释 / 可能原因 / 建议修改 / 代码 Diff**。

    真实性边界（**不可越过**）：
    - `file` / `line` 来自 `compile-runs/latest` 的**真实结构化日志行**；
    - Diff 的 `before` 来自**真实源码行**（`research_project_file.content`），
      `after` 只在能给出**可核对的替换**时产出，否则宁可不给 Diff；
    - 模型只被允许增强"人类可读解释 / 可能原因 / 建议"（§39 允许）。
    """
    res = _empty_result("latex_error", "insufficient", "")
    run = latest_compile_run(db, tenant_id=tenant_id, project_id=project_id)
    if run is None:
        res["summary"] = "尚无可用的编译记录。"
        res["actions"] = ["locate", "recompile"]
        return res

    log_lines = list(run.log_lines or [])
    errors = [ln for ln in log_lines if ln.get("level") == "error"]
    if errors:
        picked = errors[:_LATEX_ERROR_LIMIT]
        status = "conflict"
    else:
        # 编译通过但存在"未定义的引用/交叉引用"这类真实文档缺陷：仍要解释（§39 的例子）。
        # 只挑**带具体标识符**的那些行 —— `"There were undefined references."` 这种汇总行
        # 没有可定位的对象，混进卡片只会变成噪声。
        picked = [
            ln
            for ln in log_lines
            if ln.get("level") == "warning"
            and reasoning_svc.undefined_key(str(ln.get("text") or ""))
        ][:_LATEX_ERROR_LIMIT]
        status = "conflict" if picked else "verified"

    if not picked:
        # **成功态不走错误卡语义**：不再产出 `label="编译成功"` 这种会被渲染成
        # 红「! 编译成功」的 finding（§5.6 别扭点的根因就在这里）。
        res["status"] = "verified"
        res["summary"] = (
            f"最近一次编译已成功（{int(run.page_count or 0)} 页），没有需要解释的错误。"
            if run.pdf_artifact_id
            else "最近一次编译已成功，没有需要解释的错误。"
        )
        res["findings"] = []
        res["actions"] = ["locate"]
        res["confidence"] = _confidence("verified")
        return res

    files = list_files(db, tenant_id=tenant_id, project_id=project_id)
    items = [
        reasoning_svc.build_error_item(log_line=ln, entry=str(run.entry or "main.tex"), files=files)
        for ln in picked
    ]
    explained = await reasoning_svc.explain_latex_errors(items=items, files=files)

    patches: list[dict] = []
    for it in items:
        patch = reasoning_svc.build_patch(
            kind=it["kind"],
            raw=it["raw"],
            source_line=it["source_line"],
            file_=it["file"],
            line=it["line"],
            files=files,
        )
        if patch is not None:
            patches.append(patch)

    first, first_ex = items[0], explained[0]
    causes = list(first_ex.get("causes") or [])
    summary = reasoning_svc.compose_summary(
        first_ex.get("explanation") or "", causes, first_ex.get("suggestion") or ""
    )

    findings: list[dict] = [{"label": first_ex["title"], "detail": "", "math": ""}]
    findings.append({"label": "编译器原始摘要", "detail": first["raw"], "math": ""})
    if causes:
        findings.append({"label": "可能原因", "detail": "；".join(causes), "math": ""})
    for it, ex in zip(items[1:], explained[1:]):
        findings.append({"label": ex["title"], "detail": it["raw"], "math": ""})

    res["status"] = status
    res["summary"] = summary
    res["findings"] = findings
    # 只报**真实存在行号**的位置：拿不到行号就干脆不报，绝不拿"第 1 行"顶替（§90）。
    seen: set[tuple[str, int]] = set()
    locations: list[dict] = []
    for it in items:
        line = int(it["line"] or 0)
        if line < 1:
            continue
        key = (str(it["file"]), line)
        if key in seen:
            continue
        seen.add(key)
        locations.append({"file": key[0], "line": line})
    res["source_locations"] = locations
    res["patches"] = patches
    res["actions"] = ["locate", "apply_patch", "add_note"] if patches else ["locate", "add_note"]
    res["confidence"] = _confidence(status, findings=len(findings))
    return res


def _run_suggestion(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, context: dict) -> dict:
    res = _empty_result("suggestion", "supported", "")
    papers = list_papers(db, tenant_id=tenant_id, project_id=project_id)
    unread = [p for p in papers if p.read_state == "unread"]
    files = list_files(db, tenant_id=tenant_id, project_id=project_id)
    has_main = any(f.path == "main.tex" for f in files)
    has_bib = any(f.kind == "bib" for f in files)

    findings: list[dict] = []
    if not has_main:
        findings.append({"label": "缺少入口", "detail": "项目没有 main.tex，无法编译。", "math": ""})
    if papers and not has_bib:
        findings.append({"label": "缺少参考文献", "detail": f"有 {len(papers)} 篇文献但无 .bib，建议登记引用。", "math": ""})
    if unread:
        findings.append({"label": "未读文献", "detail": f"{len(unread)} 篇文献尚未精读。", "math": ""})

    query = str(context.get("query") or context.get("topic") or "").strip()
    if query:
        hits = knowledge_svc.search(db, tenant_id=tenant_id, project_id=project_id, query=query, limit=3)
        for it in hits.get("items", []):
            findings.append(
                {"label": "相关文献", "detail": f"{it['title']}（p.{it.get('page') or '?'}）", "math": ""}
            )
        res["summary"] = f"围绕「{query}」给出 {len(findings)} 条建议（基于 {len(papers)} 篇文献）。"
    else:
        res["summary"] = f"基于项目数据（{len(papers)} 篇文献、{len(files)} 个文件）给出 {len(findings)} 条建议。"

    res["findings"] = findings
    if not findings:
        res["status"] = "supported"
        res["summary"] = "项目结构完整，暂无明显改进项。"
    res["actions"] = ["locate", "add_note"]
    res["confidence"] = _confidence(res["status"], findings=len(findings))
    return res


async def dispatch(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    preset: str,
    context: dict,
) -> dict:
    """执行一个 preset，返回结构化 Research Result（同步；LLM preset 为 async）。"""
    if preset in ("derivation_check", "formula_check"):
        return await _run_math(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            preset=preset,
            context=context,
        )
    if preset == "citation":
        return _run_citation(db, tenant_id=tenant_id, project_id=project_id, context=context)
    if preset == "latex_compare":
        return _run_latex_compare(db, tenant_id=tenant_id, project_id=project_id, context=context)
    if preset == "translate":
        return await _run_translate(
            db, context, tenant_id=tenant_id, project_id=project_id
        )
    if preset == "polish":
        return await _run_polish(context)
    if preset == "latex_error":
        return await _run_latex_error(
            db, tenant_id=tenant_id, project_id=project_id, context=context
        )
    if preset == "suggestion":
        return _run_suggestion(db, tenant_id=tenant_id, project_id=project_id, context=context)
    raise UnknownPreset(f"暂不支持该科研能力：{preset!r}")


async def execute_task(task_id: uuid.UUID) -> None:
    """后台执行任务：queued → running → completed|failed（落库）。"""
    from ..db.engine import session_scope

    with session_scope() as db:
        row = db.get(ResearchAgentTaskRow, task_id)
        if row is None:
            return
        row.status = TASK_RUNNING
        row.updated_at = utcnow()

    try:
        with session_scope() as db:
            row = db.get(ResearchAgentTaskRow, task_id)
            assert row is not None
            result = await dispatch(
                db,
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                preset=row.preset,
                context=dict(row.context or {}),
            )
            row.result = result
            row.kind = str(result.get("kind") or row.kind)
            row.status = TASK_COMPLETED
            row.finished_at = utcnow()
            row.updated_at = utcnow()
    except Exception as e:  # noqa: BLE001 - 失败必须落成可读状态，不吞
        with session_scope() as db:
            row = db.get(ResearchAgentTaskRow, task_id)
            if row is not None:
                row.status = TASK_FAILED
                row.error_code = type(e).__name__[:32]
                row.error_message = str(e)[:2000]
                row.finished_at = utcnow()
                row.updated_at = utcnow()


__all__ = [
    "PRESETS",
    "RESULT_STATUSES",
    "UnknownPreset",
    "create_task",
    "dispatch",
    "execute_task",
    "get_task",
    "task_public_dict",
]
