"""教材资源库 V2 领域模型（R05，P0）。

实体关系（ERD 与数据字典见 R05 报告 D:\\ppt-rescue-control\\20260909-R05\\）：
    TextbookSource 1─N TextbookPage（原页 + 派生页图 + 富字段）
    TextbookSource 1─N TextbookSection（章节树，parent_id 自引用）
    TextbookSection 1─N TextbookContentUnit（定义/定理/公式/例题/习题/探究/图/引入/推导/总结）
    TextbookSection 1─N TextbookProblem（题目，保留题干-图-解答关联）
    TextbookSection 1─N TextbookChunk（R08 索引载体）

红线：
- 原始资产不可变：raw_pdf_asset_ref / page_image_asset_ref / original_asset_refs 指向
  MinIO object key，任何裁剪/压缩/抠图都生成新 key，禁止覆盖原件（R07 落实）；
- rights_status 默认 QUARANTINED，未经权利验证不得进入任何生产检索（active_scope 守卫）；
- 表名与旧 `textbooks`/`curriculum`/`quiz_bank`/`ppt_slots` 语义隔离，不复用混合集合。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# PG 生产用 JSONB；SQLite 测试环境退化为通用 JSON（与 B4 域模型同口径）
JsonB = JSONB().with_variant(JSON(), "sqlite")
# pgvector Vector(768)（R04 契约：teacher 768 / api 1024，禁止交叉写索引）；SQLite 退化为 JSON
Embedding = Vector(768).with_variant(JSON(), "sqlite")

# R05 手册 E1：无 LICENSE 的教材默认 QUARANTINED；状态机见 rights.py
CONTENT_UNIT_TYPES = (
    "definition",
    "theorem",
    "formula",
    "example",
    "exercise",
    "inquiry",
    "figure",
    "introduction",
    "derivation",
    "summary",
)
PROBLEM_KINDS = ("example", "exercise", "inquiry")


def _uuid() -> UUID:
    return uuid4()


class TextbookSource(Base):
    """教材源（一册教材一次导入一行）：provenance 必须完整可审计。

    school_id 为 NULL 表示平台级种子；非 NULL 时按租户隔离。
    rights_status 由 rights.py 状态机唯一驱动，禁止业务层随手改。
    """

    __tablename__ = "textbook_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    school_id: Mapped[UUID | None] = mapped_column(nullable=True)
    publisher: Mapped[str] = mapped_column(Text, nullable=False)
    edition: Mapped[str] = mapped_column(Text, nullable=False)
    grade: Mapped[str] = mapped_column(Text, nullable=False)
    volume: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="数学")
    # provenance：来源仓库/commit/路径 + 文件 SHA256（幂等与审计键）
    source_repo: Mapped[str] = mapped_column(Text, nullable=False)
    source_commit: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    rights_status: Mapped[str] = mapped_column(Text, nullable=False, default="QUARANTINED")
    rights_evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)

    # R12：管线分类 + 批次/清单溯源（direct-chunk 新 writer 必填 batch_id/manifest_sha256）
    source_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="direct-chunk", server_default="direct-chunk"
    )
    batch_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    # R12：结构化 provenance（收口 source_repo/commit/path/parser_version/import_id）；
    # 旧列保留只读兼容，新 writer 以本列为权威。
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "rights_status IN ('QUARANTINED','STAGED','ACTIVE')",
            name="ck_tbsource_rights",
        ),
        CheckConstraint(
            "source_kind IN ('direct-chunk','legacy-pdf','synth')",
            name="ck_tbsource_kind",
        ),
        UniqueConstraint("school_id", "file_sha256", name="uq_tbsource_school_sha"),
        Index("ix_tbsource_status", "rights_status"),
    )


class TextbookPage(Base):
    """单页：原 PDF 引用 + 派生页图，富字段仅供 AI 理解，不替代原图。"""

    __tablename__ = "textbook_pages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    textbook_id: Mapped[UUID] = mapped_column(ForeignKey("textbook_sources.id", ondelete="CASCADE"), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 册内稳定序号
    printed_page_no: Mapped[str | None] = mapped_column(Text, nullable=True)  # 印刷页码（如 7、插页）
    raw_pdf_asset_ref: Mapped[str] = mapped_column(Text, nullable=False)  # MinIO key，不可变原件
    page_image_asset_ref: Mapped[str] = mapped_column(Text, nullable=False)  # 派生页图 key
    text_layer: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    vision_summary: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    bbox_map: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)  # 页图内容 hash

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("textbook_id", "page_no", name="uq_tbpage_textbook_pageno"),
        Index("ix_tbpage_textbook", "textbook_id"),
    )


class TextbookSection(Base):
    """章节树节点：parent_id 自引用；page_start/end 落 TextbookPage.page_no 保持有序。"""

    __tablename__ = "textbook_sections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    textbook_id: Mapped[UUID] = mapped_column(ForeignKey("textbook_sources.id", ondelete="CASCADE"), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("textbook_sections.id", ondelete="CASCADE"), nullable=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1=章 2=节 3=课时…
    order: Mapped[int] = mapped_column(Integer, nullable=False)  # 同父下稳定顺序
    title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False)  # 检索用规范化标题
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # R12：direct-chunk 定位列——覆盖 chunk 区间（全局 1-based 行号）；新 writer 权威锚，
    # page_start/page_end 仅 legacy 兼容（切片源下二者同值，但语义为 chunk 行号非页码）。
    chunk_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # 如 1.2.3
    intro_strategy: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    derivation_chain: Mapped[list[Any] | None] = mapped_column(JsonB, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("textbook_id", "order", name="uq_tbsection_textbook_order"),
        Index("ix_tbsection_parent", "parent_id"),
        Index("ix_tbsection_textbook_level", "textbook_id", "level"),
    )


class TextbookContentUnit(Base):
    """结构化内容单元：必须能回溯到原页（page_from/to + source_bbox + asset_refs）。"""

    __tablename__ = "textbook_content_units"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    section_id: Mapped[UUID] = mapped_column(ForeignKey("textbook_sections.id", ondelete="CASCADE"), nullable=False)
    page_from: Mapped[int] = mapped_column(Integer, nullable=False)
    page_to: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    # R12：direct-chunk 定位 ref（canonical SourceLocator → chunk）；page_from/page_to 仅 legacy 兼容
    chunk_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_bbox: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    asset_refs: Mapped[list[Any] | None] = mapped_column(JsonB, nullable=True)  # 原图派生 AssetRef 列表
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"type IN {CONTENT_UNIT_TYPES}", name="ck_tbunit_type"),
        CheckConstraint("review_status IN ('pending','reviewed','rejected')", name="ck_tbunit_review"),
        Index("ix_tbunit_section_type", "section_id", "type"),
    )


class TextbookProblem(Base):
    """题目：题干/解答与页面锚点解耦存储，图以 original_asset_refs 原图引用。"""

    __tablename__ = "textbook_problems"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    section_id: Mapped[UUID] = mapped_column(ForeignKey("textbook_sections.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="example")
    order: Mapped[int] = mapped_column(Integer, nullable=False)  # 节内稳定顺序
    stem_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_ref: Mapped[int] = mapped_column(Integer, nullable=False)  # 锚定 TextbookPage.page_no（legacy 兼容）
    # R12：direct-chunk 定位 ref（canonical SourceLocator → chunk）；新 writer 权威锚，禁止再写 page_ref
    chunk_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    stem_bbox: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    original_asset_refs: Mapped[list[Any] | None] = mapped_column(JsonB, nullable=True)
    knowledge_tags: Mapped[list[str] | None] = mapped_column(JsonB, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"kind IN {PROBLEM_KINDS}", name="ck_tbproblem_kind"),
        UniqueConstraint("section_id", "order", name="uq_tbproblem_section_order"),
        Index("ix_tbproblem_section_kind", "section_id", "kind"),
    )


class TextbookChunk(Base):
    """检索分块（R08 填充 embedding 与 index_version；R05 先落 schema）。"""

    __tablename__ = "textbook_chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    section_id: Mapped[UUID] = mapped_column(ForeignKey("textbook_sections.id", ondelete="CASCADE"), nullable=False)
    page_range: Mapped[list[int] | None] = mapped_column(JsonB, nullable=True)  # [start, end] 页序
    unit_ids: Mapped[list[Any] | None] = mapped_column(JsonB, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # R08 顺序漏洞修复：册内全局 1-based 行号（权威顺序 = 切片 chunks.jsonl 行序，幂等回填；
    # null = 尚未回填，与 R07 page_ref 同口径）。切片源无原页（D-002），order 即页序锚。
    order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding = mapped_column(Embedding, nullable=True)  # R08 索引后必填
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunker_version: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("section_id", "content_hash", name="uq_tbchunk_section_hash"),
        Index("ix_tbchunk_section", "section_id"),
        Index("ix_tbchunk_section_order", "section_id", "order"),
    )
