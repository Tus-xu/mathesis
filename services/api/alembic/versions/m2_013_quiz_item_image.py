"""M2 迭代18：quiz_items 增加 image 列（P2-5 图片管道）

Revision ID: m2_013_quiz_item_image
Revises: m2_012_ai_quality_scores
Create Date: 2026-08-14

变更（P2-5 题目配图试点，AI 重绘 SVG 路线）：
- quiz_items 追加 image JSONB NOT NULL DEFAULT '[]'（对齐 question_bank.image，
  供 practice/start 与 exam 成卷题目透传配图）
"""

from alembic import op

revision = "m2_013_quiz_item_image"
down_revision = "m2_012_ai_quality_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE quiz_items ADD COLUMN IF NOT EXISTS image JSONB NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    op.execute("ALTER TABLE quiz_items DROP COLUMN IF EXISTS image")
