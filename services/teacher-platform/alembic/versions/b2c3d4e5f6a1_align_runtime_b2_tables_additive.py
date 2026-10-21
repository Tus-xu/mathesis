"""align runtime b2 tables additive

rescue 整合（2026-09-07）：B2 运行时自建表与域模型的漂移对齐——**仅增量列**。

quiz_questions 缺 stem_plain（IFC-002，B4 模型新增的 LaTeX 剥离冗余列，检索词面匹配用）。
不动的部分（登记于 RESCUE_STATE MOCK_DEBT/SCHEMA DEBT）：
- DB 侧多余的 stem_hash + uq_quiz_source_year_stemhash（B2 运行时建的幂等键）——模型未声明，
  保留不删（有真实数据依赖）；
- 类型宽度差异（TEXT vs String(n)）——无运行时影响，不盲改 785 行真实题目。

Revision ID: b2c3d4e5f6a1
Revises: 01921a4ef94b
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b2c3d4e5f6a1'
down_revision = '01921a4ef94b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('quiz_questions', sa.Column('stem_plain', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('quiz_questions', 'stem_plain')
