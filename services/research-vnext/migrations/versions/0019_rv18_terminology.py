"""RV18 · 术语记忆表（补完宪法 §42 Terminology Memory）。

Revision ID: 0019_rv18_terminology
Revises: 0018_rv18_search_tokens
Create Date: 2026-09-16

问题
----
§42 要求「至少在同一 Project 内保持：同一个专业术语，翻译一致」。
本轮已实现术语记忆的**逻辑**（`research_workspace/terminology.py`），
但为了不打断迁移链，第一版把记忆放在**进程内**——后果是：
**8300 重启后已积累的术语全部丢失**，"一致性"只在单次进程生命周期内成立。
对"同一篇稿子写几周"的真实场景来说，这个限制太大。

修复
----
新增 `research_terminology` 表，把术语记忆**持久化**：

| 列 | 说明 |
|---|---|
| `id` | PK |
| `tenant_id` / `project_id` | 隔离键（§48：跨租户/跨项目绝不串） |
| `source_term` | 规范化后的源术语（小写 + 折叠空白，与查询同源） |
| `target_term` | 已确立译名 |
| `hits` | 该术语被复用的次数（排序/淘汰依据） |
| `created_at` / `updated_at` | 时间戳 |

唯一约束 `(tenant_id, project_id, source_term)` —— 同一项目同一术语**只能有一个译名**，
这正是 §42 "一致"的**数据层保证**（而不是靠应用层自觉）。

纯加法式
--------
本迁移只**建表 + 建索引 + 建唯一约束**，不触碰任何既有表/列。
`down_revision` 单头指向 `0018`，`downgrade()` 只 drop 本迁移新建的表。

不改既有迁移（BACKEND-CONTRACT §82）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "0019_rv18_terminology"
down_revision: str | None = "0018_rv18_search_tokens"
branch_labels: str | None = None
depends_on: str | None = None

#: 与 `research_workspace/models.py` 保持一致的 schema 解析（避免 import 整个 app）
from pathlib import Path  # noqa: E402
import sys  # noqa: E402

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.config import db_schema  # noqa: E402

SCHEMA = db_schema()

TABLE = "research_terminology"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("project_id", PGUUID(as_uuid=True), nullable=False),
        # 规范化术语（小写 + 空白折叠）：查询侧用同一函数，保证命中
        sa.Column("source_term", sa.Text(), nullable=False),
        sa.Column("target_term", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # §42 的「一致」由数据层保证：同一项目同一术语只能有一个译名
        sa.UniqueConstraint(
            "tenant_id", "project_id", "source_term", name="uq_rt19_tenant_proj_term"
        ),
        schema=SCHEMA,
    )
    # 按项目取全量术语（注入 prompt / 反向校验）走这个索引
    op.create_index(
        "ix_rt19_project",
        TABLE,
        ["tenant_id", "project_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_rt19_project", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
