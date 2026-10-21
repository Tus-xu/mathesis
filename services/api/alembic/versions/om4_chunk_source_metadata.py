"""Preserve source metadata on knowledge chunks.

Classroom textbook association needs the exact book, volume and section for
each retrieved chunk.  A document-level title alone is not a traceable
student-facing citation.

Revision ID: om4_chunk_source_metadata
Revises: om3_001_dual_teacher_stage
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "om4_chunk_source_metadata"
down_revision = "om3_001_dual_teacher_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chunks", "meta")
