"""r14 teacher prep session/blueprint skeleton

R14（Teacher Prep 会话/蓝图骨架）additive 迁移：在 8100 建立
TeachingSession / TeachingBlueprint / BlueprintRevision / SessionCommand / SessionEvent
五张 `tp_` 前缀事实表（决策 DECISIONS.md §2：8100 拥有
TeachingSession / TextbookTeachingContext / TeachingBlueprint / …）。

设计要点：
- teacher/class/course/schedule 为稳定 UUID 引用（teacher/class 事实权威在 8000，
  8100 为投影/引用；course/schedule 后续业务轮建表，本轮仅引用不施 FK）；
- context_snapshot_id / prompt_release_id / ai_run_id / pending_action_id 指向
  运行时原语表（teacher_session_contexts / prompt_releases / ai_runs / pending_actions），
  这些表为 raw-SQL store（非 ORM），故引用列为 UUID 无 FK、按约定一致；
- 状态列带 CHECK 约束；revision ≥ 1；事件 seq UNIQUE(session_id, seq)；
- 幂等：命令行带 idempotency_key + request_hash，配合 reliability_idempotency 五元键表；
- 回滚：downgrade 逆序删五表，可完整回滚（不触碰任何接管/旧表）。

幂等：alembic 版本表保证重复 upgrade no-op。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "16a2b3c4d5e6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _jsonb() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # ── TeachingSession ─────────────────────────────────────────────────────
    op.create_table(
        "tp_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_event_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("intent", _jsonb(), nullable=True),
        sa.Column("context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ai_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pending_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_command_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','active','cancelled','archived')", name="ck_tp_sessions_status"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_tp_sessions_revision"),
        sa.CheckConstraint("last_event_seq >= 0", name="ck_tp_sessions_last_event_seq"),
    )
    op.create_index("ix_tp_sessions_teacher_status", "tp_sessions", ["teacher_id", "status"])
    op.create_index("ix_tp_sessions_class", "tp_sessions", ["class_id"])
    op.create_index("ix_tp_sessions_teacher_class", "tp_sessions", ["teacher_id", "class_id"])

    # ── TeachingBlueprint ───────────────────────────────────────────────────
    op.create_table(
        "tp_blueprints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposal"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposal','confirmed','rejected')", name="ck_tp_blueprints_status"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_tp_blueprints_revision"),
    )
    op.create_index("ix_tp_blueprints_session", "tp_blueprints", ["session_id"])

    # ── BlueprintRevision（不可变版本快照）──────────────────────────────────
    op.create_table(
        "tp_blueprint_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False, server_default="proposal"),
        sa.Column("source", sa.Text(), nullable=False, server_default="ai_suggestion"),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("parent_revision_no", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision_no >= 1", name="ck_tp_bp_revisions_no"),
        sa.CheckConstraint(
            "verdict IN ('proposal','confirmed','rejected')", name="ck_tp_bp_revisions_verdict"
        ),
        sa.UniqueConstraint("blueprint_id", "revision_no", name="uq_tp_bp_revisions_blueprint_no"),
    )
    op.create_index("ix_tp_bp_revisions_blueprint", "tp_blueprint_revisions", ["blueprint_id"])

    # ── SessionCommand（命令审计）───────────────────────────────────────────
    op.create_table(
        "tp_session_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_blueprints.id", ondelete="SET NULL"), nullable=True),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("result_status", sa.Text(), nullable=False, server_default="applied"),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", _jsonb(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "result_status IN ('applied','replayed','conflict','denied','invalid_transition','authority')",
            name="ck_tp_session_commands_result",
        ),
    )
    op.create_index("ix_tp_commands_session_created", "tp_session_commands", ["session_id", "created_at"])
    op.create_index("ix_tp_commands_idem", "tp_session_commands", ["idempotency_key"])

    # ── SessionEvent（durable 事件 + lineage 链）────────────────────────────
    op.create_table(
        "tp_session_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_session_commands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_blueprints.id", ondelete="SET NULL"), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", _jsonb(), nullable=False),
        sa.Column("parent_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("seq >= 1", name="ck_tp_session_events_seq"),
        sa.UniqueConstraint("session_id", "seq", name="uq_tp_session_events_session_seq"),
    )
    op.create_index("ix_tp_session_events_session", "tp_session_events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_tp_session_events_session", table_name="tp_session_events")
    op.drop_table("tp_session_events")
    op.drop_index("ix_tp_commands_idem", table_name="tp_session_commands")
    op.drop_index("ix_tp_commands_session_created", table_name="tp_session_commands")
    op.drop_table("tp_session_commands")
    op.drop_index("ix_tp_bp_revisions_blueprint", table_name="tp_blueprint_revisions")
    op.drop_table("tp_blueprint_revisions")
    op.drop_index("ix_tp_blueprints_session", table_name="tp_blueprints")
    op.drop_table("tp_blueprints")
    op.drop_index("ix_tp_sessions_teacher_class", table_name="tp_sessions")
    op.drop_index("ix_tp_sessions_class", table_name="tp_sessions")
    op.drop_index("ix_tp_sessions_teacher_status", table_name="tp_sessions")
    op.drop_table("tp_sessions")
