# coding: utf-8
"""figures / draw 域路由（图形 preset / 配方 / 画板库 / 手写识别）。

figure_presets 种子（27 个，B4 seeds.py 真实数据）首次访问幂等落库。
手写识别（/draw/hand-recognize SSE）依赖推理 sidecar——未部署，诚实报错。
"""
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
from .models import DrawLibraryItem, FigurePreset, Recipe

router = APIRouter()


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise ApiError(ErrorCode.VALIDATION, f"非法 UUID: {value}")


async def _ensure_presets(session: AsyncSession) -> None:
    """幂等种子：figure_presets 空表时从 B4 seeds.py 落 27 个真实 preset。"""
    count = (await session.execute(select(FigurePreset.id).limit(1))).scalar_one_or_none()
    if count is not None:
        return
    from .seeds import preset_rows

    for row in preset_rows():
        session.add(FigurePreset(**row))
    await session.commit()


@router.get("/figures/presets")
async def list_presets(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _ensure_presets(session)
    rows = (await session.execute(select(FigurePreset).order_by(FigurePreset.id))).scalars().all()
    return ok({"items": [
        {"id": p.id, "kind": p.kind, "name": p.name, "desc": p.desc, "level": p.level, "params_schema": p.params_schema}
        for p in rows
    ]}, request_id=request.state.request_id)


@router.post("/figures/recipes")
async def save_recipe(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    name = (body.get("name") or "").strip()
    preset_id = body.get("preset_id")
    if not name or not preset_id:
        raise ApiError(ErrorCode.VALIDATION, "name 与 preset_id 必填")
    preset = (await session.execute(select(FigurePreset).where(FigurePreset.id == preset_id))).scalar_one_or_none()
    if preset is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "图形 preset 不存在")
    params = body.get("params")
    if not isinstance(params, dict):
        raise ApiError(ErrorCode.VALIDATION, "params 必填（对象）")
    row = Recipe(
        name=name, preset_id=preset_id, params=params,
        school_shared=bool(body.get("school_shared", False)),
        note=str(body.get("note") or ""),
        owner_id=_uuid(user.id),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ok({
        "id": str(row.id), "name": row.name, "preset_id": row.preset_id,
        "params": row.params, "school_shared": row.school_shared, "note": row.note,
    }, request_id=request.state.request_id)
