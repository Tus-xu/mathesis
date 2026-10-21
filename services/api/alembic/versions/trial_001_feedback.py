"""trial_001: 体验测试留痕——trial_feedback 表（作品验收：试用反馈+身份标注）。

Revision ID: trial_001_feedback
Revises: research_002_research_chat
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "trial_001_feedback"
down_revision = "research_002_research_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trial_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("identity_label", sa.String(128), nullable=False, server_default=""),
        sa.Column("school", sa.String(128), nullable=False, server_default=""),
        sa.Column("grade", sa.String(32), nullable=False, server_default=""),
        sa.Column("ratings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("nps", sa.Integer, nullable=True),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("allow_showcase", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_trial_feedback_user_time",
        "trial_feedback",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_trial_feedback_user_time", table_name="trial_feedback")
    op.drop_table("trial_feedback")
