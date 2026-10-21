"""RV06 · research_retrieval_trace（检索 trace / cost 审计行）。

Revision ID: 0008_rv06_retrieval_trace
Revises: 0007_rv05_parser_anchors
Create Date: 2026-09-13

回滚说明（AGENTS.md 要求）：
- `downgrade()` **只** drop 本迁移创建的表与索引，不触碰 RV02–RV05 的任何表；
- 本表是**审计旁路**：删除它不会影响会话/资产/文献/锚点数据的可用性，
  只是丢失历史检索成本的统计口径；
- 表内不含 query 原文与 snippet，因此回滚不涉及任何内容数据的去留判断；
- 升级为纯新增（无列变更、无数据搬迁），可在旧库上直接 `upgrade head`。
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

revision: str = "0008_rv06_retrieval_trace"
down_revision: str | None = "0007_rv05_parser_anchors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")
EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "research_retrieval_trace",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=True),
        sa.Column("actor_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("session_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False, server_default=""),
        sa.Column("strategy", sa.String(64), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("provider_ids", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("provider_status", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("degraded_providers", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("result_ids", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collapsed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflicts_kept", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OK"),
        sa.Column("warnings", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("token_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("redaction_version", sa.String(32), nullable=False, server_default="rv06-redaction-v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "query_hash", name="uq_retrieval_trace_tenant_query"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_retrieval_trace_trace_id",
        "research_retrieval_trace",
        ["trace_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_retrieval_trace_tenant_created",
        "research_retrieval_trace",
        ["tenant_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_retrieval_trace_tenant_id",
        "research_retrieval_trace",
        ["tenant_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_research_retrieval_trace_tenant_id", table_name="research_retrieval_trace", schema=SCHEMA)
    op.drop_index("ix_retrieval_trace_tenant_created", table_name="research_retrieval_trace", schema=SCHEMA)
    op.drop_index("ix_retrieval_trace_trace_id", table_name="research_retrieval_trace", schema=SCHEMA)
    op.drop_table("research_retrieval_trace", schema=SCHEMA)
