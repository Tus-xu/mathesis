"""迭代05：error_records 增加 review_count（间隔复习 1/3/7/15 推进依据，SSOT §6.3）

Revision ID: m2_004_error_records_review
Revises: m2_003_integration_configs
Create Date: 2026-08-04
"""

import sqlalchemy as sa

from alembic import op

revision = "m2_004_error_records_review"
down_revision = "m2_003_integration_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "error_records",
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("error_records", "review_count")
