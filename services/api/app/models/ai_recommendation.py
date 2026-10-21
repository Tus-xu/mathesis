"""AI 推荐记录表模型（M2 迭代17 AI 管家化）

存储 AI 管家生成的每日任务 / 复习提醒 / 资源推荐 / 路径步骤，
记录生成时间与学生点击反馈（accept/reject/skip），构成数据飞轮。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIRecommendation(Base, TimestampMixin):
    """AI 推荐结果（可被学生反馈，数据飞轮）"""

    __tablename__ = "ai_recommendations"
    __table_args__ = (Index("idx_ai_rec_user_kind_time", "user_id", "kind", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # daily_task/review_due/variant/resource/path_step
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # butler_daily_plan/butler_recommend/...
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")  # {items, reason, ...}
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 生成模型（质量回溯）
    user_feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)  # accept/reject/skip
    shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
