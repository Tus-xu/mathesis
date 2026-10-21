"""RV14 · 导出域 REST 路由。

前缀由 app.py 统一挂载（api_prefixes() 的每个前缀）：
- `POST /sessions/{sid}/paper:export`          发起导出（去重 + enqueue）
- `GET  /paper-exports/{aid}`                  产物元数据（对象级鉴权）
- `POST /paper-exports/{aid}/download-link`    签发短时下载令牌（审计）
- `GET  /paper-exports/{aid}/download`         令牌换字节（无 session header，
                                                授权完全由令牌 + 服务器复检承担）
- `POST /paper-exports/{aid}:revoke`           撤回产物（审计）
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response

from ..api.deps import WRITE_CAPABILITY, AccessContext, authorize_session, load_access_context
from ..api.deps import trace_from
from ..db.engine import session_scope
from . import service

export_router = APIRouter(tags=["research-vnext-rv14"])


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


@export_router.post("/sessions/{session_id}/paper:export")
def post_export(session_id: uuid.UUID, request: Request, body: dict | None = None) -> dict:
    ctx = _ctx(request)
    formats = list((body or {}).get("formats") or [])
    with session_scope() as db:
        authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
        return service.start_export(db, ctx=ctx, session_id=session_id, formats=formats,
                                    trace_id=trace_from(request, ctx).trace_id)


@export_router.get("/paper-exports/{artifact_id}")
def get_export(artifact_id: uuid.UUID, request: Request) -> dict:
    ctx = _ctx(request)
    with session_scope() as db:
        return service.artifact_summary(db, ctx, artifact_id)


@export_router.post("/paper-exports/{artifact_id}/download-link")
def post_download_link(artifact_id: uuid.UUID, request: Request, body: dict | None = None) -> dict:
    ctx = _ctx(request)
    fmt = str((body or {}).get("format") or "")
    with session_scope() as db:
        return service.mint_link(db, ctx=ctx, artifact_id=artifact_id, fmt=fmt,
                                 trace_id=trace_from(request, ctx).trace_id)


@export_router.get("/paper-exports/{artifact_id}/download")
def get_download(artifact_id: uuid.UUID, request: Request, format: str = "") -> Response:
    """令牌换字节。**不**要求 AccessContext header：令牌即对象级授权凭据，
    撤权/floor/过期/篡改由 service 复检。

    事务边界（RV14 关键）：拒绝路径的 DOWNLOAD_DENIED 审计与 DomainError
    在**同一个** session 内先落库再返回错误响应 —— 异常若冒泡出
    session_scope，审计会随事务回滚丢失。"""
    from fastapi.responses import JSONResponse

    from ..domain.errors import DomainError

    token = str(request.query_params.get("token") or "")
    with session_scope() as db:
        try:
            data, media = service.download_artifact(
                db, artifact_id=artifact_id, fmt=format, token=token
            )
        except DomainError as e:
            # 在 session_scope **内部**捕获：拒绝路径的 DOWNLOAD_DENIED 审计
            # 随正常提交落库；异常若冒泡出 with，审计会随回滚丢失。
            return JSONResponse(status_code=e.http_status, content={"error": e.to_dict()})
    filename = {"markdown": "paper.md", "latex": "paper.tex", "pdf": "paper.pdf"}.get(
        format, f"paper.{format}"
    )
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@export_router.post("/paper-exports/{artifact_id}:revoke")
def post_revoke(artifact_id: uuid.UUID, request: Request, body: dict | None = None) -> dict:
    ctx = _ctx(request)
    reason = str((body or {}).get("reason") or "")
    with session_scope() as db:
        return service.revoke_artifact(db, ctx=ctx, artifact_id=artifact_id, reason=reason,
                                       trace_id=trace_from(request, ctx).trace_id)
