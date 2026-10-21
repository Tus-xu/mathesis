"""T24 HTTP 面：deck 编辑 / 局部重生成 / 逐页 QA / 导出 / artifact / 下载。

路由注册顺序有语义：`/downloads/{ticket}`、`/exports/{job_id}` 必须在 `/{artifact_id}` 之前，
否则票据会被当成 artifact_id 吞掉——这是本文件最容易出错的地方。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.errors import ApiError, ErrorCode
from app.core.response import ok
from app.core.security import UserToken
from app.ppt_v2.service import export as export_service
from app.ppt_v2.service import qa as qa_service
from app.ppt_v2.service import regen as regen_service
from app.ppt_v2.service import spec_edit
from app.storage import artifacts as artifact_service
from app.storage.minio import get_storage

async def _guard_deck_path(
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """router 级 deck 归属闸门（T25 P0-5 读越权 / P0-6 回滚写越权）。

    `ppt_v2_deck_specs` 只有 `school_id`(=class_id)、没有 owner 列，归属由
    projection/artifact 反查（`artifact_service.spec_access`，无证据即拒）。
    挂在 router 级而非逐个 handler：一次覆盖全部 `{deck_id}` 路由（当前 11 条），
    且后续新增 deck 路由不会漏。
    """
    deck_id = request.path_params.get("deck_id")
    if not deck_id:
        return
    allowed, reason = await artifact_service.spec_access(
        session,
        spec_id=str(deck_id),
        actor_id=user.id,
        roles=[user.role] if user.role else None,
    )
    if not allowed:
        raise ApiError(ErrorCode.FORBIDDEN, f"非本人课件或无权访问（{reason}）")


router = APIRouter(
    prefix="/artifacts", tags=["t24-artifacts"], dependencies=[Depends(_guard_deck_path)]
)


class PatchBody(BaseModel):
    expected_revision: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=300)
    source: str = Field(default="human", pattern="^(human|ai|regen)$")
    operations: list[dict[str, Any]] = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, max_length=120)


class ApplyBody(BaseModel):
    expected_revision: int = Field(ge=0)


class RejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class RevertBody(BaseModel):
    expected_revision: int = Field(ge=0)
    summary: str | None = Field(default=None, max_length=300)


class RegenBody(BaseModel):
    expected_revision: int = Field(ge=0)
    target: dict[str, Any]
    instruction: str = Field(min_length=1, max_length=2000)
    refs: dict[str, Any]
    # R4 AI 快捷操作：content=改写正文；note_practice=随堂练习追加备注；
    # derivation=生成可直接逐步播放的编号推导链（前端 steps + reveal）
    mode: str = Field(default="content", pattern="^(content|note_practice|derivation)$")


class AddPageBody(BaseModel):
    expected_revision: int = Field(ge=0)
    after_slide_id: str = Field(min_length=1)
    instruction: str = Field(default="", max_length=2000)
    refs: dict[str, Any]


class EnrichBody(BaseModel):
    expected_revision: int = Field(ge=0)
    refs: dict[str, Any] | None = None


class FigureBody(BaseModel):
    """为本课补齐确定性数学配图（最多 3 张；无候选时诚实 no-op）。"""

    expected_revision: int = Field(ge=0)
    refs: dict[str, Any] | None = None
    max_figures: int = Field(default=3, ge=1, le=3)


class QaRetryBody(BaseModel):
    page_no: int = Field(ge=1)
    expected_revision: int = Field(ge=0)


class ExportBody(BaseModel):
    format: str = Field(min_length=1, max_length=16)
    expected_revision: int = Field(ge=0)
    refs: dict[str, Any] | None = None


class TicketBody(BaseModel):
    ttl_seconds: int | None = Field(default=None, ge=30, le=3600)


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "")


# ── deck 规格读取 / 修订 ────────────────────────────────────────────────────
@router.get("/decks/{deck_id}")
async def get_deck(
    deck_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await spec_edit.load_deck_row(session, deck_id)
    await spec_edit.ensure_snapshot(session, row)
    await session.commit()
    return ok(spec_edit.deck_view(row), request_id=_rid(request))


@router.get("/decks/{deck_id}/assets/{asset_id}/url")
async def get_deck_asset_url(
    deck_id: str,
    asset_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """课件图片资产的短时效访问 URL（R8c：编辑器渲染 `slide.assets` 的唯一入口）。

    此前 8100 **无任何 asset 端点** ⇒ 走 MATH_RENDER 资产通道的图形（直线 / 二次函数 /
    幂函数图象）在编辑器里恒为空白，只有导出 PPTX 才可见（教师视角即「课件缺图」）。
    此处按「deck 归属」（router 级 `_guard_deck_path`）+「asset 归属本 deck」双重约束，
    发 MinIO 短时效 presign URL；不回传裸 object_key，避免绕过鉴权直取对象。
    """
    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    asset = next((a for a in (spec.assets or []) if str(a.id) == str(asset_id)), None)
    if asset is None:
        raise ApiError(ErrorCode.NOT_FOUND, "该课件不存在此图片资产")
    ttl = 1800
    try:
        url = get_storage().presign_get("teacher-assets", str(asset.object_key), expires_seconds=ttl)
    except Exception as exc:  # noqa: BLE001 - 存储不可用显式失败，绝不发空 URL
        raise ApiError(
            ErrorCode.DEPENDENCY_UNAVAILABLE, f"资产存储不可用：{type(exc).__name__}"
        ) from exc
    return ok(
        {"asset_id": str(asset_id), "url": url, "expires_in": ttl, "mime_type": asset.mime_type},
        request_id=_rid(request),
    )


@router.get("/decks/{deck_id}/revisions")
async def list_revisions(
    deck_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await spec_edit.list_revisions(session, deck_id)
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/revisions/{revision}/revert")
async def revert_revision(
    deck_id: str,
    revision: int,
    body: RevertBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await spec_edit.revert_to_revision(
        session,
        deck_id,
        revision,
        expected_revision=body.expected_revision,
        actor_id=user.id,
        summary=body.summary,
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.get("/decks/{deck_id}/pages/{slide_id}")
async def get_page(
    deck_id: str,
    slide_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    slide = next((s for s in spec.slides if s.id == slide_id), None)
    if slide is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"slide {slide_id} 不存在")
    payload = slide.model_dump(mode="json")
    return ok(
        {
            "slide_id": slide_id,
            "revision": row.revision,
            "content_sha256": spec_edit.canonical_sha256(payload),
            "slide": payload,
        },
        request_id=_rid(request),
    )


# ── 编辑提案 ────────────────────────────────────────────────────────────────
@router.post("/decks/{deck_id}/patches")
async def create_patch(
    deck_id: str,
    body: PatchBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await spec_edit.create_proposal(
        session,
        deck_id,
        expected_revision=body.expected_revision,
        summary=body.summary,
        source=body.source,
        operations=body.operations,
        actor_id=user.id,
        idempotency_key=body.idempotency_key,
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/patches/{patch_id}/apply")
async def apply_patch(
    deck_id: str,
    patch_id: str,
    body: ApplyBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await spec_edit.apply_proposal_by_id(
        session, deck_id, patch_id, expected_revision=body.expected_revision, actor_id=user.id
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/patches/{patch_id}/reject")
async def reject_patch(
    deck_id: str,
    patch_id: str,
    body: RejectBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await spec_edit.reject_proposal(session, deck_id, patch_id, reason=body.reason)
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/regen")
async def regenerate(
    deck_id: str,
    body: RegenBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    slide_id = str((body.target or {}).get("slide_id") or "")
    if (body.target or {}).get("kind") != "slide" or not slide_id:
        raise ApiError(ErrorCode.VALIDATION, "regen.target 必须是 {kind:'slide', slide_id}")
    data = await regen_service.regenerate_slide(
        session,
        deck_id,
        expected_revision=body.expected_revision,
        slide_id=slide_id,
        instruction=body.instruction,
        refs=body.refs,
        actor_id=user.id,
        mode=body.mode,  # type: ignore[arg-type]
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/pages")
async def add_page(
    deck_id: str,
    body: AddPageBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """AI 添加页：基于锚点页生成真实内容（禁止空白假页），返回 add_slide+move_slide 提案。"""
    from app.ppt_v2.service import pages as pages_service

    data = await pages_service.add_generated_page(
        session,
        deck_id,
        expected_revision=body.expected_revision,
        after_slide_id=body.after_slide_id,
        instruction=body.instruction,
        refs=body.refs,
        actor_id=user.id,
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/enrich")
async def enrich_deck(
    deck_id: str,
    body: EnrichBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """课件逐页增稠：入队异步作业（逐页 LLM 补全板书内容，写回新 revision）。

    长任务不在 API 进程执行（reliability 红线）→ 提交 taskiq 作业，前端轮询
    `GET /artifacts/exports/{job_id}` 取计数；完成后 `GET /artifacts/decks/{deck_id}`
    即读到增稠后的 revision。
    """
    from app.ppt_v2.service import enrich as enrich_service

    row = await spec_edit.load_deck_row(session, deck_id)
    if body.expected_revision != row.revision:
        payload = await spec_edit._build_conflict(  # noqa: SLF001
            session, row, expected_revision=body.expected_revision, operations=[]
        )
        raise spec_edit._conflict_error(payload)  # noqa: SLF001
    data = await enrich_service.submit_deck_enrich(
        session, user_id=user.id, deck_id=deck_id, refs=body.refs
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/figures")
async def ensure_figures(
    deck_id: str,
    body: FigureBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """为本课补齐真实数学配图（确定性资产，非 AI 生图；无候选不伪造）。"""
    from app.ppt_v2.service.deck_figure import ensure_deck_figure

    row = await spec_edit.load_deck_row(session, deck_id)
    if body.expected_revision != row.revision:
        payload = await spec_edit._build_conflict(  # noqa: SLF001
            session, row, expected_revision=body.expected_revision, operations=[]
        )
        raise spec_edit._conflict_error(payload)  # noqa: SLF001
    data = await ensure_deck_figure(
        session,
        deck_id,
        actor_id=user.id,
        refs=body.refs,
        max_figures=body.max_figures,
    )
    await session.commit()
    return ok(data or {"changed": False, "figures_added": 0}, request_id=_rid(request))


# ── 逐页 QA ─────────────────────────────────────────────────────────────────
async def _pptx_for_deck(session: AsyncSession, deck_id: str) -> tuple[Any, dict[int, bytes]]:
    """取最近一次真实导出的 PPTX 与引擎渲染出的逐页 PNG。"""
    artifact = await export_service.latest_artifact(
        session, spec_kind="teaching_deck", spec_id=deck_id, kind="pptx"
    )
    if artifact is None:
        raise ApiError(
            ErrorCode.CONFLICT, "该课件还没有真实导出的产物，无法做逐页质检（请先导出一次）"
        )
    return artifact, {}


@router.get("/decks/{deck_id}/qa")
async def get_qa(
    deck_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await spec_edit.load_deck_row(session, deck_id)
    artifact = await export_service.latest_artifact(
        session, spec_kind="teaching_deck", spec_id=deck_id, kind="pptx"
    )
    data = await qa_service.get_qa(
        session,
        row.id,
        spec_revision=row.revision,
        artifact_id=artifact.id if artifact is not None else None,
    )
    return ok(data, request_id=_rid(request))


@router.post("/decks/{deck_id}/qa/retry")
async def retry_qa(
    deck_id: str,
    body: QaRetryBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await spec_edit.load_deck_row(session, deck_id)
    if body.expected_revision != row.revision:
        payload = await spec_edit._build_conflict(  # noqa: SLF001
            session, row, expected_revision=body.expected_revision, operations=[]
        )
        raise spec_edit._conflict_error(payload)  # noqa: SLF001

    artifact, _ = await _pptx_for_deck(session, deck_id)
    engine_deck_id = ((artifact.source_lineage or {}).get("engine") or {}).get("deck_id")
    if not engine_deck_id:
        raise ApiError(ErrorCode.CONFLICT, "产物血缘缺少引擎 deck 引用，无法定位渲染页")

    from app.ppt_v2.adapters.deck_engine import DeckEngineClient

    engine = DeckEngineClient()
    engine_artifacts = await engine.list_artifacts(str(engine_deck_id))
    png_ref = next(
        (a for a in engine_artifacts if a.get("kind") == "PAGE_PNG" and int(a.get("page_no") or 0) == body.page_no),
        None,
    )
    # png_ref 可能为 None：引擎页图只登记引用时无字节，改走 8100 侧独立渲染

    store = get_storage()
    pptx_bytes = store.get_bytes(artifact.bucket, artifact.object_key)
    png_bytes: bytes | None = None
    if png_ref is not None:
        try:
            png_bytes = await engine.artifact_bytes(str(png_ref["id"]))
        except Exception:  # noqa: BLE001 - 引擎页图只登记引用时无字节，转本地独立渲染
            png_bytes = None
    if png_bytes is None:
        from app.ppt_v2.qa.render import render_pages

        rendered = await render_pages(pptx_bytes, page_no=body.page_no)
        png_bytes = rendered.get(body.page_no)
    if png_bytes is None:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"第 {body.page_no} 页无法取得渲染图，单页重试不可用")

    data = await qa_service.retry_page_qa(
        session,
        deck_id=row.id,
        spec_revision=row.revision,
        page_no=body.page_no,
        pptx_bytes=pptx_bytes,
        page_png=png_bytes,
        artifact_id=artifact.id,
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


# ── 导出 ────────────────────────────────────────────────────────────────────
@router.post("/decks/{deck_id}/exports")
async def start_deck_export(
    deck_id: str,
    body: ExportBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await spec_edit.load_deck_row(session, deck_id)
    if body.expected_revision != row.revision:
        payload = await spec_edit._build_conflict(  # noqa: SLF001
            session, row, expected_revision=body.expected_revision, operations=[]
        )
        raise spec_edit._conflict_error(payload)  # noqa: SLF001
    data = await export_service.submit_deck_export(
        session, user_id=user.id, deck_id=deck_id, fmt=body.format, refs=body.refs
    )
    return ok(data, request_id=_rid(request))


@router.get("/exports/{job_id}")
async def get_export_job(
    job_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await export_service.job_view(session, job_id, user_id=user.id)
    return ok(data, request_id=_rid(request))


@router.post("/exports/{job_id}/cancel")
async def cancel_export_job(
    job_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await export_service.cancel_job(session, job_id, user_id=user.id)
    return ok(data, request_id=_rid(request))


# ── 下载（必须在 /{artifact_id} 之前注册）────────────────────────────────────
@router.get("/decks/{deck_id}/artifacts")
async def list_deck_artifacts(
    deck_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """最近真实导出产物摘要（页数/大小）——S6 导出卡「页数·大小」的唯一真实来源。

    反空壳：只回真实 artifact 元数据（size_bytes = 实际文件字节）；从未导出过则为空，
    由前端诚实省略大小，禁止估算。
    """
    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    artifacts: dict[str, Any] = {}
    for kind in ("pptx", "pdf"):
        art = await export_service.latest_artifact(
            session, spec_kind="teaching_deck", spec_id=deck_id, kind=kind
        )
        if art is not None:
            artifacts[kind] = {
                "artifact_id": str(art.id),
                "kind": art.kind,
                "size_bytes": art.size_bytes,
                "created_at": art.created_at.isoformat() if art.created_at else None,
                "file_name": (art.acl or {}).get("filename"),
            }
    return ok(
        {"deck_id": deck_id, "pages": len(spec.slides), "artifacts": artifacts},
        request_id=_rid(request),
    )


@router.get("/downloads/{ticket}")
async def download_by_ticket(
    ticket: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    data, artifact = await artifact_service.redeem_ticket(
        session,
        ticket,
        actor_id=user.id,
        store=get_storage(),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        roles=[user.role] if user.role else None,
    )
    await session.commit()
    filename = (artifact.acl or {}).get("filename") or f"{artifact.kind}-{artifact.id}"
    return Response(
        content=data,
        media_type=artifact.media_type,
        headers={
            "X-Content-Sha256": artifact.content_sha256,
            "Content-Disposition": artifact_service.content_disposition(filename),
        },
    )


# ── artifact ────────────────────────────────────────────────────────────────
@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await artifact_service.get_artifact(session, artifact_id)
    # T25 P0-5：原读取路径完全不查权（下载闸门却查）→ 他人 PRIVATE 产物元数据/审计可读。
    allowed, reason = artifact_service.acl_allows(
        row, user.id, [user.role] if user.role else None
    )
    if not allowed:
        raise ApiError(ErrorCode.FORBIDDEN, f"无权访问该产物（{reason}）")
    return ok(artifact_service.artifact_view(row), request_id=_rid(request))


@router.get("/{artifact_id}/audit")
async def get_artifact_audit(
    artifact_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await artifact_service.get_artifact(session, artifact_id)
    allowed, reason = artifact_service.acl_allows(
        row, user.id, [user.role] if user.role else None
    )
    if not allowed:
        raise ApiError(ErrorCode.FORBIDDEN, f"无权查看该产物审计（{reason}）")
    rows = await artifact_service.list_audit(session, artifact_id)
    return ok({"artifact_id": artifact_id, "entries": rows}, request_id=_rid(request))


@router.post("/{artifact_id}/tickets")
async def create_ticket(
    artifact_id: str,
    body: TicketBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await artifact_service.get_artifact(session, artifact_id)
    allowed, reason = artifact_service.acl_allows(row, user.id, [user.role] if user.role else None)
    if not allowed:
        raise ApiError(ErrorCode.FORBIDDEN, f"无权下载该产物（{reason}）")
    data = await artifact_service.issue_ticket(
        session, row, actor_id=user.id, ttl_seconds=body.ttl_seconds
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/{artifact_id}/revoke")
async def revoke_artifact(
    artifact_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await artifact_service.get_artifact(session, artifact_id)
    if row.owner_id is not None and str(row.owner_id) != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "只有产物所有者可以撤销其下载权限")
    data = await artifact_service.revoke_artifact_access(session, row)
    await session.commit()
    return ok(data, request_id=_rid(request))


__all__ = ["router", "uuid"]
