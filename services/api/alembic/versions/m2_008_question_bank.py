"""M2 迁移：question_bank 结构化题库 + quiz_items.source 真题来源列

Revision ID: m2_008_question_bank
Revises: m2_007_episodic_memories
Create Date: 2026-08-08

变更（刷题/出题"题库优先"重做）：
- 新建 question_bank（结构化题库，与 chunks 非结构化知识库并存）：
  stem/q_type/options(JSONB)/answer/analysis/difficulty/kp_codes(ARRAY)/source/year/
  is_real_exam/embedding(Vector(1024) 可空)/hash(唯一，规范化题干 sha256 去重) + 时间戳 + 软删
  · kp_codes GIN 索引支撑"数组重叠"检索；(q_type, difficulty) 活跃行部分索引
  · embedding best-effort 可空（服务不可用落 NULL 不阻塞入库）
- quiz_items 增加 source 可空列（题库题真题来源标注；AI 生成题为 NULL）

幂等保护：表/列已存在则跳过（与 m2_006 同手法）。
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "m2_008_question_bank"
down_revision = "m2_007_episodic_memories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "question_bank" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "question_bank",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("stem", sa.Text(), nullable=False),
            sa.Column("q_type", sa.String(20), nullable=False),
            sa.Column("options", JSONB, nullable=True),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("analysis", sa.Text(), nullable=True),
            sa.Column("difficulty", sa.String(10), nullable=False, server_default="medium"),
            sa.Column("kp_codes", ARRAY(sa.String(32)), nullable=False, server_default="{}"),
            sa.Column("source", sa.String(100), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("is_real_exam", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("embedding", Vector(1024), nullable=True),
            sa.Column("hash", sa.String(64), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("hash", name="uq_question_bank_hash"),
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_question_bank_kp_codes "
            "ON question_bank USING gin (kp_codes)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_question_bank_type_diff "
            "ON question_bank (q_type, difficulty) WHERE deleted_at IS NULL"
        )

    # quiz_items.source：题库题真题来源（AI 生成题为 NULL）
    qi_cols = [c["name"] for c in sa.inspect(bind).get_columns("quiz_items")]
    if "source" not in qi_cols:
        op.add_column("quiz_items", sa.Column("source", sa.String(100), nullable=True))


def downgrade() -> None:
    qi_cols = [c["name"] for c in sa.inspect(op.get_bind()).get_columns("quiz_items")]
    if "source" in qi_cols:
        op.drop_column("quiz_items", "source")
    op.execute("DROP TABLE IF EXISTS question_bank")
