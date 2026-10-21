"""RV13 · 论文编辑审计表（paper_edit_audit）。

Revision ID: 0013_rv13_paper_edit_audit
Revises: 0012_rv12_claim_verification
Create Date: 2026-09-14

变更内容
--------
新增 `paper_edit_audit`：MANUAL_EDIT / UNDO / REDO / AI_PATCH_ACCEPT 全部留痕。

为什么 undo/redo 需要独立审计表
--------------------------------
撤销/恢复本身写**新的不可变修订行**（append-only），
"这次修订是撤销产生的、恢复了哪一版、谁操作的"放进 content 会破坏
"恢复内容与被恢复版本逐字节一致"的性质 —— 所以审计独立成表，
修订行只存纯内容。

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

revision: str = "0013_rv13_paper_edit_audit"
down_revision: str | None = "0012_rv12_claim_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "paper_edit_audit",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("paper_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("detail", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_paper_edit_audit_session",
        "paper_edit_audit",
        ["session_id", "paper_revision"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_paper_edit_audit_session", schema=SCHEMA, table_name="paper_edit_audit")
    op.drop_table("paper_edit_audit", schema=SCHEMA)
