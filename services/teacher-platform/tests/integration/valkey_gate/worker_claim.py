"""B5 Gate 专用 claim-worker：与 app.tasks.broker 同 queue/group，但 idle_timeout=1500ms。

用途：ValkeyCompatibilityGate 崩溃恢复用例——被杀 worker 的 pending 消息由本 worker
在 idle_timeout 后经 XAUTOCLAIM 认领重执行（P10「幂等重投」语义）。
B3 的 broker.py 未配置 idle_timeout（默认 600s）——Gate 结论要求其显式配置 ≤30s（见 Gate 报告）。
"""
from __future__ import annotations

from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.core.config import get_settings

_settings = get_settings()

broker = RedisStreamBroker(
    url=_settings.valkey_url,
    queue_name="teacher-tasks",
    consumer_group_name="teacher-workers",
    idle_timeout=1500,  # 崩溃恢复验证窗口：1.5s 后可被认领
).with_result_backend(
    RedisAsyncResultBackend(redis_url=_settings.valkey_url, keep_results=True)
)


@broker.task(task_name="platform.run_task")
async def execute_task(task_id: str) -> None:
    """与 B3 broker 同名任务：worker 侧按名字分派到同一 run_task 幂等入口。"""
    from app.tasks.registry import run_task

    await run_task(task_id)
