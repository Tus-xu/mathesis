"""textbook v2 domain tables

教材资源库 V2（R05）首版迁移：创建 6 张独立于旧混合集合的教材域表。

设计要点（对应 app/textbook_v2/models.py，模型与迁移一致，禁止漂移）：
- 表名前缀 textbook_*，与旧 `textbooks`/`curriculum`/`quiz_bank`/`ppt_slots`
  完全隔离，不再向旧混合集合追加任何字段；
- rights_status 默认 'QUARANTINED'（无 LICENSE 素材一律隔离，未授权不入生产检索）；
- textbook_sources.school_id NULL = 平台级种子，非 NULL 走租户隔离；
- 原始资产不可变：raw_pdf_asset_ref / page_image_asset_ref / original_asset_refs
  是 MinIO object key，任何裁剪/派生必须生成新 key（R07 落实），DB 层不存临时 URL；
- 删除 textbook_sources 级联删其 pages/sections（含自引用章节树），但**不触碰**
  MinIO 对象——对象生命周期由独立策略管理（R05 验收：删除 source 不得误删共享原件）；
- pgvector Vector(768) 与教师平台 embedding 契约一致（R04 铁律：teacher 768 / api 1024）。

Revision ID: a05b1c2d3e4f
Revises: b2c3d4e5f6a1
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a05b1c2d3e4f"
down_revision = "b2c3d4e5f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "textbook_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=False),
        sa.Column("edition", sa.Text(), nullable=False),
        sa.Column("grade", sa.Text(), nullable=False),
        sa.Column("volume", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), server_default="数学", nullable=False),
        sa.Column("source_repo", sa.Text(), nullable=False),
        sa.Column("source_commit", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.Text(), nullable=False),
        sa.Column("rights_status", sa.Text(), server_default="QUARANTINED", nullable=False),
        sa.Column("rights_evidence_ref", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("rights_status IN ('QUARANTINED','STAGED','ACTIVE')", name="ck_tbsource_rights"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("school_id", "file_sha256", name="uq_tbsource_school_sha"),
    )
    op.create_index("ix_tbsource_status", "textbook_sources", ["rights_status"], unique=False)

    op.create_table(
        "textbook_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("textbook_id", sa.Uuid(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("printed_page_no", sa.Text(), nullable=True),
        sa.Column("raw_pdf_asset_ref", sa.Text(), nullable=False),
        sa.Column("page_image_asset_ref", sa.Text(), nullable=False),
        sa.Column("text_layer", sa.Text(), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column(
            "vision_summary", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column("bbox_map", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["textbook_id"], ["textbook_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("textbook_id", "page_no", name="uq_tbpage_textbook_pageno"),
    )
    op.create_index("ix_tbpage_textbook", "textbook_pages", ["textbook_id"], unique=False)

    op.create_table(
        "textbook_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("textbook_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column(
            "intro_strategy", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column(
            "derivation_chain", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["textbook_sections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["textbook_id"], ["textbook_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("textbook_id", "order", name="uq_tbsection_textbook_order"),
    )
    op.create_index("ix_tbsection_parent", "textbook_sections", ["parent_id"], unique=False)
    op.create_index("ix_tbsection_textbook_level", "textbook_sections", ["textbook_id", "level"], unique=False)

    op.create_table(
        "textbook_content_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=False),
        sa.Column("page_to", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("latex", sa.Text(), nullable=True),
        sa.Column(
            "source_bbox", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column(
            "asset_refs", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("extraction_method", sa.Text(), nullable=True),
        sa.Column("review_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "type IN ('definition','theorem','formula','example','exercise','inquiry','figure','introduction','derivation','summary')",
            name="ck_tbunit_type",
        ),
        sa.CheckConstraint("review_status IN ('pending','reviewed','rejected')", name="ck_tbunit_review"),
        sa.ForeignKeyConstraint(["section_id"], ["textbook_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tbunit_section_type", "textbook_content_units", ["section_id", "type"], unique=False)

    op.create_table(
        "textbook_problems",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), server_default="example", nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("stem_text", sa.Text(), nullable=True),
        sa.Column("solution_text", sa.Text(), nullable=True),
        sa.Column("page_ref", sa.Integer(), nullable=False),
        sa.Column(
            "stem_bbox", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column(
            "original_asset_refs",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "knowledge_tags", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column("difficulty", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('example','exercise','inquiry')", name="ck_tbproblem_kind"),
        sa.ForeignKeyConstraint(["section_id"], ["textbook_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "order", name="uq_tbproblem_section_order"),
    )
    op.create_index("ix_tbproblem_section_kind", "textbook_problems", ["section_id", "kind"], unique=False)

    op.create_table(
        "textbook_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column(
            "page_range", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column("unit_ids", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("chunker_version", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("index_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["section_id"], ["textbook_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "content_hash", name="uq_tbchunk_section_hash"),
    )
    op.create_index("ix_tbchunk_section", "textbook_chunks", ["section_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tbchunk_section", table_name="textbook_chunks")
    op.drop_table("textbook_chunks")
    op.drop_index("ix_tbproblem_section_kind", table_name="textbook_problems")
    op.drop_table("textbook_problems")
    op.drop_index("ix_tbunit_section_type", table_name="textbook_content_units")
    op.drop_table("textbook_content_units")
    op.drop_index("ix_tbsection_textbook_level", table_name="textbook_sections")
    op.drop_index("ix_tbsection_parent", table_name="textbook_sections")
    op.drop_table("textbook_sections")
    op.drop_index("ix_tbpage_textbook", table_name="textbook_pages")
    op.drop_table("textbook_pages")
    op.drop_index("ix_tbsource_status", table_name="textbook_sources")
    op.drop_table("textbook_sources")
