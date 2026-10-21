"""RV14 · 论文导出产物表（paper_export_artifact）+ 下载审计表（paper_export_audit）。

Revision ID: 0014_rv14_export_artifact
Revises: 0013_rv13_paper_edit_audit
Create Date: 2026-09-14

变更内容
--------
1. `paper_export_artifact`：一次成功导出的产物登记（字节在 MinIO，
   行内只有 storage key / sha256 / manifest / fingerprint）。
   `(tenant_id, fingerprint)` 唯一 —— 同 PaperSpec + 同格式集 + 同
   serializer/template 版本的导出在 DB 层去重（"同 input 不重复编译"）。
2. `paper_export_audit`：下载授权链路审计（TOKEN_MINTED / DOWNLOAD /
   DOWNLOAD_DENIED / REVOKED），只追加。

回滚说明（AGENTS.md 要求）
--------------------------
`downgrade()` 只 drop 本迁移新建的表；不触碰任何既有表的数据行。
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

revision: str = "0014_rv14_export_artifact"
down_revision: str | None = "0013_rv13_paper_edit_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "paper_export_artifact",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("paper_id", UUID, nullable=False),
        sa.Column("paper_revision", sa.Integer(), nullable=False),
        sa.Column("paper_content_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("formats", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("manifest", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "fingerprint", name="uq_paper_export_fp"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_paper_export_artifact_tenant", "paper_export_artifact", ["tenant_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_paper_export_session", "paper_export_artifact", ["session_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "paper_export_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("artifact_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("detail", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_paper_export_audit_artifact", "paper_export_audit", ["artifact_id", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_paper_export_audit_artifact", schema=SCHEMA, table_name="paper_export_audit")
    op.drop_table("paper_export_audit", schema=SCHEMA)
    op.drop_index("ix_paper_export_session", schema=SCHEMA, table_name="paper_export_artifact")
    op.drop_index("ix_paper_export_artifact_tenant", schema=SCHEMA, table_name="paper_export_artifact")
    op.drop_table("paper_export_artifact", schema=SCHEMA)
