"""B3 平台端点（auth/login、upload/presign、任务中心、demo 全链 SSE）。

前缀由 routes.py 统一挂载为 `/api/teacher-v3`（契约 BASE='/api' + teacher-v3/*）。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.deps import get_current_user, get_session
from ..core.errors import ApiError, ErrorCode
from ..core.hub import get_hub
from ..core.response import fail, ok
from ..core.security import UserToken, create_access_token, verify_seed_login
from ..db.models import Tasks
from ..storage.minio import get_storage
from ..tasks.service import TaskService
from .schemas import DemoStreamResponse, LoginRequest, PresignRequest, PresignResponse

router = APIRouter()


# ---------- 鉴权 ----------

@router.post("/auth/login")
async def login(body: LoginRequest, request: Request) -> dict:
    user = verify_seed_login(body.username, body.password)
    if user is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "用户名或密码错误")
    token = create_access_token(user_id=user.id, name=user.name, role=user.role, school_id=user.school_id)
    request.state.user_id = user.id
    return ok({"token": token, "user": {"id": user.id, "name": user.name, "role": user.role, "school_id": user.school_id}}, request_id=request.state.request_id)


# ---------- 对象存储（presign 直传）----------

@router.post("/upload/presign")
async def upload_presign(body: PresignRequest, request: Request, user: UserToken = Depends(get_current_user)) -> dict:
    """presign PUT 直传。前端契约（teacherV3Upload）：发 {filename,content_type,size}，
    回 {key,url,headers}；bucket 由内容类型推导（图片→teacher-photos，其余→teacher-assets）。"""
    storage = get_storage()
    if body.bucket:  # 旧骨架形状
        bucket = body.bucket
        object_name = body.object_name or f"uploads/{user.id}/{uuid.uuid4().hex}.bin"
    else:  # 前端契约形状
        ctype = body.content_type or "application/octet-stream"
        bucket = "teacher-photos" if ctype.startswith("image/") else "teacher-assets"
        ext = ""
        if body.filename and "." in body.filename:
            ext = "." + body.filename.rsplit(".", 1)[-1].lower()[:8]
        object_name = f"originals/{user.id}/{uuid.uuid4().hex}{ext or '.bin'}"
    url = storage.presign_put(bucket, object_name, expires_seconds=900)
    return ok(
        {
            "url": url,
            "object_name": object_name,
            "key": object_name,
            "headers": {},
        },
        request_id=request.state.request_id,
    )


@router.post("/upload/file")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    user: UserToken = Depends(get_current_user),
) -> dict:
    """服务端中转上传（浏览器 → 8100 → MinIO），2026-10-05 修复「上传题目入库不可用」。

    为什么不再用 presign 直传：
      1. 线上 `teacher-minio` 只在 compose 内网（**无宿主端口映射**），浏览器根本
         PUT 不到 —— 直传在服务器上必挂；
      2. 本地直传还会被 MinIO 的 CORS 策略拦（浏览器 XHR PUT 跨源）；
      3. 直传失败后前端曾回落把整个 base64 dataURL 塞进 `stem_image`，
         留下一批裂图垃圾行（用户看到的「题库图片显示不出来」正是这批）。
    统一走后端中转：key 语义不变（DB 仍只存 key），前端拿到的 `url` 是
    presigned GET，`<img>` 可直接渲染。
    """
    data = await file.read()
    if not data:
        raise ApiError(ErrorCode.VALIDATION, "上传内容为空")
    if len(data) > 20 * 1024 * 1024:
        raise ApiError(ErrorCode.VALIDATION, "文件超过 20MB 上限")
    ctype = file.content_type or "application/octet-stream"
    bucket = "teacher-photos" if ctype.startswith("image/") else "teacher-assets"
    ext = ""
    if file.filename and "." in file.filename:
        ext = "." + file.filename.rsplit(".", 1)[-1].lower()[:8]
    object_name = f"originals/{user.id}/{uuid.uuid4().hex}{ext or '.bin'}"
    storage = get_storage()
    storage.put_bytes(bucket, object_name, data, content_type=ctype)
    url = storage.presign_get(bucket, object_name, expires_seconds=3600)
    return ok({"key": object_name, "bucket": bucket, "url": url}, request_id=request.state.request_id)


# ---------- 任务中心（真实数据，契约形状 {items, running}）----------

_TASK_STATUS_MAP = {"pending": "queued", "running": "running", "done": "succeeded", "failed": "failed", "cancelled": "failed"}
_TASK_STAGE_MAP = {"done": "完成", "running": "进行中", "failed": "失败", "pending": "等待", "cancelled": "已取消"}
_TASK_TITLE_MAP = {"demo_sleep": "平台演示任务", "export_pptx": "课件导出 PPTX", "export_pdf": "教案导出 PDF"}


def _task_to_contract(row: Tasks) -> dict:
    status = _TASK_STATUS_MAP.get(row.status, "queued")
    return {
        "task_id": str(row.id),
        "title": _TASK_TITLE_MAP.get(row.type, f"任务（{row.type}）"),
        "capability": "platform",
        "status": status,
        "progress": row.progress,
        "stage": _TASK_STAGE_MAP.get(row.status, "等待"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/tasks")
async def list_tasks(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    # sub 允许任意字符串（JWT claim 契约）；非 UUID 用户 id 容错返回空列表（不 500）
    try:
        user_uuid = uuid.UUID(user.id)
    except (ValueError, AttributeError, TypeError):
        return ok({"items": [], "running": 0}, request_id=request.state.request_id)
    rows = (await session.execute(select(Tasks).where(Tasks.user_id == user_uuid).order_by(Tasks.created_at.desc()).limit(50))).scalars().all()
    items = [_task_to_contract(r) for r in rows]
    running = sum(1 for t in items if t["status"] in ("running", "queued"))
    return ok({"items": items, "running": running}, request_id=request.state.request_id)


# ---------- demo 全链（API→worker→SSE→断线重连零丢失）----------

@router.post("/platform/demo/sleep")
async def demo_sleep(request: Request, user: UserToken = Depends(get_current_user), seconds: float = 2.0) -> dict:
    task = await TaskService.submit("demo_sleep", {"seconds": seconds}, user.id)
    return ok(
        {
            **task,
            "stream_url": f"/api/teacher-v3/platform/demo/tasks/{task['id']}/stream",
            "message": "任务已提交，worker 正在执行（SSE 见 stream_url）",
        },
        request_id=request.state.request_id,
    )


@router.get("/platform/demo/tasks/{task_id}/stream")
async def demo_task_stream(task_id: str, request: Request, user: UserToken = Depends(get_current_user)):
    """SSE 双通道：snapshot + after(Last-Event-ID) durable 重放 + live 续接（P9 断线零丢失）。"""
    from ..core.sse import sse_channel

    async def snapshot_fn() -> dict:
        task = await TaskService.get(task_id)
        return task or {"id": task_id, "status": "unknown"}

    async def replay_fn(after_seq: int) -> list[dict]:
        from ..core.events import get_publisher

        return await get_publisher().replay(scope_kind="task", scope_id=task_id, after_seq=after_seq)

    return await sse_channel(
        request,
        snapshot_fn=snapshot_fn,
        replay_fn=replay_fn,
        live_channel=f"task:{task_id}",
    )


# ---------- RealtimeHub 传输层（课堂 SSE 完整实现已移至 domains/classroom/router.py，
# rescue 整合 2026-09-07：B4 snapshot provider 注入完成后由域路由提供权威投影；
# 此处保留的传输层骨架端点与域路由路径重复，已移除以免遮蔽真实实现） ----------


# ---------- 错误响应（契约壳）----------

@router.get("/__error_demo__")
async def error_demo(request: Request) -> dict:
    """演示 fail 信封（非契约端点，仅供本地调试）。"""
    return fail(ErrorCode.ROLE_DENIED, "role_denied", request_id=request.state.request_id, error_key="role_denied")
