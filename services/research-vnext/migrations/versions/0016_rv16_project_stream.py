"""RV16 · ResearchProject 容器 + token 流落库（StreamBuffer）。

Revision ID: 0016_rv16_project_stream
Revises: 0015_rv15_review_finding
Create Date: 2026-09-15

变更内容
--------
1. 新表 `research_project`：持久科研工作区容器（归属 `tenant_id + owner_id`），
   带 `revision`（append-only 闸门）与 `archived`。此前会话直接挂一个裸
   `project_id`（UUID）但**无项目表**（见 ARCHITECTURE §9 R2）。
2. 新表 `research_stream_chunk`：token 流每轮 turn 的帧落库（StreamBuffer），
   支撑"晚订阅者补齐 + `Last-Event-ID` 断线续传"（X7 附加约束③）。
3. 旧会话 `project_id` 回填：为 `research_session` 中已出现的
   `(tenant_id, owner_id, project_id)` 组合各建一行 `research_project`，
   **不删除、不改写**任何既有会话行（纯加法式兼容）。

单链单 head
-----------
`down_revision = '0015_rv15_review_finding'`，本迁移是唯一 head 的延续。

回滚说明（AGENTS.md 要求）
--------------------------
`downgrade()` 只 drop 本迁移新建的两张表；回填生成的 `research_project`
行随表一起删除，不触碰任何既有表的数据行（会话的 `project_id` 列本就是既有列，
回滚后只是"没有对应项目表"——与迁移前状态一致）。
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

revision: str = "0016_rv16_project_stream"
down_revision: str | None = "0015_rv15_review_finding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "research_project",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("client_request_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    # 幂等唯一键用**部分索引**：仅对非空 `client_request_id` 生效。
    # 回填的既有项目无 client_request_id（空串），若用普通唯一约束，同一
    # (tenant, owner) 下的多个既有项目会互相冲突。应用层创建的项目**始终**带
    # ≥8 字符 client_request_id，故部分索引完整覆盖幂等语义。
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_research_project_idem
        ON {SCHEMA}.research_project (tenant_id, owner_id, client_request_id)
        WHERE client_request_id <> ''
        """
    )
    op.create_index("ix_rp_tenant_owner", "research_project", ["tenant_id", "owner_id"], schema=SCHEMA)
    op.create_index("ix_rp_owner_archived", "research_project", ["owner_id", "archived"], schema=SCHEMA)

    op.create_table(
        "research_stream_chunk",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("frame_type", sa.String(24), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("turn_id", "seq", name="uq_stream_chunk_turn_seq"),
        schema=SCHEMA,
    )
    op.create_index("ix_rsc_turn_seq", "research_stream_chunk", ["turn_id", "seq"], schema=SCHEMA)
    op.create_index("ix_rsc_session", "research_stream_chunk", ["session_id"], schema=SCHEMA)

    # 旧会话 project_id 回填：为已出现的 (tenant_id, owner_id, project_id) 组合建项目行。
    # 幂等：`ON CONFLICT DO NOTHING` 保证重复执行安全。
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.research_project (id, tenant_id, owner_id, name, description)
        SELECT DISTINCT s.project_id, s.tenant_id, s.owner_id, '既有项目', ''
        FROM {SCHEMA}.research_session AS s
        WHERE s.project_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_rsc_session", table_name="research_stream_chunk", schema=SCHEMA)
    op.drop_index("ix_rsc_turn_seq", table_name="research_stream_chunk", schema=SCHEMA)
    op.drop_table("research_stream_chunk", schema=SCHEMA)
    op.drop_index("ix_rp_owner_archived", table_name="research_project", schema=SCHEMA)
    op.drop_index("ix_rp_tenant_owner", table_name="research_project", schema=SCHEMA)
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_research_project_idem")
    op.drop_table("research_project", schema=SCHEMA)
