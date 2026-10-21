"""m2_019: submission item multi-image attachments support.

Adds submission_items.attachments JSONB so a student can attach multiple
photo/file answers per item (image homework upload). Pure additive change with
server_default '[]'; existing rows remain valid.

Revision ID: m2_019_submission_attachments
Revises: auth_001_unified_identity
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m2_019_submission_attachments"
down_revision = "m3_003_grading_v2_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "submission_items",
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("submission_items", "attachments")