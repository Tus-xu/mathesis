"""知识文档模型（知识库域）"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class KnowledgeDoc(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "knowledge_docs"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # tel_001 加宽（16→32）：ADVANCED_REFERENCE/CURRICULUM_STANDARD/TRAINING_MATERIAL
    # 等 16+ 字符取值此前被有损拍扁；与 alembic tel_001_two_end_link 同步加宽。
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    uploader_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    meta_: Mapped[dict] = mapped_column("meta", JSONB, nullable=False, server_default="{}")
