"""任务注册表：task.type → handler。

所有长任务（B1/B4 的生成/导入/导出/变式/批处理）都在 worker 侧经此分派；
执行前 worker 侧复查资源归属（G3：不信任 API 提交时的检查）——M1 暂由各 handler 自查。
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from ..core.events import EventPublisher, get_publisher
from ..db.models import Tasks
from ..db.session import session_factory

logger = logging.getLogger("teacher.tasks.registry")


async def run_task(task_id: str) -> None:
    """取 tasks 行 → 置 running → 分派 handler → finish done/failed（P10：崩溃后重投可幂等恢复）。"""
    publisher = get_publisher()
    tid = uuid.UUID(task_id)

    async with session_factory()() as session:
        row = (await session.execute(select(Tasks).where(Tasks.id == tid))).scalar_one_or_none()
        if row is None:
            logger.warning("task not found", extra={"task_id": task_id})
            return
        row.status = "running"
        row.progress = 1
        await session.commit()
        task_type = row.type
        user_id = str(row.user_id)

    await publisher.publish_durable(scope_kind="task", scope_id=tid, event_type="status", payload={"status": "running", "progress": 1, "message": "任务开始"})

    handler = _TASK_HANDLERS.get(task_type)
    if handler is None:
        await _finish(publisher, tid, "failed", error=f"unknown task type: {task_type}")
        return
    try:
        await handler(task_id, publisher=publisher, user_id=user_id)
    except _cancelled_exception_types() as exc:
        # 取消是独立终态：若在这里落 done，取消就变成了"悄悄成功"
        logger.info("task cancelled", extra={"task_id": task_id, "type": task_type})
        await _finish(publisher, tid, "cancelled", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 —— worker 崩溃恢复（P10）：DB 幂等兜底
        logger.exception("task handler failed", extra={"task_id": task_id, "type": task_type})
        await _finish(publisher, tid, "failed", error=str(exc))
        return
    # handler 成功 → 终态 done（此前漏调导致任务永远卡 running，P10 已暴露）
    await _finish(publisher, tid, "done", result={"ok": True})


def _cancelled_exception_types() -> tuple[type[BaseException], ...]:
    from .jobs.export_artifact_v2 import TaskCancelled

    return (TaskCancelled,)


async def _finish(publisher: EventPublisher, tid: uuid.UUID, status: str, *, result: dict | None = None, error: str | None = None) -> None:
    from .service import TaskService  # 延迟导入避免循环

    await TaskService.finish(tid, status, result=result, error=error)
    await publisher.publish_durable(scope_kind="task", scope_id=tid, event_type="done", payload={"status": status})


def register_handler(task_type: str):
    def deco(fn):
        _TASK_HANDLERS[task_type] = fn
        return fn

    return deco


# 在模块加载尾部注册内置 handler（域 handler 由各智能体在 apps 生命周期注册）
from .jobs.demo_sleep import demo_sleep  # noqa: E402

_TASK_HANDLERS: dict[str, object] = {}


@register_handler("demo_sleep")
async def _demo_handler(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    await demo_sleep(task_id, publisher=publisher)


@register_handler("export_pptx")
async def _export_pptx_handler(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    from .jobs.export_pptx import export_pptx

    await export_pptx(task_id, publisher=publisher, user_id=user_id)


@register_handler("export_pdf")
async def _export_pdf_handler(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    """PDF 引擎未实现——诚实失败（不产假 PDF）。"""
    from sqlalchemy import select

    from ...db.models import Tasks
    from ...db.session import session_factory

    async with session_factory()() as session:
        row = (await session.execute(select(Tasks).where(Tasks.id == uuid.UUID(task_id)))).scalar_one_or_none()
        payload = dict(row.payload or {}) if row and row.payload else {}
    export_job_id = str(payload.get("export_job_id") or "")
    if export_job_id:
        from .jobs.export_pptx import _mark_job

        await _mark_job(export_job_id, status="failed", error="PDF 导出引擎未实现（02-ARCHITECTURE C-7 仅定案 PPTX）")


# T24：产物导出（deck → pptx/pdf；lesson plan → markdown/pdf）。与 legacy export_pptx 完全隔离。
@register_handler("export_deck_v2")
async def _export_deck_v2_handler(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    from .jobs.export_artifact_v2 import export_deck_v2

    await export_deck_v2(task_id, publisher=publisher, user_id=user_id)


@register_handler("export_lesson_plan_v2")
async def _export_lesson_plan_v2_handler(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    from .jobs.export_artifact_v2 import export_lesson_plan_v2

    await export_lesson_plan_v2(task_id, publisher=publisher, user_id=user_id)


# ppt_v2：课件逐页增稠（AI 补全板书内容；逐页 LLM + 确定性兜底，不整单失败）
@register_handler("enrich_deck_v2")
async def _enrich_deck_v2_handler(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    from .jobs.enrich_deck_v2 import enrich_deck_v2

    await enrich_deck_v2(task_id, publisher=publisher, user_id=user_id)
