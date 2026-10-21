"""RV03 · durable job runtime：job_run / job_inbox / job_dlq

Revision ID: 0004_rv03_job_runtime
Revises: 0003_rv_cr6_msg
Create Date: 2026-09-13

设计铁律：
- **PostgreSQL 是事实权威**。Valkey（6380，前缀 `strm:rv8300`）只做投递与租约加速，
  丢 Valkey 不得丢真相 —— outbox relay 会从 `outbox_event` 重建未发布事件。
- `attempts` / `max_attempts` / `lease_expires_at` / `fencing_token` /
  `cancel_requested_at` 全部**持久化**，因为它们必须在进程重启后仍然生效。
- `job_inbox` 的唯一键 `(event_id, job_id, attempt)` 是幂等消费者的硬保证：
  重复投递在入库时被吸收，handler 不必自己实现幂等。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.config import db_schema  # noqa: E402

SCHEMA = db_schema()

revision: str = "0004_rv03_job_runtime"
down_revision: str | None = "0003_rv_cr6_msg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "job_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=True),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        # 内部状态：可能是 CLAIMED；对外投影必须经 public_status()。
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="rv03.job.v1"),
        sa.Column("input_ref", sa.String(512), nullable=False, server_default=""),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("partial_detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("stage", sa.String(64), nullable=False, server_default=""),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(128), nullable=False, server_default=""),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_request_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_jr_session", "job_run", ["session_id"], schema=SCHEMA)
    op.create_index("ix_jr_status", "job_run", ["status"], schema=SCHEMA)
    op.create_index("ix_jr_type_status", "job_run", ["job_type", "status"], schema=SCHEMA)
    # 背压/超时扫描按 (status, created_at) 走索引
    op.create_index("ix_jr_status_created", "job_run", ["status", "created_at"], schema=SCHEMA)

    op.create_table(
        "job_inbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(32), nullable=False, server_default="rv8300"),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("job_id", UUID, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumer", sa.String(128), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(32), nullable=False, server_default=""),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("event_id", "job_id", "attempt", name="uq_job_inbox_dedupe"),
        schema=SCHEMA,
    )
    op.create_index("ix_ji_job", "job_inbox", ["job_id"], schema=SCHEMA)

    op.create_table(
        "job_dlq",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("job_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("input_ref", sa.String(512), nullable=False, server_default=""),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("replayed_job_id", UUID, nullable=True),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index("ix_jd_job", "job_dlq", ["job_id"], schema=SCHEMA)
    op.create_index("ix_jd_session", "job_dlq", ["session_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_jd_session", "job_dlq", schema=SCHEMA)
    op.drop_index("ix_jd_job", "job_dlq", schema=SCHEMA)
    op.drop_table("job_dlq", schema=SCHEMA)

    op.drop_index("ix_ji_job", "job_inbox", schema=SCHEMA)
    op.drop_table("job_inbox", schema=SCHEMA)

    op.drop_index("ix_jr_status_created", "job_run", schema=SCHEMA)
    op.drop_index("ix_jr_type_status", "job_run", schema=SCHEMA)
    op.drop_index("ix_jr_status", "job_run", schema=SCHEMA)
    op.drop_index("ix_jr_session", "job_run", schema=SCHEMA)
    op.drop_table("job_run", schema=SCHEMA)
