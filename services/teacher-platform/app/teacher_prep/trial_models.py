# coding: utf-8
"""体验测试留痕模型（作品验收：试用教师结构化反馈 + 身份标注）。

行为留痕复用既有表（plans / teacher_artifacts / ai_runs / butler_messages /
conversation_threads），本表只承载结构化反馈，由 trial_router 聚合成
"使用档案"，不复制任何行为数据。表名按 R14+ 惯例用 tp_ 前缀。
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 跨方言类型：与 app/teacher_prep/{conversation,diagnostic,profile,specs}/models.py 惯例一致。
# 裸 JSONB/UUID(PG 方言) 会让 SQLite 上的 Base.metadata.create_all 抛
# `SQLiteTypeCompiler has no attribute visit_JSONB`，整个 unit 测试集的
# 建表 fixture 连带失败（2026-09-14 真实回归，149 ERROR）。
JsonB = JSONB().with_variant(JSON(), "sqlite")


class TrialFeedbackRow(Base):
    """试用教师结构化反馈（同一用户可多次提交，报告取最新一份）"""

    __tablename__ = "tp_trial_feedback"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid(as_uuid=True), nullable=False, index=True
    )
    # 身份标注（评审要求："XX学校 X 年级 教师"）——自填
    name: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    identity_label: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    school: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    ratings: Mapped[dict] = mapped_column(JsonB, nullable=False, server_default="{}")
    nps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allow_showcase: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
