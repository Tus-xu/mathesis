"""TaskService（架构 §8 签名，B3 提供）。

- `submit`：建 tasks 行（pending）→ 入队 TaskIQ（worker 分派执行）。
- `publish_durable/publish_live`：双通道事件（worker 与 API 共用）。
- `finish`：终态 + done 事件。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from ..core.events import EventPublisher, get_publisher
from ..db.models import Tasks
from ..db.session import session_factory


class TaskService:
    @staticmethod
    async def submit(task_type: str, payload: dict[str, Any], user_id: str) -> dict[str, Any]:
        async with session_factory()() as session:
            row = Tasks(type=task_type, payload=payload, user_id=uuid.UUID(user_id), status="pending", progress=0)
            session.add(row)
            await session.commit()
            task_id = row.id
        # 入队（worker 执行；任务行已落库，worker 崩溃后重投由 run_task 幂等恢复）
        from .broker import execute_task  # 延迟导入避免循环

        await execute_task.kiq(str(task_id))
        return {"id": str(task_id), "status": "pending", "type": task_type}

    @staticmethod
    async def publish_durable(task_id: str | uuid.UUID, event: str, data: dict[str, Any]) -> int:
        return await get_publisher().publish_durable(scope_kind="task", scope_id=task_id, event_type=event, payload=data)

    @staticmethod
    async def publish_live(channel: str, event: str, data: dict[str, Any]) -> None:
        await get_publisher().publish_live(channel, event, data)

    @staticmethod
    async def finish(
        task_id: str | uuid.UUID,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        tid = uuid.UUID(str(task_id))
        async with session_factory()() as session:
            row = (await session.execute(select(Tasks).where(Tasks.id == tid))).scalar_one_or_none()
            if row is None:
                return
            row.status = status
            row.progress = 100 if status == "done" else row.progress
            if result is not None:
                row.result = result
            if error is not None:
                row.error = error
            await session.commit()
        await get_publisher().publish_durable(
            scope_kind="task",
            scope_id=tid,
            event_type="done" if status == "done" else "failed",
            payload={"status": status, "error": error},
        )

    @staticmethod
    async def get(task_id: str | uuid.UUID) -> dict[str, Any] | None:
        tid = uuid.UUID(str(task_id))
        async with session_factory()() as session:
            row = (await session.execute(select(Tasks).where(Tasks.id == tid))).scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": str(row.id),
                "type": row.type,
                "status": row.status,
                "progress": row.progress,
                "message": row.message,
                "result": row.result,
                "error": row.error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
