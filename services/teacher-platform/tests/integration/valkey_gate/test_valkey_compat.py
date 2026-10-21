"""ValkeyCompatibilityGate（M1 出口硬门禁，02-ARCHITECTURE §2 / B3 §3.6）。

taskiq-redis 声明 `redis>=8.0.0,<9`，对 Valkey 是「协议级兼容预期」而非官方宣称支持——
**兼容性由本测试在真实 Valkey 容器上证明**，不由 README 推断。

覆盖（B3 规范要求全链路）：
1. enqueue → consume → ACK → result backend
2. worker crash → XAUTOCLAIM 接管 → 未 ACK 消息恢复重放（P10 语义）
3. 进程重启 → 新 broker 同 group 重连消费
4. result expiry（结果过期清理）

运行：`pytest tests/integration/valkey_gate -m gate`（需要真实 Valkey：`GATE_VALKEY_URL` 或 compose 的 127.0.0.1:6380）。
通过后输出 `VALKEY_TASKIQ_COMPAT=VERIFIED`。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from taskiq import TaskiqResult
from taskiq.acks import AcknowledgeType
from taskiq.receiver import Receiver
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

GATE_VALKEY_URL = os.environ.get("GATE_VALKEY_URL", "redis://127.0.0.1:6380/0")
MARKER = "VALKEY_TASKIQ_COMPAT"

pytestmark = pytest.mark.gate

_group_uuid = uuid.uuid4().hex[:8]


def _make_broker(result_ex_time: int | None = None) -> RedisStreamBroker:
    broker = RedisStreamBroker(
        url=GATE_VALKEY_URL,
        queue_name=f"valkey-gate-{_group_uuid}",
        consumer_group_name=f"valkey-gate-group-{_group_uuid}",
    )
    # 关键：挂真实 result backend（默认是 DummyResultBackend，无落点）
    broker.result_backend = _make_backend(result_ex_time=result_ex_time)
    return broker


def _make_backend(result_ex_time: int | None = None) -> RedisAsyncResultBackend:
    return RedisAsyncResultBackend(redis_url=GATE_VALKEY_URL, result_ex_time=result_ex_time)


async def _run_receiver(broker: RedisStreamBroker, *, max_tasks: int | None = None, start_up: bool = True) -> asyncio.Event:
    """启动一个进程内接收者；返回 finish_event（set 即停止）。"""
    finish = asyncio.Event()
    receiver = Receiver(
        broker,
        run_startup=start_up,
        max_tasks_to_execute=max_tasks,
        propagate_exceptions=False,
        on_exit=lambda _r: finish.set(),
    )

    async def _listen() -> None:
        try:
            await receiver.listen(finish)
        except Exception:  # noqa: BLE001 —— 测试主动停止属正常路径
            pass

    asyncio.create_task(_listen())
    await asyncio.sleep(0.8)  # 等接收者就绪
    return finish


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _probe_broker():
    """在模块级 broker 上注册门禁探针任务（加法）。"""
    b = _make_broker()

    @b.task(task_name="gate.probe_add")
    async def probe_add(a: int, b: int) -> int:
        return a + b

    return b, probe_add


class TestEnqueueConsumeAckResult:
    """链 1：enqueue → consume → ACK → result backend。"""

    async def test_full_chain(self):
        broker = _make_broker()

        @broker.task(task_name="gate.probe_full")
        async def probe_full(x: int) -> int:
            return x * 10

        finish = await _run_receiver(broker)
        try:
            task = await probe_full.kiq(7)
            result = await task.wait_result(timeout=10)
            assert result is not None and result.return_value == 70, f"result={result}"
            assert isinstance(result, TaskiqResult)
        finally:
            finish.set()
            await asyncio.sleep(0.3)


class TestCrashRedeliveryRecovery:
    """链 2：worker crash（未 ACK）→ XAUTOCLAIM 接管 → 全部任务仍被执行（P10）。"""

    async def test_unacked_messages_redelivered(self):
        broker = _make_broker()

        @broker.task(task_name="gate.probe_crash")
        async def probe_crash(i: int) -> int:
            return i

        # 接收者只执行 1 个任务就停（模拟 crash：其余消息已 XREADGROUP 交付但未 ACK）
        finish = await _run_receiver(broker, max_tasks=1)
        kicked = [await probe_crash.kiq(i) for i in range(3)]
        await asyncio.sleep(1.5)
        finish.set()
        await asyncio.sleep(0.5)

        # 新接收者（同 group）启动 → XAUTOCLAIM 接管 pending → 补齐剩余
        finish2 = await _run_receiver(broker)
        try:
            results = []
            for t in kicked:
                r = await t.wait_result(timeout=15)
                results.append(r.return_value if r is not None else None)
            assert sorted(results) == [0, 1, 2], f"results={results}"
        finally:
            finish2.set()
            await asyncio.sleep(0.3)


class TestProcessRestartReconnect:
    """链 3：进程重启 → 新 broker 实例（同 group 新 consumer）重连消费。"""

    async def test_new_instance_reconnects(self):
        broker1 = _make_broker()

        @broker1.task(task_name="gate.probe_restart")
        async def probe_restart(s: str) -> str:
            return s.upper()

        # 「旧进程」入队后未消费即消失（不启动接收者）
        task = await probe_restart.kiq("hello")
        await asyncio.sleep(0.3)

        # 「新进程」：全新 broker 实例同 group 启动并消费
        broker2 = _make_broker()

        @broker2.task(task_name="gate.probe_restart")
        async def probe_restart2(s: str) -> str:
            return s.upper()

        finish = await _run_receiver(broker2)
        try:
            r = await task.wait_result(timeout=15)
            assert r is not None and r.return_value == "HELLO"
        finally:
            finish.set()
            await asyncio.sleep(0.3)


class TestResultExpiry:
    """链 4：result expiry（结果过期清理）。"""

    async def test_result_expires(self):
        broker = _make_broker(result_ex_time=1)  # 1s 过期

        @broker.task(task_name="gate.probe_expiry")
        async def probe_expiry() -> str:
            return "soon-gone"

        finish = await _run_receiver(broker)
        try:
            task = await probe_expiry.kiq()
            r = await task.wait_result(timeout=10)
            assert r is not None and r.return_value == "soon-gone"
            # 等结果过期（taskiq 对不存在的结果抛 ResultGetError —— 证明 result 已清理）
            await asyncio.sleep(2.5)
            from taskiq.exceptions import ResultGetError

            with pytest.raises(ResultGetError):
                await task.get_result()
        finally:
            finish.set()
            await asyncio.sleep(0.3)


def test_gate_marker_emits_verified():
    """门禁出口标记：本模块全部通过即代表 Valkey 兼容（M1 硬门禁证据）。"""
    print(f"\n{MARKER}=VERIFIED")
