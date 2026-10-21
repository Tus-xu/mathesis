"""om8: error_records 正解与正解示意图持久化缓存

错题详情每次 GET /api/butler/error-detail 都实时调模型重生成正解（刷新即重跑、
页面"AI 生成中"长转圈的根源）。新增三列实现"首次生成、永久缓存"：

- generated_answer：已核验的 Markdown/LaTeX 正解文本，可空；
- solution_figure：正解示意图快照（与 image 同一规范契约 image/ggb），默认 []；
- solution_generated_at：首次成功生成时间，仅审计用。

写回规则：正解生成成功即提交；图形 best-effort，失败只存空图，不回滚正解。

Revision ID: om8_error_record_solution_cache
Revises: om7_task_center
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "om8_error_record_solution_cache"
down_revision = "om7_task_center"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("error_records", sa.Column("generated_answer", sa.Text(), nullable=True))
    op.add_column(
        "error_records",
        sa.Column("solution_figure", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "error_records",
        sa.Column("solution_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("error_records", "solution_generated_at")
    op.drop_column("error_records", "solution_figure")
    op.drop_column("error_records", "generated_answer")
