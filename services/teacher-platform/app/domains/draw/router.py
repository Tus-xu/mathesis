# coding: utf-8
"""draw 域路由（画板图形库 + 手写公式识别 SSE）。手写识别 sidecar 未部署——诚实报错。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ...core.deps import get_current_user, get_session
from ...core.errors import ApiError, ErrorCode
from ...core.response import ok
from ...core.security import UserToken
from ...core.sse import sse_response
from ..figures.models import DrawLibraryItem

router = APIRouter()


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise ApiError(ErrorCode.VALIDATION, f"非法 UUID: {value}")


@router.get("/draw/library")
async def draw_library(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    # 归属过滤（审计 #9/L7 判据②）：shared 公开，私有仅属主可见
    from sqlalchemy import or_
    rows = (await session.execute(
        select(DrawLibraryItem).where(or_(DrawLibraryItem.shared.is_(True), DrawLibraryItem.owner_id == _uuid(user.id)))
        .order_by(DrawLibraryItem.created_at.desc()).limit(100)
    )).scalars().all()
    return ok({"items": [
        {
            "id": str(r.id), "name": r.name, "kind": r.kind, "thumb": r.thumb,
            "records": r.records, "expr": r.expr, "shared": bool(r.shared),
        }
        for r in rows
    ]}, request_id=request.state.request_id)


@router.post("/draw/library")
async def draw_library_save(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    name = (body.get("name") or "").strip()
    kind = body.get("kind")
    if not name or kind not in ("free", "fx"):
        raise ApiError(ErrorCode.VALIDATION, "name 必填且 kind ∈ free/fx")
    row = DrawLibraryItem(
        name=name, kind=kind, thumb=str(body.get("thumb") or ""),
        records=body.get("records"), expr=body.get("expr"),
        shared=bool(body.get("shared", False)), owner_id=_uuid(user.id),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ok({
        "id": str(row.id), "name": row.name, "kind": row.kind, "thumb": row.thumb,
        "records": row.records, "expr": row.expr, "shared": row.shared,
    }, request_id=request.state.request_id)


@router.post("/draw/hand-recognize")
async def hand_recognize(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> EventSourceResponse:
    """手写公式识别 SSE：meta → recognizing → result → done。

    诚实边界：手写识别推理 sidecar 未部署（8105/8103 占位），返回显式 error 事件，
    不返回假 LaTeX（R2：识别结果必须可载入 MathField 审查——假结果违反红线）。
    """
    import json

    async def gen():
        yield {"event": "meta", "data": json.dumps({"strokes": body.get("strokes")}, ensure_ascii=False)}
        yield {"event": "error", "data": json.dumps({
            "code": 50300,
            "message": "手写识别推理 sidecar 未部署（dependency_missing）——不返回猜测结果，请使用语音公式或公式编辑器",
        }, ensure_ascii=False)}
        yield {"event": "done", "data": json.dumps({"finish_reason": "dependency_missing"})}

    return sse_response(gen())
