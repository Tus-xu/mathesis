"""M2 迁移：tutor_sessions（引导式解题会话）

Revision ID: m2_002_tutor_sessions
Revises: m2_001_files_coursework
Create Date: 2026-07-31

新增 1 张表：
- tutor_sessions（ADR-030/033 solver-then-guide 状态机持久化）
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "m2_002_tutor_sessions"
down_revision = "m2_001_files_coursework"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tutor_sessions",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="llm"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("plan", JSONB, nullable=False, server_default="{}"),
        sa.Column("current_step", sa.Integer, nullable=False, server_default="1"),
        sa.Column("hint_level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempts_on_step", sa.Integer, nullable=False, server_default="0"),
        sa.Column("hint_counts", JSONB, nullable=False, server_default="{}"),
        sa.Column("answer_requests", sa.Integer, nullable=False, server_default="0"),
        sa.Column("awaiting_attempt", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_tutor_sessions_conv_active",
        "tutor_sessions",
        ["conversation_id"],
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )
    op.create_index("idx_tutor_sessions_user", "tutor_sessions", ["user_id", "updated_at"])


def downgrade() -> None:
    op.drop_table("tutor_sessions")
