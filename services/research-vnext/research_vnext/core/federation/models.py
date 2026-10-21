"""RV06 · 检索 trace / cost 持久模型。

**只存指纹与标识，不存原文**：
- `query_hash` 而不是 query 文本；
- `result_ids` 而不是 snippet；
- `provider_status` / `warnings` 是机读 code，不含内容。

这样 trace 可以放心被查询、统计与对账，而不会变成一份"检索内容副本"。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

#: 降低误读风险：脱敏口径变更时必须换版本号，便于事后判断历史行按哪套规则写入。
REDACTION_VERSION = "rv06-redaction-v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RetrievalTraceRow(Base):
    """一次 federated 检索的审计行（同 tenant + 同 query_hash 幂等）。"""

    __tablename__ = "research_retrieval_trace"
    __table_args__ = (
        UniqueConstraint("tenant_id", "query_hash", name="uq_retrieval_trace_tenant_query"),
        Index("ix_retrieval_trace_trace_id", "trace_id"),
        Index("ix_retrieval_trace_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    provider_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    provider_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    degraded_providers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    result_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collapsed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflicts_kept: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    token_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    redaction_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default=REDACTION_VERSION
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = ["RetrievalTraceRow", "REDACTION_VERSION"]
