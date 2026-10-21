"""om5: error_records.origin 来源细分（阶段2修复轮 D4/J3/阶段5 前置）

error_records.source_channel 只有 manual_photo/auto_judge/chat_command 三枚举，
无法区分 自测/对话出题/引导解题/模拟考/变式 —— 错题本来源透明与重练归因都卡在这。
新增可空列 origin（self_test/chat_quiz/socratic/mock_exam/variant/manual），
新写入路径逐步填；旧行保持 NULL 不回填（不伪造历史）。

Revision ID: om5_error_record_origin
Revises: om4_chunk_source_metadata
"""
from alembic import op
import sqlalchemy as sa

revision = "om5_error_record_origin"
down_revision = "om4_chunk_source_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("error_records", sa.Column("origin", sa.String(length=24), nullable=True))
    op.create_index(
        "idx_error_records_user_origin",
        "error_records",
        ["user_id", "origin"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_error_records_user_origin", table_name="error_records")
    op.drop_column("error_records", "origin")
