"""demo_sleep：验证「API 提交→worker 执行→SSE 收进度→断线重连零丢失」全链的最小任务。

真实业务任务由 B1/B4 在各自域内注册（register_handler）。
"""
from __future__ import annotations

import asyncio

from ...core.events import EventPublisher


async def demo_sleep(task_id: str, *, publisher: EventPublisher, seconds: float = 2.0, steps: int = 4) -> None:
    """逐步骤发布 durable progress 事件（SSE 经双通道回放）。"""
    for i in range(1, steps + 1):
        await asyncio.sleep(seconds / steps)
        await publisher.publish_durable(
            scope_kind="task",
            scope_id=task_id,
            event_type="progress",
            payload={"progress": round(i / steps * 100), "message": f"demo 步骤 {i}/{steps}"},
        )
