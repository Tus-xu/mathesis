"""RV07 · research_evidence_card + research_citation。

Revision ID: 0009_rv07_evidence_citation
Revises: 0008_rv06_retrieval_trace
Create Date: 2026-09-13

**纯新增**：只建两张新表与索引，不改任何既有列、不搬迁数据。
可在旧库上直接 `upgrade head`。

为什么不改 RV09–RV13 的 `evidence_card`（`0005` 创建）：
它是"引用位"投影（7 个业务列），被 RV12 的"证据失效→重开 SOLVED claim"使用；
RV07 需要的是**事实对象**（类型矩阵 / 结构化 locator / rights 策略 / ACL /
provenance / stale）。把两者合并会同时打破 RV12 的既有写入路径与本轮的
边界约定。合并路径登记在 `RV07/FINAL-GATE.md` 的限制 L-RV07-2。

回滚说明（AGENTS.md 要求）：
- `downgrade()` **只** drop 本迁移创建的两张表与其索引；
- 两张表都是**旁路**：删掉不影响会话/资产/文献/锚点/检索 trace 的可用性，
  只丢失"证据事实层"与"引用元数据层"；
- 升级为纯新增（无列变更、无数据搬迁）。
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

revision: str = "0009_rv07_evidence_citation"
down_revision: str | None = "0008_rv06_retrieval_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")
EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    # ---- RV07 · EvidenceCard 事实行 ----
    op.create_table(
        "research_evidence_card",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=True),
        sa.Column("session_id", UUID, nullable=True),
        sa.Column("owner_id", UUID, nullable=True),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("source_ref", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("locator", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("locator_kind", sa.String(16), nullable=False, server_default="EXTERNAL"),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("snippet_hash", sa.String(64), nullable=False),
        sa.Column("structured_fact", JSONB(), nullable=True),
        sa.Column("provenance", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("rights", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("rights_status", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_run", sa.String(128), nullable=False, server_default=""),
        sa.Column("confidence", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("limitations", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("acl", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("stale_state", sa.String(16), nullable=False, server_default="FRESH"),
        sa.Column("stale_reason", sa.String(64), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "citation_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("labels", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("warnings", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "proposal_hash", name="uq_evidence_card_proposal"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_evidence_card_tenant_session",
        "research_evidence_card",
        ["tenant_id", "session_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_evidence_card_tenant_project",
        "research_evidence_card",
        ["tenant_id", "project_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_evidence_card_type", "research_evidence_card", ["tenant_id", "evidence_type"], schema=SCHEMA
    )
    op.create_index("ix_evidence_card_run", "research_evidence_card", ["created_by_run"], schema=SCHEMA)
    op.create_index(
        "ix_research_evidence_card_tenant_id", "research_evidence_card", ["tenant_id"], schema=SCHEMA
    )

    # ---- RV07 · Citation canonical 行 ----
    op.create_table(
        "research_citation",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=True),
        sa.Column("session_id", UUID, nullable=True),
        sa.Column("evidence_card_id", UUID, nullable=True),
        sa.Column("csl_json", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("csl_hash", sa.String(64), nullable=False),
        sa.Column("csl_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("doi", sa.String(256), nullable=False, server_default=""),
        sa.Column("bibtex", sa.Text(), nullable=False, server_default=""),
        sa.Column("ris", sa.Text(), nullable=False, server_default=""),
        sa.Column("lossy_fields", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "csl_hash", name="uq_research_citation_hash"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_citation_tenant_project", "research_citation", ["tenant_id", "project_id"], schema=SCHEMA
    )
    op.create_index("ix_research_citation_doi", "research_citation", ["tenant_id", "doi"], schema=SCHEMA)
    op.create_index("ix_research_citation_tenant_id", "research_citation", ["tenant_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_research_citation_tenant_id", table_name="research_citation", schema=SCHEMA)
    op.drop_index("ix_research_citation_doi", table_name="research_citation", schema=SCHEMA)
    op.drop_index(
        "ix_research_citation_tenant_project", table_name="research_citation", schema=SCHEMA
    )
    op.drop_table("research_citation", schema=SCHEMA)

    op.drop_index(
        "ix_research_evidence_card_tenant_id", table_name="research_evidence_card", schema=SCHEMA
    )
    op.drop_index("ix_evidence_card_run", table_name="research_evidence_card", schema=SCHEMA)
    op.drop_index("ix_evidence_card_type", table_name="research_evidence_card", schema=SCHEMA)
    op.drop_index(
        "ix_evidence_card_tenant_project", table_name="research_evidence_card", schema=SCHEMA
    )
    op.drop_index(
        "ix_evidence_card_tenant_session", table_name="research_evidence_card", schema=SCHEMA
    )
    op.drop_table("research_evidence_card", schema=SCHEMA)
