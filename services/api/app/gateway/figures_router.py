"""GeoGebra 交互图形路由（/api/figures/*）

端点：
- POST /api/figures/ggb — 由题目文本生成 GeoGebra 交互构造（引导式解题/练习点"动态演示"用）

说明：本接口只生成构造命令（不落库）；需要持久化的场景（错题本）走
POST /api/student/error-records/{id}/figure。
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.models.database import get_db
from app.services.geogebra_figure import (
    build_ggb_payload,
    generate_ggb,
    resolve_image_data_uri,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/figures", tags=["figures"])


class GgbGenerateRequest(BaseModel):
    question_text: str = Field(default="", max_length=2000)
    figure_hint: str | None = Field(default=None, max_length=300)  # 图形说明/配图描述
    interactive: bool = False  # 是否要滑块/动点动态
    view: str | None = Field(default=None, pattern="^(2d|3d)$")  # 可选：强制透视
    file_id: str | None = Field(default=None)  # 可选：题目原图 file_id（视觉读图用）


@router.post("/ggb")
async def generate_ggb_figure(
    req: GgbGenerateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """题目文本（可带原图）-> GeoGebra 构造（学生端，best-effort；失败返回 50400 由前端降级静态图）"""
    if not (req.question_text or "").strip() and not (req.figure_hint or "").strip():
        return {"code": 40001, "message": "question_text 与 figure_hint 至少填一项"}

    image_data_uri = None
    if req.file_id:
        try:
            fid = uuid.UUID(req.file_id)
        except ValueError:
            return {"code": 40001, "message": "非法 file_id"}
        image_data_uri = await resolve_image_data_uri(db, fid, uuid.UUID(user["sub"]))

    try:
        ggb = await generate_ggb(
            req.question_text,
            figure_hint=req.figure_hint,
            interactive=req.interactive,
            image_data_uri=image_data_uri,
            user_id=user["sub"],
            db=db,
        )
        if not ggb:
            return {"code": 50400, "message": "动态图形生成失败，请稍后重试"}
    except Exception as e:
        # best-effort 端点：任何未处理异常都不得以 HTTP 500 逃逸——
        # 前端契约是 code=50400 时降级静态图（2026-08-30 实测 500 事故）
        logger.warning("figures.ggb_failed", error=str(e)[:200])
        return {"code": 50400, "message": "动态图形生成失败，请稍后重试"}

    view = req.view if req.view in ("2d", "3d") else ggb["view"]
    return {
        "code": 0,
        "data": {"ggb": build_ggb_payload(ggb["commands"], view, caption=req.figure_hint or "")},
    }
