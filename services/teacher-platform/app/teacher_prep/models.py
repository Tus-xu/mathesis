"""R14 Teacher Prep 业务事实表（8100 权威，前缀 tp_）。

设计约束：
- 8100 是 TeachingSession / TeachingBlueprint 的事实权威（DECISIONS.md §2）；
- 本命名空间与 `teacher_prep_v2`（跨端契约+适配器，R13 边界守卫禁止 ORM）刻意分离：
  实体事实层可持 ORM；契约/适配层零 ORM（tests/contract/test_teacher_prep_v2_boundary.py 不扫描本目录）。
- teacher/class/course/schedule 为稳定 UUID 引用：teacher/class 事实权威在 8000
  （这里是 8100 投影/引用列），course/schedule 待后续业务轮建表（本轮仅引用不施 FK）；
- ContextSnapshot/PromptRelease/AIExecutionRun/PendingAction 指向运行时原语表
  （teacher_session_contexts / prompt_releases / ai_runs / pending_actions），
  这些表由 raw-SQL store 持有（非 ORM），故引用列为 UUID 无 FK，按约定一致；
- 命令审计与事件 lineage 与业务状态**同事务**写（service 持有 session，见 service.py）；
- SQLite（测试）/ PG（生产）双方言兼容，做法同 reliability/models.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JsonB = JSONB().with_variant(JSON(), "sqlite")
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TeachingSessionRow(Base):
    """TeachingSession 事实：教师备课会话（每班可多个，各自独立生命周期）。

    - `revision`：乐观并发计数器（任何状态/元数据变更 +1；expected_revision 校验点）；
    - `last_event_seq`：该会话已发出的最大事件序号（service 在行锁内推进，events UNIQUE(session_id,seq) 兜底）；
    - 高风险命令执行前必须对 `class_id` 重新裁决 8000 成员关系（service 层）。
    """

    __tablename__ = "tp_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','cancelled','archived')",
            name="ck_tp_sessions_status",
        ),
        CheckConstraint("revision >= 1", name="ck_tp_sessions_revision"),
        CheckConstraint("last_event_seq >= 0", name="ck_tp_sessions_last_event_seq"),
        Index("ix_tp_sessions_teacher_status", "teacher_id", "status"),
        Index("ix_tp_sessions_class", "class_id"),
        Index("ix_tp_sessions_teacher_class", "teacher_id", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    teacher_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    # E-3 可选班级：NULL = 未绑定班级（会话可正常备课；班级域能力显式守卫降级）
    class_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    course_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)

    # 运行时原语引用（约定一致；表非 ORM 所以无 FK）
    context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    prompt_release_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    pending_action_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)

    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_command_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TeachingBlueprintRow(Base):
    """TeachingBlueprint 事实：挂在会话下的教学蓝图。

    - `revision`：与 BlueprintRevision.revision_no 一一对应（同事务推进）；
      每个版本是不可变快照（body + 裁决状态），confirm/reject/propose 均产生新版本；
    - 状态转换只走 state_machines.assert_transition（LLM 载荷只能落 proposal）。
    """

    __tablename__ = "tp_blueprints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposal','confirmed','rejected')",
            name="ck_tp_blueprints_status",
        ),
        CheckConstraint("revision >= 1", name="ck_tp_blueprints_revision"),
        Index("ix_tp_blueprints_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="proposal")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)

    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BlueprintRevisionRow(Base):
    """BlueprintRevision 事实：蓝图内容不可变版本快照。

    - `revision_no` 与蓝图 revision 同步递增（服务同事务写）；
    - `verdict`：该版本最终裁决快照（proposal/confirmed/rejected），审计/回放用；
    - `source`：内容来源（ai_suggestion=LLM 提案，teacher=教师，system=系统）；与 actor 分离。
    """

    __tablename__ = "tp_blueprint_revisions"
    __table_args__ = (
        CheckConstraint("revision_no >= 1", name="ck_tp_bp_revisions_no"),
        CheckConstraint(
            "verdict IN ('proposal','confirmed','rejected')", name="ck_tp_bp_revisions_verdict"
        ),
        UniqueConstraint("blueprint_id", "revision_no", name="uq_tp_bp_revisions_blueprint_no"),
        Index("ix_tp_bp_revisions_blueprint", "blueprint_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False, default="proposal")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="ai_suggestion")
    actor_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    parent_revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionCommandRow(Base):
    """SessionCommand 审计：所有写命令（含失败 attempt）逐条入库，幂等键+指纹可对账。

    - 同幂等键并发由 reliability_idempotency 五元键表归管（service 引用）；
    - 业务状态、命令行、事件行同事务落库（service 持有调用方 session）。
    """

    __tablename__ = "tp_session_commands"
    __table_args__ = (
        CheckConstraint(
            "result_status IN ('applied','replayed','conflict','denied','invalid_transition','authority')",
            name="ck_tp_session_commands_result",
        ),
        Index("ix_tp_commands_session_created", "session_id", "created_at"),
        Index("ix_tp_commands_idem", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_blueprints.id", ondelete="SET NULL"), nullable=True
    )
    command_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    # E-3 可选班级：审计命令行的班级可为 NULL（未绑定班级会话的命令）
    class_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expected_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_status: Mapped[str] = mapped_column(Text, nullable=False, default="applied")
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionEventRow(Base):
    """SessionEvent：会话内的 durable 事件（lineage：parent_event_id 指向前一事件，链成全序）。

    - `seq` 会话内全局递增（service 在会话行锁内推进 + UNIQUE 兜底）；
    - 每个 applied 命令至少产生 command.applied 链头事件，业务事件以它为 parent；
    - 事件不可变（追加写），回放/对账只读。
    """

    __tablename__ = "tp_session_events"
    __table_args__ = (
        CheckConstraint("seq >= 1", name="ck_tp_session_events_seq"),
        UniqueConstraint("session_id", "seq", name="uq_tp_session_events_session_seq"),
        Index("ix_tp_session_events_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    command_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_session_commands.id", ondelete="SET NULL"), nullable=True
    )
    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_blueprints.id", ondelete="SET NULL"), nullable=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)
    parent_event_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
