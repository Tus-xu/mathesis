"""M3 teacher core (teaching_artifacts / actionable_insights / teacher_actions / teacher_tasks + assignments & submission_items extensions)

Revision ID: m3_001_teacher_core
Revises: m2_018_butler_kernel_v2_ledger
Create Date: 2026-08-20
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m3_001_teacher_core"
down_revision = "m2_018_butler_kernel_v2_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- teaching_artifacts ----------
    op.create_table(
        "teaching_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("logical_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(40), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("classes.id"), nullable=True),
        sa.Column("scene", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("validation", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("engine", sa.String(20), nullable=False, server_default="local"),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("parent_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("logical_id", "version", name="uq_teaching_artifact_logical_version"),
    )
    op.create_index("ix_teaching_artifacts_owner", "teaching_artifacts", ["owner_id"])
    op.create_index("ix_teaching_artifacts_class", "teaching_artifacts", ["class_id"])
    op.create_index("ix_teaching_artifacts_logical_id", "teaching_artifacts", ["logical_id"])

    # ---------- actionable_insights ----------
    op.create_table(
        "actionable_insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("recommended_actions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(40), nullable=False, server_default="local_aggregation"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now()),
    )
    op.create_index("ix_actionable_insights_class", "actionable_insights", ["class_id"])
    op.create_index("ix_actionable_insights_kind_status", "actionable_insights", ["kind", "status"])

    # ---------- teacher_actions ----------
    op.create_table(
        "teacher_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("classes.id"), nullable=True),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", sa.String(40), nullable=False),
        sa.Column("client_request_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("before_digest", sa.String(64), nullable=True),
        sa.Column("after_digest", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now()),
    )
    op.create_index("uq_teacher_actions_idem", "teacher_actions", ["idempotency_key"], unique=True)
    op.create_index("ix_teacher_actions_teacher", "teacher_actions", ["teacher_id"])

    # ---------- teacher_tasks ----------
    op.create_table(
        "teacher_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("classes.id"), nullable=True),
        sa.Column("capability", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teaching_artifacts.id"), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now()),
    )
    op.create_index("ix_teacher_tasks_owner_status", "teacher_tasks", ["owner_id", "status"])

    # ---------- assignments 扩展（仅加列，不破坏 M2 存量） ----------
    op.add_column("assignments", sa.Column("client_assignment_id", sa.String(64), nullable=True))
    op.create_index("ix_assignments_client_id", "assignments", ["client_assignment_id"])
    op.add_column(
        "assignments",
        sa.Column("source_artifact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teaching_artifacts.id"), nullable=True),
    )

    # ---------- submission_items 扩展（AI 建议分 vs 正式终评分，保留旧 score 语义） ----------
    op.add_column("submission_items", sa.Column("suggested_score", sa.Numeric(), nullable=True))
    op.add_column("submission_items", sa.Column("suggestion_rationale", postgresql.JSONB(), nullable=True))
    op.add_column("submission_items", sa.Column("suggestion_feedback", sa.Text(), nullable=True))
    op.add_column("submission_items", sa.Column("suggestion_confidence", sa.Numeric(), nullable=True))
    op.add_column(
        "submission_items",
        sa.Column("suggestion_status", sa.String(20), nullable=False, server_default="draft"),
    )
    op.add_column(
        "submission_items",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("submission_items", sa.Column("teacher_final_score", sa.Numeric(), nullable=True))
    op.add_column("submission_items", sa.Column("teacher_feedback", sa.Text(), nullable=True))
    op.add_column("submission_items", sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("submission_items", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))

    # ---------- question_bank 模型/迁移漂移修复（模型已有但历史迁移缺失的列；
    # 全新库 alembic upgrade head 后必须存在，否则 ORM 写入与 supply_questions
    # 的 is_competition/out_of_syllabus 过滤在全新库上失败。IF NOT EXISTS 幂等，
    # 已有开发/测试库不受影响） ----------
    _qb_columns = [
        ("image", "JSONB", "'[]'"),
        ("is_competition", "BOOLEAN", "false"),
        ("out_of_syllabus", "BOOLEAN", "false"),
        ("source_batch", "VARCHAR(64)", None),
        ("scope", "VARCHAR(16)", "'student'"),
        ("kp_status", "VARCHAR(16)", None),
        ("kp_confidence", "VARCHAR(8)", None),
        ("kp_granular", "VARCHAR(16)", None),
        ("kp_source", "VARCHAR(32)", None),
        ("annotate_meta", "JSONB", None),
    ]
    for _name, _type, _default in _qb_columns:
        _dflt = f" DEFAULT {_default}" if _default else ""
        op.execute(f"ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS {_name} {_type}{_dflt}")


def downgrade() -> None:
    # question_bank 漂移列
    for _name, _type, _default in [
        ("annotate_meta", "JSONB", None),
        ("kp_source", "VARCHAR(32)", None),
        ("kp_granular", "VARCHAR(16)", None),
        ("kp_confidence", "VARCHAR(8)", None),
        ("kp_status", "VARCHAR(16)", None),
        ("scope", "VARCHAR(16)", None),
        ("source_batch", "VARCHAR(64)", None),
        ("out_of_syllabus", "BOOLEAN", None),
        ("is_competition", "BOOLEAN", None),
        ("image", "JSONB", None),
    ]:
        op.execute(f"ALTER TABLE question_bank DROP COLUMN IF EXISTS {_name}")
    # submission_items
    op.drop_column("submission_items", "confirmed_at")
    op.drop_column("submission_items", "confirmed_by")
    op.drop_column("submission_items", "teacher_feedback")
    op.drop_column("submission_items", "teacher_final_score")
    op.drop_column("submission_items", "needs_review")
    op.drop_column("submission_items", "suggestion_status")
    op.drop_column("submission_items", "suggestion_confidence")
    op.drop_column("submission_items", "suggestion_feedback")
    op.drop_column("submission_items", "suggestion_rationale")
    op.drop_column("submission_items", "suggested_score")
    # assignments
    op.drop_column("assignments", "source_artifact_id")
    op.drop_index("ix_assignments_client_id", table_name="assignments")
    op.drop_column("assignments", "client_assignment_id")
    # teacher_tasks
    op.drop_index("ix_teacher_tasks_owner_status", table_name="teacher_tasks")
    op.drop_table("teacher_tasks")
    # teacher_actions
    op.drop_index("ix_teacher_actions_teacher", table_name="teacher_actions")
    op.drop_index("uq_teacher_actions_idem", table_name="teacher_actions")
    op.drop_table("teacher_actions")
    # actionable_insights
    op.drop_index("ix_actionable_insights_kind_status", table_name="actionable_insights")
    op.drop_index("ix_actionable_insights_class", table_name="actionable_insights")
    op.drop_table("actionable_insights")
    # teaching_artifacts
    op.drop_index("ix_teaching_artifacts_logical_id", table_name="teaching_artifacts")
    op.drop_index("ix_teaching_artifacts_class", table_name="teaching_artifacts")
    op.drop_index("ix_teaching_artifacts_owner", table_name="teaching_artifacts")
    op.drop_table("teaching_artifacts")
