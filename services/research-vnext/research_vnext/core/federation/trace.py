"""RV06 · 检索 trace / cost 落库（脱敏 + 幂等）。

写入纪律：
- 只写 hash 与 ID（`query_hash` / `result_ids` / `provider_ids`），**不写 query 原文**，
  也不写 snippet —— trace 不是内容副本；
- 同 tenant + 同 `query_hash` 幂等：重复查询不新增行，返回既有 trace，
  这样"同一查询重放"不会把成本统计灌水；
- 读取按租户校验：跨租户读 trace 直接 403。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..api.deps import (
    READ_CAPABILITY,
    AccessContext,
    check_revocation,
    require_capability,
)
from ..domain.errors import DomainError, TenantMismatch
from .contracts import FederatedQuery
from .models import REDACTION_VERSION, RetrievalTraceRow
from .orchestrator import FederatedResponse


class RetrievalTraceNotFound(DomainError):
    code = "RETRIEVAL_TRACE_NOT_FOUND"
    http_status = 404


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def record_retrieval_trace(
    db: Session,
    ctx: AccessContext,
    query: FederatedQuery,
    response: FederatedResponse,
    *,
    token_in: int = 0,
    token_out: int = 0,
    cost: float = 0.0,
) -> tuple[RetrievalTraceRow, bool]:
    """落一条检索 trace。返回 (row, created)。"""
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    if not isinstance(ctx, AccessContext):  # pragma: no cover - 类型自证
        raise DomainError("record_retrieval_trace 需要 AccessContext")

    existing = db.execute(
        select(RetrievalTraceRow).where(
            RetrievalTraceRow.tenant_id == ctx.tenant_id,
            RetrievalTraceRow.query_hash == query.query_hash,
        )
    ).scalars().first()
    if existing is not None:
        return existing, False

    row = RetrievalTraceRow(
        id=uuid.uuid4(),
        trace_id=response.trace_id,
        tenant_id=ctx.tenant_id,
        project_id=_as_uuid(query.project_id),
        actor_id=str(ctx.actor_id),
        session_id=str(query.session_id or ""),
        query_hash=query.query_hash,
        purpose=query.purpose,
        strategy=query.strategy,
        prompt_version=query.prompt_version,
        provider_ids=list(query.provider_allowlist),
        provider_status={k: v.value for k, v in response.provider_status.items()},
        degraded_providers=list(response.degraded_providers),
        result_ids=[r.result_id for r in response.results],
        result_count=len(response.results),
        collapsed=response.collapsed,
        conflicts_kept=response.conflicts_kept,
        status=response.status.value,
        warnings=list(response.warnings),
        token_in=int(token_in),
        token_out=int(token_out),
        cost=float(cost),
        latency_ms=int(response.latency_ms),
        redaction_version=REDACTION_VERSION,
    )
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raced = db.execute(
            select(RetrievalTraceRow).where(
                RetrievalTraceRow.tenant_id == ctx.tenant_id,
                RetrievalTraceRow.query_hash == query.query_hash,
            )
        ).scalars().first()
        if raced is not None:
            return raced, False
        raise
    return row, True


def trace_public_dict(row: RetrievalTraceRow) -> dict[str, Any]:
    """对外投影。**不含** query 原文与任何 snippet。"""
    return {
        "id": str(row.id),
        "trace_id": row.trace_id,
        "tenant_id": str(row.tenant_id),
        "project_id": str(row.project_id) if row.project_id else None,
        "query_hash": row.query_hash,
        "purpose": row.purpose,
        "strategy": row.strategy,
        "prompt_version": row.prompt_version,
        "provider_ids": list(row.provider_ids or []),
        "provider_status": dict(row.provider_status or {}),
        "degraded_providers": list(row.degraded_providers or []),
        "result_ids": list(row.result_ids or []),
        "result_count": row.result_count,
        "collapsed": row.collapsed,
        "conflicts_kept": row.conflicts_kept,
        "status": row.status,
        "warnings": list(row.warnings or []),
        "token_in": row.token_in,
        "token_out": row.token_out,
        "cost": row.cost,
        "latency_ms": row.latency_ms,
        "redaction_version": row.redaction_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "note": "trace 只存 hash 与 ID；不含 query 原文与 snippet",
    }


def get_trace(db: Session, ctx: AccessContext, trace_id: str) -> RetrievalTraceRow:
    """按 trace_id 取审计行。跨租户 → `TenantMismatch`（403）。"""
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    row = db.execute(
        select(RetrievalTraceRow).where(RetrievalTraceRow.trace_id == trace_id)
    ).scalars().first()
    if row is None:
        raise RetrievalTraceNotFound(f"检索 trace 不存在: {trace_id}")
    if row.tenant_id != ctx.tenant_id:
        raise TenantMismatch(
            "检索 trace 不属于当前 tenant",
            trace_tenant=str(row.tenant_id),
            actor_tenant=str(ctx.tenant_id),
        )
    return row


def list_traces(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[RetrievalTraceRow]:
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    q = select(RetrievalTraceRow).where(RetrievalTraceRow.tenant_id == ctx.tenant_id)
    if project_id is not None:
        q = q.where(RetrievalTraceRow.project_id == project_id)
    q = q.order_by(RetrievalTraceRow.created_at.desc()).limit(limit)
    return list(db.execute(q).scalars())


def trace_writer(db: Session, ctx: AccessContext):
    """返回可直接交给 `federated_search(trace_recorder=...)` 的回调。"""

    def _write(query: FederatedQuery, response: FederatedResponse) -> None:
        record_retrieval_trace(db, ctx, query, response)

    return _write


__all__ = [
    "RetrievalTraceNotFound",
    "record_retrieval_trace",
    "trace_public_dict",
    "get_trace",
    "list_traces",
    "trace_writer",
]
