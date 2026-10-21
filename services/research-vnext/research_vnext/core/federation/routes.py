"""RV06 · Knowledge Federation Gateway 路由。

只暴露**编排结果**，不暴露 provider 内部实现：
- `POST /federation/search` —— 一次统一只读检索（scope 由 AccessContext 派生）；
- `GET  /federation/providers` —— 五类规范目录（含 disabled 的 contract-only 两类）；
- `GET  /federation/traces` / `GET /federation/traces/{trace_id}` —— 检索审计（跨租户 403）。

维度过滤在**路由入口**完成：`evidence_types` 只允许收窄 provider allowlist，
不允许用它去"借"别的维度的字段。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..api.deps import AccessContext, load_access_context
from ..db.engine import session_scope
from ..domain.errors import DomainError
from ..federation import trace as trace_svc
from ..federation.catalog import catalog_snapshot, enabled_provider_names, get_spec
from ..federation.contracts import Budget, FederatedQuery
from ..federation.errors import FederationContractError
from ..federation.orchestrator import federated_search
from ..federation.providers.base import ProviderError
from ..federation.providers.registry import build_providers
from ..federation.scope import resolve_scope
from ..federation.types import EvidenceType
from ..observability.redaction import redact_mapping

federation_router = APIRouter(tags=["research-federation"])


class FederationSearchBody(BaseModel):
    query_text: str = ""
    # purpose / prompt_version 落 varchar(64)，且是受控标识而非自由文本：
    # 在入口就挡住超长与非白名单字符，避免"检索跑完之后才 500"。
    purpose: str = Field(default="literature_review", max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    prompt_version: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9._:-]*$")
    provider_allowlist: list[str] | None = None
    evidence_types: list[str] | None = None
    project_id: str | None = None
    school_id: str | None = None
    max_results: int = Field(default=10, ge=1, le=50)
    max_provider_calls: int = Field(default=4, ge=1, le=8)
    deadline_ms: int = Field(default=5000, ge=1, le=60_000)


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _error(status: int, code: str, message: str, detail: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": redact_mapping(detail or {})}},
    )


def _scope_ids(ctx: AccessContext, kind: str) -> set[str]:
    """从**网关签名**的 AccessContext 里取某类资源范围 id。"""
    return {
        str(s["id"])
        for s in ctx.resource_scopes
        if s.get("kind") == kind and s.get("id")
    }


def _resolve_project(ctx: AccessContext, requested: str | None) -> tuple[str | None, JSONResponse | None]:
    """project 维度只能来自 AccessContext，不能由请求体自行指定。

    AccessContext 由网关 HMAC 签名，其中的 `resource_scopes` 才是授权事实；
    `body.project_id` 只是"用户想查哪个 project"的**意向**。
    不校验就并进 scope，等于让调用方给自己扩权（本轮审查的 High 发现）。
    """
    allowed = _scope_ids(ctx, "project")
    if requested:
        if requested not in allowed:
            return None, _error(
                403,
                "PROJECT_SCOPE_VIOLATION",
                "请求的 project 不在当前 AccessContext 的 project scope 内",
                {"requested": requested},
            )
        return requested, None
    return (sorted(allowed)[0] if allowed else None), None


def _resolve_school(ctx: AccessContext, requested: str | None) -> tuple[str | None, JSONResponse | None]:
    """school 维度同理：AccessContext 目前**没有** school 字段，
    因此 school 只能来自签名 scope 里显式列出的条目；请求体不得凭空指定。"""
    allowed = _scope_ids(ctx, "school")
    if requested:
        if requested not in allowed:
            return None, _error(
                403,
                "SCHOOL_SCOPE_VIOLATION",
                "请求的 school 不在当前 AccessContext 的 school scope 内",
                {"requested": requested},
            )
        return requested, None
    return (sorted(allowed)[0] if allowed else None), None


def _resolve_allowlist(body: FederationSearchBody) -> tuple[list[str], JSONResponse | None]:
    allow = list(body.provider_allowlist) if body.provider_allowlist else list(enabled_provider_names())
    unknown = [n for n in allow if n not in {s["name"] for s in catalog_snapshot()}]
    if unknown:
        return [], _error(400, "UNKNOWN_PROVIDER", f"未知 provider: {unknown}")
    if body.evidence_types:
        try:
            wanted = {EvidenceType(x) for x in body.evidence_types}
        except ValueError as exc:
            return [], _error(400, "UNKNOWN_EVIDENCE_TYPE", str(exc))
        allow = [n for n in allow if get_spec(n).evidence_type in wanted]
    return allow, None


@federation_router.get("/federation/providers")
def list_providers(request: Request):
    """五类规范目录。**不需要对象级授权**：这里没有用户数据。"""
    _ctx(request)  # 仍要求合法 AccessContext（fail closed）
    return {
        "providers": catalog_snapshot(),
        "golden_path_enabled": list(enabled_provider_names()),
        "contract_only_disabled": [
            s["name"] for s in catalog_snapshot() if not s["enabled"]
        ],
        "note": "disabled provider 的 contract 仍在；被请求时返回 PROVIDER_DISABLED",
    }


@federation_router.post("/federation/search")
async def federated_search_route(request: Request, body: FederationSearchBody):
    ctx = _ctx(request)
    allow, err = _resolve_allowlist(body)
    if err is not None:
        return err

    # project / school 维度只能来自签名 AccessContext（见 _resolve_project 注释）
    project_id, err = _resolve_project(ctx, body.project_id)
    if err is not None:
        return err
    school_id, err = _resolve_school(ctx, body.school_id)
    if err is not None:
        return err

    project_ids = _scope_ids(ctx, "project")
    if project_id:
        project_ids.add(project_id)

    try:
        scope = resolve_scope(
            actor_id=str(ctx.actor_id),
            tenant_id=str(ctx.tenant_id),
            project_ids=project_ids,
            session_id=str(ctx.session_id) if ctx.session_id else None,
            school_id=school_id,
            capabilities=ctx.capabilities,
            provider_allowlist=allow,
        )
        query = FederatedQuery(
            actor_id=str(ctx.actor_id),
            tenant_id=str(ctx.tenant_id),
            project_id=project_id,
            session_id=str(ctx.session_id) if ctx.session_id else None,
            purpose=body.purpose,
            provider_allowlist=allow,
            filters={"evidence_types": list(body.evidence_types or [])},
            budget=Budget(
                max_results=body.max_results,
                max_provider_calls=body.max_provider_calls,
                cost_ceiling=0.0,
            ),
            deadline_ms=body.deadline_ms,
            trace_id=ctx.trace_id or uuid.uuid4().hex[:32],
            query_text=body.query_text,
            prompt_version=body.prompt_version,
        )
    except FederationContractError as exc:
        return _error(422, type(exc).__name__, str(exc))

    with session_scope() as db:
        captured: dict[str, object] = {}

        def _record(q, r) -> None:  # noqa: ANN001
            row, _created = trace_svc.record_retrieval_trace(db, ctx, q, r)
            captured["row"] = row

        try:
            response = federated_search(
                query,
                scope=scope,
                providers=build_providers(db),
                trace_recorder=_record,
            )
        except ProviderError as exc:
            status = 504 if exc.code == "PROVIDER_TIMEOUT" else 502
            return _error(status, exc.code, str(exc), exc.detail)
        except DomainError as exc:
            return _error(exc.http_status, getattr(exc, "code", "DOMAIN_ERROR"), str(exc))

    payload = response.as_dict()
    row = captured.get("row")
    if row is not None:
        # 幂等复用既有 trace 行时，响应里的 trace_id 必须换成**落库那一行**的 id。
        # 否则调用方拿着返回的 trace_id 去查审计会 404（本轮审查的 Med 发现）。
        payload["trace_id"] = getattr(row, "trace_id", payload["trace_id"])
    return JSONResponse(status_code=200, content=payload)


@federation_router.get("/federation/traces")
def list_traces(request: Request, project_id: uuid.UUID | None = None, limit: int = 50):
    ctx = _ctx(request)
    with session_scope() as db:
        try:
            rows = trace_svc.list_traces(db, ctx, project_id=project_id, limit=limit)
        except DomainError as exc:
            return _error(exc.http_status, getattr(exc, "code", "DOMAIN_ERROR"), str(exc))
    return {"traces": [trace_svc.trace_public_dict(r) for r in rows]}


@federation_router.get("/federation/traces/{trace_id}")
def get_trace(request: Request, trace_id: str):
    ctx = _ctx(request)
    with session_scope() as db:
        try:
            row = trace_svc.get_trace(db, ctx, trace_id)
        except DomainError as exc:
            return _error(exc.http_status, getattr(exc, "code", "DOMAIN_ERROR"), str(exc))
    return trace_svc.trace_public_dict(row)


__all__ = ["federation_router", "FederationSearchBody"]
