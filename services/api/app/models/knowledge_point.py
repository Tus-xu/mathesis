"""知识点模型（知识库域，无软删）"""

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class KnowledgePoint(Base, TimestampMixin):
    """知识点 — 无软删（手册 §6.4）"""

    __tablename__ = "knowledge_points"

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_points.id"), nullable=True
    )
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    aliases: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
