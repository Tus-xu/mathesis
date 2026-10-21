"""R11.5-C 可靠性正式表（reliability_* 前缀，所有权进正式迁移 e26a6b7c8d9e）。

表集合：
- reliability_jobs：长任务/异步作业事实（状态机 PENDING/CLAIMED/COMPLETED/FAILED/CANCELLED/DLQ）
- reliability_job_events：作业内 durable 事件（UNIQUE(job_id, seq)，同事务追加）
- reliability_outbox：事务性发件箱（DB 权威 → Valkey Stream 热通道；PENDING→PUBLISHED/DLQ）
- reliability_inbox：消费侧去重（重复投递 / ACK 前崩溃 / 重放兜底 → 单副作用）
- reliability_idempotency：REST 幂等（owner_service/operation/principal/idempotency_key
  + request_hash；同 key 不同 payload → 409）

SQLite（测试）/ PG（生产）双方言兼容；可空 dedupe_key 的 UNIQUE 允许无键重复行
（PG/SQLite 对 NULL 均视为互异），即「幂等去重仅在显式提供 dedupe_key 时生效」。
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


JOB_RUNNING = ("PENDING", "CLAIMED")
JOB_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "DLQ")


class ReliabilityJob(Base):
    """作业事实：payload 是作业输入；状态机与 revision 由 JobEngine 独占维护。

    - claim_token + claim_expires_at = worker 租约（fencing：过期即被 reconciler 回收重投）；
    - revision 供 expected_revision 乐观并发（任何状态修改必须携带）；
    - dedupe_key 唯一（owner_service, job_type, dedupe_key）→ 幂等入队；
    - 业务状态/事件/outbox 与作业状态修改处于同一 DB 事务（由调用方 session 承载）。
    """

    __tablename__ = "reliability_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','CLAIMED','COMPLETED','FAILED','CANCELLED','DLQ')",
            name="ck_reliability_jobs_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_reliability_jobs_attempt"),
        CheckConstraint("revision >= 1", name="ck_reliability_jobs_revision"),
        UniqueConstraint(
            "owner_service", "job_type", "dedupe_key", name="uq_reliability_jobs_dedupe"
        ),
        Index("ix_rel_jobs_owner_status_avail", "owner_service", "status", "available_at"),
        Index("ix_rel_jobs_claim_expires", "claim_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    owner_service: Mapped[str] = mapped_column(Text, nullable=False)
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
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)

    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    parent_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reliability_jobs.id", ondelete="SET NULL"), nullable=True
    )

    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReliabilityJobEvent(Base):
    """作业内 durable 事件（权威回放源）：与状态修改同事务追加，UNIQUE(job_id, seq) 防重发。"""

    __tablename__ = "reliability_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_rel_job_events_job_seq"),
        CheckConstraint("seq > 0", name="ck_rel_job_events_seq"),
        Index("ix_rel_job_events_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("reliability_jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReliabilityOutbox(Base):
    """事务性发件箱：业务事务内入队 → dispatcher 轮询 FOR UPDATE SKIP LOCKED → 发布到流。

    语义：
    - 同事务写入（业务状态 + job + outbox 一起 commit）→ DB commit 后发布失败不会丢事件
      （行保 PENDING，dispatcher 重试；消费侧 inbox 去重保证不重复副作用）；
    - 发布 = 外部副作用（Valkey XADD），成功后单独事务标记 PUBLISHED；发布后崩溃未标记 →
      下次重发同 message_id（幂等 XADD），消费侧去重；
    - attempt >= max_attempts → DLQ；available_at 指数退避 = 背压。
    """

    __tablename__ = "reliability_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','PUBLISHED','FAILED','DLQ')",
            name="ck_reliability_outbox_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_reliability_outbox_attempt"),
        UniqueConstraint("topic", "dedupe_key", name="uq_reliability_outbox_dedupe"),
        Index("ix_rel_outbox_topic_status_avail", "topic", "status", "available_at"),
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReliabilityInbox(Base):
    """消费侧去重：owner_service + dedupe_key 唯一。

    - 重复投递（Streams 重投 / dispatcher 重发）→ 命中即跳过（单副作用）；
    - 同一 dedupe_key 不同 payload_hash → 视为冲突并显式报错（不静默吞）。
    """

    __tablename__ = "reliability_inbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROCESSED','FAILED')", name="ck_reliability_inbox_status"
        ),
        UniqueConstraint(
            "owner_service", "dedupe_key", name="uq_reliability_inbox_dedupe"
        ),
        Index("ix_rel_inbox_owner_created", "owner_service", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    owner_service: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PROCESSED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReliabilityIdempotency(Base):
    """REST 幂等（formal）：五元键唯一 + request_hash。

    - 同 key + 同 request_hash → 窗口内重放首结果（200/首响应）；
    - 同 key + 不同 request_hash → 409 冲突（绝不静默重放错响应）；
    - principal（=用户维度）保证跨用户不串键。
    """

    __tablename__ = "reliability_idempotency"
    __table_args__ = (
        CheckConstraint(
            "status IN ('IN_PROGRESS','COMPLETED')", name="ck_reliability_idem_status"
        ),
        UniqueConstraint(
            "owner_service", "operation", "principal", "idempotency_key",
            name="uq_reliability_idem_key",
        ),
        Index("ix_rel_idem_owner_expires", "owner_service", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    owner_service: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    principal: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="IN_PROGRESS")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
