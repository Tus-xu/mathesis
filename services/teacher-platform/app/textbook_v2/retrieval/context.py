"""R08 load_teaching_context：从 anchor section 恢复完整有序 section 教学上下文。

聚合范围（对应 r08-handoff）：
- section 自身 + 全部后代 section（children 递归，BFS）—— top-k 命中章节中部仍
  恢复完整 section；
- R07 的 textbook_content_units（10 类）与 textbook_problems（题干/解答关联保真）
  全部复用为 Fact；
- 有序 page refs = chunk.order（册内全局 1-based 行号）升序去重；未回填时回退
  section.page_start..page_end；
- 缺失/低置信警告；每条事实带 source_ref（可 resolve_source_ref 解析）；
- 越权/不可见返回 None（不抛敏感错误）；教材事实纯净，不混入搜索内容。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.extraction.rules import extract_plan
from app.textbook_v2.models import TextbookChunk, TextbookContentUnit, TextbookProblem, TextbookSection
from app.textbook_v2.retrieval.dto import (
    ChunkPreview,
    Fact,
    SectionInfo,
    SourceInfo,
    TextbookTeachingContext,
    WarningEntry,
)
from app.textbook_v2.retrieval.provider import (
    _descendant_section_ids,
    _source_ref_chunk,
    _source_ref_problem,
    _source_ref_section,
    _source_ref_unit,
    _visible_section,
)

_PREVIEW_LEN = 160
# 低置信阈值（rule 提取器输出 confidence，越低越需人工复核）
_LOW_CONFIDENCE_THRESHOLD = 0.5


def _chunk_ref(source_id: Any, section_id: Any, chunk_id: Any) -> str:
    """canonical v1 direct-chunk locator ref（新 writer 出口，不产生假页码）。"""
    from app.textbook_v2.locator import chunk_locator

    return chunk_locator(source_id, section_id, chunk_id).to_ref()


def _preview(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= _PREVIEW_LEN else text[:_PREVIEW_LEN] + "…"


def _derive_chunk_facts(
    chunks: list[TextbookChunk],
    *,
    source_id: Any,
    section_title: str,
) -> tuple[list[Fact], set[Any]]:
    """从已授权 chunk 只读派生事实，供尚未落 R07 产物的切片使用。

    这是确定性规则提取的内存回退：不写 textbook_content_units /
    textbook_problems，也不把 chunk 序号伪装成 page_ref。事实的 source_ref
    直接指向可解析的 chunk locator，便于后续人工复核。
    """
    if not chunks:
        return [], set()
    ordered = sorted(chunks, key=lambda c: (c.order is None, c.order or 0, str(c.id)))
    plan = extract_plan(
        [{"content": c.text or ""} for c in ordered],
        section_id=ordered[0].section_id,
        section_title=section_title,
        source_id=source_id,
    )

    def chunk_at(index: int) -> TextbookChunk:
        # R07 candidate indexes are 1-based and follow the ordered chunk list.
        return ordered[max(0, min(index - 1, len(ordered) - 1))]

    def anchor(index: int) -> int:
        row = chunk_at(index)
        return row.order if row.order is not None else index

    def ref(index: int) -> str:
        row = chunk_at(index)
        return _chunk_ref(source_id, row.section_id, row.id)

    facts: list[Fact] = []
    for unit in plan.units:
        source_ref = ref(unit.chunk_idx_from)
        row = chunk_at(unit.chunk_idx_from)
        facts.append(
            Fact(
                kind="unit",
                fact_type=unit.type,
                text=unit.text,
                latex=unit.latex,
                chunk_ref=source_ref,
                chunk_start=anchor(unit.chunk_idx_from),
                chunk_end=anchor(unit.chunk_idx_to),
                confidence=unit.confidence,
                review_status=unit.review_status,
                provenance=unit.extraction_method,
                source_ref=source_ref,
                section_ref=_source_ref_section(source_id, row.section_id),
            )
        )
    for problem in plan.problems:
        source_ref = ref(problem.chunk_idx)
        row = chunk_at(problem.chunk_idx)
        facts.append(
            Fact(
                kind="problem",
                fact_type=problem.kind,
                text=problem.stem_text,
                solution=problem.solution_text,
                chunk_ref=source_ref,
                chunk_start=anchor(problem.chunk_idx),
                chunk_end=anchor(problem.chunk_idx),
                review_status="pending",
                provenance=problem.provenance,
                source_ref=source_ref,
                section_ref=_source_ref_section(source_id, row.section_id),
            )
        )
    return facts, {c.section_id for c in ordered}


def _section_info(sec: TextbookSection) -> SectionInfo:
    return SectionInfo(
        id=sec.id,
        textbook_id=sec.textbook_id,
        title=sec.title,
        section_path=sec.section_path,
        level=sec.level,
        order=sec.order,
        page_range=[sec.page_start, sec.page_end],
        chunk_start=sec.chunk_start,
        chunk_end=sec.chunk_end,
    )


async def load_teaching_context(
    session: AsyncSession,
    section_id: Any,
    *,
    include_neighbors: bool = True,
    course_context: dict[str, Any] | None = None,
) -> TextbookTeachingContext | None:
    """恢复完整有序 section 教学上下文；section 越权/不可见 → None。"""
    ctx = course_context or {}
    visible = await _visible_section(session, section_id, school_id=ctx.get("school_id"))
    if visible is None:
        return None
    section, source = visible

    source_info = SourceInfo(
        id=source.id,
        publisher=source.publisher,
        edition=source.edition,
        grade=source.grade,
        volume=source.volume,
        rights_status=source.rights_status,
        batch_id=source.batch_id,
        manifest_sha256=source.manifest_sha256,
        source_kind=source.source_kind or "direct-chunk",
    )
    section_info = _section_info(section)

    parent: SectionInfo | None = None
    if include_neighbors and section.parent_id is not None:
        parent_row = (
            (await session.execute(select(TextbookSection).where(TextbookSection.id == section.parent_id)))
            .scalars()
            .first()
        )
        if parent_row is not None:
            parent = _section_info(parent_row)

    children: list[SectionInfo] = []
    if include_neighbors:
        child_rows = (
            (
                await session.execute(
                    select(TextbookSection).where(TextbookSection.parent_id == section.id).order_by(TextbookSection.order)
                )
            )
            .scalars()
            .all()
        )
        children = [_section_info(c) for c in child_rows]

    target_ids = await _descendant_section_ids(session, section, include_descendants=True)
    section_by_id: dict[Any, TextbookSection] = {
        s.id: s
        for s in (
            await session.execute(select(TextbookSection).where(TextbookSection.id.in_(target_ids)))
        )
        .scalars()
        .all()
    }

    units = (
        (
            await session.execute(
                select(TextbookContentUnit).where(TextbookContentUnit.section_id.in_(target_ids))
            )
        )
        .scalars()
        .all()
    )
    problems = (
        (
            await session.execute(
                select(TextbookProblem).where(TextbookProblem.section_id.in_(target_ids))
            )
        )
        .scalars()
        .all()
    )
    chunks = (
        (
            await session.execute(
                select(TextbookChunk).where(TextbookChunk.section_id.in_(target_ids))
            )
        )
        .scalars()
        .all()
    )

    facts: list[Fact] = []
    for u in units:
        facts.append(
            Fact(
                kind="unit",
                fact_type=u.type,
                text=u.text,
                latex=u.latex,
                page_from=u.page_from,
                page_to=u.page_to,
                chunk_ref=u.chunk_ref,
                chunk_start=u.page_from,
                chunk_end=u.page_to,
                confidence=u.confidence,
                review_status=u.review_status,
                provenance=u.extraction_method,
                source_ref=_source_ref_unit(source.id, u.section_id, u.id),
                section_ref=_source_ref_section(source.id, u.section_id),
            )
        )
    for p in problems:
        facts.append(
            Fact(
                kind="problem",
                fact_type=p.kind,
                text=p.stem_text,
                solution=p.solution_text,
                page_ref=p.page_ref,
                chunk_ref=p.chunk_ref,
                chunk_start=p.page_ref,
                chunk_end=p.page_ref,
                provenance=p.provenance,
                source_ref=_source_ref_problem(source.id, p.section_id, p.id),
                section_ref=_source_ref_section(source.id, p.section_id),
            )
        )

    fallback_section_ids: set[Any] = set()
    if not units and not problems and chunks:
        # 已有授权切片但尚未落 R07 语义单元：只读派生，避免 T23 把真实教材误报为空。
        facts, fallback_section_ids = _derive_chunk_facts(
            chunks,
            source_id=source.id,
            section_title=section.title,
        )

    # 教材原序：direct-chunk 事实优先用 chunk 锚；legacy unit/problem 才回退 page_*。
    facts.sort(
        key=lambda f: (
            (f.chunk_start if f.chunk_start is not None else f.page_from if f.page_from is not None else f.page_ref)
            or 0,
            f.kind != "unit",
            f.source_ref,
        )
    )

    chunks_sorted = sorted(chunks, key=lambda c: (c.order is None, c.order or 0))
    ordered_page_refs = sorted({c.order for c in chunks if c.order is not None})
    if not ordered_page_refs:  # 未回填 order 时回退 section 页区间
        ordered_page_refs = list(range(section.page_start, section.page_end + 1))

    from app.textbook_v2.locator import section_locator

    locator = section_locator(
        source.id, section.id, chunk_start=section.chunk_start, chunk_end=section.chunk_end
    ).to_ref()

    context = TextbookTeachingContext(
        source=source_info,
        section=section_info,
        parent=parent,
        children=children,
        facts=facts,
        ordered_page_refs=ordered_page_refs,
        chunks=[
            ChunkPreview(
                chunk_id=c.id,
                order=c.order,
                page_range=c.page_range,
                text_preview=_preview(c.text),
                source_ref=_source_ref_chunk(source.id, c.section_id, c.id),
                chunk_ref=_chunk_ref(source.id, c.section_id, c.id),
            )
            for c in chunks_sorted
        ],
        locator=locator,
        chunk_order=ordered_page_refs,
    )
    _fill_categories(context)
    _fill_warnings(
        context,
        sections=section_by_id,
        units=units,
        problems=problems,
        fallback_section_ids=fallback_section_ids,
    )
    return context


_CATEGORY_MAP = {
    "definition": "definitions",
    "theorem": "theorems",
    "formula": "formulas",
    "derivation": "derivations",
    "example": "examples",
    "exercise": "exercises",
    "inquiry": "inquiries",
    "figure": "figures",
    "introduction": "introductions",
    "summary": "summaries",
}


def _fill_categories(context: TextbookTeachingContext) -> None:
    """按事实类型分桶（unit 类型直接映射；problem 的 kind 即 example/exercise/inquiry）。"""
    for f in context.facts:
        bucket = _CATEGORY_MAP.get(f.fact_type)
        if bucket is None:
            continue
        getattr(context, bucket).append(f)


def _fill_warnings(
    context: TextbookTeachingContext,
    *,
    sections: dict[Any, TextbookSection],
    units: list[TextbookContentUnit],
    problems: list[TextbookProblem],
    fallback_section_ids: set[Any] | None = None,
) -> None:
    """缺失/低置信警告（不携带任何搜索内容）。

    no_units 采用子树感知：仅当 section 自身及其全部后代都无 content unit / problem
    才告警（结构性父节点不误报）。
    """
    # no_units remains truthful: it describes the absence of persisted R07
    # semantic rows even when a read-only fallback supplied provisional facts.
    content_sec_ids = {u.section_id for u in units} | {p.section_id for p in problems}

    # 子树内容可达性（BFS 自底向上）：leaf → root 传播
    children_of: dict[Any, list[Any]] = {sid: [] for sid in sections}
    for sid, sec in sections.items():
        if sec.parent_id in sections:
            children_of[sec.parent_id].append(sid)
    subtree_has_content = {sid: (sid in content_sec_ids) for sid in sections}
    # 从叶子向上传播（按 level 降序保证子先于父）
    for sid in sorted(sections, key=lambda s: sections[s].level, reverse=True):
        if subtree_has_content[sid]:
            parent = sections[sid].parent_id
            if parent in subtree_has_content:
                subtree_has_content[parent] = True

    for sec_id in sections:
        if not subtree_has_content[sec_id]:
            context.warnings.append(
                WarningEntry(
                    code="no_units",
                    message="section 及其后代均无 content unit / problem（语义提取缺失或未提取）",
                    ref=_source_ref_section(context.source.id, sec_id),
                )
            )

    if fallback_section_ids:
        context.warnings.append(
            WarningEntry(
                code="rule_extraction_fallback",
                message="教材切片尚无持久化语义单元：已使用只读确定性规则提取，结果需人工复核",
                ref=_source_ref_section(context.source.id, sorted(fallback_section_ids, key=str)[0]),
            )
        )

    pending = [u for u in units if u.review_status != "reviewed"]
    if pending:
        refs = [_source_ref_unit(context.source.id, u.section_id, u.id) for u in pending[:5]]
        context.warnings.append(
            WarningEntry(
                code="pending_review",
                message=f"{len(pending)} 条 content unit 未复核（review_status != reviewed）",
                ref=refs[0] if refs else None,
            )
        )

    low_conf = [u for u in units if u.confidence is not None and u.confidence < _LOW_CONFIDENCE_THRESHOLD]
    if low_conf:
        context.warnings.append(
            WarningEntry(
                code="low_confidence",
                message=f"{len(low_conf)} 条 content unit 置信度低于 {_LOW_CONFIDENCE_THRESHOLD}",
                ref=_source_ref_unit(context.source.id, low_conf[0].section_id, low_conf[0].id),
            )
        )

    figure_sections = {u.section_id for u in units if u.type == "figure"}
    for sec_id in figure_sections:
        context.warnings.append(
            WarningEntry(
                code="figure_placeholder",
                message="figure 占位 unit 存在（图题资产整块移交 R13，MinIO/AssetRef/Vision）",
                ref=_source_ref_section(context.source.id, sec_id),
            )
        )
