"""M3 fullstack closure: daily question compatibility and durable classroom mode.

Revision ID: m3_002_fullstack_closure
Revises: m3_001_teacher_core
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m3_002_fullstack_closure"
down_revision = "m3_001_teacher_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The ORM has used per-user daily questions for several iterations while the
    # original migration still created one global row per date. Attribute legacy
    # rows to the owning quiz user; rows with no valid owner cannot be represented
    # truthfully and are removed rather than assigned to an arbitrary student.
    op.execute("ALTER TABLE daily_questions ADD COLUMN IF NOT EXISTS user_id UUID")
    op.execute(
        "UPDATE daily_questions d SET user_id = q.user_id "
        "FROM quizzes q WHERE d.quiz_id = q.id AND d.user_id IS NULL"
    )
    op.execute("DELETE FROM daily_questions WHERE user_id IS NULL")
    op.execute("ALTER TABLE daily_questions ALTER COLUMN user_id SET NOT NULL")
    op.execute("ALTER TABLE daily_questions DROP CONSTRAINT IF EXISTS daily_questions_date_key")
    op.execute("DROP INDEX IF EXISTS ix_daily_questions_user_id")
    op.create_index("ix_daily_questions_user_id", "daily_questions", ["user_id"])
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_daily_questions_user_id') THEN "
        "ALTER TABLE daily_questions ADD CONSTRAINT fk_daily_questions_user_id "
        "FOREIGN KEY (user_id) REFERENCES users(id); END IF; END $$"
    )
    op.create_unique_constraint(
        "uq_daily_questions_user_date", "daily_questions", ["user_id", "date"]
    )

    op.create_table(
        "classroom_modes",
        sa.Column(
            "class_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("classes.id"),
            primary_key=True,
        ),
        sa.Column(
            "teacher_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_classroom_modes_teacher_id", "classroom_modes", ["teacher_id"])


def downgrade() -> None:
    op.drop_index("ix_classroom_modes_teacher_id", table_name="classroom_modes")
    op.drop_table("classroom_modes")
    op.drop_constraint("uq_daily_questions_user_date", "daily_questions", type_="unique")
    op.drop_index("ix_daily_questions_user_id", table_name="daily_questions")
    op.drop_constraint("fk_daily_questions_user_id", "daily_questions", type_="foreignkey")
    op.drop_column("daily_questions", "user_id")
    op.create_unique_constraint("daily_questions_date_key", "daily_questions", ["date"])
