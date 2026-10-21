"""PPT Studio V2 持久化模型（R10）。

只落两件事：deck 全量 spec JSONB（唯一事实来源的持久化载体）与资产注册表。
- 表 名 前缀 ppt_v2_*，与旧 Deck/contents/generation 表完全隔离；
- 乐观并发由 revision 列承载（读写经 domain 层校验，禁止绕过 Patch 直改 revision）；
- 资产对象（MinIO）生命周期独立于本表；删除 deck 不进级联删对象（R10 不实现删除）。
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

# PG 生产用 JSONB；SQLite 测试环境退化为通用 JSON（与 B4/教材域同口径）
JsonB = JSONB().with_variant(JSON(), "sqlite")


def _uuid() -> UUID:
    return uuid4()


class PptV2DeckSpecRecord(Base):
    """deck 唯一事实来源的持久化载体：整份 TeachingDeckSpec JSON 存于 spec 列。

    revision 与 spec 内嵌 revision 必须一致（domain 层负责维护）；
    schema_version 冗余存储便于版本扫描与读前过滤。
    """

    __tablename__ = "ppt_v2_deck_specs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    school_id: Mapped[UUID | None] = mapped_column(nullable=True)  # NULL = 平台级
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_pptdeck_revision"),
        Index("ix_pptdeck_school_schema", "school_id", "schema_version"),
    )


class PptV2DeckAssetRecord(Base):
    """资产注册表：AssetRef 的持久化事实（不可变字段 + rights 守卫）。

    - object_key 指向 MinIO 不可变对象；任何派生必须新建行/新 key（禁止覆盖）；
    - rights_status 默认 QUARANTINED：未授权不得进入任何生产渲染/检索（与教材域同哲学）；
    - preserve_original 为 false 仅允许非 TEACHER_UPLOAD 类型（域校验保证）。
    """

    __tablename__ = "ppt_v2_assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID] = mapped_column(
        ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rights_status: Mapped[str] = mapped_column(Text, nullable=False, default="QUARANTINED")
    rights_evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    preserve_original: Mapped[bool] = mapped_column(nullable=False, default=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_meta: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)  # transform_history 等

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "kind IN ('TEXTBOOK','TEACHER_UPLOAD','MATH_RENDER','AI_IMAGE')",
            name="ck_pptasset_kind",
        ),
        CheckConstraint(
            "rights_status IN ('QUARANTINED','STAGED','ACTIVE')",
            name="ck_pptasset_rights",
        ),
        UniqueConstraint("deck_id", "object_key", name="uq_pptasset_deck_key"),
        Index("ix_pptasset_kind", "kind"),
    )


class PptV2SpecRevision(Base):
    """deck 修订快照（T24）。每次成功写 revision 都留一份全量快照，撤销以快照生成新 revision。

    不能只靠 change_log：change_log 记的是增量描述，撤销需要可重放的全量事实。
    """

    __tablename__ = "ppt_v2_spec_revisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID] = mapped_column(
        ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    spec_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    base_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("deck_id", "revision", name="uq_pptspecrev_deck_revision"),
        CheckConstraint("revision >= 1", name="ck_pptspecrev_revision"),
        CheckConstraint(
            "kind IN ('projection','patch','regen','revert','baseline')",
            name="ck_pptspecrev_kind",
        ),
        Index("ix_pptspecrev_deck", "deck_id"),
    )


class PptV2EditProposal(Base):
    """编辑提案（T24）：修改先是 proposed，apply 才写 revision+1（INV-4）。

    `expected_revision` 与 `operations` 同时入库，是为了在 409 时能给出
    "你的提案 vs 服务端现状" 的字段级 diff，而不是只回一个冲突码。
    """

    __tablename__ = "ppt_v2_edit_proposals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID] = mapped_column(
        ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="proposed")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="human")
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, nullable=False)
    refs: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    affected: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','applied','rejected','superseded','failed')",
            name="ck_pptedit_status",
        ),
        CheckConstraint("source IN ('human','ai','regen')", name="ck_pptedit_source"),
        CheckConstraint("expected_revision >= 0", name="ck_pptedit_expected"),
        Index("ix_pptedit_deck_status", "deck_id", "status"),
    )


class PptV2PageQa(Base):
    """逐页 QA 结果（T24）：按 (deck, spec_revision, page_no) 收敛，重试只覆盖目标页。

    落库而不是只回给前端，是因为"局部重试"必须能证明其它页的上次结论没被顺手改掉。
    """

    __tablename__ = "ppt_v2_page_qa"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID] = mapped_column(
        ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"), nullable=False
    )
    spec_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    defects: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    detector_version: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[UUID | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("deck_id", "spec_revision", "page_no", name="uq_pptqa_deck_rev_page"),
        CheckConstraint("page_no >= 1", name="ck_pptqa_page_no"),
        CheckConstraint("status IN ('pass','fail','unknown')", name="ck_pptqa_status"),
    )
