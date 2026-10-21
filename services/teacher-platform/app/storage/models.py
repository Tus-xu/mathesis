"""产物投递事实（T24）：artifact 注册 / 下载票据 / 下载审计。

为什么单独一个命名空间：artifact 与下载是"产物投递"这一类关注点，deck（ppt_v2）
与教案（teacher_prep）都要用；放进任何一侧都会让另一侧反向依赖。

不变量：
- 字节在对象存储，本表只存 locator/hash/血缘/ACL —— 浏览器永远拿不到 bucket/key；
- 票据短时、单次使用、绑定 actor + artifact + ACL epoch；
- 每次 redeem 都写审计行（allow 与 deny 都写），撤权/过期留下的是"被拒绝"的证据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JsonB = JSONB().with_variant(JSON(), "sqlite")


def _uuid() -> UUID:
    return uuid4()


class TeacherArtifact(Base):
    """产物信封：platform_contracts.artifact.ArtifactRef 的持久化载体。

    `content_sha256` 是唯一的完整性锚：下载前重新读字节并复算，不一致即拒绝投递。
    """

    __tablename__ = "teacher_artifacts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    owner_id: Mapped[UUID | None] = mapped_column(nullable=True)
    spec_kind: Mapped[str] = mapped_column(Text, nullable=False)
    spec_id: Mapped[str] = mapped_column(Text, nullable=False)
    spec_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_lineage: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    acl: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    retention: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    qa_status: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    acl_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("object_key", name="uq_teacher_artifact_object_key"),
        CheckConstraint(
            "kind IN ('pptx','pdf','docx','markdown','qa_json')", name="ck_teacher_artifact_kind"
        ),
        CheckConstraint(
            "spec_kind IN ('teaching_deck','lesson_plan')", name="ck_teacher_artifact_spec_kind"
        ),
        CheckConstraint("spec_revision >= 1", name="ck_teacher_artifact_spec_revision"),
        Index("ix_teacher_artifact_spec", "spec_kind", "spec_id"),
        Index("ix_teacher_artifact_owner", "owner_id"),
    )


class TeacherArtifactTicket(Base):
    """短时下载票据。`acl_epoch` 记录签发时的 epoch，撤权即提升 epoch 使旧票立即失效。"""

    __tablename__ = "teacher_artifact_tickets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    ticket: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acl_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_teacher_ticket_artifact", "artifact_id"),)


class TeacherArtifactDownloadAudit(Base):
    """下载审计：allow / deny 都留痕（撤权与过期必须留下被拒绝的证据）。"""

    __tablename__ = "teacher_artifact_download_audit"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    ticket: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("decision IN ('allow','deny')", name="ck_teacher_dl_audit_decision"),
        Index("ix_teacher_dl_audit_artifact", "artifact_id"),
    )
