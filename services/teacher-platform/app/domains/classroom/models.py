# coding: utf-8
"""classroom 域 models（02-ARCHITECTURE §4.6，G7 完整域）。

幂等核心（§14，靠 DB 唯一约束不靠检查后插入）：
  - classroom_responses  UNIQUE(activity_id, participant_id, attempt_no)
  - classroom_events     UNIQUE(session_id, seq)（durable 事件，PG 权威回放源）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# PG 生产用 JSONB；SQLite 测试环境退化为通用 JSON（B4 规范 §8 协议 fake 口径，列类型导入面不变）
JsonB = JSONB().with_variant(JSON(), "sqlite")
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> UUID:
    return uuid4()


class ClassroomSession(Base):
    __tablename__ = 'classroom_sessions'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    teacher_id: Mapped[UUID] = mapped_column()
    class_id: Mapped[UUID] = mapped_column()
    join_code: Mapped[str] = mapped_column(String(6), unique=True)   # 6 位数字，开课时生成（冲突重试）
    status: Mapped[str] = mapped_column(String(16), default='created')  # 三层状态机·Session 层
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('created','open','ended','archived')", name='ck_csession_status'),
        Index('ix_csession_teacher', 'teacher_id', 'created_at'),
    )


class ClassroomParticipant(Base):
    __tablename__ = 'classroom_participants'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    session_id: Mapped[UUID] = mapped_column(ForeignKey('classroom_sessions.id', ondelete='CASCADE'))
    student_name: Mapped[str] = mapped_column(String(64))   # PII：日志脱敏（G3）
    anon_id: Mapped[str] = mapped_column(String(32))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint('session_id', 'anon_id', name='uq_cparticipant_session_anon'),)


class ClassroomActivity(Base):
    __tablename__ = 'classroom_activities'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    session_id: Mapped[UUID] = mapped_column(ForeignKey('classroom_sessions.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(16))   # question/poll/game/photo_submit
    question_id: Mapped[UUID | None] = mapped_column(nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    ord: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default='draft')  # 三层状态机·Activity 层
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "kind IN ('question','poll','game','photo_submit') AND "
            "status IN ('draft','ready','pushed','collecting','locked','revealed','completed')",
            name='ck_cactivity_kind_status',
        ),
        Index('ix_cactivity_session', 'session_id', 'ord'),
    )


class ClassroomResponse(Base):
    __tablename__ = 'classroom_responses'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    activity_id: Mapped[UUID] = mapped_column(ForeignKey('classroom_activities.id', ondelete='CASCADE'))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey('classroom_participants.id', ondelete='CASCADE'))
    answer: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    image_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 幂等核心约束（G8）：重复提交返回首次结果
        UniqueConstraint('activity_id', 'participant_id', 'attempt_no', name='uq_cresponse_attempt'),
    )


# classroom_events 唯一定义在 app/db/models/platform.py（B3 迁移底座：BigInteger 自增 id +
# UNIQUE(session_id, seq)；真实库即此结构）。域内别名复用（rescue 整合裁决 2026-09-07）。
from app.db.models.platform import ClassroomEvents as ClassroomEvent  # noqa: E402


class ClassroomAiGeneration(Base):
    __tablename__ = 'classroom_ai_generations'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    activity_id: Mapped[UUID] = mapped_column(ForeignKey('classroom_activities.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(16))   # variation/quiz_gen
    status: Mapped[str] = mapped_column(String(24), default='not_requested')  # 三层状态机·AiGeneration 层
    content: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    verifier_result: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)  # G5 联动
    ai_run_id: Mapped[UUID | None] = mapped_column(nullable=True)   # ai_runs 台账（G6）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('not_requested','generating','verifying','awaiting_teacher','approved','pushed','rejected')",
            name='ck_caigen_status',
        ),
    )


class ClassroomSummary(Base):
    __tablename__ = 'classroom_summaries'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    session_id: Mapped[UUID] = mapped_column(unique=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    insight: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
