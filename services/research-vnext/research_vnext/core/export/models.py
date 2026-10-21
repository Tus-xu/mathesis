"""RV14 · 导出产物与审计模型。

- `paper_export_artifact`：一次成功导出的产物登记（字节在 MinIO，
  这里只有 key / sha256 / manifest / fingerprint）。
- `paper_export_audit`：下载授权链路审计（TOKEN_MINTED / DOWNLOAD /
  DOWNLOAD_DENIED / REVOKED），只追加。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaperExportArtifactRow(Base):
    __tablename__ = "paper_export_artifact"
    __table_args__ = (
        # 同租户内同 fingerprint（同 PaperSpec + 同格式集）唯一 → 去重的 DB 依据
        UniqueConstraint("tenant_id", "fingerprint", name="uq_paper_export_fp"),
        Index("ix_paper_export_session", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    #: PaperSpec 溯源（manifest.paperSpec 同源）
    paper_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    paper_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: 去重指纹 = sha256(paperId|revision|contentHash|sorted formats|serializer|template)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    #: {"markdown": {"sha256","sizeBytes","storageKey"}, ...}
    formats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class PaperExportAuditRow(Base):
    __tablename__ = "paper_export_audit"
    __table_args__ = (
        Index("ix_paper_export_audit_artifact", "artifact_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: TOKEN_MINTED | DOWNLOAD | DOWNLOAD_DENIED | REVOKED
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


__all__ = ["PaperExportArtifactRow", "PaperExportAuditRow"]
