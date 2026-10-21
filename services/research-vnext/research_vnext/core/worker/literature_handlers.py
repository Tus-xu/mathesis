"""RV04 · literature.ingest 作业 handler（RV03 JobContract）。

`Handler = async (HandlerContext) -> HandlerOutcome`；provider 错误码原样透出，
供 job_service 做 retryable/permanent 分类（不在 HTTP request 内长时等待）。
"""

from __future__ import annotations

import uuid

from ..literature import service as lit_svc
from ..literature.crossref import CrossrefProvider
from ..literature.provider import LiteratureProviderError
from .runtime import HandlerContext, HandlerOutcome


async def literature_ingest_handler(ctx: HandlerContext) -> HandlerOutcome:
    payload = ctx.input_payload or {}
    try:
        tenant_id = uuid.UUID(str(payload.get("tenant_id") or ctx.tenant_id))
        actor_id = uuid.UUID(str(payload.get("actor_id") or ""))
        project_id = uuid.UUID(str(payload["project_id"]))
        query = str(payload.get("query") or "").strip()
    except (KeyError, ValueError) as e:
        return HandlerOutcome(
            error_code="INVALID_INPUT",
            error_message=f"literature.ingest 参数非法: {e}",
            failed_stages=["parse_input"],
        )
    if not query:
        return HandlerOutcome(
            error_code="INVALID_INPUT",
            error_message="query 必填",
            failed_stages=["parse_input"],
        )
    if ctx.stopped():
        return HandlerOutcome(
            error_code="CANCELLED",
            error_message="取消请求在安全点生效",
            failed_stages=["cancelled"],
        )

    limit = int(payload.get("limit") or 5)
    from ..api.deps import AccessContext
    from ..db.engine import session_scope

    access = AccessContext(
        actor_id=actor_id,
        tenant_id=tenant_id,
        workspace="research",
        domain="research-vnext",
        capabilities=["workspace.research.enter", "research.session.write"],
        resource_scopes=list(payload.get("resource_scopes") or []),
    )
    provider = CrossrefProvider()
    request_id = payload.get("request_id") or f"job-lit-{uuid.uuid4().hex[:12]}"
    await ctx.heartbeat()
    try:
        metas = provider.search(query, limit=limit, request_id=request_id)
    except LiteratureProviderError as e:
        # 原样透出 code，交给 RV03 classify_error（PROVIDER_* 可重试）
        return HandlerOutcome(
            error_code=e.code,
            error_message=e.message,
            failed_stages=["provider_search"],
            result={"retryable": e.retryable, "detail": e.detail},
        )

    outcomes: list[dict] = []
    for i, meta in enumerate(metas):
        if ctx.stopped():
            return HandlerOutcome(
                succeeded_stages=[f"ingest:{j}" for j in range(i)],
                failed_stages=["cancelled"],
                error_code="CANCELLED",
                error_message="取消请求在安全点生效",
                result={"partial": outcomes},
            )
        with session_scope() as db:
            row, outcome = lit_svc.ingest_metadata(db, access, meta, project_id=project_id)
            outcomes.append({"id": str(row.id), "doi": row.doi, "outcome": outcome})
        await ctx.report(stage=f"ingest:{i}", progress=int((i + 1) * 100 / max(len(metas), 1)))

    return HandlerOutcome(
        result={
            "provider": provider.name,
            "provider_version": provider.version,
            "request_id": request_id,
            "query": query,
            "ingested": outcomes,
        },
        succeeded_stages=[f"ingest:{j}" for j in range(len(outcomes))],
    )


def register_literature_jobs() -> None:
    from .runtime import register

    register("literature.ingest", literature_ingest_handler)
