"""R16 TextbookTeachingContext 确定性 builder（零 LLM、零编造）。

输入：教师确认的 ContextSelection（教材版本/章节/知识点/课时/班级）。
输出：不可变 TextbookTeachingContext（含 SourceLocator、coverage、confidence、
warnings、snapshot hash）。

铁律（DECISIONS.md §3/§4/§6）：
- 只消费 direct-chunk 数据（Source→Section→Chunk→Unit/Problem）；禁止 PDF、
  TextbookPage、page_ref/pNN 新写入；
- 教材事实可定位：每条事实带 source_ref（resolve_source_ref 可解析）与
  chunk_ref（canonical v1 locator）；
- 覆盖不足/证据冲突/章节不完整 → 显式 warning，不编造；
- 教材 chunk 文本 = UNTRUSTED DATA：注入行为中和 + 显式告警（safety.py）；
- 确定性：同 DB 状态 + 同 selection → 同骨架、同 snapshot hash（可复现验收）。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.models import TextbookChunk, TextbookSection
from app.textbook_v2.retrieval.context import load_teaching_context
from app.textbook_v2.retrieval.dto import TextbookTeachingContext as RetrievalContext

from .models import (
    ContextCoverage,
    ContextFact,
    ContextSelection,
    ContextWarning,
    SectionInfo,
    SourceInfo,
    TextbookTeachingContext,
    canonical_snapshot_hash,
)
from ..text_safety import strip_html_tags, strip_media_syntax
from .safety import detect_injection, is_bad_chunk, neutralize_untrusted

# 覆盖/置信阈值（显式常量；验收口径）
LOW_COVERAGE_THRESHOLD = 0.5
LOW_CONFIDENCE_THRESHOLD = 0.5
# 综合覆盖率权重（知识点命中为主，chunk 连续性为辅）
POINT_RATIO_WEIGHT = 0.6
CHUNK_RATIO_WEIGHT = 0.4

# 重点难点/易错 显式措辞信号（仅教材原文证据；不推测）
_KEY_DIFFICULT_RE = re.compile(r"(重点|难点|易错|核心素养|学习目标)")

# 知识点点名归一化（匹配用）：去空白/顿号/括号层级，转小写
_NORM_STRIP = str.maketrans("", "", " \t\n\r　·、，。；：（）()【】[]「」")

# R08 警告码 → R16 警告码（同语义透传）
_R08_WARNING_MAP = {
    "no_units": "no_units",
    "rule_extraction_fallback": "rule_extraction_fallback",
    "pending_review": "pending_review",
    "low_confidence": "low_confidence",
    "figure_placeholder": "figure_placeholder",
}

# 先备知识信号类型（父章节中视为前置事实的类型）
_PRIOR_FACT_TYPES = frozenset({"definition", "theorem", "formula", "summary"})
# 目标信号（教材显式目标：小结/章末）
_OBJECTIVE_FACT_TYPES = frozenset({"summary"})
# 设计意图信号（引入/推导/小结）
_DESIGN_INTENT_TYPES = frozenset({"introduction", "derivation", "summary"})


def _normalize_term(term: str) -> str:
    return (term or "").translate(_NORM_STRIP).lower()


def _section_info(sec: TextbookSection | Any) -> SectionInfo:
    return SectionInfo(
        id=str(sec.id),
        textbook_id=str(sec.textbook_id),
        title=sec.title,
        section_path=sec.section_path,
        level=sec.level,
        order=sec.order,
        chunk_start=sec.chunk_start,
        chunk_end=sec.chunk_end,
    )


def _source_info(src: Any) -> SourceInfo:
    return SourceInfo(
        id=str(src.id),
        publisher=src.publisher,
        edition=src.edition,
        grade=src.grade,
        volume=src.volume,
        rights_status=src.rights_status,
        batch_id=src.batch_id,
        manifest_sha256=src.manifest_sha256,
        source_kind=src.source_kind or "direct-chunk",
    )


def _normalize_selection(selection: ContextSelection) -> ContextSelection:
    """确定性归一：知识点排序去重；chunk_range 归一为升序。"""
    points = sorted({p.strip() for p in selection.knowledge_points if p and p.strip()})
    rng = selection.chunk_range
    if rng is not None:
        lo, hi = sorted(rng)
        rng = (lo, hi)
    return selection.model_copy(update={"knowledge_points": points, "chunk_range": rng})


def _facts_from_retrieval(
    ctx: RetrievalContext,
    *,
    warnings: list[ContextWarning],
) -> list[ContextFact]:
    """R08 Fact → R16 ContextFact；注入中和 + 坏 chunk/冲突告警。"""
    facts: list[ContextFact] = []
    seen: dict[tuple[str, int | None, int | None], int] = {}
    for f in ctx.facts:
        raw_text = (f.text or "") if f.kind == "unit" else (f.text or "")
        if detect_injection(raw_text):
            warnings.append(
                ContextWarning(
                    code="injection_detected",
                    message="事实文本命中指令注入模式：已中和为数据引用，不进入系统指令",
                    ref=f.source_ref or f.section_ref or None,
                )
            )
        text = strip_media_syntax(strip_html_tags(neutralize_untrusted(raw_text)))
        if not text.strip():
            continue  # 空事实不进入上下文（不编造）
        key = (f.fact_type, f.chunk_start, f.chunk_end)
        seen[key] = seen.get(key, 0) + 1
        facts.append(
            ContextFact(
                fact_id=str(f.source_ref.rsplit("/", 1)[-1]) if f.source_ref else "",
                kind="unit" if f.kind == "unit" else "problem",
                fact_type=f.fact_type,
                text=text,
                solution=strip_media_syntax(strip_html_tags(neutralize_untrusted(f.solution))) if f.solution else None,
                confidence=f.confidence,
                review_status=f.review_status,
                provenance=f.provenance,
                source_ref=f.source_ref,
                chunk_ref=f.chunk_ref,
                section_ref=f.section_ref,
                chunk_start=f.chunk_start,
                chunk_end=f.chunk_end,
            )
        )
    # 冲突证据：同类型同 chunk 区间重复出现（去重后的重复 = 证据冲突/重复）
    dups = {k: c for k, c in seen.items() if c > 1}
    if dups:
        example = min(dups)
        warnings.append(
            ContextWarning(
                code="conflicting_evidence",
                message=f"{len(dups)} 组同类型同区间重复事实（type/chunk_start/chunk_end 一致），需人工复核",
                ref=next((f.source_ref for f in facts if (f.fact_type, f.chunk_start, f.chunk_end) == example), None),
            )
        )
    # 确定性排序：chunk 锚 → source_ref → fact_id（同输入同序）
    facts.sort(key=lambda f: ((f.chunk_start if f.chunk_start is not None else -1), f.source_ref, f.fact_id))
    return facts


def _buckets(facts: list[ContextFact]) -> dict[str, list[ContextFact]]:
    """按教学语义分桶（确定性；无证据桶保持空列表）。"""
    buckets: dict[str, list[ContextFact]] = {
        "objectives": [],
        "prior_knowledge": [],
        "concepts": [],
        "theorems": [],
        "examples": [],
        "inquiries": [],
        "design_intent": [],
        "key_difficult_points": [],
    }
    for f in facts:
        t = f.fact_type
        if t in _OBJECTIVE_FACT_TYPES:
            buckets["objectives"].append(f)
        if t in _DESIGN_INTENT_TYPES:
            buckets["design_intent"].append(f)
        if t in ("definition", "formula"):
            buckets["concepts"].append(f)
        if t == "theorem":
            buckets["theorems"].append(f)
        if t == "example" or (f.kind == "problem" and t == "example"):
            buckets["examples"].append(f)
        if t == "inquiry" or (f.kind == "problem" and t == "inquiry"):
            buckets["inquiries"].append(f)
        if _KEY_DIFFICULT_RE.search(f.text):
            buckets["key_difficult_points"].append(f)
    return buckets


async def _prior_knowledge_facts(
    session: AsyncSession,
    parent: SectionInfo | None,
    *,
    course_context: dict[str, Any] | None,
) -> tuple[list[ContextFact], list[ContextWarning]]:
    """父章节先备知识：父 section 的 definition/theorem/formula/summary 事实。

    父章节缺失或先备事实缺失 → 空 + 显式警告（不编造前置知识）。
    """
    warnings: list[ContextWarning] = []
    if parent is None:
        return [], [ContextWarning(code="no_prior_knowledge", message="无父章节信息，先备知识无法定位（章节树不完整）")]
    pctx = await load_teaching_context(
        session, uuid.UUID(parent.id), include_neighbors=False, course_context=course_context
    )
    if pctx is None:
        return [], [ContextWarning(code="no_prior_knowledge", message="父章节不可见，先备知识无法构建", ref=parent.id)]
    facts: list[ContextFact] = []
    for f in pctx.facts:
        if f.fact_type in _PRIOR_FACT_TYPES:
            text = strip_media_syntax(strip_html_tags(neutralize_untrusted(f.text or "")))
            if not text.strip():
                continue
            facts.append(
                ContextFact(
                    fact_id=str(f.source_ref.rsplit("/", 1)[-1]) if f.source_ref else "",
                    kind="unit" if f.kind == "unit" else "problem",
                    fact_type=f.fact_type,
                    text=text,
                    solution=strip_media_syntax(strip_html_tags(neutralize_untrusted(f.solution))) if f.solution else None,
                    confidence=f.confidence,
                    review_status=f.review_status,
                    provenance=f.provenance,
                    source_ref=f.source_ref,
                    chunk_ref=f.chunk_ref,
                    section_ref=f.section_ref,
                    chunk_start=f.chunk_start,
                    chunk_end=f.chunk_end,
                )
            )
    if not facts:
        warnings.append(ContextWarning(code="no_prior_knowledge", message="父章节无先备事实（definition/theorem/formula/summary 均缺）"))
    return facts, warnings


async def _load_section_chunks(
    session: AsyncSession,
    descendant_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    """按 (order, id) 确定性加载全部后代 chunk 全文（用于知识点匹配/坏 chunk 检测）。"""
    if not descendant_ids:
        return []
    rows = (
        (
            await session.execute(
                select(TextbookChunk)
                .where(TextbookChunk.section_id.in_(descendant_ids))
                .order_by(TextbookChunk.order, TextbookChunk.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {"chunk_id": str(c.id), "section_id": str(c.section_id), "order": c.order, "text": c.text or ""}
        for c in rows
    ]


async def _descendant_ids(session: AsyncSession, section_id: uuid.UUID) -> list[uuid.UUID]:
    """BFS 后代 section id（含自身）——确定性顺序。"""
    ids: list[uuid.UUID] = [section_id]
    frontier = [section_id]
    while frontier:
        rows = (
            (await session.execute(select(TextbookSection.id).where(TextbookSection.parent_id.in_(frontier))))
            .scalars()
            .all()
        )
        nxt = [uuid.UUID(str(r)) for r in rows]
        ids.extend(nxt)
        frontier = nxt
    return ids


async def build_teaching_context(
    session: AsyncSession,
    *,
    selection: ContextSelection,
    course_context: dict[str, Any] | None = None,
) -> TextbookTeachingContext | None:
    """确定性构建不可变教材教学上下文；section 越权/不可见 → None（fail closed）。

    `course_context` 可选键：school_id（租户隔离，透传 R08 语义）。
    """
    selection = _normalize_selection(selection)
    try:
        section_uid = uuid.UUID(selection.section_id)
    except ValueError:
        return None  # 非法 section_id → fail closed
    ctx = await load_teaching_context(session, section_uid, course_context=course_context)
    if ctx is None:
        return None

    warnings: list[ContextWarning] = []
    # R08 既有警告透传（no_units/pending_review/low_confidence/figure_placeholder）
    for w in ctx.warnings:
        code = _R08_WARNING_MAP.get(w.code)
        if code is not None:
            warnings.append(ContextWarning(code=code, message=w.message, ref=w.ref))  # type: ignore[arg-type]

    # 章节完整性：chunk 锚缺失 → 显式警告（legacy 回退 page_range）
    if ctx.section.chunk_start is None or ctx.section.chunk_end is None:
        warnings.append(
            ContextWarning(
                code="incomplete_chapter",
                message="section 缺 chunk_start/chunk_end（direct-chunk 锚缺失），chunk 区间回退 page_range",
            )
        )

    # 坏 chunk 检测（后代全量 chunk 全文；空/异常内容显式告警并排除）
    descendant_ids = await _descendant_ids(session, section_uid)
    chunks = await _load_section_chunks(session, descendant_ids)
    bad_count = 0
    for c in chunks:
        if is_bad_chunk(c["text"]):
            bad_count += 1
    if bad_count:
        warnings.append(
            ContextWarning(
                code="bad_chunk",
                message=f"{bad_count} 个坏 chunk（空/纯空白/异常控制字符）已排除，不进入上下文",
            )
        )
    injection_in_chunks = sorted({p for c in chunks for p in detect_injection(c["text"])})
    if injection_in_chunks:
        warnings.append(
            ContextWarning(
                code="injection_detected",
                message=f"chunk 文本命中指令注入模式 {injection_in_chunks}：已中和为数据引用",
            )
        )

    # 事实（注入中和 + 冲突检测 + 确定性排序）
    facts = _facts_from_retrieval(ctx, warnings=warnings)
    # 课时范围收窄（可选）：只保留与 chunk_range 重叠的事实
    rng = selection.chunk_range
    if rng is not None:
        lo, hi = rng
        facts = [
            f
            for f in facts
            if f.chunk_start is not None
            and f.chunk_end is not None
            and f.chunk_start <= hi
            and f.chunk_end >= lo
        ]

    buckets = _buckets(facts)
    prior_facts, prior_warnings = await _prior_knowledge_facts(
        session, _section_info(ctx.parent) if ctx.parent else None, course_context=course_context
    )
    warnings.extend(prior_warnings)

    # ── 覆盖报告（确定性）──────────────────────────────────────────────────
    expected_points = len(selection.knowledge_points)
    matched_points = 0
    if expected_points:
        haystacks = []
        for f in facts:
            haystacks.append(f.text)
        haystacks.extend(c["text"] for c in chunks)
        haystacks.append(ctx.section.title)
        hay_terms = [_normalize_term(h) for h in haystacks]
        for term in selection.knowledge_points:
            t = _normalize_term(term)
            if any(t and t in h for h in hay_terms):
                matched_points += 1
        if matched_points < expected_points:
            unmatched = [p for p in selection.knowledge_points if not any(_normalize_term(p) and _normalize_term(p) in h for h in hay_terms)]
            warnings.append(
                ContextWarning(
                    code="knowledge_point_unmatched",
                    message=f"{len(unmatched)} 个确认知识点未在章节内容中命中：{unmatched}",
                )
            )
    point_ratio = (matched_points / expected_points) if expected_points else 1.0

    lo = (rng or (ctx.section.chunk_start, ctx.section.chunk_end))[0]
    hi = (rng or (ctx.section.chunk_start, ctx.section.chunk_end))[1]
    expected_span = (hi - lo + 1) if (lo is not None and hi is not None and hi >= lo) else 0
    present_orders = {
        c["order"]
        for c in chunks
        if c["order"] is not None
        and lo is not None
        and hi is not None
        and lo <= c["order"] <= hi
        and not is_bad_chunk(c["text"])
    }
    present_chunks = len(present_orders)
    chunk_ratio = (present_chunks / expected_span) if expected_span else 1.0
    if expected_span and present_chunks < expected_span:
        warnings.append(
            ContextWarning(
                code="low_coverage",
                message=f"chunk 覆盖不足：期望 {expected_span} 个，实际 {present_chunks} 个",
            )
        )

    if expected_points and point_ratio < LOW_COVERAGE_THRESHOLD:
        warnings.append(
            ContextWarning(
                code="low_coverage",
                message=f"知识点覆盖不足：{matched_points}/{expected_points} 命中（<{LOW_COVERAGE_THRESHOLD}）",
            )
        )
    if expected_points and expected_span:
        overall = POINT_RATIO_WEIGHT * point_ratio + CHUNK_RATIO_WEIGHT * min(1.0, chunk_ratio)
    elif expected_points:
        overall = point_ratio
    elif expected_span:
        overall = min(1.0, chunk_ratio)
    else:
        overall = 1.0
    coverage = ContextCoverage(
        expected_points=expected_points,
        matched_points=matched_points,
        point_ratio=round(point_ratio, 4),
        expected_chunks=expected_span,
        present_chunks=present_chunks,
        chunk_ratio=round(min(1.0, chunk_ratio), 4),
        overall=round(min(1.0, overall), 4),
    )

    # ── 置信度：事实 confidence 均值（无事实 → 0.0 + no_units 已由 R08 透传）──
    confs = [f.confidence for f in facts if f.confidence is not None]
    confidence = round(sum(confs) / len(confs), 4) if confs else 0.0

    # ── 目标/设计意图/重点难点 缺失显式告警（不编造）────────────────────────
    if not buckets["objectives"]:
        warnings.append(ContextWarning(code="no_objectives_evidence", message="无 summary 类教学目标证据（教材未显式声明目标）"))
    if not buckets["design_intent"]:
        warnings.append(ContextWarning(code="no_design_intent", message="无 introduction/derivation/summary 类设计意图证据"))
    if not buckets["key_difficult_points"]:
        warnings.append(ContextWarning(code="no_key_difficult_evidence", message="教材未显式措辞重点/难点，需 Blueprint proposal 派生并教师确认"))

    # 警告确定性排序（code, message, ref）
    warnings.sort(key=lambda w: (w.code, w.message, w.ref or ""))

    children = sorted((_section_info(c) for c in ctx.children), key=lambda s: (s.order, s.id))
    chunk_order = sorted(present_orders)

    built = TextbookTeachingContext(
        schema_version="1.0.0",
        snapshot_hash="",
        built_at=datetime.now(UTC),
        selection=selection,
        source=_source_info(ctx.source),
        section=_section_info(ctx.section),
        parent=_section_info(ctx.parent) if ctx.parent else None,
        children=children,
        facts=facts,
        objectives=buckets["objectives"],
        prior_knowledge=prior_facts,
        concepts=buckets["concepts"],
        theorems=buckets["theorems"],
        examples=buckets["examples"],
        inquiries=buckets["inquiries"],
        design_intent=buckets["design_intent"],
        key_difficult_points=buckets["key_difficult_points"],
        coverage=coverage,
        confidence=confidence,
        warnings=warnings,
        locator=ctx.locator,
        chunk_order=chunk_order,
    )
    snapshot = canonical_snapshot_hash(built)
    return built.model_copy(update={"snapshot_hash": snapshot})
