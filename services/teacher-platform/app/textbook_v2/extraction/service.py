"""R07 语义提取编排：dry-run（不触库）/ staged（写 textbook_content_units + textbook_problems）。

约束（交接书 r07-handoff + D-007）：
- 只写 textbook_v2 目标表；零图片（asset 管线归 R13）；
- 幂等：同 (section_id, type, text, extraction_method) 的 unit 重跑 SKIP；
  同 (section_id, kind, stem_text) 的 problem 重跑 SKIP（新内容以新 order 追加，不覆盖旧行）；
- LLM 默认不启用（llm=None），rule 路径独立成立；写库仅由本模块执行。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.extraction.dto import ExtractionPlan
from app.textbook_v2.extraction.rules import EXTRACTOR_VERSION, extract_plan
from app.textbook_v2.models import TextbookContentUnit, TextbookProblem


def plan_extraction_section(
    chunks: list[dict[str, Any]],
    *,
    section_id: Any,
    section_title: str = "",
    source_id: Any = None,
    llm: Callable[[str], dict[str, Any]] | None = None,
) -> ExtractionPlan:
    """dry-run：只产出计划，不写任何库。chunks 为 R06 load_chunks 同构 dict 列表。"""
    return extract_plan(
        chunks,
        section_id=section_id,
        section_title=section_title,
        source_id=source_id,
        llm=llm,
    )


async def _existing_unit_keys(session: AsyncSession, section_id: Any) -> set[tuple[Any, str, str, str]]:
    rows = (
        (
            await session.execute(
                select(TextbookContentUnit.type, TextbookContentUnit.text, TextbookContentUnit.extraction_method).where(
                    TextbookContentUnit.section_id == section_id
                )
            )
        )
        .all()
    )
    return {(section_id, t, text, method) for t, text, method in rows}


async def _existing_problem_keys(session: AsyncSession, section_id: Any) -> set[tuple[Any, str, str]]:
    rows = (
        (
            await session.execute(
                select(TextbookProblem.kind, TextbookProblem.stem_text, TextbookProblem.solution_text).where(
                    TextbookProblem.section_id == section_id
                )
            )
        )
        .all()
    )
    return {(section_id, kind, stem) for kind, stem, _ in rows}


async def staged_extract_section(
    session: AsyncSession,
    chunks: list[dict[str, Any]],
    *,
    section_id: Any,
    section_title: str = "",
    source_id: Any = None,
    llm: Callable[[str], dict[str, Any]] | None = None,
) -> ExtractionPlan:
    """staged 写库：units + problems；幂等 SKIP 已存在（内容不变），不覆盖旧行。"""
    plan = plan_extraction_section(
        chunks, section_id=section_id, section_title=section_title, source_id=source_id, llm=llm
    )

    existing_units = await _existing_unit_keys(session, section_id)
    existing_problems = await _existing_problem_keys(session, section_id)

    added_units = 0
    for u in plan.units:
        key = (section_id, u.type, u.text, u.extraction_method or "")
        if key in existing_units:
            continue
        session.add(
            TextbookContentUnit(
                section_id=section_id,
                page_from=u.chunk_idx_from,
                page_to=u.chunk_idx_to,
                type=u.type,
                text=u.text,
                latex=u.latex,
                confidence=u.confidence,
                extraction_method=u.extraction_method,
                review_status=u.review_status,
            )
        )
        existing_units.add(key)
        added_units += 1

    added_problems = 0
    for p in plan.problems:
        if (section_id, p.kind, p.stem_text) in existing_problems:
            continue
        session.add(
            TextbookProblem(
                section_id=section_id,
                kind=p.kind,
                order=p.order,
                stem_text=p.stem_text,
                solution_text=p.solution_text,
                page_ref=p.chunk_idx,
                knowledge_tags=p.knowledge_tags or None,
                difficulty=p.difficulty,
                provenance=p.provenance,
            )
        )
        existing_problems.add((section_id, p.kind, p.stem_text))
        added_problems += 1

    if added_units == 0 and added_problems == 0:
        plan.issues.append("SKIP 全量幂等命中，无新增行")
    await session.commit()
    plan.issues.append(f"新增 units={added_units} problems={added_problems} (extractor={EXTRACTOR_VERSION})")
    return plan
