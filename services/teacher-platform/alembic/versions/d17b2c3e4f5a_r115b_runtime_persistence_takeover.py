"""r11.5-b runtime persistence takeover — 正式接管运行时原语

DECISIONS §4/§5（人工裁决 2026-09-10）：

  接管目标（此前为运行时幂等自建 / 进程内内存，R11.5-B 起必须生产 fail-closed）：
  - teacher_session_contexts（ContextSnapshot）
  - teacher_memories / teacher_memory_history（TeacherMemory）
  - butler_messages（Message）
  - ai_runs（AIExecutionRun；本轮补 input/output hash + lineage 审计列）
  - conversation_threads / conversation_summaries（ConversationThread / Summary，新增）
  - prompt_releases（PromptRelease：approved release/hash/schema/toolset/model policy 绑定）
  - tool_call_records（ToolCallRecord：工具调用可审计台账）
  - pending_actions（PendingAction：取代进程内 _pending_actions，重启可查/确认/拒绝/过期）
  - LangGraph checkpoint 4 表（checkpoints / checkpoint_blobs / checkpoint_writes /
    checkpoint_migrations，langgraph-checkpoint-postgres==3.1.2 最终 schema 收编）

语义（重要）：
  1. 对「已存在运行时表」做 preflight（inspector.has_table）→ 存在则不动数据（无损），
     缺表才 CREATE；缺列才 ADD（ai_runs 增列、其余对齐现有表权限不变）。
  2. 幂等：upgrade 可重复运行（Alembic 版本表保证只跑一次；对本迁移内 preflight 分支亦安全）。
  3. 回滚边界：downgrade 仅 drop 本轮【新增】表（conversation_*/prompt_releases/tool_call_records/
     pending_actions）与 ai_runs 新增审计列；不得删除任何接管前已存在的运行时表及其数据
     （teacher_session_contexts/teacher_memories*/butler_messages/checkpoints* 保留，
     回滚=切回旧运行时自建状态，数据不丢）。
  4. checkpoint_migrations 预置 v=0..9（== langgraph 3.1.2 MIGRATIONS 长度）：
     若生产/开发仍有 setup() 调用，读到已是最新版本 → 不再执行任何 DDL 迁移（双保险）；
     生产 ManagedCheckpointer 不再调用 setup()（改 preflight 校验，见 agent/checkpoint.py）。

Revision ID: d17b2c3e4f5a
Revises: c17d2e3f405a
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d17b2c3e4f5a"
down_revision = "c17d2e3f405a"
branch_labels = None
depends_on = None

# langgraph-checkpoint-postgres 3.1.2 MIGRATIONS 最终版本号总数（v 从 0 起）
_CHECKPOINT_SCHEMA_VERSIONS = 10


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table(name)


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def _create_teacher_session_contexts() -> None:
    op.create_table(
        "teacher_session_contexts",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("textbook_id", sa.Text(), nullable=True),
        sa.Column("chapter_path", sa.Text(), nullable=True),
        sa.Column("class_id", sa.Uuid(), nullable=True),
        sa.Column("deck_id", sa.Uuid(), nullable=True),
        sa.Column(
            "retrieval_scope_ids",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "sticky",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("sticky_teacher_updated_idx", "teacher_session_contexts", ["teacher_id", "updated_at"])


def _create_teacher_memories() -> None:
    op.create_table(
        "teacher_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_md5", sa.Text(), nullable=False),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "teacher_memories_teacher_layer_idx",
        "teacher_memories",
        ["teacher_id", "layer", sa.text("created_at DESC")],
    )


def _create_teacher_memory_history() -> None:
    op.create_table(
        "teacher_memory_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_butler_messages() -> None:
    op.create_table(
        "butler_messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_butler_messages_teacher", "butler_messages", ["teacher_id", sa.text("created_at DESC")])


def _create_ai_runs() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_name", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("temperature", sa.Numeric(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("rag_collections", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("retrieved_doc_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "evidence_manifest",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("evidence_source_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("retrieval_query", sa.Text(), nullable=True),
        sa.Column("verifier_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("teacher_accepted", sa.Boolean(), nullable=True),
        sa.Column("teacher_edit_distance", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        # R11.5-B 新增审计列（输入/输出 hash + 完整 lineage，DB 不存原始 prompt/输出原文 → 脱敏）
        sa.Column(
            "lineage",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("output_hash", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ai_runs_feature_created_at_idx", "ai_runs", ["feature", "created_at"])


def _create_conversation_threads() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_conversation_thread_session"),
    )
    op.create_index("ix_conversation_thread_teacher_updated", "conversation_threads", ["teacher_id", sa.text("updated_at DESC")])


def _create_conversation_summaries() -> None:
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_summary_session_created", "conversation_summaries", ["session_id", sa.text("created_at DESC")])


def _create_prompt_releases() -> None:
    op.create_table(
        "prompt_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prompt_name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("schema", sa.Text(), nullable=True),
        sa.Column(
            "toolset",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "model_policy",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'APPROVED'"), nullable=False),
        sa.Column("released_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_name", "version", name="uq_prompt_release_name_version"),
    )


def _create_tool_call_records() -> None:
    op.create_table(
        "tool_call_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("teacher_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("tool_kind", sa.Text(), nullable=True),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column(
            "params_redacted",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_call_run_id", "tool_call_records", ["run_id"])


def _create_pending_actions() -> None:
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column(
            "card",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column(
            "params_overrides",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("fingerprint", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", name="uq_pending_action_action_id"),
    )
    op.create_index(
        "ix_pending_action_teacher_status_created",
        "pending_actions",
        ["teacher_id", "status", sa.text("created_at DESC")],
    )


# ── LangGraph checkpoint 表（langgraph-checkpoint-postgres==3.1.2 最终 schema）──


def _create_checkpoint_tables() -> None:
    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("v"),
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=True),  # MIGRATIONS[4] 后为 nullable
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "channel", "version"),
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
    )
    op.create_index("checkpoints_thread_id_idx", "checkpoints", ["thread_id"])
    op.create_index("checkpoint_blobs_thread_id_idx", "checkpoint_blobs", ["thread_id"])
    op.create_index("checkpoint_writes_thread_id_idx", "checkpoint_writes", ["thread_id"])


def _seed_checkpoint_versions() -> None:
    """预置 langgraph schema 版本号（v=0..9），使 setup() 读到最新版本后不再执行 DDL。"""
    bind = op.get_bind()
    existing = bind.execute(sa.text("SELECT count(*) FROM checkpoint_migrations")).scalar()
    if not existing:
        bind.execute(
            sa.text(
                "INSERT INTO checkpoint_migrations (v) VALUES "
                + ", ".join(f"({i})" for i in range(_CHECKPOINT_SCHEMA_VERSIONS))
            )
        )


def upgrade() -> None:
    # ── 接管已存在运行时表（preflight：存在则跳过，绝不 drop/重建 → 无损）──
    if not _table_exists("teacher_session_contexts"):
        _create_teacher_session_contexts()
    if not _table_exists("teacher_memories"):
        _create_teacher_memories()
    if not _table_exists("teacher_memory_history"):
        _create_teacher_memory_history()
    if not _table_exists("butler_messages"):
        _create_butler_messages()
    if not _table_exists("ai_runs"):
        _create_ai_runs()
    else:
        # ai_runs 已存在（历史运行时表）→ 仅补审计列（无损增量）
        if not _column_exists("ai_runs", "lineage"):
            op.add_column(
                "ai_runs",
                sa.Column("lineage", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True),
            )
        if not _column_exists("ai_runs", "input_hash"):
            op.add_column("ai_runs", sa.Column("input_hash", sa.Text(), nullable=True))
        if not _column_exists("ai_runs", "output_hash"):
            op.add_column("ai_runs", sa.Column("output_hash", sa.Text(), nullable=True))

    # ── 新增正式表 ──
    if not _table_exists("conversation_threads"):
        _create_conversation_threads()
    if not _table_exists("conversation_summaries"):
        _create_conversation_summaries()
    if not _table_exists("prompt_releases"):
        _create_prompt_releases()
    if not _table_exists("tool_call_records"):
        _create_tool_call_records()
    if not _table_exists("pending_actions"):
        _create_pending_actions()

    # ── LangGraph checkpoint 所有权收编（仅当缺表时建；版本号幂等预置）──
    if not _table_exists("checkpoint_migrations"):
        _create_checkpoint_tables()
        _seed_checkpoint_versions()
    elif not _table_exists("checkpoints"):
        # 异常半态（极少见）：补建业务表本身（含版本表已存在时的跳版本场景，保守不入版本行）
        op.create_table(
            "checkpoints",
            sa.Column("thread_id", sa.Text(), nullable=False),
            sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
            sa.Column("checkpoint_id", sa.Text(), nullable=False),
            sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
            sa.Column("type", sa.Text(), nullable=True),
            sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column(
                "metadata",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
        )
        op.create_index("checkpoints_thread_id_idx", "checkpoints", ["thread_id"])
    if not _table_exists("checkpoint_blobs"):
        op.create_table(
            "checkpoint_blobs",
            sa.Column("thread_id", sa.Text(), nullable=False),
            sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
            sa.Column("channel", sa.Text(), nullable=False),
            sa.Column("version", sa.Text(), nullable=False),
            sa.Column("type", sa.Text(), nullable=False),
            sa.Column("blob", sa.LargeBinary(), nullable=True),
            sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "channel", "version"),
        )
        op.create_index("checkpoint_blobs_thread_id_idx", "checkpoint_blobs", ["thread_id"])
    if not _table_exists("checkpoint_writes"):
        op.create_table(
            "checkpoint_writes",
            sa.Column("thread_id", sa.Text(), nullable=False),
            sa.Column("checkpoint_ns", sa.Text(), server_default=sa.text("''"), nullable=False),
            sa.Column("checkpoint_id", sa.Text(), nullable=False),
            sa.Column("task_id", sa.Text(), nullable=False),
            sa.Column("idx", sa.Integer(), nullable=False),
            sa.Column("channel", sa.Text(), nullable=False),
            sa.Column("type", sa.Text(), nullable=True),
            sa.Column("blob", sa.LargeBinary(), nullable=False),
            sa.Column("task_path", sa.Text(), server_default=sa.text("''"), nullable=False),
            sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
        )
        op.create_index("checkpoint_writes_thread_id_idx", "checkpoint_writes", ["thread_id"])


def downgrade() -> None:
    """回滚边界：只 drop 本轮新增表 + ai_runs 审计列；接管前已存在的运行时表不删（数据保留）。

    显式不 drop：teacher_session_contexts / teacher_memories / teacher_memory_history /
    butler_messages / checkpoints* —— 这些表在 R11.5-B 之前即由运行时幂等自建，
    回滚 = 回到「运行时自建」状态（ownership 交还旧逻辑），数据必须保留。
    """
    for table in (
        "pending_actions",
        "tool_call_records",
        "prompt_releases",
        "conversation_summaries",
        "conversation_threads",
    ):
        if _table_exists(table):
            op.drop_table(table)

    if _table_exists("ai_runs"):
        for col in ("output_hash", "input_hash", "lineage"):
            if _column_exists("ai_runs", col):
                op.drop_column("ai_runs", col)