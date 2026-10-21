"""8200 API 路由（统一前缀 /api/ppt-studio-v2/v1）。

必需：healthz/readyz、作业 submit/get/cancel/events/pages、DLQ 列表；
R21：deck ingest（approved 闸门）、deck/artifact 查询。
T24：artifact 字节内容端点（/artifacts/{id}/content，导出链路取字节）。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.artifact_store import LocalArtifactStore
from app.artifacts import media_type_for_kind, read_artifact_bytes
from app.config import get_settings
from app.contracts.job_protocols import ALLOWED_JOB_TYPES, SubmitJobRequest
from app.db.session import session_factory
from app.decks import deck_view, get_deck, ingest_deck
from app.errors import ApiError, ErrorCode
from app.persistence.models import PptV2Artifact, PptV2DeckPage
from app.reliability.jobs import JobEngine
from app.reliability.queue_policy import OWNER_SERVICE_8200, policy_for, stream_of
from app.response import ok

logger = logging.getLogger("ps2.api")

router = APIRouter()


def _engine() -> JobEngine:
    s = get_settings()
    return JobEngine(
        lease_seconds=s.lease_seconds,
        backoff_base_s=s.backoff_base_s,
        backoff_max_s=s.backoff_max_s,
    )


# ── 健康检查 ──────────────────────────────────────────────────────
@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with session_factory()() as session:
            await session.execute(_text("SELECT 1"))
        checks["db"] = "up"
    except Exception as exc:  # noqa: BLE001
        checks["db"] = f"down({type(exc).__name__})"
    try:
        from redis.asyncio import Redis

        r = Redis.from_url(get_settings().valkey_url)
        checks["valkey"] = "up" if await r.ping() else "down"
        await r.aclose()
    except Exception as exc:  # noqa: BLE001
        checks["valkey"] = f"down({type(exc).__name__})"
    all_up = checks.get("db") == "up" and checks.get("valkey") == "up"
    return JSONResponse(
        status_code=200 if all_up else 503,
        content={
            "status": "ok" if all_up else "degraded",
            "service": "ppt-studio-v2",
            "checks": checks,
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    return await healthz(request)


# ── 作业 ──────────────────────────────────────────────────────────
@router.post("/api/ppt-studio-v2/v1/jobs", status_code=201)
async def submit_job(
    body: SubmitJobRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    if body.job_type not in ALLOWED_JOB_TYPES:
        raise ApiError(ErrorCode.VALIDATION, f"job_type 不在白名单: {body.job_type}", error_key="job_type_not_allowed")
    if body.job_type == "ppt.deck.generate" and not str(body.payload.get("deck_id") or ""):
        raise ApiError(ErrorCode.VALIDATION, "generate 作业必须携带 payload.deck_id", error_key="deck_id_required")
    # 同请求幂等：generate 未显式给 dedupe_key 时按 deck_id 派生（同 deck → 单 job → 单产物）
    dedupe = body.dedupe_key
    if body.job_type == "ppt.deck.generate" and not dedupe:
        dedupe = f"ppt.deck.generate:{body.payload.get('deck_id')}"
    # R3 修复：dedupe_key 不得把【已终态（DLQ/FAILED/CANCELLED）或已超期】的作业当作
    # "仍在进行"——否则该 deck 的后续重试会永远 409，DECK_PPTX 永远等不到。
    # 终态/超期 → 释放旧 dedupe_key 再建新作业（同 deck 仍只保留一个活跃作业）。
    if dedupe:
        from datetime import UTC, datetime

        from app.persistence.models import PptV2Job as _PptV2Job

        existing = (
            await session.execute(
                select(_PptV2Job).where(_PptV2Job.job_type == body.job_type, _PptV2Job.dedupe_key == dedupe)
            )
        ).scalar_one_or_none()
        if existing is not None:
            deadline = existing.deadline_at
            if deadline is not None and deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            expired = deadline is not None and deadline <= datetime.now(UTC)
            if existing.status in ("DLQ", "FAILED", "CANCELLED") or expired:
                existing.dedupe_key = None
                await session.flush()
    engine = _engine()
    job = await engine.create(
        session,
        job_type=body.job_type,
        payload=body.payload,
        max_attempts=body.max_attempts,
        timeout_s=body.timeout_s,
        dedupe_key=dedupe,
        trace_id=getattr(request.state, "trace_id", None),
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    await session.commit()
    return ok(job, message="作业已受理", request_id=getattr(request.state, "request_id", ""))


@router.get("/api/ppt-studio-v2/v1/jobs/{job_id}")
async def get_job(
    job_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    job = await _engine().get(session, job_id)
    if job is None:
        raise ApiError(ErrorCode.NOT_FOUND, "作业不存在", error_key="job_not_found")
    return ok(job, request_id=getattr(request.state, "request_id", ""))


@router.get("/api/ppt-studio-v2/v1/jobs/{job_id}/events")
async def get_job_events(
    job_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    events = await _engine().list_events(session, job_id)
    return ok(events, request_id=getattr(request.state, "request_id", ""))


@router.get("/api/ppt-studio-v2/v1/jobs/{job_id}/pages")
async def get_job_pages(
    job_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    """页级 checkpoint 视图（QA/局部重试证据面）。"""
    rows = (
        await session.execute(
            select(PptV2DeckPage).where(PptV2DeckPage.job_id == job_id).order_by(PptV2DeckPage.page_no)
        )
    ).scalars().all()
    return ok(
        [
            {
                "page_no": r.page_no,
                "slide_id": r.slide_id,
                "status": r.status,
                "content_sha256": r.content_sha256,
                "fragment_ref": r.fragment_ref,
                "png_ref": r.png_ref,
                "cache_hit": r.cache_hit,
                "elapsed_ms": r.elapsed_ms,
                "qa": r.qa,
                "last_error": r.last_error,
            }
            for r in rows
        ],
        request_id=getattr(request.state, "request_id", ""),
    )


# ── Deck（R21：approved TeachingDeckSpec 消费口）──────────────────
@router.post("/api/ppt-studio-v2/v1/decks/ingest")
async def ingest_deck_route(
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
    body: dict[str, Any] = Body(...),  # noqa: B008
) -> dict:
    """body = {spec: <TeachingDeckSpec>, assets_inline?: {asset_id: b64}}。"""
    spec_raw = body.get("spec")
    if not isinstance(spec_raw, dict):
        raise ApiError(ErrorCode.VALIDATION, "body.spec 必须是 TeachingDeckSpec 对象", error_key="spec_required")
    assets_inline = body.get("assets_inline") or {}
    result = await ingest_deck(session, spec_raw, assets_inline)
    await session.commit()
    return ok(result, message="deck 已受理", request_id=getattr(request.state, "request_id", ""))


@router.get("/api/ppt-studio-v2/v1/decks/{deck_id}")
async def get_deck_route(
    deck_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    row = await get_deck(session, deck_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "deck 不存在", error_key="deck_not_found")
    return ok(deck_view(row), request_id=getattr(request.state, "request_id", ""))


@router.get("/api/ppt-studio-v2/v1/decks/{deck_id}/artifacts")
async def deck_artifacts_route(
    deck_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    row = await get_deck(session, deck_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "deck 不存在", error_key="deck_not_found")
    rows = (
        await session.execute(
            select(PptV2Artifact)
            .where(PptV2Artifact.deck_id == row.id)
            .order_by(PptV2Artifact.created_at, PptV2Artifact.kind)
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": str(a.id),
                "kind": a.kind,
                "object_key": a.object_key,
                "sha256": a.sha256,
                "size_bytes": a.size_bytes,
                "page_no": a.page_no,
                "job_id": str(a.job_id) if a.job_id else None,
                "artifact_meta": a.artifact_meta,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
        request_id=getattr(request.state, "request_id", ""),
    )


@router.get("/api/ppt-studio-v2/v1/decks/{deck_id}/pages")
async def deck_pages_route(
    deck_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    """deck 的逐页渲染清单（deck 作用域）。

    为什么需要它：`ppt_v2_artifacts.object_key` 是**全局内容寻址**的（同内容只登记一次，
    行归属首次渲染它的 deck），因此 `/decks/{id}/artifacts` 无法给出本 deck 的逐页产物。
    这里改用 deck 作用域的 `ppt_v2_deck_pages`（每 deck+页一行），
    给出 png_ref / fragment_ref 与**字节是否已归档**的真实状态（不猜、不假装可读）。
    """
    row = await get_deck(session, deck_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "deck 不存在", error_key="deck_not_found")
    pages = (
        await session.execute(
            select(PptV2DeckPage)
            .where(PptV2DeckPage.deck_id == row.id)
            .order_by(PptV2DeckPage.page_no)
        )
    ).scalars().all()
    store = LocalArtifactStore()
    latest: dict[int, PptV2DeckPage] = {}
    for p in pages:
        latest[p.page_no] = p  # 同页多 attempt 取最后一行
    return ok(
        [
            {
                "page_no": p.page_no,
                "slide_id": p.slide_id,
                "status": p.status,
                "png_ref": p.png_ref,
                "fragment_ref": p.fragment_ref,
                "png_available": bool(p.png_ref) and store.exists(f"pps2/pages/{p.png_ref}.png"),
                "png_content_url": (
                    f"/api/ppt-studio-v2/v1/decks/{row.id}/pages/{p.page_no}/content"
                    if p.png_ref and store.exists(f"pps2/pages/{p.png_ref}.png")
                    else None
                ),
                "last_error": p.last_error,
            }
            for p in (latest[k] for k in sorted(latest))
        ],
        request_id=getattr(request.state, "request_id", ""),
    )


@router.get("/api/ppt-studio-v2/v1/decks/{deck_id}/pages/{page_no}/content")
async def deck_page_content_route(
    deck_id: str,
    page_no: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> Response:
    """单页渲染图（内核 PowerPoint COM 逐页导出的 PNG）字节。

    T24 EXPORT-QA E1 的收口：页字节此前只留在 8201 私有 root，8200 content 恒 404。
    worker 现已把页字节归档进 8200 store（`_persist_page_bytes`）；本端点是
    **deck 作用域**的读取入口，不依赖全局 artifact 行归属。
    字节缺失/哈希漂移一律明确报错，绝不发出可疑字节。
    """
    row = await get_deck(session, deck_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "deck 不存在", error_key="deck_not_found")
    page = (
        await session.execute(
            select(PptV2DeckPage)
            .where(PptV2DeckPage.deck_id == row.id, PptV2DeckPage.page_no == page_no)
            .order_by(PptV2DeckPage.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if page is None or not page.png_ref:
        raise ApiError(ErrorCode.NOT_FOUND, "该页尚无渲染图", error_key="page_render_missing")
    data = LocalArtifactStore().get(f"pps2/pages/{page.png_ref}.png")
    if data is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"第 {page_no} 页渲染图字节缺失（object_key=pps2/pages/{page.png_ref[:16]}….png）",
            error_key="page_bytes_missing",
        )
    if page.png_sha256:
        actual = hashlib.sha256(data).hexdigest()
        if actual != page.png_sha256:
            raise ApiError(
                ErrorCode.INTERNAL,
                f"第 {page_no} 页渲染图与登记 sha256 不一致，拒绝发出",
                error_key="page_hash_mismatch",
            )
    return Response(
        content=data,
        media_type="image/png",
        headers={"X-Content-Sha256": page.png_sha256 or "", "X-Page-No": str(page_no)},
    )


@router.get("/api/ppt-studio-v2/v1/artifacts/{artifact_id}/content")
async def artifact_content_route(
    artifact_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> Response:
    """artifact 原始字节（T24 S2.6：8100 导出链路取 DECK_PPTX/PNG 字节，本地校验 sha256）。"""
    row, data = await read_artifact_bytes(session, artifact_id)
    return Response(
        content=data,
        media_type=media_type_for_kind(row.kind),
        headers={"X-Content-Sha256": row.sha256, "X-Artifact-Kind": row.kind},
    )


@router.post("/api/ppt-studio-v2/v1/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    job = await _engine().request_cancel(session, job_id)
    if job is None:
        raise ApiError(ErrorCode.NOT_FOUND, "作业不存在", error_key="job_not_found")
    await session.commit()
    return ok(job, message="取消已受理", request_id=getattr(request.state, "request_id", ""))


@router.get("/api/ppt-studio-v2/v1/dlq")
async def list_dlq(
    request: Request,
    session: Annotated[AsyncSession, Depends(require_auth)],
) -> dict:
    rows = await _engine().list_dlq(session, limit=50)
    return ok(rows, request_id=getattr(request.state, "request_id", ""))


@router.get("/api/ppt-studio-v2/v1/queue-policy")
async def queue_policy_public(request: Request) -> dict:
    """非敏感配置审计面（无密钥）：展示 8200 队列隔离策略。"""
    p = policy_for(OWNER_SERVICE_8200)
    from app.reliability.queue_policy import policy_json

    return ok(
        {
            "owner_service": p.owner_service,
            "stream_prefix": p.stream_prefix,
            "consumer_group": p.consumer_group,
            "dedupe_scope": p.dedupe_scope,
            "max_pool": p.max_pool,
            "quota_per_min": p.quota_per_min,
            "object_prefix": p.object_prefix,
            "example_job_stream": stream_of(OWNER_SERVICE_8200, "ppt.job"),
            "all_owners": policy_json(),
        },
        request_id=getattr(request.state, "request_id", ""),
    )
