"""PPT Studio V2 持久化模型（8200 独立库 ppt_studio_v2）。

前缀统一 `ppt_v2_*`（与 8100 共用同一领域命名，但物理库/迁移链完全独立）。
- deck/asset 全量 spec JSONB 载体（V2 唯一事实来源的持久化，中性契约不依赖 8100）；
- jobs/outbox/inbox/events：可恢复空流程的可靠性运行时表。

SQLite（测试）/ PG（生产）双方言兼容；JsonB 用 with_variant 退化为 JSON。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JsonB = JSONB().with_variant(JSON(), "sqlite")
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


def _uuid() -> UUID:
    return uuid4()


# ─────────── deck 事实权威（中性契约载体）───────────


class PptV2DeckSpecRecord(Base):
    __tablename__ = "ppt_v2_deck_specs"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_pptdeck_revision"),
        Index("ix_pptdeck_school_schema", "school_id", "schema_version"),
        UniqueConstraint("spec_sha256", name="uq_pptdeck_spec_sha"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    school_id: Mapped[UUID | None] = mapped_column(nullable=True)  # NULL = 平台级
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    # R21：内容寻址指纹（canonical JSON sha256）→ 同 spec 幂等入库（同请求无重复 artifact 的源头）
    spec_sha256: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PptV2AssetRecord(Base):
    __tablename__ = "ppt_v2_assets"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('TEXTBOOK','TEACHER_UPLOAD','MATH_RENDER','AI_IMAGE')",
            name="ck_pptasset_kind",
        ),
        CheckConstraint("rights_status IN ('QUARANTINED','STAGED','ACTIVE')", name="ck_pptasset_rights"),
        UniqueConstraint("deck_id", "object_key", name="uq_pptasset_deck_key"),
        Index("ix_pptasset_kind", "kind"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID] = mapped_column(
        ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rights_status: Mapped[str] = mapped_column(Text, nullable=False, default="QUARANTINED")
    rights_evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    preserve_original: Mapped[bool] = mapped_column(nullable=False, default=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_meta: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─────────── 可恢复空流程：Job / 事件 / outbox / inbox ───────────

JOB_RUNNING = ("PENDING", "CLAIMED")
JOB_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "DLQ")


class PptV2Job(Base):
    __tablename__ = "ppt_v2_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','CLAIMED','COMPLETED','FAILED','CANCELLED','DLQ')",
            name="ck_pptjobs_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_pptjobs_attempt"),
        CheckConstraint("revision >= 1", name="ck_pptjobs_revision"),
        UniqueConstraint("job_type", "dedupe_key", name="uq_pptjobs_dedupe"),
        Index("ix_pptjobs_status_avail", "status", "available_at"),
        Index("ix_pptjobs_claim_expires", "claim_expires_at"),
        Index("ix_pptjobs_owner_created", "owner_service", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    owner_service: Mapped[str] = mapped_column(Text, nullable=False, default="ppt_studio_v2")
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    claim_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)

    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)

    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PptV2JobEvent(Base):
    __tablename__ = "ppt_v2_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_pptjob_events_job_seq"),
        CheckConstraint("seq > 0", name="ck_pptjob_events_seq"),
        Index("ix_pptjob_events_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("ppt_v2_jobs.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PptV2Outbox(Base):
    __tablename__ = "ppt_v2_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','PUBLISHED','FAILED','DLQ')",
            name="ck_pptoutbox_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_pptoutbox_attempt"),
        UniqueConstraint("topic", "dedupe_key", name="uq_pptoutbox_dedupe"),
        Index("ix_pptoutbox_topic_status_avail", "topic", "status", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PptV2Inbox(Base):
    __tablename__ = "ppt_v2_inbox"
    __table_args__ = (
        CheckConstraint("status IN ('PROCESSED','FAILED')", name="ck_pptinbox_status"),
        UniqueConstraint("dedupe_key", name="uq_pptinbox_dedupe"),
        Index("ix_pptinbox_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    owner_service: Mapped[str] = mapped_column(Text, nullable=False, default="ppt_studio_v2")
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PROCESSED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ─────────── R21：页级 checkpoint + 产物登记 ───────────


class PptV2DeckPage(Base):
    """页级 checkpoint：内容寻址（content_sha256）→ 局部重试只补缺失页，失败不丢整套。"""

    __tablename__ = "ppt_v2_deck_pages"
    __table_args__ = (
        CheckConstraint("status IN ('COMPLETED','FAILED')", name="ck_pptpage_status"),
        CheckConstraint("page_no >= 1", name="ck_pptpage_no"),
        CheckConstraint("attempt >= 0", name="ck_pptpage_attempt"),
        UniqueConstraint("deck_id", "page_no", "content_sha256", name="uq_pptpage_deck_no_hash"),
        Index("ix_pptpage_deck_status", "deck_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID] = mapped_column(
        ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ppt_v2_jobs.id", ondelete="SET NULL"), nullable=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    slide_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="COMPLETED")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fragment_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    png_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    fragment_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    png_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    qa: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(nullable=False, default=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PptV2Artifact(Base):
    """产物登记（8200 事实权威）：唯一 object_key → 同请求天然无重复 artifact。"""

    __tablename__ = "ppt_v2_artifacts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('DECK_PPTX','PAGE_FRAGMENT','PAGE_PNG','CONTACT_SHEET','ASSET')",
            name="ck_pptartifact_kind",
        ),
        UniqueConstraint("object_key", name="uq_pptartifact_object_key"),
        Index("ix_pptartifact_deck", "deck_id"),
        Index("ix_pptartifact_job", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ppt_v2_deck_specs.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ppt_v2_jobs.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_meta: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
