"""M2 迭代16 第二批迁移：FSRS write-path 接入（wrong_count 列）

Revision ID: m2_011_fsrs_write_path
Revises: m2_010_growth_foundation
Create Date: 2026-08-13

变更（方案《M2_迭代16_前端融合版后端改造方案_v1.0》§5 第二批）：
- error_records 追加 wrong_count INTEGER NOT NULL DEFAULT 1
  （存量行默认 1，与 enrich_error_fsrs 保守口径一致；forgotten 复习时 +1）
"""

from alembic import op

revision = "m2_011_fsrs_write_path"
down_revision = "m2_010_growth_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE error_records ADD COLUMN IF NOT EXISTS wrong_count INTEGER NOT NULL DEFAULT 1"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE error_records DROP COLUMN IF EXISTS wrong_count")
