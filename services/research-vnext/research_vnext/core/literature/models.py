"""RV04 · 文献 metadata 持久模型。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

AVAIL_UNKNOWN = "UNKNOWN"
AVAIL_YES = "YES"
AVAIL_NO = "NO"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LiteratureRecordRow(Base):
    __tablename__ = "literature_record"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "provider_record_id", name="uq_lit_tenant_provider_rec"),
        Index("ix_lit_tenant_doi", "tenant_id", "doi"),
        Index("ix_lit_tenant_fingerprint", "tenant_id", "fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    provider_record_id: Mapped[str] = mapped_column(String(256), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    query: Mapped[str] = mapped_column(Text, nullable=False, default="")

    doi: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    published_date: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    venue: Mapped[str] = mapped_column(Text, nullable=False, default="")

    abstract_available: Mapped[str] = mapped_column(String(8), nullable=False, default=AVAIL_UNKNOWN)
    fulltext_available: Mapped[str] = mapped_column(String(8), nullable=False, default=AVAIL_UNKNOWN)
    rights_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    rights_detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    raw_response_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: metadata **不是** Evidence；标记来源层级，禁止下游冒充
    record_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="METADATA_ONLY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class LiteratureDedupConflictRow(Base):
    """同 DOI / 同指纹但 payload 不一致 → 记录冲突，**不静默合并**。"""

    __tablename__ = "literature_dedup_conflict"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    doi: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    existing_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    incoming_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    existing_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
