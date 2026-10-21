"""8201 Presenton adapter API：health + R21 真实渲染引擎端点。

- POST /api/v1/deck/pages/render  逐页构建（可编辑 OOXML）+ PowerPoint 渲染 + QA；
  内容寻址（content_sha256）幂等：同内容重复请求命中缓存（cache_hit=true）；
- POST /api/v1/deck/assemble      装配最终 PPTX + 打开检查 + contact sheet；
  deck_key（builder+spec_hash+page_refs）幂等：同请求零重建、零重复产物；
- POST /api/v1/deck/export/pdf    T24：PPTX 字节 → 真实 PDF（PowerPoint COM SaveAs 32；失败诚实失败）；
- GET  /api/v1/artifacts/{ref}    artifact 元数据（存在性/大小/类型）；
- 故障注入（仅 dev）：GET /api/v1/simulate 切换 down/503/unreachable 供 e2e 使用。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from presenton_adapter.config import get_settings
from presenton_adapter.engine import pdf_export
from presenton_adapter.engine import qa as qa_mod
from presenton_adapter.engine.assembler import assemble_deck
from presenton_adapter.engine.builder import BUILDER_VERSION, PageRequest
from presenton_adapter.engine.figures import math_object_figure_cached
from presenton_adapter.engine.layout_resolver import MATH_OBJECT_ASSET_ID
from presenton_adapter.engine.store import ArtifactStore

router = APIRouter()

# 内存故障开关（仅 dev，重启即清零）
_fail_mode = "none"
_store: ArtifactStore | None = None


def _engine_store() -> ArtifactStore:
    global _store  # noqa: PLW0603
    if _store is None:
        s = get_settings()
        _store = ArtifactStore(Path(s.engine_data_dir))
    return _store


def _guard() -> None:
    if _fail_mode != "none":
        raise HTTPException(status_code=503, detail=f"simulated:{_fail_mode}")


class RenderPageBody(BaseModel):
    deck_id: str = Field(min_length=1)
    deck_title: str = Field(min_length=1)
    page_no: int = Field(ge=1)
    total_pages: int = Field(ge=1, le=99)
    slide: dict[str, Any]
    assets_bytes: dict[str, str] = Field(default_factory=dict)  # asset_id -> b64
    visual_plan: dict[str, Any] | None = None  # R21.1：ResolvedVisualPlan per-slide（无坐标）
    deck_style_tokens: dict[str, Any] | None = None  # R21.1：deck 级 tokens（字号/色彩档位）
    render: bool = True


class AssembleBody(BaseModel):
    deck_id: str = Field(min_length=1)
    deck_title: str = Field(min_length=1)
    deck_spec_sha256: str = Field(min_length=64, max_length=64)
    pages: list[dict[str, Any]]  # [{page_no, page_ref, content_sha256, png_ref?}]


class ExportPdfBody(BaseModel):
    """T24：pptx 字节由调用方（8100，经 8200 取得）传入，不经引擎存储。"""

    pptx_b64: str = Field(min_length=1)
    file_stem: str = Field(default="deck", min_length=1, max_length=80)


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "service": "presenton-adapter", "ts": int(time.time())}


@router.get("/api/v1/health")
async def health() -> dict:
    if _fail_mode == "unreachable":
        import sys

        sys.exit(1)  # noqa: PLR1722  (只读副作用：进程退出模拟不可达)
    if _fail_mode != "none":
        raise HTTPException(status_code=503, detail=f"simulated:{_fail_mode}")
    return {"status": "ok", "service": "presenton-adapter", "engine": BUILDER_VERSION}


@router.post("/api/v1/deck/pages/render")
async def render_page(body: RenderPageBody) -> dict:
    _guard()
    settings = get_settings()
    store = _engine_store()
    assets_bytes = {aid: base64.b64decode(b64) for aid, b64 in body.assets_bytes.items()}
    # R8-U：页承载参数化 math_object → 引擎确定性渲染为 PNG 并注入 figure 资产槽。
    # 渲染失败 = 可见失败（422 → 8200 判 EngineRejected，页失败并带真实原因），
    # 绝不静默产出「无图 PPTX」。
    math_object = body.slide.get("math_object") if isinstance(body.slide, dict) else None
    if isinstance(math_object, dict):
        try:
            assets_bytes[MATH_OBJECT_ASSET_ID] = math_object_figure_cached(math_object)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail=f"math_object 渲染失败（图形必须真实内嵌）: {type(exc).__name__}: {exc}",
            ) from exc
    total_bytes = sum(len(b) for b in assets_bytes.values())
    if total_bytes > settings.max_artifact_bytes:
        raise HTTPException(status_code=413, detail="assets exceed engine budget")
    req = PageRequest(
        deck_id=body.deck_id,
        deck_title=body.deck_title,
        page_no=body.page_no,
        total_pages=body.total_pages,
        slide=body.slide,
        assets_bytes=assets_bytes,
        visual_plan=body.visual_plan,
        deck_style_tokens=body.deck_style_tokens,
    )
    result = await qa_mod.run_page_qa(req, store, render=body.render)
    return {
        "ok": result.ok,
        "content_sha256": result.content_sha256,
        "fragment_ref": result.fragment_ref,
        "png_ref": result.png_ref,
        "fragment_sha256": result.fragment_ref,
        "png_sha256": result.png_ref,
        "fragment_size": result.fragment_size,
        "png_size": result.png_size,
        "qa": result.qa,
        "errors": result.errors,
        "elapsed_ms": result.elapsed_ms,
        "engine": BUILDER_VERSION,
    }


@router.post("/api/v1/deck/assemble")
async def assemble(body: AssembleBody) -> dict:
    _guard()
    settings = get_settings()
    if not body.pages:
        raise HTTPException(status_code=422, detail="pages empty")
    ordered = sorted(body.pages, key=lambda p: int(p["page_no"]))
    page_refs = [str(p["page_ref"]) for p in ordered]
    for p in ordered:
        if not _engine_store().exists(str(p["page_ref"])):
            raise HTTPException(status_code=409, detail=f"page_ref not found: {str(p['page_ref'])[:12]}")
    # R3d 去重复渲染：逐页 QA 真实页图（png_ref）随装配下发 → contact sheet 直接复用；
    # 任一页图缺位则整体回退整册渲染（如实降级，不拿半套图充数）。
    page_pngs: list[bytes] = []
    for p in ordered:
        ref = str(p.get("png_ref") or "")
        data = _engine_store().get(ref) if ref else None
        page_pngs.append(data if data else b"")
    res = await assemble_deck(
        _engine_store(),
        deck_id=body.deck_id,
        deck_title=body.deck_title,
        deck_spec_sha256=body.deck_spec_sha256,
        page_refs=page_refs,
        page_nos=[int(p["page_no"]) for p in ordered],
        budget_timeout_s=settings.assemble_timeout_s,
        page_png_bytes=page_pngs if all(page_pngs) else None,
    )
    if res.errors:
        # 装配/渲染失败 = 真实失败：如实返回 500 语义信息，不生成假成功
        raise HTTPException(status_code=500, detail="; ".join(res.errors))
    return {
        "deck_key": res.deck_key,
        "deck_ref": res.deck_ref,
        "deck_sha256": res.deck_sha256,
        "size_bytes": res.size_bytes,
        "page_count": res.page_count,
        "contact_sheet_ref": res.contact_ref,
        "openable": res.openable,
        "elapsed_ms": res.elapsed_ms,
        "engine": BUILDER_VERSION,
    }


@router.post("/api/v1/deck/export/pdf")
async def export_deck_pdf(body: ExportPdfBody) -> dict:
    """PPTX 字节 → 真实 PDF（PowerPoint COM `SaveAs(path, 32)`）。

    失败诚实失败：COM 不可用/超时/产物非法 → 4xx/503 错误体，**不**返回假 PDF。
    """
    _guard()
    settings = get_settings()
    try:
        pptx_bytes = base64.b64decode(body.pptx_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"pptx_b64 不是合法 base64: {exc}") from exc
    if not pptx_bytes:
        raise HTTPException(status_code=422, detail="pptx_b64 解码为空字节")
    if pptx_bytes[:2] != b"PK":
        raise HTTPException(status_code=422, detail="pptx_b64 不是 OOXML(pptx) 字节（缺 PK 魔数）")
    if len(pptx_bytes) > settings.max_artifact_bytes:
        raise HTTPException(status_code=413, detail="pptx 超过引擎预算")
    try:
        res = await pdf_export.export_pdf_sync(
            pptx_bytes, file_stem=body.file_stem, timeout_s=settings.export_pdf_timeout_s
        )
    except pdf_export.PdfExportUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"powerpoint-com 不可用: {exc}") from exc
    return {
        "pdf_b64": base64.b64encode(res.pdf_bytes).decode("ascii"),
        "page_count": res.page_count,
        "bytes": len(res.pdf_bytes),
        "sha256": hashlib.sha256(res.pdf_bytes).hexdigest(),
        "engine": {"name": res.engine_name, "version": res.engine_version},
    }


@router.get("/api/v1/artifacts/{ref}")
async def artifact_meta(ref: str) -> dict:
    store = _engine_store()
    data = store.get(ref)
    if data is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {
        "ref": ref,
        "sha256": ArtifactStore.sha256(data),
        "size_bytes": len(data),
    }


@router.get("/api/v1/artifacts/{ref}/content")
async def artifact_content(ref: str):
    """字节获取（8200 校验/留存产物用；预算内）。"""
    _guard()
    settings = get_settings()
    store = _engine_store()
    data = store.get(ref)
    if data is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    if len(data) > settings.max_artifact_bytes:
        raise HTTPException(status_code=413, detail="artifact exceeds budget")
    # 按内容嗅探媒体类型
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    is_pptx = data[:2] == b"PK"
    media = "image/png" if is_png else ("application/vnd.openxmlformats-officedocument.presentationml.presentation" if is_pptx else "application/octet-stream")
    from fastapi import Response

    return Response(content=data, media_type=media, headers={"Content-Length": str(len(data))})


@router.get("/api/v1/simulate")
async def simulate(request: Request) -> JSONResponse:
    """仅开发允许注入故障（服务令牌保护；重启即清零）。"""
    settings = get_settings()
    if not settings.simulate_allowed or settings.environment == "production":
        raise HTTPException(status_code=403, detail="simulation disabled")
    global _fail_mode  # noqa: PLW0603
    mode = request.query_params.get("mode", "none")
    if mode not in ("none", "down", "503", "unreachable"):
        raise HTTPException(status_code=422, detail=f"invalid mode {mode!r}")
    _fail_mode = mode
    return JSONResponse({"fail_mode": _fail_mode})


def current_fail_mode() -> str:
    return _fail_mode
