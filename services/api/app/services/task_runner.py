"""通用后台任务执行器（TaskRunner · 阶段3 G 系列）

设计参照（详见 deliverables/ai-butler-phase0/技术基准分析.md）：
- 执行范式 = 自有 classroom 生成范式推广（stage_router.py:2794 create_task +
  独立后台会话 + DB 状态 + stale 自愈 2916），生成与 HTTP/SSE 连接解耦——
  用户离开页面任务继续跑，进程重启后由 stale 扫描重新拉起。
- 状态机字段 = Dify WorkflowRun（status/outputs/error/elapsed_time/finished_at）。
- 并发闸 = asyncio.Semaphore（全局 4 / 每用户 2）；批量速率闸二期升级漏桶。

纪律：
- 任务行在业务事务内创建并随业务一起 commit（规避 butler v3.0 方案遗留
  「异步 emit 语义」待确认 #11：同库同事务，不漏不阻塞）。
- 幂等：idempotency_key 唯一，同键返回已有行；重试 = 同行 reset + attempt+1，
  处理器按 result 已存在短路，防重复产物。
- 通知：终态写 notifications，dedup_key 唯一 = 同任务同终态同 attempt 只推一条。
- 取消：协作式——cancel 只置状态位，执行流在里程碑检查点感知后停止收尾。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import background_session_factory
from app.models.task import Notification, Task

logger = structlog.get_logger()

# stale 自愈阈值：running/queued 且无活跃执行者且超过该时长 → 视为进程重启遗留，重新拉起
TASK_STALE_SECONDS = 120
RESUME_SCAN_INTERVAL_S = 30

# 并发闸：全局 4 / 每用户 2（G-8）
_GLOBAL_SEM = asyncio.Semaphore(4)
_USER_SEMS: dict[str, asyncio.Semaphore] = {}

# 活跃执行者登记：task_id -> asyncio.Task（stale 扫描据此区分「排队等待并发闸」与「进程重启遗留」）
_ACTIVE: dict[str, asyncio.Task] = {}

# 处理器注册表：kind -> async fn(task, db, progress) -> result dict
_HANDLERS: dict[str, Callable[[Task, AsyncSession, Callable], Awaitable[dict]]] = {}

_resume_loop_task: asyncio.Task | None = None


class TaskPermanentError(Exception):
    """不可重试类失败（参数非法/配额不足等），错误信息直接透出给用户。"""


def register_handler(
    kind: str,
) -> Callable[[Callable[[Task, AsyncSession, Callable], Awaitable[dict]]], Any]:
    """注册任务处理器：@register_handler("practice.generate")"""

    def deco(fn):
        _HANDLERS[kind] = fn
        return fn

    return deco


def _user_sem(user_id: str) -> asyncio.Semaphore:
    return _USER_SEMS.setdefault(user_id, asyncio.Semaphore(2))


async def progress_cb_factory(task: Task, db: AsyncSession) -> Callable[[str, int], Awaitable]:
    """构造里程碑进度回调：更新 stage/progress 并落库（协作取消检查点）。"""

    async def progress(stage: str, pct: int) -> None:
        await db.refresh(task)
        if task.status == "cancelled":
            raise asyncio.CancelledError()
        task.stage = (stage or "")[:200]
        task.progress = max(task.progress or 0, min(100, int(pct)))
        await db.commit()

    return progress


async def create_task(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    role: str,
    kind: str,
    payload: dict,
    idempotency_key: str,
) -> tuple[Task, bool]:
    """业务事务内创建任务行（随业务一起 commit）。幂等：同 key 返回已有行。

    返回 (task, created)。
    """
    existing = await db.execute(select(Task).where(Task.idempotency_key == idempotency_key))
    task = existing.scalar_one_or_none()
    if task is not None:
        return task, False
    task = Task(
        user_id=user_id,
        role=role,
        kind=kind,
        payload=payload or {},
        idempotency_key=idempotency_key,
    )
    db.add(task)
    await db.flush()
    logger.info("task.created", task_id=str(task.id), kind=kind, user_id=str(user_id))
    return task, True


def spawn(task: Task) -> None:
    """调度执行（业务 commit 之后调用；进程内 create_task，与连接解耦）。"""
    key = str(task.id)
    if key in _ACTIVE:
        return
    _ACTIVE[key] = asyncio.get_running_loop().create_task(_run(key))


async def _run(task_id: str) -> None:
    """执行体：独立后台会话（NullPool），状态全程落库。"""
    t0 = time.monotonic()
    try:
        async with background_session_factory() as db:
            task = await db.get(Task, uuid.UUID(task_id))
            if task is None or task.status in ("succeeded", "cancelled"):
                return
            user_id = str(task.user_id)
            async with _GLOBAL_SEM, _user_sem(user_id):
                # 并发闸排队期间可能已被取消/重试调度：拿到信号量先复核终态，
                # 防止排队取消的任务被 pending runner 复活为 running（竞态修复）
                await db.refresh(task)
                if task.status in ("succeeded", "failed", "cancelled"):
                    logger.info("task.skip_terminal_before_run", task_id=task_id, status=task.status)
                    return
                task.status = "running"
                task.stage = task.stage or "准备中"
                task.attempt = (task.attempt or 0) + 1
                await db.commit()
                try:
                    handler = _HANDLERS.get(task.kind)
                    if handler is None:
                        raise TaskPermanentError(f"未注册的任务类型: {task.kind}")
                    progress = await progress_cb_factory(task, db)
                    result = await handler(task, db, progress)
                    # 协作取消：handler 内检查点可能已把状态置 cancelled
                    await db.refresh(task)
                    if task.status == "cancelled":
                        logger.info("task.cancelled_midway", task_id=task_id)
                        return
                    task.status = "succeeded"
                    task.result = result or {}
                    task.progress = 100
                    task.stage = "已完成"
                    task.error = None
                    task.elapsed_ms = int((time.monotonic() - t0) * 1000)
                    task.finished_at = datetime.now(timezone.utc)
                    await _notify_terminal(db, task, ok=True)
                    await db.commit()
                    logger.info(
                        "task.succeeded", task_id=task_id, kind=task.kind,
                        elapsed_ms=task.elapsed_ms,
                    )
                except asyncio.CancelledError:
                    await db.rollback()
                    logger.info("task.cancelled_at_checkpoint", task_id=task_id)
                    return
                except Exception as e:
                    await db.rollback()
                    await _fail(db, task_id, e, t0)
    except Exception:
        # 连后台会话都开不了的极端情况：只记日志（任务留 queued/running 等 stale 扫描重拉）
        logger.exception("task.runner_crashed", task_id=task_id)
    finally:
        _ACTIVE.pop(task_id, None)


async def _fail(db: AsyncSession, task_id: str, e: Exception, t0: float) -> None:
    """失败收尾：原因人话落库 + 失败通知（G-4）。"""
    task = await db.get(Task, uuid.UUID(task_id))
    if task is None or task.status in ("succeeded", "cancelled"):
        return
    msg = str(e) if isinstance(e, TaskPermanentError) else f"{type(e).__name__}: {e}"
    task.status = "failed"
    task.error = msg[:500]
    task.stage = "失败"
    task.elapsed_ms = int((time.monotonic() - t0) * 1000)
    task.finished_at = datetime.now(timezone.utc)
    await _notify_terminal(db, task, ok=False)
    await db.commit()
    logger.warning("task.failed", task_id=task_id, kind=task.kind, error=msg[:200])


async def _notify_terminal(db: AsyncSession, task: Task, *, ok: bool) -> None:
    """终态通知（G-9）：同任务同终态同 attempt 只一条（uq 兜底 + 预查）。"""
    ntype = "task.succeeded" if ok else "task.failed"
    dedup = f"task:{task.id}:{ntype}:{task.attempt}"
    exists = await db.execute(select(Notification).where(Notification.dedup_key == dedup))
    if exists.scalar_one_or_none() is not None:
        return
    kind_label = _HANDLER_LABELS.get(task.kind, task.kind)
    if ok:
        title = f"{kind_label}已完成"
        body = None
    else:
        title = f"{kind_label}未完成"
        body = task.error or "请重试"
    db.add(
        Notification(
            user_id=task.user_id,
            type=ntype,
            title=title[:200],
            body=body[:500] if body else None,
            payload={"task_id": str(task.id), "jump": (task.result or {}).get("jump"), "kind": task.kind},
            dedup_key=dedup,
        )
    )
    await db.flush()


# 处理器中文标签（通知文案；新增 kind 在 task_handlers 内一并登记）
_HANDLER_LABELS: dict[str, str] = {
    "practice.generate": "练习生成",
    "classroom.session": "双师课堂",
    "socratic.autosolve": "引导解题",
    "teacher.quiz.create": "组卷",
    "teacher.materials": "教学材料",
}


async def cancel_task(db: AsyncSession, task: Task) -> Task:
    """协作式取消：queued/running → cancelled（G-5）。执行流在检查点感知。"""
    if task.status in ("queued", "running"):
        task.status = "cancelled"
        task.stage = "已取消"
        task.finished_at = datetime.now(timezone.utc)
        await db.commit()
    return task


async def retry_task(db: AsyncSession, task: Task) -> Task:
    """重试：仅 failed/cancelled；同行 reset（attempt+1）→ 重新调度（G-4）。

    处理器按 result 已存在短路，保证幂等不产生重复产物。
    """
    if task.status not in ("failed", "cancelled"):
        return task
    task.status = "queued"
    task.progress = 0
    task.stage = "重新排队"
    task.error = None
    task.finished_at = None
    await db.commit()
    spawn(task)
    return task


async def maybe_resume(db: AsyncSession, task: Task) -> Task:
    """详情/列表读路径的惰性自愈（对齐 classroom 范式）：陈旧且无活跃执行者 → 重新拉起。"""
    if task.status in ("queued", "running") and str(task.id) not in _ACTIVE:
        updated = task.updated_at
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        stale = (
            updated is None
            or datetime.now(timezone.utc) - updated > timedelta(seconds=TASK_STALE_SECONDS)
        )
        if stale:
            logger.warning("task.stale_resume", task_id=str(task.id), kind=task.kind)
            spawn(task)
    return task


async def resume_stale(db: AsyncSession) -> int:
    """周期扫描自愈（G-3）：进程重启遗留的 queued/running 任务重新拉起。"""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=TASK_STALE_SECONDS)
    rows = await db.execute(
        select(Task).where(Task.status.in_(("queued", "running")), Task.updated_at < cutoff)
    )
    relaunched = 0
    for task in rows.scalars():
        if str(task.id) in _ACTIVE:
            continue  # 排队等待并发闸的正常任务
        logger.warning("task.stale_resume_scan", task_id=str(task.id), kind=task.kind)
        spawn(task)
        relaunched += 1
    return relaunched


async def _resume_loop() -> None:
    """lifespan 周期扫描（30s）。ASGITransport 测试环境不启动，无副作用。"""
    while True:
        await asyncio.sleep(RESUME_SCAN_INTERVAL_S)
        try:
            async with background_session_factory() as db:
                await resume_stale(db)
        except Exception:
            logger.exception("task.resume_loop_error")


def start_resume_loop() -> None:
    global _resume_loop_task
    if _resume_loop_task is None or _resume_loop_task.done():
        _resume_loop_task = asyncio.get_running_loop().create_task(_resume_loop())


def stop_resume_loop() -> None:
    global _resume_loop_task
    if _resume_loop_task is not None:
        _resume_loop_task.cancel()
        _resume_loop_task = None


def handler_label(kind: str) -> str:
    return _HANDLER_LABELS.get(kind, kind)
