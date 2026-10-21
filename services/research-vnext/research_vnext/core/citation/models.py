"""RV07 · Citation 持久模型（表 `research_citation`）。

**权威是 `csl_json`**：BibTeX / RIS 是**派生缓存**，随时可由 `csl_json` 重算
（`bibtex` / `ris` 两列只是避免每次导出都重渲染）。`lossy_fields` 记录该条目
在派生格式里**结构上无法承载**的字段，供 UI 明确告知"导出会丢哪些信息"。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CitationRow(Base):
    """一条引用的 canonical 元数据 + 派生格式缓存。"""

    __tablename__ = "research_citation"
    __table_args__ = (
        # 同租户内 canonical 内容相同 → 同一行（引用去重）
        UniqueConstraint("tenant_id", "csl_hash", name="uq_research_citation_hash"),
        Index("ix_research_citation_tenant_project", "tenant_id", "project_id"),
        Index("ix_research_citation_doi", "tenant_id", "doi"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: 来源证据卡（若有）。引用可以由证据卡生成，也可以由检索 metadata 直接生成。
    evidence_card_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    #: **canonical**（唯一权威）
    csl_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    csl_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csl_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    doi: Mapped[str] = mapped_column(String(256), nullable=False, default="")

    #: 派生缓存（可由 csl_json 重算，不是权威）
    bibtex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ris: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 该条目在派生格式里结构上无法承载的字段（显式登记，不静默丢弃）
    lossy_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = ["CitationRow"]
