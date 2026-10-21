"""RV04 · Asset / Literature Gateway 路由。

全部经 AccessContext 对象级授权；不暴露 MinIO 内部地址。
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..assets import service as asset_svc
from ..literature import service as lit_svc
from ..literature.crossref import CrossrefProvider
from ..literature.provider import LiteratureProviderError
from ..observability.redaction import redact_mapping
from .deps import (
    WRITE_CAPABILITY,
    AccessContext,
    authorize_create,
    load_access_context,
    require_capability,
    trace_from,
)

asset_router = APIRouter(tags=["research-assets"])
literature_router = APIRouter(tags=["research-literature"])


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _err(e: Exception) -> JSONResponse:
    from ..domain.errors import DomainError

    if isinstance(e, DomainError):
        return JSONResponse(status_code=e.http_status, content={"error": e.to_dict()})
    if isinstance(e, LiteratureProviderError):
        return JSONResponse(
            status_code=429 if e.retryable and e.code == "PROVIDER_RATE_LIMITED" else 502,
            content={"error": {"code": e.code, "message": e.message, "detail": redact_mapping(e.detail)}},
        )
    raise e


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------


@asset_router.post("/assets")
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    session_id: str | None = Form(None),
    rights_status: str | None = Form(None),
):
    ctx = _ctx(request)
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            authorize_create(db, ctx, ctx.tenant_id)
            pid = uuid.UUID(project_id)
            sid = uuid.UUID(session_id) if session_id else None
            rights = {"status": rights_status or "UNKNOWN"} if rights_status else None
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                path = Path(tmp.name)
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
            try:
                row, deduped = asset_svc.upload_asset(
                    db,
                    ctx,
                    project_id=pid,
                    data_path=path,
                    filename=file.filename or "",
                    claimed_media_type=file.content_type or "",
                    session_id=sid,
                    rights=rights,
                    client_request_id=request.headers.get("X-Client-Request-Id", ""),
                    trace=trace_from(request, ctx),
                )
            finally:
                path.unlink(missing_ok=True)
        return JSONResponse(
            status_code=200 if deduped else 201,
            content={"asset": asset_svc.asset_public_dict(row), "deduplicated": deduped},
            headers={"X-Trace-Id": ctx.trace_id or ""},
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@asset_router.get("/assets/{asset_id}")
def get_asset_meta(request: Request, asset_id: uuid.UUID):
    ctx = _ctx(request)
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            row = asset_svc.get_asset(db, asset_id)
            if row is None:
                return _err(asset_svc.AssetNotFound("资产不存在"))
            asset_svc.authorize_asset(db, ctx, row)
            return JSONResponse(content={"asset": asset_svc.asset_public_dict(row)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@asset_router.post("/assets/{asset_id}/signed-url")
def issue_signed(request: Request, asset_id: uuid.UUID, ttl_seconds: int | None = None):
    ctx = _ctx(request)
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            url, row = asset_svc.issue_signed_token(db, ctx, asset_id, ttl_seconds=ttl_seconds)
        return JSONResponse(
            content={
                "url": url,
                "expires_in_seconds": ttl_seconds or asset_svc.default_ttl_seconds(),
                "asset_id": str(row.id),
                "note": "gateway-relative; no MinIO host",
            }
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@asset_router.post("/assets/{asset_id}/revoke")
def revoke_asset(request: Request, asset_id: uuid.UUID, reason: str = ""):
    ctx = _ctx(request)
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            row = asset_svc.revoke_asset(db, ctx, asset_id, reason=reason)
            return JSONResponse(content={"asset": asset_svc.asset_public_dict(row)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@asset_router.get("/assets/{asset_id}/content")
def download_content(
    request: Request,
    asset_id: uuid.UUID,
    token: str | None = None,
    range: str | None = Header(default=None, alias="Range"),
):
    ctx = None
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            if token:
                row, body, headers = asset_svc.deliver_via_token(db, token=token, range_header=range)
            else:
                ctx = _ctx(request)
                row, body, headers = asset_svc.deliver_direct(db, ctx, asset_id, range_header=range)
            status = 206 if "Content-Range" in headers else 200
            return Response(content=body, status_code=status, headers=headers)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# --------------------------------------------------------------------------
# Literature
# --------------------------------------------------------------------------


@literature_router.post("/literature/search")
async def literature_search(request: Request):
    """立即检索并入库。真正长任务应走 JobContract；本端点做短超时同步抓取。"""
    ctx = _ctx(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    query = (body.get("query") or "").strip()
    limit = int(body.get("limit") or 5)
    project_id = body.get("project_id") or ""
    if not query or not project_id:
        return JSONResponse(status_code=400, content={"error": {"code": "INVALID_INPUT", "message": "query/project_id 必填"}})

    try:
        require_capability(ctx, WRITE_CAPABILITY)
        provider = CrossrefProvider()
        request_id = f"lit-{uuid.uuid4().hex[:16]}"
        metas = provider.search(query, limit=limit, request_id=request_id)
        from ..db.engine import session_scope

        results = []
        with session_scope() as db:
            authorize_create(db, ctx, ctx.tenant_id)
            for meta in metas:
                row, outcome = lit_svc.ingest_metadata(
                    db, ctx, meta, project_id=uuid.UUID(project_id)
                )
                results.append({**lit_svc.record_public_dict(row), "outcome": outcome})
        return JSONResponse(
            content={
                "provider": provider.name,
                "provider_version": provider.version,
                "query": query,
                "request_id": request_id,
                "count": len(results),
                "items": results,
            }
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@literature_router.get("/literature")
def literature_list(request: Request, project_id: uuid.UUID | None = None, limit: int = 50):
    ctx = _ctx(request)
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            rows = lit_svc.list_records(db, ctx, project_id=project_id, limit=limit)
            return JSONResponse(
                content={
                    # 诚实标注：库内检索为关键词/结构模式，无 embedding（KERNEL §2.3）。
                    "retrieval_mode": "keyword",
                    "items": [lit_svc.record_public_dict(r) for r in rows],
                }
            )
    except Exception as e:  # noqa: BLE001
        return _err(e)
