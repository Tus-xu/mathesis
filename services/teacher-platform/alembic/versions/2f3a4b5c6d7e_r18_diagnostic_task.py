"""r18 pre-class diagnostic task tables

R18（学前诊断任务教师侧闭环）additive 迁移：在 8100 新增两张 `tp_` 前缀表
（tp_diagnostic_tasks / tp_diagnostic_revisions）。

设计要点（DECISIONS.md §5/§6）：
- 诊断草稿/发布事实在 8100（tp_diagnostic_tasks），8000 assignments.id 经
  provision 列承载映射（不复制学生任务事实表）；
- 内容不可变版本快照（tp_diagnostic_revisions）与 BlueprintRevision 同构；
- 时长 5–10 分钟硬约束 DB CHECK（防“预习变大量作业”漂移）；
- 状态机 CHECK：proposal/draft/published/skipped（终态 published/skipped）。
- 回滚：downgrade 逆序删两表，可完整回滚（不触碰任何接管/旧表）。

幂等：alembic 版本表保证重复 upgrade no-op。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "2f3a4b5c6d7e"
down_revision = "1e2f3a4b5c6d"
branch_labels = None
depends_on = None


def _jsonb() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # ── tp_diagnostic_tasks（诊断草稿 / 发布事实）────────────────────────────
    op.create_table(
        "tp_diagnostic_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposal"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("pending_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provision", _jsonb(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skipped_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skipped_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposal','draft','published','skipped')",
            name="ck_tp_diag_tasks_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_tp_diag_tasks_revision"),
        sa.CheckConstraint(
            "estimated_duration_minutes BETWEEN 5 AND 10",
            name="ck_tp_diag_tasks_duration",
        ),
    )
    op.create_index("ix_tp_diag_tasks_session", "tp_diagnostic_tasks", ["session_id"])
    op.create_index("ix_tp_diag_tasks_blueprint", "tp_diagnostic_tasks", ["blueprint_id"])

    # ── tp_diagnostic_revisions（内容不可变版本快照）──────────────────────────
    op.create_table(
        "tp_diagnostic_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diagnostic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_diagnostic_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False, server_default="proposal"),
        sa.Column("source", sa.Text(), nullable=False, server_default="ai_suggestion"),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("parent_revision_no", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision_no >= 1", name="ck_tp_diag_revisions_no"),
        sa.CheckConstraint(
            "verdict IN ('proposal','draft','published','skipped')",
            name="ck_tp_diag_revisions_verdict",
        ),
        sa.UniqueConstraint("diagnostic_id", "revision_no", name="uq_tp_diag_revisions_diag_no"),
    )
    op.create_index("ix_tp_diag_revisions_diag", "tp_diagnostic_revisions", ["diagnostic_id"])


def downgrade() -> None:
    op.drop_index("ix_tp_diag_revisions_diag", table_name="tp_diagnostic_revisions")
    op.drop_table("tp_diagnostic_revisions")
    op.drop_index("ix_tp_diag_tasks_blueprint", table_name="tp_diagnostic_tasks")
    op.drop_index("ix_tp_diag_tasks_session", table_name="tp_diagnostic_tasks")
    op.drop_table("tp_diagnostic_tasks")
