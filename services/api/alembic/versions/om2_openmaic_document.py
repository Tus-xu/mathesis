"""OpenMAIC classroom document SSOT for courses.

Add nullable JSONB `openmaic_document` column to `courses` so the teacher-bound
OpenMAIC classroom document ({stage, scenes}) is persisted in our backend as the
source of truth, then seeded to OpenMAIC's /api/classroom for iframe serving.

Revision ID: om2_openmaic_document
Revises: om1_openmaic_course_linkage
Create Date: 2026-08-23
"""

from alembic import op

revision = "om2_openmaic_document"
down_revision = "om1_openmaic_course_linkage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS openmaic_document JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE courses DROP COLUMN IF EXISTS openmaic_document")
