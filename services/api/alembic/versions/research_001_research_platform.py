"""research_001: 科研端模型落地（文献库/阅读/知识库/写作/任务全表）。

Revision ID: research_001_research_platform
Revises: om8_error_record_solution_cache
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "research_001_research_platform"
down_revision = "om8_error_record_solution_cache"
branch_labels = None
depends_on = None


def _up_cols(table: str) -> tuple:
    """公共列：UUID pk + created_at/updated_at（server_default now）"""
    return (
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    # 扩展防护（已装则幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---------- 文献库 ----------
    op.create_table(
        "research_papers",
        *_up_cols("research_papers"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("authors", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("doi", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("file_sha256", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("parse_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("reading_status", sa.Text(), nullable=False, server_default="unread"),
        sa.Column("starred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_click", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "doi", name="uq_research_papers_user_doi"),
    )
    op.create_index("idx_research_papers_user_created", "research_papers", ["user_id", "created_at"])
    op.create_index("idx_research_papers_user_reading", "research_papers", ["user_id", "reading_status"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_papers_title_trgm "
        "ON research_papers USING gin (title gin_trgm_ops)"
    )

    op.create_table(
        "research_collections",
        *_up_cols("research_collections"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_collections.id"),
            nullable=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_research_collections_user", "research_collections", ["user_id", "sort_order"])

    op.create_table(
        "research_paper_collections",
        *_up_cols("research_paper_collections"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False
        ),
        sa.Column(
            "collection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_collections.id"),
            nullable=False,
        ),
        sa.UniqueConstraint("paper_id", "collection_id", name="uq_research_paper_collections"),
    )

    op.create_table(
        "research_tags",
        *_up_cols("research_tags"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False, server_default="#4169e1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "name", name="uq_research_tags_user_name"),
    )

    op.create_table(
        "research_paper_tags",
        *_up_cols("research_paper_tags"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_tags.id"), nullable=False),
        sa.UniqueConstraint("paper_id", "tag_id", name="uq_research_paper_tags"),
    )

    # ---------- 阅读 ----------
    op.create_table(
        "research_reading_positions",
        *_up_cols("research_reading_positions"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("zoom", sa.Float(), nullable=False, server_default="1.0"),
        sa.UniqueConstraint("user_id", "paper_id", name="uq_research_reading_positions"),
    )

    op.create_table(
        "research_annotations",
        *_up_cols("research_annotations"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rect_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
        sa.Column("kind", sa.Text(), nullable=False, server_default="highlight"),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("note_text", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_research_annotations_paper_page", "research_annotations", ["user_id", "paper_id", "page"]
    )

    op.create_table(
        "research_notes",
        *_up_cols("research_notes"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.Text(), nullable=False, server_default="note"),
    )
    op.create_index("idx_research_notes_paper", "research_notes", ["user_id", "paper_id"])

    op.create_table(
        "research_citations",
        *_up_cols("research_citations"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("authors", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("bibtex", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("idx_research_citations_paper", "research_citations", ["user_id", "paper_id"])

    # ---------- 知识库 ----------
    op.create_table(
        "research_kb_chunks",
        *_up_cols("research_kb_chunks"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="paragraph"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_state", sa.Text(), nullable=False, server_default="none"),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "idx_research_kb_chunks_paper_order", "research_kb_chunks", ["user_id", "paper_id", "order_index"]
    )
    op.create_index("idx_research_kb_chunks_paper_kind", "research_kb_chunks", ["user_id", "paper_id", "kind"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_kb_chunks_content_trgm "
        "ON research_kb_chunks USING gin (content gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_kb_chunks_embedding "
        "ON research_kb_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m=16, ef_construction=64)"
    )

    op.create_table(
        "research_math_objects",
        *_up_cols("research_math_objects"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_papers.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("object_type", sa.Text(), nullable=False, server_default="formula"),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("canonical_text", sa.Text(), nullable=True),
    )
    op.create_index("idx_research_math_objects_paper", "research_math_objects", ["user_id", "paper_id", "page"])

    # ---------- 写作 ----------
    op.create_table(
        "research_manuscripts",
        *_up_cols("research_manuscripts"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default="未命名文稿"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("format", sa.Text(), nullable=False, server_default="latex"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compile_status", sa.Text(), nullable=False, server_default="idle"),
        sa.Column("compile_log", sa.Text(), nullable=True),
        sa.Column("compile_pdf_key", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_research_manuscripts_user_updated", "research_manuscripts", ["user_id", "updated_at"])

    op.create_table(
        "research_manuscript_versions",
        *_up_cols("research_manuscript_versions"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "manuscript_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_manuscripts.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("manuscript_id", "version", name="uq_research_manuscript_versions"),
    )

    op.create_table(
        "research_writing_materials",
        *_up_cols("research_writing_materials"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "manuscript_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_manuscripts.id"),
            nullable=False,
        ),
        sa.Column(
            "source_paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_papers.id"),
            nullable=True,
        ),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("material_type", sa.Text(), nullable=False, server_default="quote"),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "idx_research_writing_materials_manuscript", "research_writing_materials", ["user_id", "manuscript_id"]
    )

    op.create_table(
        "research_manuscript_citations",
        *_up_cols("research_manuscript_citations"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "manuscript_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_manuscripts.id"),
            nullable=False,
        ),
        sa.Column("citation_key", sa.Text(), nullable=False),
        sa.Column(
            "source_paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_papers.id"),
            nullable=True,
        ),
        sa.Column("bibtex", sa.Text(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("manuscript_id", "citation_key", name="uq_research_manuscript_citations_key"),
    )
    op.create_index(
        "idx_research_manuscript_citations", "research_manuscript_citations", ["user_id", "manuscript_id"]
    )

    op.create_table(
        "research_ai_suggestions",
        *_up_cols("research_ai_suggestions"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "manuscript_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_manuscripts.id"),
            nullable=False,
        ),
        sa.Column("suggestion_type", sa.Text(), nullable=False, server_default="language"),
        sa.Column("severity", sa.Text(), nullable=False, server_default="info"),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("replacement_text", sa.Text(), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
    )
    op.create_index("idx_research_ai_suggestions_ms", "research_ai_suggestions", ["manuscript_id", "status"])

    # ---------- 任务 ----------
    op.create_table(
        "research_tasks",
        *_up_cols("research_tasks"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_research_tasks_user_status", "research_tasks", ["user_id", "status", "created_at"])


def downgrade() -> None:
    for table in (
        "research_tasks",
        "research_ai_suggestions",
        "research_manuscript_citations",
        "research_writing_materials",
        "research_manuscript_versions",
        "research_manuscripts",
        "research_math_objects",
        "research_kb_chunks",
        "research_citations",
        "research_notes",
        "research_annotations",
        "research_reading_positions",
        "research_paper_tags",
        "research_tags",
        "research_paper_collections",
        "research_collections",
        "research_papers",
    ):
        op.drop_table(table)