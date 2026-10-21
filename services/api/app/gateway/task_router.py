"""任务中心与站内通知路由（task_router · 阶段3 G 系列）

端点（验收用例 G-1..G-9 / S-B5 / S-B6）：
- GET    /api/tasks                      任务列表（本人；?status=&since=&limit=）
- GET    /api/tasks/{task_id}            任务详情（读路径惰性自愈，对齐 classroom 范式）
- POST   /api/tasks/{task_id}/cancel     取消（queued/running）
- POST   /api/tasks/{task_id}/retry      重试（failed/cancelled；幂等不重复产物）
- GET    /api/notifications              通知列表（?unread_only=&limit=）
- GET    /api/notifications/unread-count 未读数（红点）
- POST   /api/notifications/{id}/read    标记已读
- POST   /api/notifications/read-all     全部已读

约定：信封 {code:0, message:"ok", data:...}；鉴权 get_current_user；
越权一律 40400（不暴露他人任务存在性，G-6）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.models.database import get_db
from app.models.task import Notification, Task
from app.services import task_runner

logger = structlog.get_logger(__name__)

tasks_router = APIRouter(prefix="/api/tasks", tags=["任务中心"])
notifications_router = APIRouter(prefix="/api/notifications", tags=["站内通知"])


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


def _err(code: int, message: str) -> dict:
    return {"code": code, "message": message}


def _task_row(task: Task) -> dict:
    return {
        "task_id": str(task.id),
        "kind": task.kind,
        "kind_label": task_runner.handler_label(task.kind),
        "role": task.role,
        "status": task.status,
        "progress": task.progress or 0,
        "stage": task.stage,
        "payload": task.payload or {},
        "result": task.result,
        "error": task.error,
        "attempt": task.attempt or 0,
        "elapsed_ms": task.elapsed_ms,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


async def _get_owned_task(db: AsyncSession, user_id: uuid.UUID, task_id: str) -> Task | None:
    """本人任务；不存在/非本人 → None（路由层统一 40400，防存在性探测）。"""
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        return None
    result = await db.execute(
        select(Task).where(Task.id == tid, Task.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none()


# ==================== 任务 ====================

# 一期开放下单的任务类型（kind 白名单；新处理器上线后在此登记）
_CREATABLE_KINDS = {
    "practice.generate",
    "classroom.session",
    "socratic.autosolve",
    "teacher.quiz.create",
    "teacher.materials",
}


class TaskCreateRequest(BaseModel):
    kind: str
    payload: dict = {}
    idempotency_key: str | None = None  # 缺省服务端生成（不保证幂等）；显式传入可防重复下单


@tasks_router.post("")
async def create_task(
    req: TaskCreateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下单后台任务（G-1/S-B2 入口；管家对话与前端任务中心共用）。"""
    user_id = uuid.UUID(user["sub"])
    if req.kind not in _CREATABLE_KINDS:
        return _err(40001, f"不支持的任务类型: {req.kind}")
    idem = req.idempotency_key or f"auto:{uuid.uuid4()}"
    idem = idem[:160]
    task, created = await task_runner.create_task(
        db,
        user_id=user_id,
        role=user.get("active_role", "student"),
        kind=req.kind,
        payload=req.payload,
        idempotency_key=idem,
    )
    await db.commit()
    if created:
        task_runner.spawn(task)
    return _ok({**_task_row(task), "created": created})


@tasks_router.get("")
async def list_tasks(
    status: str | None = None,
    since: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """本人任务列表，created_at 倒序；since=ISO 时间只返回该时刻后更新的（增量轮询 G-7）。"""
    user_id = uuid.UUID(user["sub"])
    stmt = select(Task).where(Task.user_id == user_id).order_by(Task.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Task.status == status)
    if since:
        try:
            # 容错：未编码的 query 里 ISO 时间的 "+" 会被解析成空格
            dt = datetime.fromisoformat(since.replace(" ", "+"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            stmt = stmt.where(Task.updated_at >= dt)
        except ValueError:
            return _err(40001, "非法 since 时间格式")
    rows = (await db.execute(stmt)).scalars().all()
    # 读路径惰性自愈（G-3 兜底：轮询详情/列表也能触发重启遗留任务的重新拉起）
    for t in rows:
        if t.status in ("queued", "running"):
            await task_runner.maybe_resume(db, t)
    return _ok({"items": [_task_row(t) for t in rows]})


@tasks_router.get("/{task_id}")
async def task_detail(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user["sub"])
    task = await _get_owned_task(db, user_id, task_id)
    if task is None:
        return _err(40400, "任务不存在")
    await task_runner.maybe_resume(db, task)
    return _ok(_task_row(task))


@tasks_router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user["sub"])
    task = await _get_owned_task(db, user_id, task_id)
    if task is None:
        return _err(40400, "任务不存在")
    if task.status not in ("queued", "running"):
        return _err(40901, f"任务已 {task.status}，不能取消")
    await task_runner.cancel_task(db, task)
    return _ok(_task_row(task))


@tasks_router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user["sub"])
    task = await _get_owned_task(db, user_id, task_id)
    if task is None:
        return _err(40400, "任务不存在")
    if task.status not in ("failed", "cancelled"):
        return _err(40901, f"任务当前为 {task.status}，无需重试")
    await task_runner.retry_task(db, task)
    return _ok(_task_row(task))


# ==================== 通知 ====================


def _notification_row(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "payload": n.payload or {},
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@notifications_router.get("")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user["sub"])
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    return _ok({"items": [_notification_row(n) for n in rows]})


@notifications_router.get("/unread-count")
async def unread_count(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user["sub"])
    total = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    return _ok({"count": int(total.scalar_one() or 0)})


@notifications_router.post("/read-all")
async def read_all(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user["sub"])
    rows = (await db.execute(select(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    ))).scalars().all()
    now = datetime.now(timezone.utc)
    for n in rows:
        n.read_at = now
    await db.commit()
    return _ok({"marked": len(rows)})


@notifications_router.post("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = uuid.UUID(user["sub"])
    try:
        nid = uuid.UUID(notification_id)
    except ValueError:
        return _err(40400, "通知不存在")
    result = await db.execute(
        select(Notification).where(Notification.id == nid, Notification.user_id == user_id).limit(1)
    )
    n = result.scalar_one_or_none()
    if n is None:
        return _err(40400, "通知不存在")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        await db.commit()
    return _ok(_notification_row(n))
