"""RV02 · Research Core 基线：session / event / outbox / idempotency / pending action / summary / revocation floor

Revision ID: 0001_rv02
Revises: None
Create Date: 2026-09-12

设计约束：
- PostgreSQL 是 snapshot / revision / event / proposal / pending action 的**事实权威**；
- outbox 与 domain event 同事务写入（应用层保证，表结构一一对应）；
- 单链单 head（`0001_rv02` 为第一条）。
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

revision: str = "0001_rv02"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "research_session",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False, server_default="MODELING"),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_artifact_refs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("summary_ref", sa.String(256), nullable=False),
        sa.Column("policy_refs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("confirmation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_event_id", sa.String(128), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_rs_tenant", "research_session", ["tenant_id"], schema=SCHEMA)
    op.create_index("ix_rs_project", "research_session", ["project_id"], schema=SCHEMA)
    op.create_index("ix_rs_owner", "research_session", ["owner_id"], schema=SCHEMA)

    op.create_table(
        "domain_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(128), nullable=False, unique=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("aggregate_type", sa.String(32), nullable=False, server_default="session"),
        schema=SCHEMA,
    )
    op.create_index("ix_de_session", "domain_event", ["session_id"], schema=SCHEMA)
    op.create_index("ix_de_occurred", "domain_event", ["occurred_at"], schema=SCHEMA)
    op.create_index("ix_de_trace", "domain_event", ["trace_id"], schema=SCHEMA)

    op.create_table(
        "outbox_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(128), nullable=False, unique=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_ob_session", "outbox_event", ["session_id"], schema=SCHEMA)
    op.create_index("ix_ob_status", "outbox_event", ["status"], schema=SCHEMA)

    op.create_table(
        "command_record",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("client_request_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("command_type", sa.String(64), nullable=False),
        sa.Column("idempotency_fingerprint", sa.String(64), nullable=False),
        sa.Column("session_id", UUID, nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "actor_id", "client_request_id", name="uq_command_record_idem"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "pending_action",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "required_capability",
            sa.String(128),
            nullable=False,
            server_default="research.session.write",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", UUID, nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_pa_session", "pending_action", ["session_id"], schema=SCHEMA)

    op.create_table(
        "pending_action_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pending_action_id", UUID, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        schema=SCHEMA,
    )
    op.create_index("ix_paa_pa", "pending_action_audit", ["pending_action_id"], schema=SCHEMA)

    op.create_table(
        "session_summary",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("summary_version", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("generated_by", sa.String(64), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "summary_version", name="uq_session_summary_version"),
        schema=SCHEMA,
    )
    op.create_index("ix_ss_session", "session_summary", ["session_id"], schema=SCHEMA)

    op.create_table(
        "actor_security_floor",
        sa.Column("actor_id", UUID, primary_key=True),
        sa.Column("min_security_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for t in (
        "actor_security_floor",
        "session_summary",
        "pending_action_audit",
        "pending_action",
        "command_record",
        "outbox_event",
        "domain_event",
        "research_session",
    ):
        op.drop_table(t, schema=SCHEMA)
