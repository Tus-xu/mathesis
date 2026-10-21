"""Add explicit question score-point fields for Grading V2.

Existing questions stay nullable: a missing full mark/rubric is a truthful
manual-review state, not permission to manufacture a generic score scheme.

Revision ID: m3_003_grading_v2_workspace
Revises: om2_openmaic_document
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m3_003_grading_v2_workspace"
down_revision = "om2_openmaic_document"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quiz_items", sa.Column("max_score", sa.Numeric(), nullable=True))
    op.add_column(
        "quiz_items",
        sa.Column("grading_rubric", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quiz_items", "grading_rubric")
    op.drop_column("quiz_items", "max_score")
