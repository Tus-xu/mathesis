"""R20 版本化 ClassLearningProfile 事实表（8100 权威，前缀 tp_）。

设计约束（DECISIONS.md §5/§6，与 teacher_prep/models.py 同构）：
- `revision` 乐观并发 + `body`（VersionedClassLearningProfile 权威 JSON）；
- `computed_at`（聚合计算时间）与 `expires_at`（时效窗口终点）为时效列——
  旧 Profile 判定（freshness.stale）与 body 内嵌 freshness 双写一致；
- `coverage` / `warnings` 列冗余快照（与 body 内嵌证据质量一致，供查询过滤）；
- `status`：collecting → preview → published → archived（published 才可作为
  evidence 进入修订提案）；
- 每个版本是不可变快照（tp_class_learning_profile_revisions），publish/update
  均产生新版本；
- SQLite（测试）/ PG（生产）双方言兼容。
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

PROFILE_STATUSES = ("collecting", "preview", "published", "archived")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class ClassLearningProfileRow(Base):
    """班级学习画像事实：版本化 + 时效列 + 证据质量快照。

    - 每班一个 profile 实体（id 稳定），revision 随 publish/update 递增；
    - `expires_at`：时效窗口终点（NULL=未设上限）；超期 → freshness.stale。
    """

    __tablename__ = "tp_class_learning_profiles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('collecting','preview','published','archived')",
            name="ck_tp_profiles_status",
        ),
        CheckConstraint("revision >= 1", name="ck_tp_profiles_revision"),
        Index("ix_tp_profiles_class", "class_id"),
        Index("ix_tp_profiles_class_status", "class_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    class_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="collecting")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_window: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    coverage: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    warnings: Mapped[list[Any]] = mapped_column(JsonB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClassLearningProfileRevisionRow(Base):
    """Profile 内容不可变版本快照（同 BlueprintRevisionRow 语义）。

    - `revision_no` 与 profile revision 同步递增（服务同事务写）；
    - `verdict`：该版本裁决快照（collecting/preview/published/archived）。
    """

    __tablename__ = "tp_class_learning_profile_revisions"
    __table_args__ = (
        CheckConstraint("revision_no >= 1", name="ck_tp_profile_revisions_no"),
        CheckConstraint(
            "verdict IN ('collecting','preview','published','archived')",
            name="ck_tp_profile_revisions_verdict",
        ),
        UniqueConstraint("profile_id", "revision_no", name="uq_tp_profile_revisions_profile_no"),
        Index("ix_tp_profile_revisions_profile", "profile_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_class_learning_profiles.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False, default="collecting")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    actor_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    parent_revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "ClassLearningProfileRow",
    "ClassLearningProfileRevisionRow",
    "PROFILE_STATUSES",
]
