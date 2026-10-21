"""r12 direct-chunk locators

R12（教材 PDF 语义 → 预切片教材数据直接入库的最小兼容补丁）additive 迁移：

- textbook_sources += source_kind / batch_id / manifest_sha256 / provenance：
  管线分类 + 批次/清单溯源 + 结构化 provenance（收口）；source_kind 对存量行
  默认 'direct-chunk'（R06 全量均为预切片导入，无 legacy-pdf 存量）。
- textbook_sections += chunk_start / chunk_end：direct-chunk 定位列
  （全局 1-based chunk 行号区间；page_start/page_end 仅 legacy 兼容）。
- textbook_problems += chunk_ref / textbook_content_units += chunk_ref：
  canonical SourceLocator ref（新 writer 权威锚；page_ref 仅 legacy 兼容）。
- textbook_pages 零改动：旧表 dormant，不删除（决策 D-002 / DECISIONS.md §3）。

幂等：alembic 版本表保证重复 upgrade no-op；downgrade 逆序删列，可回滚。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "e26a6b7c8d9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # textbook_sources：管线分类 + 批次/清单溯源 + 结构化 provenance
    op.add_column(
        "textbook_sources",
        sa.Column("source_kind", sa.Text(), server_default="direct-chunk", nullable=False),
    )
    op.add_column("textbook_sources", sa.Column("batch_id", sa.Text(), nullable=True))
    op.add_column("textbook_sources", sa.Column("manifest_sha256", sa.Text(), nullable=True))
    op.add_column(
        "textbook_sources",
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_tbsource_kind",
        "textbook_sources",
        "source_kind IN ('direct-chunk','legacy-pdf','synth')",
    )
    op.create_index("ix_tbsource_batch", "textbook_sources", ["batch_id"], unique=False)

    # textbook_sections：direct-chunk 定位列
    op.add_column("textbook_sections", sa.Column("chunk_start", sa.Integer(), nullable=True))
    op.add_column("textbook_sections", sa.Column("chunk_end", sa.Integer(), nullable=True))
    op.create_index("ix_tbsection_chunk", "textbook_sections", ["chunk_start", "chunk_end"], unique=False)

    # textbook_problems / textbook_content_units：canonical chunk_ref
    op.add_column("textbook_problems", sa.Column("chunk_ref", sa.Text(), nullable=True))
    op.add_column("textbook_content_units", sa.Column("chunk_ref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("textbook_content_units", "chunk_ref")
    op.drop_column("textbook_problems", "chunk_ref")
    op.drop_index("ix_tbsection_chunk", table_name="textbook_sections")
    op.drop_column("textbook_sections", "chunk_end")
    op.drop_column("textbook_sections", "chunk_start")
    op.drop_index("ix_tbsource_batch", table_name="textbook_sources")
    op.drop_constraint("ck_tbsource_kind", "textbook_sources", type_="check")
    op.drop_column("textbook_sources", "provenance")
    op.drop_column("textbook_sources", "manifest_sha256")
    op.drop_column("textbook_sources", "batch_id")
    op.drop_column("textbook_sources", "source_kind")
