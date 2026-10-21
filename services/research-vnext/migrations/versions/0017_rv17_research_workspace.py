"""RV17 · 科研工作区（项目级文献 / 笔记 / 草稿文件树 / 编译运行 / Agent 任务 / 状态恢复）。

Revision ID: 0017_rv17_research_workspace
Revises: 0016_rv16_project_stream
Create Date: 2026-09-16

变更内容（**纯加法式**，不动任何既有表/迁移）
------------------------------------------------
1. 新表 `research_paper`：项目↔文献归属 + 元数据 + 阅读定位 + 已解析文本缓存。
2. 新表 `research_note`：带 locator（paper/page/section/selected_text）的笔记。
3. 新表 `research_project_file`：LaTeX 草稿文件树（path/kind/content/revision）。
4. 新表 `research_compile_run`：项目级真实编译运行 + 结构化 `log_lines`（含 file+line）。
5. 新表 `research_agent_task`：统一 Research Agent 任务（preset → 真实能力）。
6. 新表 `research_project_recent`：重新进入项目时的 READ/WRITE/页码/文件/行恢复。

检索设施（P0-6）
----------------
项目知识检索**不引入任何向量设施**（铁律 D4：禁止交叉写 embedding 索引）。
`research_paper.search_text` 是已解析文本的拼接列，配：
- `pg_trgm` GIN 索引（`gin_trgm_ops`）——覆盖中文等无空格语言；
- `to_tsvector('simple', search_text)` 表达式 GIN 索引 ——覆盖英文词检索。

回滚说明（AGENTS.md 要求）
--------------------------
`downgrade()` 只 drop 本迁移新建的 6 张表与 2 个索引；**不触碰**任何既有表。
`pg_trgm` 扩展不 drop（可能被其它对象依赖，且 drop extension 具破坏性）。
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

revision: str = "0017_rv17_research_workspace"
down_revision: str | None = "0016_rv16_project_stream"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    # ---- 0) 检索用扩展（trigram）。CREATE EXTENSION 幂等。 ----
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---- 1) 项目级文献 ----
    op.create_table(
        "research_paper",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("asset_id", UUID, nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("authors", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=False, server_default=""),
        sa.Column("doi", sa.String(256), nullable=False, server_default=""),
        sa.Column("read_state", sa.String(16), nullable=False, server_default="unread"),
        sa.Column("last_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parse_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("pages", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("page_sections", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("parse_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("client_request_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_rp17_project", "research_paper", ["project_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_rp17_asset", "research_paper", ["asset_id"], schema=SCHEMA)
    # 同项目内同内容 hash 去重（仅非空 hash 生效；部分唯一索引）。
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_research_paper_project_hash
        ON {SCHEMA}.research_paper (project_id, content_hash)
        WHERE content_hash <> '' AND deleted_at IS NULL
        """
    )
    # 检索索引：trigram（CJK 友好）+ simple tsvector（英文词）。
    op.execute(
        f"""
        CREATE INDEX ix_rp17_search_trgm
        ON {SCHEMA}.research_paper USING gin (search_text gin_trgm_ops)
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_rp17_search_tsv
        ON {SCHEMA}.research_paper
        USING gin (to_tsvector('simple', search_text))
        """
    )

    # ---- 2) 笔记 ----
    op.create_table(
        "research_note",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("paper_id", UUID, nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.Text(), nullable=False, server_default=""),
        sa.Column("selected_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("locator", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("xref_kind", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("xref_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_rn17_project", "research_note", ["project_id", "created_at"], schema=SCHEMA)

    # ---- 3) 草稿文件树 ----
    op.create_table(
        "research_project_file",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="tex"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("asset_id", UUID, nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_rpf17_project_path", "research_project_file", ["project_id", "path"],
        unique=True, schema=SCHEMA,
    )

    # ---- 4) 项目级编译运行 ----
    op.create_table(
        "research_compile_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("engine", sa.String(16), nullable=False, server_default="xelatex"),
        sa.Column("entry", sa.String(128), nullable=False, server_default="main.tex"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("pdf_artifact_id", UUID, nullable=True),
        sa.Column("pdf_storage_key", sa.String(512), nullable=False, server_default=""),
        sa.Column("pdf_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("log_lines", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("diagnostics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_rcr17_project", "research_compile_run", ["project_id", "created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_rcr17_project_status", "research_compile_run", ["project_id", "status"], schema=SCHEMA
    )

    # ---- 5) Research Agent 任务 ----
    op.create_table(
        "research_agent_task",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default=""),
        sa.Column("context", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_rat17_project", "research_agent_task", ["project_id", "created_at"], schema=SCHEMA
    )

    # ---- 6) 最近状态（重新进入项目恢复） ----
    op.create_table(
        "research_project_recent",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("workspace", sa.String(8), nullable=False, server_default="read"),
        sa.Column("paper_id", UUID, nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(512), nullable=False, server_default=""),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_rpr17_project_actor", "research_project_recent", ["project_id", "actor_id"],
        unique=True, schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("research_project_recent", schema=SCHEMA)
    op.drop_table("research_agent_task", schema=SCHEMA)
    op.drop_index("ix_rcr17_project_status", table_name="research_compile_run", schema=SCHEMA)
    op.drop_index("ix_rcr17_project", table_name="research_compile_run", schema=SCHEMA)
    op.drop_table("research_compile_run", schema=SCHEMA)
    op.drop_index("uq_rpf17_project_path", table_name="research_project_file", schema=SCHEMA)
    op.drop_table("research_project_file", schema=SCHEMA)
    op.drop_index("ix_rn17_project", table_name="research_note", schema=SCHEMA)
    op.drop_table("research_note", schema=SCHEMA)
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_rp17_search_tsv")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_rp17_search_trgm")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_research_paper_project_hash")
    op.drop_index("ix_rp17_asset", table_name="research_paper", schema=SCHEMA)
    op.drop_index("ix_rp17_project", table_name="research_paper", schema=SCHEMA)
    op.drop_table("research_paper", schema=SCHEMA)
