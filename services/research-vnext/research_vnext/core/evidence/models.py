"""RV07 · EvidenceCard 持久模型（表 `research_evidence_card`）。

**为什么是新表而不是改 RV09–RV13 的 `evidence_card`**：

`evidence_card`（迁移 `0005`）是 RV09–RV13 的**引用位**投影：只有
`source/label/locator(文本)/snippet/asset_version/authorized` 七个业务列，
被 `domain_service.invalidate_evidence` 与 `claim` 重开判定使用。
RV07 需要的是**事实对象**（类型矩阵 + 结构化 locator + rights 策略 + ACL +
provenance + stale），把它塞进同一张表会同时打破两件事：
① RV12 的"失效即重开"语义（新列的非空约束会牵连既有写入路径）；
② 本轮的边界约定（只动 evidence/citation 域，不改 RV09–RV13 的表）。

因此 RV07 新建 `research_evidence_card`，并在 `EVIDENCE-TYPE-MATRIX.md`
登记两者的关系与后续合并路径（见 FINAL-GATE 限制 L-RV07-2）。

`proposal_hash` 唯一约束提供**幂等**：同一个 proposal 重复提交只落一行。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceCardV2Row(Base):
    """RV07 EvidenceCard 事实行。

    **列即契约**：`to_dict()` 的每个必需字段在这里都有对应列，
    没有"只在 JSONB 里存在"的必需字段 —— 这样审计可以直接用 SQL 对账。
    """

    __tablename__ = "research_evidence_card"
    __table_args__ = (
        # 幂等键：同租户内同 proposal → 同一行（不产生重复证据）
        UniqueConstraint("tenant_id", "proposal_hash", name="uq_evidence_card_proposal"),
        Index("ix_evidence_card_tenant_session", "tenant_id", "session_id"),
        Index("ix_evidence_card_tenant_project", "tenant_id", "project_id"),
        Index("ix_evidence_card_type", "tenant_id", "evidence_type"),
        Index("ix_evidence_card_run", "created_by_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    locator: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    locator_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="EXTERNAL")

    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snippet_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    structured_fact: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rights: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rights_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")

    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_run: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    confidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    limitations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    acl: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    stale_state: Mapped[str] = mapped_column(String(16), nullable=False, default="FRESH")
    stale_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    #: 来源内容指纹（stale 判定的锚点；与 provenance.source_hash 同源）
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 派生列（由类型矩阵决定），落库便于 SQL 过滤"可引用证据"
    citation_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    labels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = ["EvidenceCardV2Row"]
