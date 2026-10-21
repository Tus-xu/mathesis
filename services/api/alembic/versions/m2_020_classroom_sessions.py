"""m2_020: AI 数学课堂会话（OpenMAIC 融合改造第一阶段数据落地）。

Revision ID: m2_020_classroom_sessions
Revises: m2_019_submission_attachments
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m2_020_classroom_sessions"
down_revision = "m2_019_submission_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classroom_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="sync"),
        sa.Column("slide_count", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="generating"
        ),
        sa.Column("outlines", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
        sa.Column("slides", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
        sa.Column("engine", sa.String(30), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_classroom_sessions_course_id", "classroom_sessions", ["course_id"])
    op.create_index("ix_classroom_sessions_user_id", "classroom_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_classroom_sessions_user_id", table_name="classroom_sessions")
    op.drop_index("ix_classroom_sessions_course_id", table_name="classroom_sessions")
    op.drop_table("classroom_sessions")