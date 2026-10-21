"""ValkeyCompatibilityGate（M1 出口硬门禁 · 02-ARCHITECTURE §17）。

分工：**B3 主跑 / B5 见证复核**——B3 的 gate 资产未落地前，由 B5 在自有路径
（tests/integration/ 归 B5，§9）代持本测试并产出实证；B3 落地后本文件转为复核工具。

证明四件事（全部在真实 `teacher-valkey` 9.1.2 容器上，禁进程内模拟——红线 5）：
  A. 传输原语：XADD/XREADGROUP/XACK/XPENDING/XAUTOCLAIM（崩溃重投语义）/maxlen 裁剪/PubSub；
  B. TaskIQ 全链：真实 worker 进程 enqueue→consume→execute→ACK→事件双通道→DB 终态；
  C. 崩溃恢复（P10 语义）：杀 worker → pending 消息由 claim-worker（idle_timeout=1.5s）重投完成；
  D. 结果后端在 Valkey 上可用（注意：app/tasks/broker.py 的 result_backend 未挂接，见 Gate 报告）。

跑法：compose 全栈起后 `pytest tests/integration/valkey_gate/ -v`。
自洁：测试行删除（数据审计黑名单）。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.compose

VALKEY_URL = os.environ.get("TEACHER_VALKEY_URL", "redis://127.0.0.1:6380/0")
PG_DSN = os.environ.get("TEACHER_PG_DSN", "postgres://teacher:teacher_dev_pw@127.0.0.1:5433/teacher_platform")
BACKEND_ROOT = Path(__file__).resolve().parents[3]
GATE_STREAM = "b5gate:transport"


# ---------------- 夹具 ----------------


@pytest.fixture
async def valkey():
    redis = pytest.importorskip("redis.asyncio", reason="redis 未安装")
    client = redis.Redis.from_url(VALKEY_URL, decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=5)
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        pytest.skip(f"teacher-valkey 不可达（compose 未启动？）：{exc}")
    yield client
    await client.aclose()


@pytest.fixture
async def pg():
    asyncpg = pytest.importorskip("asyncpg", reason="asyncpg 未安装")
    try:
        conn = await asyncpg.connect(PG_DSN, timeout=5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"teacher-postgres 不可达：{exc}")
    yield conn
    await conn.close()


def _spawn_worker(module: str) -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(BACKEND_ROOT))
    return subprocess.Popen(
        [sys.executable, "-m", "taskiq", "worker", module, "--workers", "1"],
        cwd=str(BACKEND_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


async def _wait_workers(valkey, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    """worker 就绪 = consumer group 内出现 ≥1 个 consumer。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise RuntimeError(f"worker 提前退出：\n{out[-1500:]}")
        try:
            n = await valkey.xinfo_consumers("teacher-tasks", "teacher-workers")
            if n:
                return
        except Exception:  # noqa: BLE001 —— group 未创建
            pass
        await asyncio.sleep(0.5)
    raise TimeoutError("worker 30s 未就绪（consumer group 无 consumer）")


async def _poll(poll_fn, timeout: float, interval: float = 0.25, desc: str = ""):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await poll_fn()
        if last:
            return last
        await asyncio.sleep(interval)
    raise TimeoutError(f"等待超时（{desc or poll_fn}，{timeout}s）")


async def _cleanup_task(pg, tid):
    tid = uuid.UUID(str(tid))  # asyncpg 返回其自身 UUID 类型，统一转标准库
    await pg.execute("DELETE FROM task_events WHERE task_id = $1", tid)
    await pg.execute("DELETE FROM tasks WHERE id = $1", tid)


# ---------------- A. 传输原语 ----------------


async def test_a_stream_primitives_on_real_valkey(valkey):
    """XADD→XREADGROUP→XACK→XPENDING→XAUTOCLAIM / maxlen 裁剪 / PubSub——Valkey 协议级兼容。"""
    await valkey.delete(GATE_STREAM)
    try:
        await valkey.xgroup_destroy(GATE_STREAM, "b5gate")
    except Exception:  # noqa: BLE001
        pass
    await valkey.xgroup_create(GATE_STREAM, "b5gate", id="0", mkstream=True)

    # 投递 + 组读 + ACK
    await valkey.xadd(GATE_STREAM, {"seq": "1", "event_type": "probe"})
    read = await valkey.xreadgroup("b5gate", "consumer-1", {GATE_STREAM: ">"}, count=10, block=2000)
    assert read and read[0][1], "XREADGROUP 未读到消息"
    msg_id = read[0][1][0][0]
    assert (await valkey.xack(GATE_STREAM, "b5gate", msg_id)) == 1
    pend = await valkey.xpending(GATE_STREAM, "b5gate")
    assert pend["pending"] == 0, "ACK 后 pending 应清零"

    # 已投递未 ACK → XAUTOCLAIM 认领（taskiq-redis 崩溃重投所依赖的语义）
    await valkey.xadd(GATE_STREAM, {"seq": "2", "event_type": "orphan"})
    read2 = await valkey.xreadgroup("b5gate", "consumer-2", {GATE_STREAM: ">"}, count=10, block=2000)
    assert read2[0][1]
    await asyncio.sleep(0.15)
    claimed = await valkey.xautoclaim(GATE_STREAM, "b5gate", "consumer-3", min_idle_time=50, count=10)
    assert claimed[1], "XAUTOCLAIM 未认领孤儿 pending 消息——崩溃重投语义不成立"

    # maxlen 硬裁剪（durable 热通道过期策略）
    trim_stream = "b5gate:trim"
    await valkey.delete(trim_stream)
    for i in range(6):
        await valkey.xadd(trim_stream, {"i": i}, maxlen=3, approximate=False)
    assert await valkey.xlen(trim_stream) == 3, "maxlen 裁剪未生效"

    # PubSub live 广播（get_message 单次调用有竞态——按重试循环取首条 message）
    pubsub = valkey.pubsub()
    await pubsub.subscribe("b5gate:live")
    receivers = await valkey.publish("b5gate:live", json.dumps({"event_type": "probe", "payload": {"ok": 1}}))
    assert receivers >= 1, "Publish 无订阅者——订阅未注册成功"
    got = None
    for _ in range(6):
        msg = await pubsub.get_message(timeout=1.0)
        if msg and msg.get("type") == "message":
            got = msg
            break
    assert got is not None and json.loads(got["data"])["payload"]["ok"] == 1, "PubSub 收不到广播"
    await pubsub.aclose()

    # 结果存储语义（result backend 依赖）：SET/GET/EXPIRE
    await valkey.set("b5gate:result", json.dumps({"ok": True}), ex=10)
    assert json.loads(await valkey.get("b5gate:result"))["ok"] is True
    assert await valkey.ttl("b5gate:result") > 0
    await valkey.delete("b5gate:result")
    await valkey.delete(GATE_STREAM, trim_stream)


# ---------------- B. TaskIQ 全链（真实 worker 进程） ----------------


async def test_b_taskiq_full_chain_with_real_worker(valkey, pg):
    """enqueue→真实 worker consume→execute_task→run_task→demo_sleep 事件双通道→tasks done。"""
    worker = _spawn_worker("app.tasks.broker:broker")
    try:
        await _wait_workers(valkey, worker)
        tid = await pg.fetchval(
            "INSERT INTO tasks (id, user_id, type, status, progress) VALUES ($1, $2, 'demo_sleep', 'pending', 0) RETURNING id",
            uuid.uuid4(),
            uuid.uuid4(),
        )
        try:
            from app.tasks.broker import broker, execute_task

            await broker.startup()
            try:
                await execute_task.kiq(str(tid))
            finally:
                await broker.shutdown()

            async def _status_done():
                st = await pg.fetchval("SELECT status FROM tasks WHERE id = $1", tid)
                return st if st == "done" else None

            status = await _poll(_status_done, timeout=25, desc="tasks.status=done")
            assert status == "done", (
                f"任务终态应为 done，实际 {status}——"
                "若 task_events 已有 progress 全量事件而状态恒 running，即 DEF-004（run_task 成功路径漏 _finish，B3）"
            )

            # 事件双通道：PG 权威源（status + progress×4 + done，seq 严格递增）
            events = await pg.fetch(
                "SELECT seq, event_type FROM task_events WHERE task_id = $1 ORDER BY seq", tid
            )
            seqs = [e["seq"] for e in events]
            types = [e["event_type"] for e in events]
            assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"seq 非严格递增：{seqs}"
            assert types[0] == "status" and types[-1] == "done" and types.count("progress") == 4, (
                f"事件序列不符：{types}"
            )
            # Valkey Streams 热通道同步落了同一批事件
            stream_entries = await valkey.xrange(f"events:task:{tid}")
            assert len(stream_entries) >= len(events), (
                f"Valkey Stream 事件数 {len(stream_entries)} < PG 权威源 {len(events)}"
            )
        finally:
            await _cleanup_task(pg, tid)
    finally:
        _kill(worker)


# ---------------- C. 崩溃恢复（P10：幂等重投） ----------------


async def test_c_crash_recovery_redelivery(valkey, pg):
    """杀掉执行中的 worker → pending 消息由 claim-worker（idle_timeout=1.5s）重投 → 任务完成。

    结论映射：taskiq-redis 1.1.2 的 XAUTOCLAIM 重投**可用但取决于 idle_timeout 配置**；
    B3 broker.py 默认 600s → P10「≤60s 恢复」必须显式配置（见 Gate 报告）。
    """
    worker_victim = _spawn_worker("app.tasks.broker:broker")
    try:
        await _wait_workers(valkey, worker_victim)
        tid = await pg.fetchval(
            "INSERT INTO tasks (id, user_id, type, status, progress) VALUES ($1, $2, 'demo_sleep', 'pending', 0) RETURNING id",
            uuid.uuid4(),
            uuid.uuid4(),
        )
        try:
            from app.tasks.broker import broker, execute_task

            await broker.startup()
            try:
                await execute_task.kiq(str(tid))
            finally:
                await broker.shutdown()

            # 等任务进入 running（worker 已取走消息、未 ACK）→ 立刻杀 worker
            async def _running():
                st = await pg.fetchval("SELECT status FROM tasks WHERE id = $1", tid)
                return st == "running"

            await _poll(_running, timeout=15, interval=0.1, desc="tasks.status=running")
            _kill(worker_victim)

            pend = await valkey.xpending("teacher-tasks", "teacher-workers")
            if pend["pending"] == 0:
                pytest.skip("崩溃窗口错过（消息已 ACK 完成）——重跑本用例即可")

            # claim-worker：1.5s idle 后 XAUTOCLAIM 认领重执行
            claim_worker = _spawn_worker("tests.integration.valkey_gate.worker_claim:broker")
            try:
                await _wait_workers(valkey, claim_worker)

                async def _status_done():
                    st = await pg.fetchval("SELECT status FROM tasks WHERE id = $1", tid)
                    return st if st == "done" else None

                status = await _poll(
                    _status_done,
                    timeout=30,
                    interval=0.5,
                    desc="崩溃后任务重投完成（done）",
                )
                assert status == "done", "崩溃后任务未被重投完成——P10 幂等重投链路不成立"
                # 重投证据：done 前有完整的单次执行轨迹（status + progress×4）——
                # 首次运行可能在「提交 running 后、发事件前」被杀，running 事件数可能只有 1 条
                trail = await pg.fetch(
                    "SELECT event_type, payload->>'progress' AS p FROM task_events"
                    " WHERE task_id = $1 ORDER BY seq",
                    tid,
                )
                types = [t["event_type"] for t in trail]
                assert types[-1] == "done", f"事件轨迹应以 done 收尾：{types}"
                assert types.count("progress") >= 4, f"重投执行应产生产出完整 progress 轨迹：{trail}"
            finally:
                _kill(claim_worker)
        finally:
            await _cleanup_task(pg, tid)
    finally:
        _kill(worker_victim)


# ---------------- D. 结果后端（未挂接问题的对照证据） ----------------


async def test_d_result_backend_roundtrip(valkey):
    """RedisAsyncResultBackend 在 Valkey 上可用（SET/GET 语义）——与 broker 未挂接问题对照。"""
    from taskiq_redis import RedisAsyncResultBackend
    from taskiq.result import TaskiqResult

    backend = RedisAsyncResultBackend(redis_url=VALKEY_URL, keep_results=True)
    await backend.startup()
    try:
        task_id = uuid.uuid4().hex
        await backend.set_result(
            task_id,
            TaskiqResult(
                is_err=False,
                log=None,
                return_value={"ok": 1, "gate": "valkey"},
                execution_time=0.01,
                labels={},
                error=None,
            ),
        )
        raw = await backend.get_result(task_id)
        assert raw is not None, "结果后端读回为空"
    finally:
        await backend.shutdown()
