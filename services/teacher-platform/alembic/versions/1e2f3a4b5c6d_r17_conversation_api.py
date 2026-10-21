"""r17 conversation-first orchestration tables

R17（Conversation-first 编排 API）additive 迁移：在 8100 新增三张 `tp_` 前缀表
（tp_conversation_messages / tp_message_runs / tp_context_snapshots）。

设计要点（DECISIONS.md §5/§7）：
- 服务端状态机唯一权威：消息 / 意图 / 上下文 / run 全持久化；
  localStorage 只存 UI 偏好（本迁移不涉及前端）；
- 消息 append-only：seq 会话内全序（UNIQUE(session_id, seq) 兜底双写）；
- 消息组件引用（component_type/props）是 allowlisted 契约（catalog 层校验后落库）；
- run 状态机 CHECK：PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/BASIC_MODE；
- 上下文快照不可变（snapshot_hash 64 hex CHECK）。
- 回滚：downgrade 逆序删三表，可完整回滚（不触碰任何接管/旧表）。

幂等：alembic 版本表保证重复 upgrade no-op。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "1e2f3a4b5c6d"
down_revision = "16a2b3c4d5e6"
branch_labels = None
depends_on = None


def _jsonb() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # 说明（2026-09-20 全新库 bootstrap 修复）：`tp_message_runs.message_id`
    # 与 `tp_conversation_messages.run_id` 互为循环外键。原实现只对后者做了
    # 后置 ALTER，前者仍为内联 FK —— 在**全新空库**上 CREATE TABLE 会因被引用表
    # 尚未创建而失败（UndefinedTableError: tp_conversation_messages）。
    # 增量升级过的旧库不会暴露该问题，因此长期潜伏。此处按作者既有的
    # 「两表建齐后 ALTER 后置添加」模式修正，语义与约束完全等价。
    # ── tp_context_snapshots（不可变上下文快照）──────────────────────────────
    op.create_table(
        "tp_context_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot", _jsonb(), nullable=False),
        sa.Column("snapshot_hash", sa.Text(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(snapshot_hash) = 64", name="ck_tp_ctx_snapshots_hash"),
    )
    op.create_index("ix_tp_ctx_snapshots_session_created", "tp_context_snapshots", ["session_id", "created_at"])

    # ── tp_message_runs（编排 run 状态机）────────────────────────────────────
    op.create_table(
        "tp_message_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        # message_id 的外键为循环依赖（→tp_conversation_messages），
        # 与 run_id 一样改为两表建齐后 ALTER 后置添加（见文件末尾）。
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_error", sa.Text(), nullable=True),
        sa.Column("ai_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED','BASIC_MODE')",
            name="ck_tp_conv_runs_status",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_tp_conv_runs_attempt"),
    )
    op.create_index("ix_tp_conv_runs_session_created", "tp_message_runs", ["session_id", "created_at"])

    # ── tp_conversation_messages（durable 消息 / 卡片 / 意图快照）────────────
    # 注意：run_id 外键（→tp_message_runs）为循环依赖，两表建齐后 ALTER 后置添加。
    op.create_table(
        "tp_conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("component_type", sa.Text(), nullable=True),
        sa.Column("component_props", _jsonb(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("intent", _jsonb(), nullable=True),
        sa.Column("intent_hash", sa.Text(), nullable=True),
        sa.Column("context_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_context_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("blueprint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tp_blueprints.id", ondelete="SET NULL"), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ai_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="applied"),
        sa.Column("editable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_label", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("seq >= 1", name="ck_tp_conv_messages_seq"),
        sa.UniqueConstraint("session_id", "seq", name="uq_tp_conv_messages_session_seq"),
    )
    op.create_index("ix_tp_conv_messages_session_seq", "tp_conversation_messages", ["session_id", "seq"])
    op.create_index("ix_tp_conv_messages_session_created", "tp_conversation_messages", ["session_id", "created_at"])
    # 循环外键后置（PG 要求被引用表已存在）
    op.create_foreign_key(
        "fk_tp_conv_messages_run",
        "tp_conversation_messages",
        "tp_message_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_tp_message_runs_message",
        "tp_message_runs",
        "tp_conversation_messages",
        ["message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_tp_message_runs_message", "tp_message_runs", type_="foreignkey")
    op.drop_constraint("fk_tp_conv_messages_run", "tp_conversation_messages", type_="foreignkey")
    op.drop_index("ix_tp_conv_messages_session_created", table_name="tp_conversation_messages")
    op.drop_index("ix_tp_conv_messages_session_seq", table_name="tp_conversation_messages")
    op.drop_table("tp_conversation_messages")
    op.drop_index("ix_tp_conv_runs_session_created", table_name="tp_message_runs")
    op.drop_table("tp_message_runs")
    op.drop_index("ix_tp_ctx_snapshots_session_created", table_name="tp_context_snapshots")
    op.drop_table("tp_context_snapshots")
