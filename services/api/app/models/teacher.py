"""M3 教师端数据模型（ADR-M3-001）

新增主表：
- teaching_artifacts：教师 AI 产物（教案/课件/题集/建议/讲解/预处理/文档），版本化 + 状态机
- actionable_insights：班级可行动洞察（evidence + recommended_actions）
- teacher_actions：教师正式写操作审计（确认/发布/改分/应用洞察/课堂控制），幂等
- teacher_tasks：异步任务（课件渲染/大文件预处理），queued|running|succeeded|failed|cancelled

扩展既有表：
- assignments：新增 client_assignment_id / source_artifact_id
- submission_items：新增建议分/终评分/教师反馈/确认信息/needs_review

安全纪律：
- 不保存密钥、完整敏感原文、SQL、路径与无关学生身份；审计只存 digest/脱敏摘要。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class TeachingArtifact(Base, TimestampMixin, SoftDeleteMixin):
    """教师端 AI 产物，版本化 + 状态机（draft|confirmed|published|archived）。

    version 从 1 起；同一逻辑产物（logical_id 相同）内版本唯一。
    修改已确认/已发布产物 → 生成新版本 draft（仍共享 logical_id），不原地篡改。
    """

    __tablename__ = "teaching_artifacts"
    __table_args__ = (
        UniqueConstraint("logical_id", "version", name="uq_teaching_artifact_logical_version"),
        Index("ix_teaching_artifacts_owner", "owner_id"),
        Index("ix_teaching_artifacts_class", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    logical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    artifact_type: Mapped[str] = mapped_column(String(40))
    # lesson_plan | slide_deck | quiz_set | grading_suggestion | explanation | preprocess | document
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id"), nullable=True
    )
    scene: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|confirmed|published|archived
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list)
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    engine: Mapped[str] = mapped_column(String(20), default="local")  # local|model_router|xingchen|zhiwen
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionableInsight(Base, TimestampMixin):
    """班级可行动洞察（数据由确定性 SQL 聚合，LLM 只润色 summary/action，不编造 evidence）。"""

    __tablename__ = "actionable_insights"
    __table_args__ = (
        Index("ix_actionable_insights_class", "class_id"),
        Index("ix_actionable_insights_kind_status", "kind", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # submission_trend|error_cluster|low_mastery|review_backlog|prep_gap
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    recommended_actions: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|applied|dismissed|expired
    source: Mapped[str] = mapped_column(String(40), default="local_aggregation")
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TeacherAction(Base, TimestampMixin):
    """教师正式写操作审计（确认/发布/改分/应用洞察/课堂控制）。

    只存变更前后 digest 与脱敏摘要，不保存密钥/完整敏感原文。
    idempotency_key 唯一（部分唯一索引）；重放返回首次结果。
    """

    __tablename__ = "teacher_actions"
    __table_args__ = (
        # 唯一索引允许多个 NULL：仅对真实幂等键保证唯一，无键行不受约束
        Index("uq_teacher_actions_idem", "idempotency_key", unique=True),
        Index("ix_teacher_actions_teacher", "teacher_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id"), nullable=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action_type: Mapped[str] = mapped_column(String(40))
    # artifact.confirm | assignment.publish | grade.confirm | insight.apply | classroom.mode.set
    client_request_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    before_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class ClassroomMode(Base):
    """班级课堂模式的持久状态；每个班级最多一行。"""

    __tablename__ = "classroom_modes"

    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id"), primary_key=True
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TeacherTask(Base):
    """教师异步任务（课件渲染/大文件预处理等无法同步完成的业务工作）。"""

    __tablename__ = "teacher_tasks"
    __table_args__ = (Index("ix_teacher_tasks_owner_status", "owner_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id"), nullable=True
    )
    capability: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|failed|cancelled
    progress: Mapped[int] = mapped_column(Integer, default=0)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teaching_artifacts.id"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
