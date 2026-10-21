"""RV05 · 解析运行与 Anchor 持久化模型。

大文件字节不入库；只存 hash / bbox / section / confidence / warnings。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

PARSE_QUEUED = "QUEUED"
PARSE_RUNNING = "RUNNING"
PARSE_SUCCEEDED = "SUCCEEDED"
PARSE_PARTIAL = "PARTIAL"
PARSE_FAILED = "FAILED"
PARSE_CANCELLED = "CANCELLED"
PARSE_STATUSES = frozenset(
    {PARSE_QUEUED, PARSE_RUNNING, PARSE_SUCCEEDED, PARSE_PARTIAL, PARSE_FAILED, PARSE_CANCELLED}
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetParseRunRow(Base):
    """一次结构化解析运行（对应 document.parse job）。"""

    __tablename__ = "asset_parse_run"
    __table_args__ = (
        Index("ix_asset_parse_run_asset", "asset_id", "created_at"),
        UniqueConstraint("job_id", name="uq_asset_parse_run_job"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    asset_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    asset_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PARSE_QUEUED)
    parser_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    parser_digest: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    parser_license: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    parser_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    parser_config_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    isolation: Mapped[str] = mapped_column(String(32), nullable=False, default="subprocess")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_pages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    failed_pages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    missing_kinds: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    section_tree: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    @property
    def is_partial(self) -> bool:
        return self.status == PARSE_PARTIAL

    @property
    def is_complete_success(self) -> bool:
        return self.status == PARSE_SUCCEEDED


class AssetAnchorRow(Base):
    """版本绑定 anchor。asset hash/version 变化后 stale，不静默重绑定。"""

    __tablename__ = "asset_anchor"
    __table_args__ = (
        Index("ix_asset_anchor_asset", "asset_id", "asset_version", "content_hash"),
        Index("ix_asset_anchor_parse_run", "parse_run_id"),
        UniqueConstraint("anchor_id", name="uq_asset_anchor_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    anchor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    bbox: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    section_path: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    text_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    region_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    page_width: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    page_height: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def to_public_dict(self, *, stale: bool = False, stale_reason: str = "") -> dict:
        return {
            "id": self.anchor_id,
            "type": self.type,
            "asset_id": str(self.asset_id),
            "asset_version": str(self.asset_version),
            "asset_hash": self.content_hash,
            "parse_run_id": str(self.parse_run_id),
            "parser_version": self.parser_version,
            "page": self.page,
            "bbox": list(self.bbox or []),
            "section_path": list(self.section_path or []),
            "text_preview": self.text_preview[:500],
            "text_hash": self.text_hash,
            "region_hash": self.region_hash,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "confidence": self.confidence,
            "warnings": list(self.warnings or []),
            "stale": stale,
            "stale_reason": stale_reason,
            "asset_available": not stale,
        }
