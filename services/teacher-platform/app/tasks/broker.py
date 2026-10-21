"""TaskIQ broker（Valkey Stream broker）。

`RedisStreamBroker` 以 Valkey Streams 为传输（enqueue→consume→XACK→result 全链路），
任务在独立 worker 进程执行（API 进程只做「校验→建 task→订阅双通道→转发」）。
兼容性由 `tests/integration/valkey_gate/` 在真实 Valkey 容器上证明（ValkeyCompatibilityGate）。
"""
from __future__ import annotations

from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from ..core.config import get_settings

_settings = get_settings()

broker = RedisStreamBroker(
    url=_settings.valkey_url,
    queue_name="teacher-tasks",
    consumer_group_name="teacher-workers",
    # P10：worker 崩溃后 pending 消息 idle 即被 XAUTOCLAIM 接管恢复。
    # ⚠ 2026-09-23 修复（备课中心重构）：原值 30_000（30s）短于真实长任务
    # （课件逐页增稠 wall ≈43s、导出 ≈60s、单页 LLM 上限 300s），消息在任务仍在
    # 执行时被判 idle → 被重复投递给另一消费者 → 同一长任务并发跑多份（实测
    # 重复消耗 LLM 额度、并发写同一 deck spec）。Redis Streams 的 idle = 距上次
    # 投递时间（无心跳），故必须 > 最长任务时长。取 900s（15min）：覆盖单页 300s
    # 上限 × 允许的排队延迟，同时保留崩溃可恢复性。
    idle_timeout=900_000,
)
result_backend = RedisAsyncResultBackend(redis_url=_settings.valkey_url, keep_results=True)

# 注册任务：worker 入口 `taskiq worker app.tasks.broker:broker`
from .registry import run_task  # noqa: E402


@broker.task(task_name="platform.run_task")
async def execute_task(task_id: str) -> None:
    """通用任务入口：按 tasks 行 type 分派到 TASK_HANDLERS（生成/导入/导出/变式…）。"""
    await run_task(task_id)
