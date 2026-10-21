"""RV09–RV13 · 领域对象表（修订链 + 证据 + 运行 + 验证 + 论文 + 审稿发现）

Revision ID: 0005_rv_domain_objects
Revises: 0004_rv03_job_runtime
Create Date: 2026-09-13

统一形态：**不可变修订链** —— `(session_id, revision)` 唯一；每次确认**新增**一行，
旧行永不修改。这样"谁在何时基于哪个 revision 改了什么"无需额外审计表即可回答。

本迁移同时给 `pending_action` 增加两列（`object_ref` / `source_message_id`）：
确认门必须知道"这次确认要落到哪个对象族"，否则 accept 无法路由。
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

revision: str = "0005_rv_domain_objects"
down_revision: str | None = "0004_rv03_job_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")
EMPTY_ARR = sa.text("'[]'::jsonb")


def _revisioned_columns() -> list[sa.Column]:
    return [
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("client_request_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", UUID, nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    ]


def _create_revisioned(table: str, *, extra: list[sa.Column] | None = None) -> None:
    op.create_table(
        table,
        sa.Column("id", UUID, primary_key=True),
        *_revisioned_columns(),
        *(extra or []),
        sa.UniqueConstraint("session_id", "revision", name=f"uq_{table}_rev"),
        schema=SCHEMA,
    )
    op.create_index(f"ix_{table}_session", table, ["session_id"], schema=SCHEMA)


def _drop_revisioned(table: str) -> None:
    op.drop_index(f"ix_{table}_session", table, schema=SCHEMA)
    op.drop_table(table, schema=SCHEMA)


def upgrade() -> None:
    # ---- 确认门扩展：object_ref（路由到哪个对象族）+ source_message_id（哪句话触发） ----
    op.add_column(
        "pending_action",
        sa.Column("object_ref", sa.String(64), nullable=False, server_default=""),
        schema=SCHEMA,
    )
    op.add_column(
        "pending_action",
        sa.Column("source_message_id", UUID, nullable=True),
        schema=SCHEMA,
    )

    # ---- RV09–RV13 修订链对象 ----
    _create_revisioned("research_intake")
    _create_revisioned("problem_map")
    _create_revisioned("research_map")
    _create_revisioned("experiment_plan")
    _create_revisioned("paper_spec")

    _create_revisioned(
        "model_spec",
        extra=[
            sa.Column("label", sa.String(256), nullable=False, server_default=""),
            sa.Column("constraints", JSONB(), nullable=False, server_default=EMPTY_ARR),
            sa.Column("evidence_coverage", JSONB(), nullable=False, server_default=EMPTY_ARR),
            sa.Column("gaps", JSONB(), nullable=False, server_default=EMPTY_ARR),
        ],
    )
    _create_revisioned("research_blueprint")
    _create_revisioned(
        "claim",
        extra=[
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="INCONCLUSIVE"),
            sa.Column("support_quality", sa.String(32), nullable=False, server_default="UNSUPPORTED"),
            sa.Column("relation", sa.String(32), nullable=False, server_default="unknown"),
            sa.Column("evidence_refs", JSONB(), nullable=False, server_default=EMPTY_ARR),
            sa.Column("experiment_refs", JSONB(), nullable=False, server_default=EMPTY_ARR),
            sa.Column("verification_refs", JSONB(), nullable=False, server_default=EMPTY_ARR),
            sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reopened_reason", sa.String(512), nullable=False, server_default=""),
        ],
    )

    # ---- 证据（引用 + 授权位；本体属平台资产层） ----
    op.create_table(
        "evidence_card",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("source", sa.String(256), nullable=False, server_default=""),
        sa.Column("label", sa.String(512), nullable=False, server_default=""),
        sa.Column("locator", sa.String(512), nullable=False, server_default=""),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("asset_version", sa.String(128), nullable=False, server_default=""),
        sa.Column("authorized", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_ec_session", "evidence_card", ["session_id"], schema=SCHEMA)

    # ---- 实验运行（可复现元数据；claim_holds 与 exit_code 分离） ----
    op.create_table(
        "experiment_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("plan_id", UUID, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("executor", sa.String(64), nullable=False, server_default=""),
        sa.Column("code_ref", sa.String(512), nullable=False, server_default=""),
        sa.Column("dataset_ref", sa.String(512), nullable=False, server_default=""),
        sa.Column("environment", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("params", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("result", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("metrics", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("logs_ref", sa.String(512), nullable=False, server_default=""),
        sa.Column("figures", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        # 独立于 exit_code；NULL = 尚未判定（**不是**成立）
        sa.Column("claim_holds", sa.Boolean(), nullable=True),
        sa.Column("not_reproducible_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("repro", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("client_request_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_er_session", "experiment_run", ["session_id"], schema=SCHEMA)

    # ---- 独立验证（PASS | FAIL | UNKNOWN；UNKNOWN 绝不强制转 PASS） ----
    op.create_table(
        "verification_record",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("claim_id", UUID, nullable=True),
        sa.Column("method", sa.String(128), nullable=False, server_default=""),
        sa.Column("result", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("detail", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("evidence_refs", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("independent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_vr_session", "verification_record", ["session_id"], schema=SCHEMA)
    op.create_index("ix_vr_claim", "verification_record", ["claim_id"], schema=SCHEMA)

    # ---- 审稿发现 ----
    op.create_table(
        "review_finding",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("paper_spec_id", UUID, nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="minor"),
        sa.Column("location", sa.String(512), nullable=False, server_default=""),
        sa.Column("related_claim_id", UUID, nullable=True),
        sa.Column("related_evidence_refs", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("suggestion", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("resolution_evidence_refs", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_rf_session", "review_finding", ["session_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_rf_session", "review_finding", schema=SCHEMA)
    op.drop_table("review_finding", schema=SCHEMA)

    op.drop_index("ix_vr_claim", "verification_record", schema=SCHEMA)
    op.drop_index("ix_vr_session", "verification_record", schema=SCHEMA)
    op.drop_table("verification_record", schema=SCHEMA)

    op.drop_index("ix_er_session", "experiment_run", schema=SCHEMA)
    op.drop_table("experiment_run", schema=SCHEMA)

    op.drop_index("ix_ec_session", "evidence_card", schema=SCHEMA)
    op.drop_table("evidence_card", schema=SCHEMA)

    for t in (
        "claim",
        "research_blueprint",
        "model_spec",
        "paper_spec",
        "experiment_plan",
        "research_map",
        "problem_map",
        "research_intake",
    ):
        _drop_revisioned(t)

    op.drop_column("pending_action", "source_message_id", schema=SCHEMA)
    op.drop_column("pending_action", "object_ref", schema=SCHEMA)
