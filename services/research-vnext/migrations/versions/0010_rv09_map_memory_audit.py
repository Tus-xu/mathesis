"""RV09 · 记忆 checkpoint / 长期偏好命名空间 / 修订审计 + AI run 上下文与成本列。

Revision ID: 0010_rv09_map_memory_audit
Revises: 0009_rv07_evidence_citation
Create Date: 2026-09-14

变更内容
--------
1. 新增 `research_thread_checkpoint`（短期记忆，`(session_id, thread_key)` 唯一）；
2. 新增 `research_preference`（长期偏好，`(tenant,user,research,purpose,key)` 唯一）；
3. 新增 `object_revision_audit`（只追加的修订因果审计：谁确认的、依据哪次 AI run、
   哪个 prompt 版本、上下文裁了什么、花了多少钱、对应哪个 event）；
4. `research_ai_run` 追加 5 列：`context_manifest` / `prompt_version` /
   `prompt_published` / `cost_usd` / `cost_basis`。

为什么 `cost_usd` 与 `cost_basis` 必须成对
------------------------------------------
`cost_usd = 0` 与"不知道这次花了多少钱"是两件事。只存金额会让未知被读成免费；
`cost_basis` 让二者在数据上可区分（`table:<版本>` / `unknown-model` / `no-usage`）。

回滚说明（AGENTS.md 要求）
--------------------------
- `downgrade()` **只** drop 本迁移新增的三张表与其索引，并 drop 追加的 5 列；
- 三张表都是**旁路**：删掉不影响会话/消息/领域对象/事件的可用性，
  只丢失"记忆""审计"与"上下文可复核性"；
- 追加列全部可空或带 server_default，因此**在已有数据上 upgrade 不会失败**；
- 本迁移**不**创建/修改任何领域对象表（problem_map / research_map 在 `0005` 已存在）。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.config import db_schema  # noqa: E402

SCHEMA = db_schema()

revision: str = "0010_rv09_map_memory_audit"
down_revision: str | None = "0009_rv07_evidence_citation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")
EMPTY_ARR = sa.text("'[]'::jsonb")

_AI_RUN_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, str], ...] = (
    ("context_manifest", JSONB(), "'{}'::jsonb"),
    ("prompt_version", sa.Integer(), "0"),
    ("prompt_published", sa.Boolean(), "false"),
    ("cost_usd", sa.Numeric(12, 6), None),
    ("cost_basis", sa.String(64), "''"),
)


def upgrade() -> None:
    # ---- 1. 短期记忆：会话 / thread checkpoint ----
    op.create_table(
        "research_thread_checkpoint",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("thread_key", sa.String(64), nullable=False, server_default="main"),
        sa.Column("last_message_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("state", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("session_id", "thread_key", name="uq_thread_checkpoint"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_thread_checkpoint_session_id",
        "research_thread_checkpoint",
        ["session_id"],
        schema=SCHEMA,
    )

    # ---- 2. 长期偏好：命名空间隔离 ----
    op.create_table(
        "research_preference",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("research_id", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(48), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("authorized", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(64), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "research_id", "purpose", "key",
            name="uq_research_preference_ns",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_preference_ns",
        "research_preference",
        ["tenant_id", "user_id", "research_id", "purpose"],
        schema=SCHEMA,
    )

    # ---- 3. 修订因果审计（只追加） ----
    op.create_table(
        "object_revision_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("object_ref", sa.String(64), nullable=False),
        sa.Column("object_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_object_id", UUID, nullable=True),
        sa.Column("previous_revision", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("diff", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("pending_action_id", UUID, nullable=True),
        sa.Column("ai_run_id", UUID, nullable=True),
        sa.Column("prompt_ref", sa.String(160), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_manifest_ref", sa.String(128), nullable=False, server_default=""),
        sa.Column("context_trim_reasons", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("cost_basis", sa.String(64), nullable=False, server_default=""),
        sa.Column("event_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("client_request_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_object_revision_audit_obj",
        "object_revision_audit",
        ["session_id", "object_ref", "revision"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_object_revision_audit_session_id",
        "object_revision_audit",
        ["session_id"],
        schema=SCHEMA,
    )

    # ---- 4. research_ai_run 追加列（全部可空或有 server_default） ----
    for name, type_, default in _AI_RUN_NEW_COLUMNS:
        kwargs: dict = {"nullable": True}
        if default is not None:
            kwargs["server_default"] = sa.text(default)
        op.add_column("research_ai_run", sa.Column(name, type_, **kwargs), schema=SCHEMA)


def downgrade() -> None:
    for name, _type, _default in reversed(_AI_RUN_NEW_COLUMNS):
        op.drop_column("research_ai_run", name, schema=SCHEMA)

    op.drop_index("ix_object_revision_audit_session_id", table_name="object_revision_audit", schema=SCHEMA)
    op.drop_index("ix_object_revision_audit_obj", table_name="object_revision_audit", schema=SCHEMA)
    op.drop_table("object_revision_audit", schema=SCHEMA)

    op.drop_index("ix_research_preference_ns", table_name="research_preference", schema=SCHEMA)
    op.drop_table("research_preference", schema=SCHEMA)

    op.drop_index(
        "ix_research_thread_checkpoint_session_id",
        table_name="research_thread_checkpoint",
        schema=SCHEMA,
    )
    op.drop_table("research_thread_checkpoint", schema=SCHEMA)
