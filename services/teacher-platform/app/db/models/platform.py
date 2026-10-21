"""B3 平台基础表（users/classes/tasks）+ 事件/幂等/导出台账。

schema 依据 02-ARCHITECTURE §4.1/§4.2/§4.7。各域表由域所有者提交 model 文件（本文件不覆盖其域）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import Uuid as UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# PG 生产用 BIGSERIAL；SQLite 测试环境退化为 INTEGER 主键（SQLite 仅 INTEGER PRIMARY KEY 是 rowid 别名，
# BIGINT 主键无自增会导致 INSERT 缺 id 失败——B4 classroom 域写入即踩此坑）
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


class Users(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('teacher','admin')", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    school_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Classes(Base):
    __tablename__ = "classes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    grade: Mapped[str | None] = mapped_column(String(40), nullable=True)
    student_count: Mapped[int | None] = mapped_column(nullable=True)
    school_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class Tasks(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','done','failed','cancelled')", name="ck_tasks_status"),
        # 索引铁律：tasks(user_id, created_at desc)（02-ARCHITECTURE §4.9）
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    progress: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TaskEvents(Base):
    """durable 事件权威回放源（G4 双通道：Streams 裁剪后 PG 兜底）。

    UNIQUE(task_id, seq) —— 幂等靠 DB 唯一约束兜底（G8/红线 8），重发同 seq 被拒。
    """

    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_task_events_task_seq"),
        CheckConstraint("seq > 0", name="ck_task_events_seq"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    seq: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecords(Base):
    """REST 幂等（G8）：键 + 用户 主键，存首次响应，窗口内重放首结果。"""

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(200))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportJobs(Base):
    """导出任务台账（B3 §4.1，M2-B 启用；此处建表供迁移底座使用）。"""

    __tablename__ = "export_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deck_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    format: Mapped[str] = mapped_column(String(20))
    file_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClassroomEvents(Base):
    """课堂 durable 事件（G7/B3 传输层 + B4 状态机共用）：UNIQUE(session_id, seq)。"""

    __tablename__ = "classroom_events"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_classroom_events_session_seq"),
        CheckConstraint("seq > 0", name="ck_classroom_events_seq"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    seq: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
