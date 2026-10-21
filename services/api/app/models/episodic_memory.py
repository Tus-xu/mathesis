"""情景记忆模型

学生长期记忆（mem0 简化版）：chat 轮次结束后异步提取学习事实写库，
上下文装配时注入 P6 槽位（详见 kernel/memory.py 与 kernel/context.py）。

注意：embedding 允许 NULL——embedding 服务不可用时不阻塞写库，
读路径降级为"最近 + kind 优先级"排序（不走向量检索）。
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class EpisodicMemory(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "episodic_memories"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # 记忆类型：weak_kp（常错知识点）/ preference（讲解偏好）/ goal（学习目标）/ note（其他）
    kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default="note")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 来源：chat（对话提取）/ quiz（测验）/ manual（手工）等
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="chat")
    # 来源会话（软引用不建 FK：记忆寿命可能长于会话本身）
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    # 向量尽力而为：embedding 服务不可用时落 NULL，读路径自动降级
    embedding: Mapped[list | None] = mapped_column(Vector(1024), nullable=True)
    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    kp_ids: Mapped[list] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
