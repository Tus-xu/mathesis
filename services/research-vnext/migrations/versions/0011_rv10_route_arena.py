"""RV10 · RouteArena 修订链表。

Revision ID: 0011_rv10_route_arena
Revises: 0010_rv09_map_memory_audit
Create Date: 2026-09-14

变更内容
--------
1. 新增 `route_arena`（不可变修订链）：2–3 条候选建模路线 + 服务端 rubric。
   blueprint 的绑定对象是**已确认的竞技场修订**，而不是模型的一面之词；
2. 其余零改动——blueprint 复用 `0005` 的 `research_blueprint` 表（content JSONB
   承载 binds / route / modelSpec / experimentPlan / gates / budget）。

为什么竞技场是一张独立的修订链表
--------------------------------
arena 是**被确认的比较事实**：哪几条路线参选、各自的结构声明、因什么被拒。
它有自己的生命周期（人工编辑 → 确认 → 后续可整体重新生成），
与 model_spec（单条路线的规格）和 research_blueprint（承诺书）都不同——
塞进任何一张已有表都会让"绑定哪个 arena 修订"失去唯一真源。

回滚说明（AGENTS.md 要求）
--------------------------
- `downgrade()` 只 drop 本迁移新增的表与其索引；
- RV10 之前没有任何会话写入过 `route_arena`，drop 是无损的；
- 本迁移不触碰既有表（含 `research_blueprint`）。
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

revision: str = "0011_rv10_route_arena"
down_revision: str | None = "0010_rv09_map_memory_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")


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


def upgrade() -> None:
    op.create_table(
        "route_arena",
        sa.Column("id", UUID, primary_key=True),
        *_revisioned_columns(),
        sa.UniqueConstraint("session_id", "revision", name="uq_route_arena_rev"),
        schema=SCHEMA,
    )
    op.create_index("ix_route_arena_session", "route_arena", ["session_id"], schema=SCHEMA)


def downgrade() -> None:
    # RV11 修复（1 行，登记于 RV11 FINAL-GATE）：`table=` 关键字不是 alembic
    # drop_index 的合法参数（TypeError: Additional arguments should be named
    # <dialectname>_<argument>），downgrade base 自 RV10 起即不可用；
    # 改为与 0005 相同的位置参数写法。
    op.drop_index("ix_route_arena_session", "route_arena", schema=SCHEMA)
    op.drop_table("route_arena", schema=SCHEMA)
