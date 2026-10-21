# coding: utf-8
"""trial_001: 体验测试留痕——tp_trial_feedback（试用教师结构化反馈 + 身份标注）。

Rollback（down/回滚说明，教师平台 AGENTS.md 数据库纪律）：
    downgrade() 删除本迁移创建的表与索引；不触碰任何既有表数据，
    回滚即完全移除本特性（feedback 数据不迁移，丢弃前应先导出报告）。

Revision ID: trial_001_teacher
Revises: c8d9e0f1a2b3
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "trial_001_teacher"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tp_trial_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("identity_label", sa.String(128), nullable=False, server_default=""),
        sa.Column("school", sa.String(128), nullable=False, server_default=""),
        sa.Column("ratings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("nps", sa.Integer, nullable=True),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("allow_showcase", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_tp_trial_feedback_teacher",
        "tp_trial_feedback",
        ["teacher_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_tp_trial_feedback_teacher", table_name="tp_trial_feedback")
    op.drop_table("tp_trial_feedback")
