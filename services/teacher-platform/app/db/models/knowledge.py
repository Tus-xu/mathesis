"""B2 域内 DB 模型（架构 §4.9 RAG 表 + §4.10 knowledge_documents / memories 增列）。

所有权：本文件归 B2（G2 矩阵：app/db/models/*.py 由域所有者编写、B3 评审迁移一致性）。
红线：
- knowledge_chunks.doc_ref 必须能回指 knowledge_documents（元数据宿主）；
- 一切向量检索必须经 knowledge_documents 租户预过滤（K4，架构红线 15）；
- memories.layer / source_refs：L2/L3 无 L1 source_refs 不入库（可审计，KNR 红线）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

# PG 生产用 JSONB；SQLite 离线测试环境退化为通用 JSON（与 B4 域模型同口径，列类型导入面不变）
JsonB = JSONB().with_variant(JSON(), "sqlite")
# pgvector Vector 同理：PG 用 vector(768)，SQLite 测试退化为 JSON
Embedding = Vector(768).with_variant(JSON(), "sqlite")

try:  # B3 bootstrap 后统一走 app.db.base 的声明基类
    from app.db.base import Base  # type: ignore[attr-defined]
except ImportError:  # B3 尚未落库前的独立运行（M1 骨架期）
    from sqlalchemy.orm import DeclarativeBase

    class Base(DeclarativeBase):
        """临时基类：集成时由 B3 统一 Base 替换（见 docs/handoff/DEP-B2-*.md）。"""


class KnowledgeDocument(Base):
    """知识文档一级实体（V2.1 KNR 最小版，架构 §4.10）。

    租户铁律：种子语料 school_id NULL + visibility='platform'；
    教师私有 owner_teacher_id + visibility='private'；school 档 P1 启用。
    """

    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_ref: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    school_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    owner_teacher_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="platform")
    manifest_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','indexing','ready','error')", name="ck_kdoc_status"
        ),
        CheckConstraint(
            "visibility IN ('private','school','platform')", name="ck_kdoc_visibility"
        ),
    )


class KnowledgeChunk(Base):
    """向量分块（架构 §4.9）。embedding 维度 768（讯飞 xop3qwen8bembedding）。"""

    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection: Mapped[str] = mapped_column(Text, nullable=False)
    doc_ref: Mapped[str] = mapped_column(
        Text, ForeignKey("knowledge_documents.doc_ref", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Embedding, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JsonB, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_kchunk_collection", "collection"),
        Index("ix_kchunk_doc_ref", "doc_ref"),
        Index("uq_kchunk_doc_chunk", "doc_ref", "chunk_index", unique=True),
    )


class WebSearchCache(Base):
    """联网搜索结果缓存（架构 §4.9）。一次搜索一条，query_hash 为键。"""

    __tablename__ = "web_search_cache"

    query_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    results: Mapped[dict] = mapped_column(JsonB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Memory(Base):
    """教师记忆（架构 §4.9 + §4.10 三层 Lite：B1 读写 / B2 embedding）。"""

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Embedding, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False, default="L1")
    source_refs: Mapped[dict | None] = mapped_column(JsonB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("layer IN ('L1','L2','L3')", name="ck_memory_layer"),
        Index("ix_memory_user", "user_id"),
    )
