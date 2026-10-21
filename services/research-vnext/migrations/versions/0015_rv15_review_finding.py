"""RV15 · Review Finding v2（Review Review Board）。

Revision ID: 0015_rv15_review_finding
Revises: 0014_rv14_export_artifact
Create Date: 2026-09-14

变更内容
--------
1. 新表 `review_finding_v2`：RV15 权威审查发现表（十类 taxonomy / 六态状态机 /
   severity / target+locator / 指纹 / stale）。
2. 新表 `review_finding_transition`：状态迁移审计（append-only）。
3. 新表 `review_finding_resolution`：RESOLVED 证据闭环（修复修订 + 证据）。
4. 新表 `review_finding_waiver`：WAIVED 审批链（理由/批准/到期）。
5. 新表 `review_run`：一次 Review run 的审计。

与 legacy `review_finding`（0005）的关系：**不合并、不删除**——legacy 表保留
只读（RV13 时代产物），新写入只进 v2；与 evidence_card / research_evidence_card
的 D-TABLE-1 裁决口径一致（权威表唯一真源，legacy 显式标注降级）。

回滚说明（AGENTS.md 要求）
--------------------------
`downgrade()` 只 drop 本迁移新建的表；不触碰任何既有表（含 legacy
review_finding）的数据行。
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

revision: str = "0015_rv15_review_finding"
down_revision: str | None = "0014_rv14_export_artifact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "review_finding_v2",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("taxonomy", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False, server_default="P2"),
        sa.Column("status", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("target", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("target_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by_run", sa.String(128), nullable=False, server_default=""),
        sa.Column("assignee", UUID, nullable=True),
        sa.Column("due", sa.Date(), nullable=True),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen_run", sa.String(128), nullable=False, server_default=""),
        sa.Column("reopen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("needs_recheck", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolution", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("waiver", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("row_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("audit", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_rfv2_session", "review_finding_v2", ["session_id"], schema=SCHEMA)
    op.create_index("ix_rfv2_session_status", "review_finding_v2",
                    ["session_id", "status"], schema=SCHEMA)
    op.create_index("ix_rfv2_session_fp", "review_finding_v2",
                    ["session_id", "fingerprint"], schema=SCHEMA)

    op.create_table(
        "review_finding_transition",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("finding_id", UUID, nullable=False),
        sa.Column("from_status", sa.String(24), nullable=False),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("by_run", sa.String(128), nullable=False, server_default=""),
        sa.Column("reason", sa.String(128), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        schema=SCHEMA,
    )
    op.create_index("ix_rft_finding", "review_finding_transition", ["finding_id"], schema=SCHEMA)

    op.create_table(
        "review_finding_resolution",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("finding_id", UUID, nullable=False),
        sa.Column("revision_ref", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        schema=SCHEMA,
    )
    op.create_index("ix_rfr_finding", "review_finding_resolution", ["finding_id"], schema=SCHEMA)

    op.create_table(
        "review_finding_waiver",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("finding_id", UUID, nullable=False),
        sa.Column("reason", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expiry", sa.String(10), nullable=False, server_default=""),
        sa.Column("approved_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("expired_reopened", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        schema=SCHEMA,
    )
    op.create_index("ix_rfw_finding", "review_finding_waiver", ["finding_id"], schema=SCHEMA)

    op.create_table(
        "review_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("proposed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reopened_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_run_id", UUID, nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PROPOSED"),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_rrun_session", "review_run", ["session_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_rrun_session", table_name="review_run", schema=SCHEMA)
    op.drop_table("review_run", schema=SCHEMA)
    op.drop_index("ix_rfw_finding", table_name="review_finding_waiver", schema=SCHEMA)
    op.drop_table("review_finding_waiver", schema=SCHEMA)
    op.drop_index("ix_rfr_finding", table_name="review_finding_resolution", schema=SCHEMA)
    op.drop_table("review_finding_resolution", schema=SCHEMA)
    op.drop_index("ix_rft_finding", table_name="review_finding_transition", schema=SCHEMA)
    op.drop_table("review_finding_transition", schema=SCHEMA)
    op.drop_index("ix_rfv2_session_fp", table_name="review_finding_v2", schema=SCHEMA)
    op.drop_index("ix_rfv2_session_status", table_name="review_finding_v2", schema=SCHEMA)
    op.drop_index("ix_rfv2_session", table_name="review_finding_v2", schema=SCHEMA)
    op.drop_table("review_finding_v2", schema=SCHEMA)
