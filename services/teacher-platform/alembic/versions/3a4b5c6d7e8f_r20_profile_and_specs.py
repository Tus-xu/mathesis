"""r20 versioned class learning profile and dual spec projection tables

R20（学情证据→Blueprint 修订→教师确认→双 Spec 投影）additive 迁移：
在 8100 新增四张表：

1. `tp_class_learning_profiles`：版本化 ClassLearningProfile 事实（8100 权威）。
   - `revision` 乐观并发 + `body` 内嵌 revision 双写（与 tp_blueprints 同构）；
   - 时效列：`computed_at`（聚合计算时间）、`expires_at`（时效窗口终点，
     NULL=无上限）；`evidence_window`（证据时间范围，可为 NULL）；
   - `coverage`（evidence 覆盖率快照）与 `warnings`（低覆盖/旧 Profile/证据冲突
     warning 码）确定性判定，随版本不可变；
   - 状态机：collecting → preview → published → archived（published 才可作为
     evidence 进入修订提案）。

2. `tp_class_learning_profile_revisions`：Profile 内容不可变版本快照
   （与 BlueprintRevisionRow 同构；UNIQUE(profile_id, revision_no)）。

3. `tp_lesson_plan_specs`：LessonPlanSpec 事实（8100 权威，DECISIONS §2）。
   - 同一 approved blueprint revision + 不可变 context snapshot 投影而来；
   - `approved_blueprint_revision` 与 `context_snapshot_hash` 显式存储，
     「双 Spec 的 approved blueprint/context hash 一致」验收锚点；
   - `lineage`：prompt/model/run 血缘（prompt_release_id/ai_run_id/model/run_hash）。

4. `tp_spec_projections`：双 Spec 投影审计/幂等日志（kind=lesson_plan|deck）。
   - `projection_hash` = 投影输入规范化 hash（同输入可重试/幂等）；
   - idempotency_key/request_hash 承接 reliability_idempotency 五元键；
   - 不存正文，只存锚点（spec_target_id + 双 hash + lineage）。

设计要点（DECISIONS.md §5/§6/§3）：
- 教材引用零 `page_refs/pNN`：投影只写 SourceLocator（chunk_refs/locator），
  DB 层不新增任何页码列；
- 回滚：downgrade 逆序删四表，可完整回滚（不触碰任何接管/旧表）。

幂等：alembic 版本表保证重复 upgrade no-op。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "3a4b5c6d7e8f"
down_revision = "2f3a4b5c6d7e"
branch_labels = None
depends_on = None

PROFILE_STATUSES = "('collecting','preview','published','archived')"


def _jsonb() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # ── tp_class_learning_profiles（版本化班级学习画像）────────────────────────
    op.create_table(
        "tp_class_learning_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="collecting"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_window", _jsonb(), nullable=True),
        sa.Column("coverage", _jsonb(), nullable=True),
        sa.Column("warnings", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            f"status IN {PROFILE_STATUSES}",
            name="ck_tp_profiles_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_tp_profiles_revision"),
    )
    op.create_index("ix_tp_profiles_class", "tp_class_learning_profiles", ["class_id"])
    op.create_index("ix_tp_profiles_class_status", "tp_class_learning_profiles", ["class_id", "status"])

    # ── tp_class_learning_profile_revisions（内容不可变版本快照）───────────────
    op.create_table(
        "tp_class_learning_profile_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_class_learning_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False, server_default="collecting"),
        sa.Column("source", sa.Text(), nullable=False, server_default="system"),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("parent_revision_no", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision_no >= 1", name="ck_tp_profile_revisions_no"),
        sa.CheckConstraint(
            f"verdict IN {PROFILE_STATUSES}",
            name="ck_tp_profile_revisions_verdict",
        ),
        sa.UniqueConstraint("profile_id", "revision_no", name="uq_tp_profile_revisions_profile_no"),
    )
    op.create_index("ix_tp_profile_revisions_profile", "tp_class_learning_profile_revisions", ["profile_id"])

    # ── tp_lesson_plan_specs（LessonPlanSpec 事实）────────────────────────────
    op.create_table(
        "tp_lesson_plan_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default="lesson_plan"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("approved_blueprint_revision", sa.Integer(), nullable=False),
        sa.Column("context_snapshot_hash", sa.Text(), nullable=False),
        sa.Column("lineage", _jsonb(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_tp_lessonplan_revision"),
        sa.CheckConstraint(
            "purpose IN ('lesson_plan')",
            name="ck_tp_lessonplan_purpose",
        ),
        sa.UniqueConstraint("blueprint_id", "revision", name="uq_tp_lessonplan_blueprint_rev"),
    )
    op.create_index("ix_tp_lessonplan_blueprint", "tp_lesson_plan_specs", ["blueprint_id"])

    # ── tp_spec_projections（双 Spec 投影审计/幂等日志）────────────────────────
    op.create_table(
        "tp_spec_projections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("spec_target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("projection_hash", sa.Text(), nullable=False),
        sa.Column("approved_blueprint_revision", sa.Integer(), nullable=False),
        sa.Column("context_snapshot_hash", sa.Text(), nullable=False),
        sa.Column("lineage", _jsonb(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('lesson_plan','deck')",
            name="ck_tp_spec_proj_kind",
        ),
        sa.UniqueConstraint(
            "blueprint_id", "kind", "projection_hash",
            name="uq_tp_spec_proj_bp_kind_hash",
        ),
    )
    op.create_index("ix_tp_spec_proj_session", "tp_spec_projections", ["session_id"])
    op.create_index("ix_tp_spec_proj_blueprint", "tp_spec_projections", ["blueprint_id"])


def downgrade() -> None:
    op.drop_index("ix_tp_spec_proj_blueprint", table_name="tp_spec_projections")
    op.drop_index("ix_tp_spec_proj_session", table_name="tp_spec_projections")
    op.drop_table("tp_spec_projections")
    op.drop_index("ix_tp_lessonplan_blueprint", table_name="tp_lesson_plan_specs")
    op.drop_table("tp_lesson_plan_specs")
    op.drop_index("ix_tp_profile_revisions_profile", table_name="tp_class_learning_profile_revisions")
    op.drop_table("tp_class_learning_profile_revisions")
    op.drop_index("ix_tp_profiles_class_status", table_name="tp_class_learning_profiles")
    op.drop_index("ix_tp_profiles_class", table_name="tp_class_learning_profiles")
    op.drop_table("tp_class_learning_profiles")
