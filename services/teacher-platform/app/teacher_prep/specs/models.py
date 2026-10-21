"""R20 双 Spec 投影事实表（8100 权威，前缀 tp_）。

- `tp_lesson_plan_specs`：LessonPlanSpec 事实（body 为 LessonPlanSpec 权威 JSON；
  `approved_blueprint_revision` / `context_snapshot_hash` 显式冗余列，
  「双 Spec 的 approved blueprint/context hash 一致」验收锚点；
  revision 乐观并发 + body 内嵌 revision 双写，编辑历史在 body.change_log）。
- `tp_spec_projections`：投影审计/幂等日志（kind=lesson_plan|deck）。
  `projection_hash` = 投影输入规范化 hash；UNIQUE(blueprint_id, kind, projection_hash)
  保证同输入重试 → 重放（幂等），不同输入 → 新投影。
- `tp_lesson_plan_revisions` / `tp_lesson_plan_edit_proposals`（T24/S2.2）：教案修订快照与
  编辑提案（先提案后落库 INV-4；revert 以快照生成新 revision INV-5）。

SQLite（测试）/ PG（生产）双方言兼容，做法同 teacher_prep/models.py。
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

SPEC_KINDS = ("lesson_plan", "deck")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class LessonPlanSpecRow(Base):
    """LessonPlanSpec 事实：挂在已确认蓝图下（8100 权威，DECISIONS §2）。"""

    __tablename__ = "tp_lesson_plan_specs"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_tp_lessonplan_revision"),
        CheckConstraint("purpose IN ('lesson_plan')", name="ck_tp_lessonplan_purpose"),
        UniqueConstraint("blueprint_id", "revision", name="uq_tp_lessonplan_blueprint_rev"),
        Index("ix_tp_lessonplan_blueprint", "blueprint_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="lesson_plan")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    approved_blueprint_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    context_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    lineage: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SpecProjectionRow(Base):
    """双 Spec 投影审计/幂等日志（不存正文，只存锚点 + 血缘）。"""

    __tablename__ = "tp_spec_projections"
    __table_args__ = (
        CheckConstraint("kind IN ('lesson_plan','deck')", name="ck_tp_spec_proj_kind"),
        UniqueConstraint(
            "blueprint_id", "kind", "projection_hash",
            name="uq_tp_spec_proj_bp_kind_hash",
        ),
        Index("ix_tp_spec_proj_session", "session_id"),
        Index("ix_tp_spec_proj_blueprint", "blueprint_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    blueprint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_blueprints.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    spec_target_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    projection_hash: Mapped[str] = mapped_column(Text, nullable=False)
    approved_blueprint_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    context_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    lineage: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LessonPlanSpecRevisionRow(Base):
    """教案修订快照（T24/S2.2）：每次成功 apply/regen/revert 落一条，禁止原地改写历史。

    - `body` 为该 revision 的完整 LessonPlanSpec JSON（撤销/revert 以快照生成**新** revision，不删历史）；
    - `body_sha256` = 规范化 body hash，供 INV-2 局部性与篡改检测逐字节比对；
    - UNIQUE(spec_id, revision) 是乐观并发的落库兜底（domain 层已挡一次，防并发双写同一 revision）。
    """

    __tablename__ = "tp_lesson_plan_revisions"
    __table_args__ = (
        UniqueConstraint("spec_id", "revision", name="uq_tp_lessonplan_rev_spec_rev"),
        CheckConstraint("revision >= 1", name="ck_tp_lessonplan_rev_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    spec_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_lesson_plan_specs.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # projection | patch | regen | revert | baseline
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    body_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LessonPlanEditProposalRow(Base):
    """教案编辑提案（T24/S2.2，INV-4 先提案后落库）：proposed → applied/rejected/superseded/failed。

    - `operations` 为 LessonPlanPatchOp 列表（JSONB）；`affected` 为 AffectedBlockSet（INV-2 声明面）；
    - `refs` 存 regen 显式引用（blueprint/context/prompt/evidence），只引用不改写 Blueprint 事实；
    - UNIQUE(idempotency_key)：同一客户端请求重试只留一行（NULL 表示不参与幂等去重）；
    - `applied_revision` 只在 status=applied 时填写，是 proposal 与 revision 快照的锚点。
    """

    __tablename__ = "tp_lesson_plan_edit_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','applied','rejected','superseded','failed')",
            name="ck_tp_lessonplan_prop_status",
        ),
        UniqueConstraint("idempotency_key", name="uq_tp_lessonplan_prop_idem"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    spec_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_lesson_plan_specs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="proposed")
    source: Mapped[str] = mapped_column(Text, nullable=False)  # human | ai | regen
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)  # regen 的自然语言指令
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, nullable=False)
    refs: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    affected: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


__all__ = [
    "LessonPlanSpecRow",
    "LessonPlanSpecRevisionRow",
    "LessonPlanEditProposalRow",
    "SpecProjectionRow",
    "SPEC_KINDS",
]
