"""RV05 · Parser / Anchor Gateway 路由。

PDF.js 读字节一律走既有 Asset content（受权 + Range）；本路由只管
解析运行与 version-specific anchors。
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..assets.service import get_asset
from ..domain.errors import DomainError
from ..observability.redaction import redact_mapping
from ..parser import service as parse_svc
from ..parser.provider import ParserProviderError
from .deps import AccessContext, load_access_context, trace_from

parser_router = APIRouter(tags=["research-parser"])


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _err(e: Exception) -> JSONResponse:
    if isinstance(e, DomainError):
        return JSONResponse(status_code=e.http_status, content={"error": e.to_dict()})
    if isinstance(e, ParserProviderError):
        status = 504 if e.code == "PARSER_TIMEOUT" else 502
        return JSONResponse(
            status_code=status,
            content={"error": {"code": e.code, "message": e.message, "detail": redact_mapping(e.detail)}},
        )
    raise e


@parser_router.post("/assets/{asset_id}/parse")
async def submit_parse(request: Request, asset_id: uuid.UUID, mode: str = "job"):
    """提交解析。mode=job → RV03 队列；mode=inline → 同步（仅测试/小文件）。"""
    ctx = _ctx(request)
    try:
        from ..application import job_service as js
        from ..db.engine import session_scope

        with session_scope() as db:
            asset = get_asset(db, asset_id)
            if asset is None:
                return _err(parse_svc.ParseNotFound("资产不存在"))
            from ..assets.service import authorize_asset

            authorize_asset(db, ctx, asset)
            if mode == "inline":
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    path = Path(tmp.name)
                try:
                    from ..assets.storage import MinioObjectStorage, StorageConfig

                    st = MinioObjectStorage(StorageConfig.from_env())
                    path.write_bytes(st.get_bytes(asset.storage_key))
                    run, rows = parse_svc.run_parse_on_asset(db, ctx=ctx, asset_id=asset_id, pdf_path=path)
                finally:
                    path.unlink(missing_ok=True)
                return JSONResponse(
                    status_code=201,
                    content={
                        "parse_run": parse_svc.parse_run_public(run),
                        "anchor_count": len(rows),
                    },
                    headers={"X-Trace-Id": ctx.trace_id or ""},
                )

            session_id = asset.session_id or uuid.uuid4()
            job = js.enqueue(
                db,
                session_id=session_id,
                tenant_id=ctx.tenant_id,
                job_type="document.parse",
                input_payload={
                    "asset_id": str(asset_id),
                    "tenant_id": str(ctx.tenant_id),
                    "actor_id": str(ctx.actor_id),
                    "project_id": str(asset.project_id),
                    "resource_scopes": list(ctx.resource_scopes),
                },
                user_id=ctx.actor_id,
                project_id=asset.project_id,
                client_request_id=request.headers.get("X-Client-Request-Id", ""),
                trace_id=trace_from(request, ctx).trace_id,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "job_id": str(job.id),
                    "job_type": job.job_type,
                    "status": "QUEUED",
                    "asset_id": str(asset_id),
                    "asset_version": str(asset.version),
                    "asset_hash": asset.content_hash,
                },
                headers={"X-Trace-Id": ctx.trace_id or ""},
            )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@parser_router.get("/assets/{asset_id}/parse-runs")
def list_parse_runs(request: Request, asset_id: uuid.UUID):
    ctx = _ctx(request)
    try:
        from ..assets.service import authorize_asset
        from ..db.engine import session_scope

        with session_scope() as db:
            asset = get_asset(db, asset_id)
            if asset is None:
                return _err(parse_svc.ParseNotFound("资产不存在"))
            authorize_asset(db, ctx, asset)
            runs = parse_svc.list_parse_runs(db, asset_id, tenant_id=ctx.tenant_id)
            return JSONResponse(
                content={"parse_runs": [parse_svc.parse_run_public(r) for r in runs]}
            )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@parser_router.get("/parse-runs/{run_id}")
def get_parse_run(request: Request, run_id: uuid.UUID):
    ctx = _ctx(request)
    try:
        from ..assets.service import authorize_asset
        from ..db.engine import session_scope

        with session_scope() as db:
            run = parse_svc.get_parse_run(db, run_id)
            if run is None:
                return _err(parse_svc.ParseNotFound("解析运行不存在"))
            if run.tenant_id != ctx.tenant_id:
                from ..domain.errors import TenantMismatch

                return _err(TenantMismatch("跨 tenant 禁止读取 parse run"))
            asset = get_asset(db, run.asset_id)
            if asset is None:
                return _err(parse_svc.ParseNotFound("资产不存在"))
            authorize_asset(db, ctx, asset)
            return JSONResponse(content={"parse_run": parse_svc.parse_run_public(run)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@parser_router.get("/assets/{asset_id}/anchors")
def list_anchors(
    request: Request,
    asset_id: uuid.UUID,
    parse_run_id: uuid.UUID | None = None,
    type: str | None = None,  # noqa: A002
    include_stale: bool = True,
):
    ctx = _ctx(request)
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            anchors = parse_svc.list_anchors(
                db,
                ctx=ctx,
                asset_id=asset_id,
                parse_run_id=parse_run_id,
                anchor_type=type,
                include_stale=include_stale,
            )
            stale_count = sum(1 for a in anchors if a.get("stale"))
            return JSONResponse(
                content={
                    "anchors": anchors,
                    "count": len(anchors),
                    "stale_count": stale_count,
                    "note": "anchors are asset-version-specific; stale anchors never silently remap",
                }
            )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@parser_router.get("/anchors/{anchor_id}")
def resolve_anchor(request: Request, anchor_id: str):
    ctx = _ctx(request)
    try:
        from ..db.engine import session_scope

        with session_scope() as db:
            public = parse_svc.resolve_anchor(db, ctx=ctx, anchor_id=anchor_id)
            return JSONResponse(content={"anchor": public})
    except Exception as e:  # noqa: BLE001
        return _err(e)
