"""切片模型（知识库域）"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Chunk(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "idx_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    doc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_docs.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list | None] = mapped_column(Vector(1024), nullable=True)
    kp_ids: Mapped[list] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    # 切片级出处：教材关联、课堂引用必须能回溯至具体册别与章节，不能只保留文档标题。
    meta_: Mapped[dict] = mapped_column("meta", JSONB, nullable=False, server_default="{}")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
