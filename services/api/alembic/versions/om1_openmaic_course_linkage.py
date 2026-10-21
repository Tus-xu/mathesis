"""OpenMAIC dual-teacher classroom linkage for courses.

Add nullable `openmaic_stage_id` column to `courses` so a course can be bound
to an OpenMAIC interactive classroom (stage) id. Student /dual embeds the
OpenMAIC classroom via iframe using this id. Additive only; existing rows and
other columns untouched.

Revision ID: om1_openmaic_course_linkage
Revises: auth_002_role_selective_sms
Create Date: 2026-08-23
"""

from alembic import op

revision = "om1_openmaic_course_linkage"
down_revision = "auth_002_role_selective_sms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS openmaic_stage_id VARCHAR(128)")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_courses_openmaic_stage_id') THEN "
        "CREATE INDEX ix_courses_openmaic_stage_id ON courses (openmaic_stage_id); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_courses_openmaic_stage_id")
    op.execute("ALTER TABLE courses DROP COLUMN IF EXISTS openmaic_stage_id")
