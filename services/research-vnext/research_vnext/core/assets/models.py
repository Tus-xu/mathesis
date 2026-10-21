"""RV04 · 资产元数据模型。

铁律：**大文件字节不得入库**。这里只有 hash / locator / ACL / rights / scan。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
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

SCAN_PENDING = "PENDING"
SCAN_CLEAN = "CLEAN"
SCAN_REJECTED = "REJECTED"
SCAN_QUARANTINED = "QUARANTINED"
SCAN_STATUSES = frozenset({SCAN_PENDING, SCAN_CLEAN, SCAN_REJECTED, SCAN_QUARANTINED})

RIGHTS_UNKNOWN = "UNKNOWN"
RIGHTS_PUBLIC = "PUBLIC"
RIGHTS_RESTRICTED = "RESTRICTED"
RIGHTS_STATUSES = frozenset({RIGHTS_UNKNOWN, RIGHTS_PUBLIC, RIGHTS_RESTRICTED})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchAssetRow(Base):
    __tablename__ = "research_asset"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_hash", name="uq_research_asset_tenant_hash"),
        Index("ix_research_asset_owner", "tenant_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 嗅探后的权威 media type，**不是**客户端声称的 Content-Type。
    media_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    claimed_media_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="minio")
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    acl: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rights: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    retention: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scan_status: Mapped[str] = mapped_column(String(32), nullable=False, default=SCAN_PENDING)
    scan_detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    @property
    def rights_status(self) -> str:
        raw = (self.rights or {}).get("status") or RIGHTS_UNKNOWN
        return raw if raw in RIGHTS_STATUSES else RIGHTS_UNKNOWN

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.scan_status == SCAN_CLEAN


class AssetSignedUrlRow(Base):
    """签名交付凭据。撤权时逐条失效，不依赖 TTL 单独兜底。"""

    __tablename__ = "asset_signed_url"
    __table_args__ = (Index("ix_asset_signed_url_asset", "asset_id", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
