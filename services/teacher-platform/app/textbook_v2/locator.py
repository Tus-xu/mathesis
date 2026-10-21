"""R12 版本化 SourceLocator：direct-chunk 证据定位与解析（P0）。

ref 规范（v1，新 writer 唯一出口）：
    textbook_v2/locator/v1/source/<source_id>
        [/section/<section_id>]
        [/chunk/<chunk_id> | /chunk-range/<start>-<end>]
        [/unit/<unit_id> | /problem/<problem_id> | /asset/<asset_id>]

legacy 兼容（schema_version=0，只读，不允许新写）：
    textbook_v2/source/<source_id>[/section/<section_id>[/unit|problem|chunk/<id>]]

解析语义（与 resolve_source_ref 一致）：
- 权限：rights 可见（STAGED+ACTIVE，visible_scope 语义）+ 租户隔离；
- 越权/非法/非教材域 → None（不抛敏感错误）；
- chunk-range 解析为 chunk_start/chunk_end（全局 1-based 行号区间）。

铁律：
- 新 artifact 一律用 v1 ref；page_ref/page_refs/pNN 只允许 legacy 读取，新 writer 零写入；
- locator 不可指向 TextbookPage（切片源无原页，D-002）。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.models import (
    TextbookChunk,
    TextbookContentUnit,
    TextbookProblem,
    TextbookSection,
    TextbookSource,
)
from app.textbook_v2.retrieval.dto import SourceView

# 检索可见 rights（与 provider.VISIBLE_RIGHTS 同语义，避免跨模块常量漂移）
VISIBLE_RIGHTS = ("STAGED", "ACTIVE")

# 当前 locator 格式版本；跨格式断裂必须 bump（新 writer 只允许当前版本）
LOCATOR_SCHEMA_VERSION = 1
# legacy 格式（旧 resolve_source_ref 产出）只读标记
LEGACY_SCHEMA_VERSION = 0

_PREVIEW_LEN = 160

# v1 规范格式（严格锚定到行尾）
_V1_RE = re.compile(
    r"^textbook_v2/locator/v1/source/([0-9a-fA-F-]{36})"
    r"(?:/section/([0-9a-fA-F-]{36}))?"
    r"(?:/chunk/([0-9a-fA-F-]{36})|/chunk-range/(\d+)-(\d+))?"
    r"(?:/(unit|problem|asset)/([0-9a-fA-F-]{36}))?$"
)
# legacy 格式（旧 resolve_source_ref 产出，只读兼容）
_LEGACY_RE = re.compile(
    r"^textbook_v2/source/([0-9a-fA-F-]{36})"
    r"(?:/section/([0-9a-fA-F-]{36}))?"
    r"(?:/(unit|problem|chunk)/([0-9a-fA-F-]{36}))?$"
)


@dataclass(frozen=True)
class SourceLocator:
    """版本化定位器：source_id 必填；section/chunk/unit/problem/asset 逐级可选。

    chunk_start/chunk_end：section 级 chunk 区间（全局 1-based 行号），
    与 chunk_id 互斥（同一 locator 要么指向单个 chunk，要么指向区间）。
    """

    source_id: uuid.UUID
    section_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    problem_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    chunk_start: int | None = None
    chunk_end: int | None = None
    schema_version: int = LOCATOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.chunk_id is not None and (self.chunk_start is not None or self.chunk_end is not None):
            raise ValueError("chunk_id 与 chunk_start/chunk_end 互斥（单 chunk 或区间二选一）")
        if (self.chunk_start is None) != (self.chunk_end is None):
            raise ValueError("chunk_start 与 chunk_end 必须同时提供或同时省略")
        if self.unit_id is not None and self.problem_id is not None:
            raise ValueError("unit_id 与 problem_id 互斥")
        if self.schema_version not in (LOCATOR_SCHEMA_VERSION, LEGACY_SCHEMA_VERSION):
            raise ValueError(f"不支持的 locator schema_version: {self.schema_version}")

    def to_ref(self) -> str:
        """序列化为 canonical v1 ref；legacy locator 禁止新写（只能由 from_ref 解析出）。"""
        if self.schema_version != LOCATOR_SCHEMA_VERSION:
            raise ValueError("legacy locator 只读：禁止以新 writer 出口序列化")
        parts = [f"textbook_v2/locator/v1/source/{self.source_id}"]
        if self.section_id is not None:
            parts.append(f"section/{self.section_id}")
        if self.chunk_id is not None:
            parts.append(f"chunk/{self.chunk_id}")
        elif self.chunk_start is not None:
            parts.append(f"chunk-range/{self.chunk_start}-{self.chunk_end}")
        if self.unit_id is not None:
            parts.append(f"unit/{self.unit_id}")
        if self.problem_id is not None:
            parts.append(f"problem/{self.problem_id}")
        if self.asset_id is not None:
            parts.append(f"asset/{self.asset_id}")
        return "/".join(parts)

    @classmethod
    def from_ref(cls, ref: str) -> SourceLocator | None:
        """解析 v1 或 legacy ref；非法/非教材域 → None。"""
        ref = (ref or "").strip()
        m = _V1_RE.match(ref)
        if m:
            return cls(
                source_id=uuid.UUID(m.group(1)),
                section_id=_opt_uuid(m.group(2)),
                chunk_id=_opt_uuid(m.group(3)),
                chunk_start=int(m.group(4)) if m.group(4) else None,
                chunk_end=int(m.group(5)) if m.group(5) else None,
                unit_id=_opt_uuid(m.group(7)) if m.group(6) == "unit" else None,
                problem_id=_opt_uuid(m.group(7)) if m.group(6) == "problem" else None,
                asset_id=_opt_uuid(m.group(7)) if m.group(6) == "asset" else None,
                schema_version=LOCATOR_SCHEMA_VERSION,
            )
        m = _LEGACY_RE.match(ref)
        if m:
            entity = m.group(3)
            return cls(
                source_id=uuid.UUID(m.group(1)),
                section_id=_opt_uuid(m.group(2)),
                chunk_id=_opt_uuid(m.group(4)) if entity == "chunk" else None,
                unit_id=_opt_uuid(m.group(4)) if entity == "unit" else None,
                problem_id=_opt_uuid(m.group(4)) if entity == "problem" else None,
                schema_version=LEGACY_SCHEMA_VERSION,
            )
        return None


def _opt_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 构造助手（新 writer 唯一出口）
# ─────────────────────────────────────────────────────────────────────────────


def source_locator(source_id: Any) -> SourceLocator:
    return SourceLocator(source_id=uuid.UUID(str(source_id)))


def section_locator(source_id: Any, section_id: Any, *, chunk_start: int | None = None, chunk_end: int | None = None) -> SourceLocator:
    return SourceLocator(
        source_id=uuid.UUID(str(source_id)),
        section_id=uuid.UUID(str(section_id)),
        chunk_start=chunk_start,
        chunk_end=chunk_end,
    )


def chunk_locator(source_id: Any, section_id: Any, chunk_id: Any) -> SourceLocator:
    return SourceLocator(
        source_id=uuid.UUID(str(source_id)),
        section_id=uuid.UUID(str(section_id)),
        chunk_id=uuid.UUID(str(chunk_id)),
    )


def unit_locator(source_id: Any, section_id: Any, chunk_id: Any, unit_id: Any) -> SourceLocator:
    return SourceLocator(
        source_id=uuid.UUID(str(source_id)),
        section_id=uuid.UUID(str(section_id)),
        chunk_id=uuid.UUID(str(chunk_id)),
        unit_id=uuid.UUID(str(unit_id)),
    )


def problem_locator(source_id: Any, section_id: Any, chunk_id: Any, problem_id: Any) -> SourceLocator:
    return SourceLocator(
        source_id=uuid.UUID(str(source_id)),
        section_id=uuid.UUID(str(section_id)),
        chunk_id=uuid.UUID(str(chunk_id)),
        problem_id=uuid.UUID(str(problem_id)),
    )


def asset_locator(source_id: Any, section_id: Any, asset_key: Any) -> SourceLocator:
    """asset 定位：asset_key 非 UUID 时降级为区间/章节定位（不伪造 asset id）。"""
    try:
        uid = uuid.UUID(str(asset_key))
    except ValueError:
        uid = None
    return SourceLocator(
        source_id=uuid.UUID(str(source_id)),
        section_id=uuid.UUID(str(section_id)) if section_id is not None else None,
        asset_id=uid,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 解析器（权限 + 租户隔离；越权/非法 → None）
# ─────────────────────────────────────────────────────────────────────────────


def _preview(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= _PREVIEW_LEN else text[:_PREVIEW_LEN] + "…"


async def _visible_source(session: AsyncSession, source_id: Any, *, school_id: Any | None) -> TextbookSource | None:
    source = (
        await session.execute(select(TextbookSource).where(TextbookSource.id == source_id))
    ).scalars().first()
    if source is None or source.rights_status not in VISIBLE_RIGHTS:
        return None
    if school_id is None:
        if source.school_id is not None:
            return None
    elif source.school_id is not None and source.school_id != school_id:
        return None
    return source


async def _visible_section(
    session: AsyncSession, source: TextbookSource, section_id: Any
) -> TextbookSection | None:
    section = (
        await session.execute(select(TextbookSection).where(TextbookSection.id == section_id))
    ).scalars().first()
    if section is None or section.textbook_id != source.id:
        return None
    return section


async def resolve_locator(
    session: AsyncSession,
    locator: SourceLocator,
    *,
    course_context: dict[str, Any] | None = None,
) -> SourceView | None:
    """把 SourceLocator 解析成可读 SourceView（文本截断预览）；越权/不可见返回 None。

    权限语义与 provider.resolve_source_ref 完全一致（rights visible + 租户隔离）。
    """
    ctx = course_context or {}
    school_id = ctx.get("school_id")
    source = await _visible_source(session, locator.source_id, school_id=school_id)
    if source is None:
        return None

    textbook = f"{source.publisher}·{source.edition}·{source.volume}"
    if locator.section_id is None:
        return SourceView(
            kind="source",
            source_ref=locator.to_ref() if locator.schema_version == LOCATOR_SCHEMA_VERSION else "",
            locator=locator.to_ref() if locator.schema_version == LOCATOR_SCHEMA_VERSION else None,
            textbook=textbook,
            rights_status=source.rights_status,
            text_preview=_preview(source.publisher),
            chunk_ref=None,
        )

    section = await _visible_section(session, source, locator.section_id)
    if section is None:
        return None
    section_path = section.section_path
    section_title = section.title

    if locator.chunk_id is not None or locator.chunk_start is not None:
        chunk_ref = locator.to_ref() if locator.schema_version == LOCATOR_SCHEMA_VERSION else None
        if locator.chunk_id is not None:
            chunk = (
                await session.execute(select(TextbookChunk).where(TextbookChunk.id == locator.chunk_id))
            ).scalars().first()
            if chunk is None or chunk.section_id != section.id:
                return None
            return SourceView(
                kind="chunk",
                source_ref=chunk_ref or "",
                locator=chunk_ref,
                textbook=textbook,
                section_title=section_title,
                section_path=section_path,
                order=chunk.order,
                page_range=chunk.page_range or [section.chunk_start or section.page_start, section.chunk_end or section.page_end],
                text_preview=_preview(chunk.text),
                rights_status=source.rights_status,
                chunk_ref=chunk_ref,
            )
        return SourceView(
            kind="section",
            source_ref=chunk_ref or "",
            locator=chunk_ref,
            textbook=textbook,
            section_title=section_title,
            section_path=section_path,
            order=None,
            page_range=[locator.chunk_start, locator.chunk_end],
            text_preview=_preview(section.normalized_title),
            rights_status=source.rights_status,
            chunk_ref=chunk_ref,
        )

    if locator.unit_id is not None:
        unit = (
            await session.execute(select(TextbookContentUnit).where(TextbookContentUnit.id == locator.unit_id))
        ).scalars().first()
        if unit is None or unit.section_id != section.id:
            return None
        return SourceView(
            kind="unit",
            source_ref="",
            locator=locator.to_ref() if locator.schema_version == LOCATOR_SCHEMA_VERSION else None,
            textbook=textbook,
            section_title=section_title,
            section_path=section_path,
            order=unit.page_from,
            page_range=[unit.page_from, unit.page_to],
            text_preview=_preview(unit.text or unit.latex or ""),
            rights_status=source.rights_status,
            extraction_method=unit.extraction_method,
            chunk_ref=unit.chunk_ref,
        )

    if locator.problem_id is not None:
        problem = (
            await session.execute(select(TextbookProblem).where(TextbookProblem.id == locator.problem_id))
        ).scalars().first()
        if problem is None or problem.section_id != section.id:
            return None
        return SourceView(
            kind="problem",
            source_ref="",
            locator=locator.to_ref() if locator.schema_version == LOCATOR_SCHEMA_VERSION else None,
            textbook=textbook,
            section_title=section_title,
            section_path=section_path,
            order=problem.page_ref,
            page_range=[problem.page_ref, problem.page_ref],
            text_preview=_preview(problem.stem_text or ""),
            rights_status=source.rights_status,
            chunk_ref=problem.chunk_ref,
        )

    return SourceView(
        kind="section",
        source_ref="",
        locator=locator.to_ref() if locator.schema_version == LOCATOR_SCHEMA_VERSION else None,
        textbook=textbook,
        section_title=section_title,
        section_path=section_path,
        order=None,
        page_range=[section.chunk_start or section.page_start, section.chunk_end or section.page_end],
        text_preview=_preview(section.normalized_title),
        rights_status=source.rights_status,
        chunk_ref=None,
    )


async def resolve_source_ref(
    session: AsyncSession,
    source_ref: str,
    *,
    course_context: dict[str, Any] | None = None,
) -> SourceView | None:
    """兼容入口：legacy ref（textbook_v2/source/...）与 v1 locator ref 均解析。

    legacy ref 只读解析（schema_version=0），不会产生任何新写入。
    """
    locator = SourceLocator.from_ref(source_ref)
    if locator is None:
        return None
    view = await resolve_locator(session, locator, course_context=course_context)
    if view is None:
        return None
    # legacy ref 保留原始字符串（可被旧调用方继续使用）；v1 用 canonical 串
    if locator.schema_version == LEGACY_SCHEMA_VERSION:
        view.source_ref = source_ref
        view.locator = None
    return view
