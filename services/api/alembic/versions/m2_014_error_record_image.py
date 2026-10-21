"""M2 迭代18：error_records 增加 image 列（P2-5 图片管道，错题本配图快照）

Revision ID: m2_014_error_record_image
Revises: m2_013_quiz_item_image
Create Date: 2026-08-14

变更（P2-5）：错题收录时快照题目配图，错题本详情可直接渲染
"""

from alembic import op

revision = "m2_014_error_record_image"
down_revision = "m2_013_quiz_item_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE error_records ADD COLUMN IF NOT EXISTS image JSONB NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    op.execute("ALTER TABLE error_records DROP COLUMN IF EXISTS image")
