"""om3_001: 双师课堂重构·阶段一核心闭环字段。

给 classroom_sessions 增加历史闭环与内容质量守卫所需字段：
- 来源（source_type/source_ref）：主题/拍题/文件，原件留存与可重试
- 学习进度 progress（继续学习闭环）
- 服务端笔记 notes
- 问答摘要 qa_summary（含错因摘要）
- 数学验证结果 verification
- 知识点锚定 knowledge_points（历史筛选）
- 内容版本 content_version（只重生错误页的可追溯锚点）
- 软删除 deleted_at

全部为增量列，不破坏现有行；旧数据自动落到默认值。

Revision ID: om3_001_dual_teacher_stage
Revises: m2_020_classroom_sessions
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "om3_001_dual_teacher_stage"
down_revision = "m2_020_classroom_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "classroom_sessions",
        sa.Column("source_type", sa.String(20), nullable=False, server_default="topic"),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("source_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("qa_summary", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("verification", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("knowledge_points", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("content_version", sa.String(30), nullable=True),
    )
    op.add_column(
        "classroom_sessions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 历史筛选常用索引：来源 + 归属用户（复合）
    op.create_index(
        "ix_classroom_sessions_user_source",
        "classroom_sessions",
        ["user_id", "source_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_classroom_sessions_user_source", table_name="classroom_sessions")
    op.drop_column("classroom_sessions", "deleted_at")
    op.drop_column("classroom_sessions", "content_version")
    op.drop_column("classroom_sessions", "knowledge_points")
    op.drop_column("classroom_sessions", "verification")
    op.drop_column("classroom_sessions", "qa_summary")
    op.drop_column("classroom_sessions", "notes")
    op.drop_column("classroom_sessions", "progress")
    op.drop_column("classroom_sessions", "source_ref")
    op.drop_column("classroom_sessions", "source_type")