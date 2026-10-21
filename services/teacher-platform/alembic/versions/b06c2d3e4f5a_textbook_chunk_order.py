"""textbook_chunks.order 顺序列（R08）

R07 实测发现的顺序漏洞修复：textbook_chunks 无顺序列（created_at 同事务相等，
DB 侧无法恢复切片顺序）。本迁移新增 `order` 列（册内全局 1-based 行号），
权威顺序 = 切片 chunks.jsonl 行序（与 R06 build_section_tree / R07 输入流同口径），
由 R08 幂等回填脚本填充（null = 尚未回填）。

- 向后兼容：新增 nullable 列，不触碰旧行数据与既有索引/约束；回填前检索可用
  lexical-only 路径，回填后启用有序完整 section 恢复；
- down/rollback：downgrade 仅 drop_index + drop_column，不删除任何向量/行
  （index_version 回滚 = 切换读取版本，不删向量）；
- 表名与旧混合集合隔离（textbook_* 前缀），不向 services/api 的 1024 结构写任何数据。

Revision ID: b06c2d3e4f5a
Revises: a05b1c2d3e4f
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b06c2d3e4f5a"
down_revision = "a05b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("textbook_chunks", sa.Column("order", sa.Integer(), nullable=True))
    op.create_index("ix_tbchunk_section_order", "textbook_chunks", ["section_id", "order"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tbchunk_section_order", table_name="textbook_chunks")
    op.drop_column("textbook_chunks", "order")
