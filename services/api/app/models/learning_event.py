"""学习事件表模型（M2 迭代17 AI 管家化）

所有学习行为统一事件化，作为 AI 管家决策的输入源：
- quiz_judge 判分（答对/答错）
- error_recorded 错题收录 / error_reviewed 错题复习完成
- mastery_changed 掌握度变化
- streak_updated 打卡 / login 登录 / daily_plan_request 请求推荐

与既有埋点表 events 的区别：events 是"行为流水"（BIGSERIAL 主键、仅统计用）；
learning_events 是"管家决策输入"，带幂等键 + 处理状态 + 重试，驱动 butler orchestrator。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class LearningEvent(Base, TimestampMixin):
    """学习事件（管家决策输入源，纯流水）"""

    __tablename__ = "learning_events"
    __table_args__ = (
        Index("idx_learning_events_user_status", "user_id", "status", "created_at"),
        Index("idx_learning_events_type_time", "event_type", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)  # quiz_judge/error_recorded/...
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # practice/exam/chat/system
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 源记录 id（软引用，无 FK）
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")  # pending/processed/failed
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
