"""tel_001: 两端贯通——knowledge_docs.source_type 加宽（String(16) → String(32)）

背景（PLAN.md §3.1 / D7）：
- knowledge_docs.source_type 现为 String(16)，现存量 ADVANCED_REFERENCE /
  CURRICULUM_STANDARD / TRAINING_MATERIAL 等取值被有损拍扁（tools/corpus_integrity
  实测 divergent 10/30 docs、972 chunks width-blocked）；
- 本迁移单向加宽到 String(32)，与 ORM（app/models/knowledge_doc.py:16）同步加宽；
- down 为 no-op（单向放宽，不做有损收窄回退；如需回退请先人工确认无 >16 字符值）；
- 无新表、无新列（tel 方案明确禁止给 roster/learning-overview 建缓存表）。

Revision ID: tel_001_two_end_link
Revises: stu_002_learning_ledger_tables（当前唯一 head，写迁移前已重跑
`uv run alembic heads` 确认未漂移）
Create Date: 2026-10-12
"""

import sqlalchemy as sa

from alembic import op

revision = "tel_001_two_end_link"
down_revision = "stu_002_learning_ledger_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "knowledge_docs",
        "source_type",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 单向放宽：不做有损收窄（>16 字符值会被截断/拒绝）。回退请人工评估存量后手写。
    pass
