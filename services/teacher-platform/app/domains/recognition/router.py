# coding: utf-8
"""recognition 域路由（拍照入库 SSE / 识别相关端点）。

诚实边界：pix2text（8103）/funasr（8104）/katex（8105）推理 sidecar 当前为
busybox 占位容器，OCR/VLM 管线不可用。凡依赖识别的端点返回 DEPENDENCY_UNAVAILABLE
（或 SSE error 事件），绝不返回假识别块/假 LaTeX（05-MOCK-DISSECTION 副作用清单）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from ...core.deps import get_current_user
from ...core.errors import ApiError, ErrorCode
from ...core.security import UserToken
from ...core.sse import sse_response

router = APIRouter()

_SIDEcars = "OCR/识别推理 sidecar（pix2text:8103）未部署——识别链路不可用，不返回模拟结果"


@router.post("/recognition/photo-ingest")
async def photo_ingest(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> EventSourceResponse:
    """拍照出课件（SSE）：meta → photo* → block* → paginate → done。

    上传/增强/OCR/block 合并/kp 建议全链需要真实推理管线；sidecar 未部署时
    仅发 meta + 显式 error，不产生假识别块。
    """
    import json

    photos = body.get("photos") or []
    async def gen():
        yield {"event": "meta", "data": json.dumps({"total": len(photos)}, ensure_ascii=False)}
        yield {"event": "error", "data": json.dumps({
            "code": 50300,
            "message": "OCR/识别推理 sidecar（pix2text:8103）未部署——识别链路不可用，不返回模拟结果。"
                       "原图可通过上传通道（POST /upload/presign → PUT）留存；识别管线部署后可重新发起",
        }, ensure_ascii=False)}
        yield {"event": "done", "data": json.dumps({"finish_reason": "dependency_missing"})}

    return sse_response(gen())


@router.post("/recognition/rebuild")
async def rebuild(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> dict:
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "图形重建（VLM 读题→构造校验门）推理通道未部署，不返回候选")


@router.post("/recognition/photo-to-formula")
async def photo_to_formula(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> dict:
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "公式识别（pix2text）未部署，请使用语音公式或公式编辑器")


@router.post("/recognition/plan-photo-draft")
async def plan_photo_draft(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> dict:
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "教案拍照填充（OCR+整理）未部署，不返回草稿")


@router.post("/recognition/preview")
async def recognition_preview(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> dict:
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "识别预览（pix2text）未部署——前端将以真实失败态呈现")
