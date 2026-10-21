"""om6: classroom_sessions 分层练习（效果图右栏「分层练习」契约）

课堂会话新增：
- practice: {basic:[{question,options,answer,analysis}], advanced:[...], challenge:[...]}
  生成完成后一次性写入（LLM 失败则保持 NULL，前端隐藏练习卡，不阻塞课堂）。
- practice_stats: {basic:{total,correct}, advanced:{...}, challenge:{...}}
  学生作答即时累加，驱动正确率环形图。

Revision ID: om6_classroom_practice
Revises: om5_error_record_origin
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "om6_classroom_practice"
down_revision = "om5_error_record_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("classroom_sessions", sa.Column("practice", JSONB, nullable=True))
    op.add_column(
        "classroom_sessions",
        sa.Column("practice_stats", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("classroom_sessions", "practice_stats")
    op.drop_column("classroom_sessions", "practice")
