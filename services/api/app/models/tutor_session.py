"""引导式解题会话模型（ADR-030/033 solver-then-guide）

一题一会话。plan 存完整的、经 self-consistency + SymPy 校验的参考解，
只作为引导链的隐藏上下文，绝不整体发给用户。
教学状态机（提示阶梯/判答推进）全部在代码侧，本表是其持久化载体。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin

# 会话状态
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_REVEALED = "revealed"
STATUS_DEGRADED = "degraded"
STATUS_ABANDONED = "abandoned"


class TutorSession(Base, TimestampMixin, SoftDeleteMixin):
    """引导式解题会话"""

    __tablename__ = "tutor_sessions"
    __table_args__ = (
        # 会话粘连查询：按 conversation_id 找 active 会话（热路径）
        Index(
            "idx_tutor_sessions_conv_active",
            "conversation_id",
            postgresql_where=text("status = 'active' AND deleted_at IS NULL"),
        ),
        Index("idx_tutor_sessions_user", "user_id", "updated_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    # 首条（触发）消息 ID，可空：assistant 消息在 skill 流结束后才落库
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="llm")
    # active / completed / revealed / degraded / abandoned
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=STATUS_ACTIVE)

    # {steps: [{assertion, reason}], final_answer, verified, solve_attempts,
    #  consistency, leak_blocked}
    plan: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    current_step: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    hint_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # 0~3
    attempts_on_step: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # {point, teach, bottom_out}
    hint_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    answer_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # 防提示滥用：给过提示后需先作答，再要提示只反问不升级
    awaiting_attempt: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
