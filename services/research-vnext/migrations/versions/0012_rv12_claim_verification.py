"""RV12 · Claim / Verification 域的落地表与投影列。

Revision ID: 0012_rv12_claim_verification
Revises: 0011_rv10_route_arena
Create Date: 2026-09-14

变更内容
--------
1. `claim` 追加**投影列**（`claim_key` / owner / ACL / claim_kind / support_band /
   requires_review / review_reasons / published_at / published_by）。
   真相仍在 `content` JSONB（服务端 Gate 的产物），这些列只让 SQL 能过滤与对账。
2. 新增 `claim_evidence_link`：claim ↔ evidence 关联（含**每链接**的支持评估快照）。
3. 新增 `claim_transition`：状态迁移审计（只追加，回答"为什么变成这个状态"）。
4. `verification_record` 追加审计列（verifier/kind/version/artifact_refs/
   tool_policy/prompt_ref/context_manifest_ref/ai_run_id/cost/created_by/input_hash）。

为什么 `claim_key` 是**必须**的
------------------------------
`claim` 是不可变修订链：每次变更新增一行。若没有跨修订稳定身份，
"这个 claim 的当前版本是哪个"只能靠 `max(revision)` 猜，而"同一 claim 的两个
版本"与"两个不同 claim"在数据上无法区分 —— 于是"反证只增不减""修订冲突检测"
都会退化成启发式。`claim_key` 让两者在类型上可分。

回滚说明（AGENTS.md 要求）
--------------------------
- `downgrade()` 先删新增表，再删新增列；
- 新增列**全部可空或有 server_default**，故 downgrade 无损；
- 本迁移不触碰任何既有表的数据行。
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

revision: str = "0012_rv12_claim_verification"
down_revision: str | None = "0011_rv10_route_arena"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")
EMPTY_ARR = sa.text("'[]'::jsonb")
FALSE = sa.text("false")

#: `claim` 新增的投影列（upgrade 顺序 = downgrade 逆序）。
_CLAIM_COLUMNS: list[sa.Column] = [
    sa.Column("claim_key", UUID, nullable=True),
    sa.Column("owner_id", UUID, nullable=True),
    sa.Column("acl", JSONB(), nullable=False, server_default=EMPTY_OBJ),
    sa.Column("claim_kind", sa.String(32), nullable=False, server_default="PARAPHRASE"),
    sa.Column("support_band", sa.String(16), nullable=False, server_default="NONE"),
    sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=FALSE),
    sa.Column("review_reasons", JSONB(), nullable=False, server_default=EMPTY_ARR),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("published_by", UUID, nullable=True),
]

_VERIFICATION_COLUMNS: list[sa.Column] = [
    sa.Column("claim_key", UUID, nullable=True),
    sa.Column("verifier", sa.String(64), nullable=False, server_default=""),
    sa.Column("kind", sa.String(48), nullable=False, server_default=""),
    sa.Column("verifier_version", sa.String(32), nullable=False, server_default=""),
    sa.Column("artifact_refs", JSONB(), nullable=False, server_default=EMPTY_ARR),
    sa.Column("tool_policy", JSONB(), nullable=False, server_default=EMPTY_OBJ),
    sa.Column("prompt_ref", sa.String(160), nullable=False, server_default=""),
    sa.Column("context_manifest_ref", sa.String(128), nullable=False, server_default=""),
    sa.Column("ai_run_id", UUID, nullable=True),
    sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
    sa.Column("cost_basis", sa.String(64), nullable=False, server_default=""),
    sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
    sa.Column("input_hash", sa.String(64), nullable=False, server_default=""),
]


def upgrade() -> None:
    for column in _CLAIM_COLUMNS:
        op.add_column("claim", column, schema=SCHEMA)
    op.create_index("ix_claim_key", "claim", ["claim_key"], schema=SCHEMA)

    op.create_table(
        "claim_evidence_link",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("claim_key", UUID, nullable=False),
        sa.Column("claim_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("evidence_card_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="SUPPORTS"),
        sa.Column("claim_kind", sa.String(32), nullable=False, server_default="PARAPHRASE"),
        sa.Column("note", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("claim_key", "evidence_card_id", "role", name="uq_claim_evidence_role"),
        schema=SCHEMA,
    )
    op.create_index("ix_claim_evidence_claim", "claim_evidence_link", ["claim_key"], schema=SCHEMA)
    op.create_index(
        "ix_claim_evidence_card", "claim_evidence_link", ["evidence_card_id"], schema=SCHEMA
    )

    op.create_table(
        "claim_transition",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_key", UUID, nullable=False),
        sa.Column("claim_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=False, server_default=""),
        sa.Column("to_status", sa.String(32), nullable=False, server_default=""),
        sa.Column("gate_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("satisfied", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("missing", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("conditions", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("refutation", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("review_reasons", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("actor_kind", sa.String(16), nullable=False, server_default="SYSTEM"),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("action", sa.String(64), nullable=False, server_default=""),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_claim_transition_claim", "claim_transition", ["claim_key", "revision"], schema=SCHEMA)
    op.create_index("ix_claim_transition_session", "claim_transition", ["session_id"], schema=SCHEMA)

    for column in _VERIFICATION_COLUMNS:
        op.add_column("verification_record", column, schema=SCHEMA)


def downgrade() -> None:
    for column in reversed(_VERIFICATION_COLUMNS):
        op.drop_column("verification_record", column.name, schema=SCHEMA)

    op.drop_index("ix_claim_transition_session", "claim_transition", schema=SCHEMA)
    op.drop_index("ix_claim_transition_claim", "claim_transition", schema=SCHEMA)
    op.drop_table("claim_transition", schema=SCHEMA)

    op.drop_index("ix_claim_evidence_card", "claim_evidence_link", schema=SCHEMA)
    op.drop_index("ix_claim_evidence_claim", "claim_evidence_link", schema=SCHEMA)
    op.drop_table("claim_evidence_link", schema=SCHEMA)

    op.drop_index("ix_claim_key", "claim", schema=SCHEMA)
    for column in reversed(_CLAIM_COLUMNS):
        op.drop_column("claim", column.name, schema=SCHEMA)
