"""消息模型"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Message(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_msg_id", name="uq_messages_conv_client"),
        Index(
            "idx_messages_conv",
            "conversation_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_messages_conv_parent", "conversation_id", "parent_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    client_msg_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    envelope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    skill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # M2 对话重构：线性链 + 版本分支
    # parent_id = 活动线程中的前一条消息（会话首条 user 消息为 NULL）
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # NULL = 该版本处于活动分支；同 parent 的兄弟版本中仅一个为 NULL
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # user 消息持久化附件：[{file_id, kind, name, mime, size}]
    attachments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # assistant 消息持久化思考内容（无则 NULL）
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 反馈持久化：'up'/'down'，NULL = 未反馈
    feedback: Mapped[str | None] = mapped_column(String(8), nullable=True)
    feedback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
