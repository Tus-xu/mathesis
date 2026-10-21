"""迭代05 迁移：courses（F9 双师课堂课程表，SSOT §4.10 / ADR-034）

Revision ID: m2_004_courses
Revises: m2_004_error_records_review
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "m2_004_courses"
down_revision = "m2_004_error_records_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 并行开发期间表可能被手动预建，存在则跳过（幂等保护）
    bind = op.get_bind()
    if "courses" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "courses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("class_id", UUID(as_uuid=True), sa.ForeignKey("classes.id"), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("preprocess_result", JSONB, nullable=False, server_default="{}"),
        sa.Column("preprocess_engine", sa.String(30), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_courses_user_id", "courses", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_courses_user_id", table_name="courses")
    op.drop_table("courses")
