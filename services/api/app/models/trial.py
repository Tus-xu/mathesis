"""体验测试留痕模型（作品验收：真实用户试用反馈 + 身份标注）

评审要求：至少 2 名真实用户（标注身份，如"XX学校 X 年级学生"）的试用反馈
或结构化测试记录。本表承载**结构化试用反馈**；行为留痕复用既有表
（conversations/messages/tutor_sessions/error_records/learning_events/events），
由 trial_router 聚合成"用户使用档案"，不复制任何行为数据。
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TrialFeedback(Base, TimestampMixin):
    """试用者结构化反馈（同一用户可多次提交，报告取最新一份）"""

    __tablename__ = "trial_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    # 身份标注（评审要求："XX学校 X 年级学生/教师"）——自填，不外键
    name: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    identity_label: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    school: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    grade: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    # 结构化评分：usability/usefulness/speed/accuracy/overall（1-5）
    ratings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    nps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-10 推荐值
    comment: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 自报试用时长
    allow_showcase: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )  # 是否允许作为评审案例展示
