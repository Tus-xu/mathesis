"""RV18 · 检索分词列（修复 P0-6：CJK 全文检索 0 命中）。

Revision ID: 0018_rv18_search_tokens
Revises: 0017_rv17_research_workspace
Create Date: 2026-09-16

问题
----
`to_tsvector('simple', search_text)` 对**中文无效**：PG 默认解析器按空白/标点
切词，CJK 无词边界 → 整段中文退化成单个巨型 token，查询侧同法切出的短 token
永远不可能相等。表现为「拉丁文可检索、CJK 0 命中」（`误差`/`误差估计`/`有限元`
全 0）。`pg_trgm` 对 2 字中文短查询阈值不稳，不足以单独兜底。

修复
----
1. 新增 `research_paper.search_tokens` / `research_note.search_tokens`：
   由 `research_workspace.search_tokenize.index_text()` 生成的**词元串**
   （CJK 连续串 → 重叠 2-gram；拉丁词原样小写；空格连接）。
2. 对两列建 GIN 索引 `to_tsvector('simple', search_tokens)`；查询侧用**同一**
   分词器生成 `to_tsquery`（词元间 AND）。于是 `误差估计` → `误差 & 差估 & 估计`。
3. **回填存量行**：复用应用内 `index_text`，保证索引/查询同源、不产生漂移。

纯加法式
--------
本迁移只 **增列 + 建索引 + 回填**，不改动任何既有列/表/索引，`down_revision`
单头指向 `0017`。`downgrade()` 只 drop 本迁移新增的 2 个索引与 2 个列。

不改既有迁移（BACKEND-CONTRACT §82）。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.config import db_schema  # noqa: E402
from research_vnext.core.research_workspace.search_tokenize import index_text  # noqa: E402

SCHEMA = db_schema()

revision: str = "0018_rv18_search_tokens"
down_revision: str | None = "0017_rv17_research_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill(bind: sa.engine.Connection) -> None:
    """用与应用同源的 `index_text` 回填存量 `search_tokens`（幂等）。"""
    papers = (
        bind.execute(
            sa.text(
                f"SELECT id, title, authors, venue, doi, search_text "
                f"FROM {SCHEMA}.research_paper"
            )
        )
        .mappings()
        .all()
    )
    for r in papers:
        authors = r["authors"] or []
        joined = (
            authors if isinstance(authors, str) else " ".join(str(a) for a in authors)
        )
        tok = index_text(r["title"], joined, r["venue"], r["doi"], r["search_text"])
        bind.execute(
            sa.text(f"UPDATE {SCHEMA}.research_paper SET search_tokens = :t WHERE id = :id"),
            {"t": tok, "id": r["id"]},
        )

    notes = (
        bind.execute(
            sa.text(
                f"SELECT id, title, body, section, selected_text "
                f"FROM {SCHEMA}.research_note"
            )
        )
        .mappings()
        .all()
    )
    for r in notes:
        tok = index_text(r["title"], r["body"], r["section"], r["selected_text"])
        bind.execute(
            sa.text(f"UPDATE {SCHEMA}.research_note SET search_tokens = :t WHERE id = :id"),
            {"t": tok, "id": r["id"]},
        )


def upgrade() -> None:
    # ---- 1) 增列（server_default 让存量行立即可用；随后回填真实词元） ----
    op.add_column(
        "research_paper",
        sa.Column("search_tokens", sa.Text(), nullable=False, server_default=""),
        schema=SCHEMA,
    )
    op.add_column(
        "research_note",
        sa.Column("search_tokens", sa.Text(), nullable=False, server_default=""),
        schema=SCHEMA,
    )

    # ---- 2) CJK/拉丁词元 GIN 索引 ----
    op.execute(
        f"""
        CREATE INDEX ix_rp17_tokens_tsv
        ON {SCHEMA}.research_paper
        USING gin (to_tsvector('simple', search_tokens))
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_rn17_tokens_tsv
        ON {SCHEMA}.research_note
        USING gin (to_tsvector('simple', search_tokens))
        """
    )

    # ---- 3) 回填存量行（复用应用分词器，索引/查询同源） ----
    _backfill(op.get_bind())


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_rn17_tokens_tsv")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_rp17_tokens_tsv")
    op.drop_column("research_note", "search_tokens", schema=SCHEMA)
    op.drop_column("research_paper", "search_tokens", schema=SCHEMA)
