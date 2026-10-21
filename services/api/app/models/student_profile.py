"""学生画像表模型（M2 迭代17 AI 管家化）

AI 管家计算并缓存的学生长期画像：
- tags：AI 生成的学生标签（如"函数扎实""导数薄弱"）
- weak_point_rank：薄弱点排名 [{kp_code, kp_name, mastery, trend}]
- learning_style：学习风格（practice/visual/auditory...）
- current_stage：当前学习阶段
- profile_card：学情画像卡文本缓存（复用 learning_profile 聚合结果落库，供注入 system prompt）

与 learning_profile service 的关系：service 负责聚合计算 + Redis 短缓存（60s）；
本表作为"落库的画像"由 AI 管家异步更新，跨会话稳定复用。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class StudentProfile(Base, TimestampMixin):
    """学生画像（单行/用户，无软删）"""

    __tablename__ = "student_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    school_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")  # AI 学生标签
    weak_point_rank: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )  # [{kp_code,kp_name,mastery,trend}]
    learning_style: Mapped[str] = mapped_column(String(16), nullable=False, server_default="practice")
    current_stage: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    profile_card: Mapped[str | None] = mapped_column(Text, nullable=True)  # 画像卡文本缓存
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
