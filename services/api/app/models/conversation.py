"""会话模型"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Conversation(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "idx_conversations_user",
            "user_id",
            "updated_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    active_role: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False, server_default="新对话")
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # M2 对话重构：会话置顶（列表排序 pinned 优先）
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
