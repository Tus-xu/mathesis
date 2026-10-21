"""M2 迁移：question_bank 新增 figure_params（参数化配图参数）

Revision ID: m2_015_figure_params
Revises: m2_014_error_record_image
Create Date: 2026-08-15

变更：
- question_bank 新增 figure_params JSONB 可空列：
  存储参数化配图渲染参数（见 app/services/figure_renderer.py 的 FIGURE_SCHEMA_DOC），
  渲染时由 figure_renderer.render_figure() 确定性生成 SVG，替代 AI 自由生成配图。
- image 字段语义不变（仍存 data:image/svg+xml;base64 URI），前端零改动。

幂等保护：列已存在则跳过。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "m2_015_figure_params"
down_revision = "m2_014_error_record_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("question_bank")]
    if "figure_params" not in cols:
        op.add_column("question_bank", sa.Column("figure_params", JSONB, nullable=True))
        op.execute(
            "COMMENT ON COLUMN question_bank.figure_params IS "
            "'参数化配图参数（figure_renderer 渲染，替代 AI 自由生成 SVG）'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("question_bank")]
    if "figure_params" in cols:
        op.drop_column("question_bank", "figure_params")
