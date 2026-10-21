"""research_002: 科研端 AI 对话会话与消息（首页对话记忆持久化）。

Revision ID: research_002_research_chat
Revises: research_001_research_platform
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "research_002_research_chat"
down_revision = "research_001_research_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default="新对话"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "idx_research_chat_sessions_user", "research_chat_sessions", ["user_id", "updated_at"]
    )

    op.create_table(
        "research_chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_chat_sessions.id"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "idx_research_chat_messages_session", "research_chat_messages", ["session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_research_chat_messages_session", table_name="research_chat_messages")
    op.drop_table("research_chat_messages")
    op.drop_index("idx_research_chat_sessions_user", table_name="research_chat_sessions")
    op.drop_table("research_chat_sessions")