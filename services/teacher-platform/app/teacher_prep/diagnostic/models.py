"""R18 学前诊断任务事实表（8100 权威，前缀 tp_）。

设计约束（DECISIONS.md §5/§6，与 teacher_prep/models.py 同构）：
- 8100 是诊断任务草稿/发布事实的权威；8000 assignments.id 经 provision 列承载映射；
- `body` 为 DiagnosticDraft（教师可编辑草稿）权威 JSON；发布时映射为 R13
  PreClassDiagnosticTask 跨端契约（学生端零页面改造，不做学生 UI/迁移）；
- `revision` 与 DiagnosticRevisionRow.revision_no 一一对应（同事务推进），
  每个版本是不可变快照（body + 裁决状态），confirm/edit/skip 均产生新版本；
- 状态转换只走 state_machines 的 diagnostic 转移表（新增，见 orchestrator）；
- `pending_action_id` 引用运行时原语表 pending_actions（非 ORM，按约定一致）；
- SQLite（测试）/ PG（生产）双方言兼容，做法同 reliability/models.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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

# 诊断任务状态（教师侧状态机；published/skipped 为终态）
DIAGNOSTIC_PROPOSAL, DIAGNOSTIC_DRAFT, DIAGNOSTIC_PUBLISHED, DIAGNOSTIC_SKIPPED = (
    "proposal", "draft", "published", "skipped",
)

DIAGNOSTIC_TRANSITIONS: dict[str, tuple[str, ...]] = {
    DIAGNOSTIC_PROPOSAL: (DIAGNOSTIC_DRAFT, DIAGNOSTIC_PUBLISHED, DIAGNOSTIC_SKIPPED),
    DIAGNOSTIC_DRAFT: (DIAGNOSTIC_PUBLISHED, DIAGNOSTIC_SKIPPED),
    DIAGNOSTIC_PUBLISHED: (),
    DIAGNOSTIC_SKIPPED: (),
}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class DiagnosticTaskRow(Base):
    """学前诊断任务事实：挂在会话/已确认蓝图下的教师侧诊断草稿与发布事实。

    - `status`：proposal（LLM 提案）→ draft（教师编辑）→ published / skipped（终态）；
    - `body`：DiagnosticDraft 权威 JSON（教师可编辑；发布时映射为跨端契约）；
    - `provision`：8000 assignments.id 映射快照（发布成功后写入）；
    - `estimated_duration_minutes`：5–10 分钟硬约束（DB CHECK 兜底）。
    """

    __tablename__ = "tp_diagnostic_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposal','draft','published','skipped')",
            name="ck_tp_diag_tasks_status",
        ),
        CheckConstraint("revision >= 1", name="ck_tp_diag_tasks_revision"),
        CheckConstraint(
            "estimated_duration_minutes BETWEEN 5 AND 10",
            name="ck_tp_diag_tasks_duration",
        ),
        Index("ix_tp_diag_tasks_session", "session_id"),
        Index("ix_tp_diag_tasks_blueprint", "blueprint_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default=DIAGNOSTIC_PROPOSAL)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)

    # 高风险发布（PendingAction 引用 + 8000 映射快照）
    pending_action_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    provision: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skipped_by: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DiagnosticRevisionRow(Base):
    """诊断任务内容不可变版本快照（同 BlueprintRevisionRow 语义）。

    - `revision_no` 与任务 revision 同步递增（服务同事务写）；
    - `verdict`：该版本裁决快照（proposal/published/skipped），审计/回放用；
    - `source`：内容来源（ai_suggestion=LLM 提案，teacher=教师，system=系统）。
    """

    __tablename__ = "tp_diagnostic_revisions"
    __table_args__ = (
        CheckConstraint("revision_no >= 1", name="ck_tp_diag_revisions_no"),
        CheckConstraint(
            "verdict IN ('proposal','draft','published','skipped')",
            name="ck_tp_diag_revisions_verdict",
        ),
        UniqueConstraint("diagnostic_id", "revision_no", name="uq_tp_diag_revisions_diag_no"),
        Index("ix_tp_diag_revisions_diag", "diagnostic_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    diagnostic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_diagnostic_tasks.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False, default=DIAGNOSTIC_PROPOSAL)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="ai_suggestion")
    actor_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    parent_revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "DiagnosticTaskRow",
    "DiagnosticRevisionRow",
    "DIAGNOSTIC_PROPOSAL",
    "DIAGNOSTIC_DRAFT",
    "DIAGNOSTIC_PUBLISHED",
    "DIAGNOSTIC_SKIPPED",
    "DIAGNOSTIC_TRANSITIONS",
]
