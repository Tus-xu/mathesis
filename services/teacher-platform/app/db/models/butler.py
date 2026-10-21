# coding: utf-8
"""管家会话消息（跨会话记忆源）。

MIGRATION_REQUEST-002 三表（butler_sessions/messages/actions）的最小落地：
本表承载「跨会话记忆」数据来源——按 teacher 聚合最近 user 消息注入 system prompt。
表在运行时惰性创建（IF NOT EXISTS，与域表运行时自建模式一致），并注册进
models/__init__.py 供 Alembic autogenerate 未来收编。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ButlerMessageRow(Base):
    """一条管家对话消息（当前只落 user 消息即可支撑记忆；assistant 全文留待后续收编）。"""

    __tablename__ = "butler_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, nullable=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column("text", Text)  # 列名显式 text（text_ 仅作 Python 属性名）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
