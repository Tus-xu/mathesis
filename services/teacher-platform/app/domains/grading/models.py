# coding: utf-8
"""grading 域 models（02-ARCHITECTURE §4.5：批改三视图）。

幂等：confirm-cluster 以 Idempotency-Key + 批量绑定全量成员（重复确认不重复写）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# PG 生产用 JSONB；SQLite 测试环境退化为通用 JSON（B4 规范 §8 协议 fake 口径，列类型导入面不变）
JsonB = JSONB().with_variant(JSON(), "sqlite")
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> UUID:
    return uuid4()


class GradingAssignment(Base):
    __tablename__ = 'grading_assignments'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(256))
    class_id: Mapped[UUID] = mapped_column()
    total: Mapped[int] = mapped_column(Integer, default=0)
    submitted: Mapped[int] = mapped_column(Integer, default=0)
    graded: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())


class GradingQuestion(Base):
    __tablename__ = 'grading_questions'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    assignment_id: Mapped[UUID] = mapped_column(ForeignKey('grading_assignments.id', ondelete='CASCADE'))
    kp_code: Mapped[str] = mapped_column(String(64))
    stem_latex: Mapped[str] = mapped_column(Text)
    error_rate: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)  # ≥0.3 触发变式（M3 数据面）
    # 2026-10-05 批改工作台真实化（trial_002）：题号 / 满分 / 题干配图（V3GradingQuestion 契约）
    q_no: Mapped[int] = mapped_column(Integer, default=1)
    full_score: Mapped[float] = mapped_column(Numeric(5, 1), default=10)
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # 题干配图（MinIO key，teacher-photos）
    reference_answer: Mapped[str | None] = mapped_column(Text, nullable=True)  # 参考答案（聚类 kind 判定依据）


class GradingAnswer(Base):
    __tablename__ = 'grading_answers'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    assignment_id: Mapped[UUID] = mapped_column(ForeignKey('grading_assignments.id', ondelete='CASCADE'))
    question_id: Mapped[UUID] = mapped_column(ForeignKey('grading_questions.id', ondelete='CASCADE'))
    student_name: Mapped[str] = mapped_column(String(64))     # PII：日志脱敏白名单外（G3）
    image_key: Mapped[str] = mapped_column(String(256))       # 作答原图（R8 原图在场）
    cluster_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # 2026-10-05 批改工作台真实化（trial_002）：文本作答 + 终审定分
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 文本作答（无照片时的真实作答载体）
    score: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)  # 教师终审定分

    __table_args__ = (Index('ix_ganswer_assignment', 'assignment_id', 'question_id'),)


class GradingCluster(Base):
    __tablename__ = 'grading_clusters'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    question_id: Mapped[UUID] = mapped_column(ForeignKey('grading_questions.id', ondelete='CASCADE'))
    summary: Mapped[str] = mapped_column(Text, default='')
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default='wrong')  # correct/partial/wrong/blank
    tag: Mapped[str | None] = mapped_column(String(16), nullable=True)  # V3ErrorTag
    members: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, default=list)  # [{answer_id,...}] 绑定面
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
