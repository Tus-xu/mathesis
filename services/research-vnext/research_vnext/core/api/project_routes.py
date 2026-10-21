"""RV16 · ResearchProject 路由 + 欢迎页聚合端点。

`APIRouter` **不写 prefix**（前缀由 `app.py` 双前缀循环注入，见共享约定 §6.1）。

端点：
- `POST   /projects`            创建项目（幂等，归属 tenant+actor）
- `GET    /projects`            列出当前 actor 的项目
- `GET    /projects/{id}`       读取单个项目（对象级鉴权）
- `PATCH  /projects/{id}`       更新项目（revision 闸门 → 409）
- `GET    /welcome`             欢迎页聚合（最近项目 / 文献 / 能力卡）
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..db.engine import session_scope
from ..domain.errors import CapabilityMissing, DomainError
from ..project import service as project_svc
from ..project.models import ResearchProjectCreate, ResearchProjectUpdate
from .deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    AccessContext,
    check_revocation,
    input_error_response,
    load_access_context,
    require_capability,
)


class InvalidInput(DomainError):
    code = "INVALID_INPUT"
    http_status = 422


router = APIRouter(tags=["research-projects"])


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _err(e: Exception) -> JSONResponse:
    if isinstance(e, DomainError):
        return JSONResponse(status_code=e.http_status, content={"error": e.to_dict()})
    # 手工构造请求模型（ResearchProjectCreate/Update）时的字段非法/缺失是**可预期输入错误**，
    # 必须回 422 而非 500（统一收敛到 deps.input_error_response）。
    input_err = input_error_response(e)
    if input_err is not None:
        return input_err
    raise e


def _authorize(ctx: AccessContext, capability: str) -> None:
    """项目域的统一前置鉴权：workspace → 撤权 → capability。"""
    if ctx.workspace != "research":
        raise CapabilityMissing(f"workspace={ctx.workspace!r} 无权访问 research 域")
    require_capability(ctx, capability)


@router.post("/projects", status_code=201)
def create_project(request: Request, body_in: dict | None = Body(default=None)) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        _authorize(ctx, WRITE_CAPABILITY)
        payload = ResearchProjectCreate(
            name=raw.get("name") or "",
            description=raw.get("description"),
        )
        client_request_id = str(raw.get("client_request_id") or "")
        if len(client_request_id) < 8:
            raise InvalidInput("client_request_id 必填（≥8 字符）")
        with session_scope() as db:
            check_revocation(db, ctx)
            row = project_svc.create_project(
                db,
                tenant_id=ctx.tenant_id,
                owner_id=ctx.actor_id,
                name=payload.name,
                description=payload.description,
                client_request_id=client_request_id,
            )
            out = project_svc._to_out(row, project_svc.project_counts(db, tenant_id=ctx.tenant_id, project_id=row.id))
        return JSONResponse(status_code=201, content={"project": out.model_dump(mode="json")})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects")
def list_projects(request: Request, include_archived: bool = False, limit: int = 50) -> JSONResponse:
    ctx = _ctx(request)
    try:
        _authorize(ctx, READ_CAPABILITY)
        with session_scope() as db:
            check_revocation(db, ctx)
            rows = project_svc.list_projects(
                db,
                tenant_id=ctx.tenant_id,
                owner_id=ctx.actor_id,
                include_archived=include_archived,
                limit=limit,
            )
            items = [project_svc._to_out(r).model_dump(mode="json") for r in rows]
        return JSONResponse(content={"items": items})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}")
def get_project(request: Request, project_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        _authorize(ctx, READ_CAPABILITY)
        with session_scope() as db:
            check_revocation(db, ctx)
            row = project_svc.get_project(
                db, tenant_id=ctx.tenant_id, owner_id=ctx.actor_id, project_id=project_id
            )
            out = project_svc._to_out(
                row, project_svc.project_counts(db, tenant_id=ctx.tenant_id, project_id=row.id)
            )
        return JSONResponse(content={"project": out.model_dump(mode="json")})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.patch("/projects/{project_id}")
def update_project(
    request: Request, project_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        _authorize(ctx, WRITE_CAPABILITY)
        payload = ResearchProjectUpdate(
            name=raw.get("name"),
            description=raw.get("description"),
            archived=raw.get("archived"),
            expected_revision=int(raw.get("expected_revision") or 0),
            client_request_id=str(raw.get("client_request_id") or ""),
        )
        with session_scope() as db:
            check_revocation(db, ctx)
            row = project_svc.update_project(
                db,
                tenant_id=ctx.tenant_id,
                owner_id=ctx.actor_id,
                project_id=project_id,
                expected_revision=payload.expected_revision,
                name=payload.name,
                description=payload.description,
                archived=payload.archived,
            )
            out = project_svc._to_out(row)
        return JSONResponse(content={"project": out.model_dump(mode="json"), "revision": out.revision})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/welcome")
def welcome(request: Request) -> JSONResponse:
    """欢迎页聚合端点（K1 无假按钮：欢迎页 4 能力卡 + 最近项目/文献都来自真实数据）。"""
    ctx = _ctx(request)
    try:
        _authorize(ctx, READ_CAPABILITY)
        with session_scope() as db:
            check_revocation(db, ctx)
            body = project_svc.welcome_summary(
                db, tenant_id=ctx.tenant_id, owner_id=ctx.actor_id
            )
        return JSONResponse(content=body)
    except Exception as e:  # noqa: BLE001
        return _err(e)
