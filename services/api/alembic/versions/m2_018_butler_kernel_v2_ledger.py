"""butler kernel v2 ledger tables (agent_runs / agent_steps / tool_invocations)

Revision ID: m2_018_butler_kernel_v2_ledger
Revises: m2_017_error_records_perf_index
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m2_018_butler_kernel_v2_ledger"
down_revision = "m2_017_error_records_perf_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("scene", sa.String(64), nullable=False),
        sa.Column("client_request_id", sa.String(128), nullable=False),
        sa.Column("intent", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("model_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "user_id", "client_request_id", name="uq_agent_runs_user_client_req"
        ),
    )
    op.create_index(
        "ix_agent_runs_user_created", "agent_runs", ["user_id", "created_at"]
    )

    op.create_table(
        "agent_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_steps_run_sequence"),
    )
    op.create_index("ix_agent_steps_run", "agent_steps", ["run_id"])

    op.create_table(
        "tool_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False
        ),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("arguments_digest", sa.String(64), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tool_invocations_run", "tool_invocations", ["run_id"])
    op.create_index("ix_tool_invocations_tool_name", "tool_invocations", ["tool_name"])
    op.create_index(
        "ix_tool_invocations_idempotency_key", "tool_invocations", ["idempotency_key"]
    )


def downgrade() -> None:
    op.drop_table("tool_invocations")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
