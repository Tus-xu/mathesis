"""r11.5-c reliability gates — 异步可靠性/幂等/追踪基础闸门正式表

DECISIONS §5（P0 可靠性闸门，人工裁决 2026-09-10）：

  事务化收口：业务状态 / Job / 事件 / outbox 处于同一 DB 事务（禁止把 PPT 长任务放进
  API BackgroundTasks）；worker 原子 claim/fencing；request 幂等五元键 + request_hash（同
  key 不同 payload → 409）；消费侧去重（重复投递 / ACK 前崩溃 / 重放兜底）；trace 贯穿。

表（reliability_* 前缀，独立于既有域表与 ppt_v2/textbook_v2 命名空间）：
  - reliability_jobs：作业状态机 PENDING/CLAIMED/COMPLETED/FAILED/CANCELLED/DLQ
  - reliability_job_events：作业内 durable 事件（UNIQUE(job_id, seq)）
  - reliability_outbox：事务性发件箱（PENDING→PUBLISHED/DLQ；dedupe_key 唯一）
  - reliability_inbox：消费侧去重（UNIQUE(owner_service, dedupe_key)）
  - reliability_idempotency：REST 幂等五元键（owner_service/operation/principal/
    idempotency_key）+ request_hash
增量：ai_runs 补 trace_id（无损增量列，与 R11.5-B 审计列同风格）。

回滚边界（downgrade）：仅 drop 本轮 5 张新增表 + ai_runs.trace_id 列；不动任何
既有表/数据（含 R11.5-B 接管的运行时表）。

Revision ID: e26a6b7c8d9e
Revises: d17b2c3e4f5a
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e26a6b7c8d9e"
down_revision = "d17b2c3e4f5a"
branch_labels = None
depends_on = None


def _jsonb() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def _big_pk() -> sa.types.TypeEngine:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _column_exists(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _create_reliability_jobs() -> None:
    op.create_table(
        "reliability_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_service", sa.Text(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("payload", _jsonb(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("claim_token", sa.Text(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_s", sa.Integer(), server_default=sa.text("600"), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", _jsonb(), nullable=True),
        sa.Column("parent_job_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','CLAIMED','COMPLETED','FAILED','CANCELLED','DLQ')",
            name="ck_reliability_jobs_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_reliability_jobs_attempt"),
        sa.CheckConstraint("revision >= 1", name="ck_reliability_jobs_revision"),
        sa.ForeignKeyConstraint(["parent_job_id"], ["reliability_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_service", "job_type", "dedupe_key", name="uq_reliability_jobs_dedupe"),
    )
    op.create_index(
        "ix_rel_jobs_owner_status_avail", "reliability_jobs", ["owner_service", "status", "available_at"]
    )
    op.create_index("ix_rel_jobs_claim_expires", "reliability_jobs", ["claim_expires_at"])


def _create_reliability_job_events() -> None:
    op.create_table(
        "reliability_job_events",
        sa.Column("id", _big_pk(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", _jsonb(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["reliability_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "seq", name="uq_rel_job_events_job_seq"),
        sa.CheckConstraint("seq > 0", name="ck_rel_job_events_seq"),
    )
    op.create_index("ix_rel_job_events_job_id", "reliability_job_events", ["job_id"])


def _create_reliability_outbox() -> None:
    op.create_table(
        "reliability_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("payload", _jsonb(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','PUBLISHED','FAILED','DLQ')",
            name="ck_reliability_outbox_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_reliability_outbox_attempt"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic", "dedupe_key", name="uq_reliability_outbox_dedupe"),
    )
    op.create_index(
        "ix_rel_outbox_topic_status_avail", "reliability_outbox", ["topic", "status", "available_at"]
    )


def _create_reliability_inbox() -> None:
    op.create_table(
        "reliability_inbox",
        sa.Column("id", _big_pk(), autoincrement=True, nullable=False),
        sa.Column("owner_service", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'PROCESSED'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('PROCESSED','FAILED')", name="ck_reliability_inbox_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_service", "dedupe_key", name="uq_reliability_inbox_dedupe"),
    )
    op.create_index("ix_rel_inbox_owner_created", "reliability_inbox", ["owner_service", "created_at"])


def _create_reliability_idempotency() -> None:
    op.create_table(
        "reliability_idempotency",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_service", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("principal", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response", _jsonb(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'IN_PROGRESS'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('IN_PROGRESS','COMPLETED')", name="ck_reliability_idem_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_service", "operation", "principal", "idempotency_key",
            name="uq_reliability_idem_key",
        ),
    )
    op.create_index("ix_rel_idem_owner_expires", "reliability_idempotency", ["owner_service", "expires_at"])


def upgrade() -> None:
    # 全新增表：显式建（不存在才建，幂等）
    if not _table_exists("reliability_jobs"):
        _create_reliability_jobs()
    if not _table_exists("reliability_job_events"):
        _create_reliability_job_events()
    if not _table_exists("reliability_outbox"):
        _create_reliability_outbox()
    if not _table_exists("reliability_inbox"):
        _create_reliability_inbox()
    if not _table_exists("reliability_idempotency"):
        _create_reliability_idempotency()

    # 增量：ai_runs 补 trace_id（无损）
    if _table_exists("ai_runs") and not _column_exists("ai_runs", "trace_id"):
        op.add_column("ai_runs", sa.Column("trace_id", sa.Text(), nullable=True))


def downgrade() -> None:
    """回滚边界：仅 drop 本轮新增表 + ai_runs.trace_id；不触碰任何既有表/数据。"""
    for table in (
        "reliability_idempotency",
        "reliability_inbox",
        "reliability_outbox",
        "reliability_job_events",
        "reliability_jobs",
    ):
        if _table_exists(table):
            op.drop_table(table)
    if _table_exists("ai_runs") and _column_exists("ai_runs", "trace_id"):
        op.drop_column("ai_runs", "trace_id")
