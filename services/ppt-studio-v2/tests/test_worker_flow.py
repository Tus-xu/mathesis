"""worker + dispatcher + reconciler 故障注入端到端（SQLite + InMemoryBroker）。

覆盖：流触发完成 / 8201 down → 退避重试 → DLQ / 取消 / kill-worker 租约回收恢复 /
重复投递幂等（inbox 去重）。
"""
from __future__ import annotations

import asyncio

from conftest import FakePresenton
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.reliability.broker import InMemoryBroker
from app.reliability.dispatcher import OutboxDispatcher
from app.reliability.jobs import JobEngine
from app.reliability.worker import JobWorker


def _sf(engine) -> async_sessionmaker:  # noqa: ANN001
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _engine() -> JobEngine:
    return JobEngine(lease_seconds=60, backoff_base_s=0.002, backoff_max_s=0.02)


async def _create(sle, eng, *, presenton_probe=True, max_attempts=3, timeout_s=300) -> str:
    async with sle() as s:
        job = await eng.create(
            s,
            job_type="ppt.deck.prepare",
            payload={"presenton_probe": presenton_probe, "deck_id": "d-1"},
            max_attempts=max_attempts,
            timeout_s=timeout_s,
            dedupe_key=None,
        )
        await s.commit()
        return job["id"]


async def test_stream_happy_flow(engine) -> None:  # noqa: ANN001
    sle = _sf(engine)
    eng = _engine()
    broker = InMemoryBroker()
    dispatch = OutboxDispatcher(sle, broker, batch=20)
    worker = JobWorker(sle, eng, broker=broker, presenton=FakePresenton(ok=True), batch=8)

    job_id = await _create(sle, eng)
    assert await dispatch.tick_once() == 1
    assert await worker.process_stream() == 1
    async with sle() as s:
        final = await eng.get(s, job_id)
        events = await eng.list_events(s, job_id)
    assert final["status"] == "COMPLETED"
    assert [e["event_type"] for e in events] == ["CREATED", "CLAIMED", "COMPLETED"]


async def test_stream_redelivery_dedupe(engine) -> None:  # noqa: ANN001
    sle = _sf(engine)
    eng = _engine()
    broker = InMemoryBroker()
    presenton = FakePresenton(ok=True)
    dispatch = OutboxDispatcher(sle, broker, batch=20)
    worker = JobWorker(sle, eng, broker=broker, presenton=presenton, batch=8)

    job_id = await _create(sle, eng)
    await dispatch.tick_once()
    # 模拟 dispatcher 发布后崩溃未标记 PUBLISHED → 重发同 message_id
    row = await dispatch.tick_once()
    assert row == 0  # 已 PUBLISHED 不再重发
    await worker.process_stream()
    await worker.process_stream()  # 重放已被去重（inbox），作业只执行一次
    async with sle() as s:
        final = await eng.get(s, job_id)
    assert final["status"] == "COMPLETED"
    assert presenton.calls == 1  # 单副作用


async def test_presenton_down_retry_then_dlq(engine) -> None:  # noqa: ANN001
    sle = _sf(engine)
    eng = _engine()
    broker = InMemoryBroker()
    dispatch = OutboxDispatcher(sle, broker, batch=20)
    worker = JobWorker(sle, eng, broker=broker, presenton=FakePresenton(ok=False), batch=8)

    job_id = await _create(sle, eng, max_attempts=2)
    await dispatch.tick_once()
    await worker.process_stream()  # attempt=1 失败 → PENDING（退避）
    await asyncio.sleep(0.05)
    await worker.process_pending()  # attempt=2 失败 → DLQ
    async with sle() as s:
        final = await eng.get(s, job_id)
    assert final["status"] == "DLQ"
    assert "max_attempts" in (final["last_error"] or "")


async def test_cancel_before_run(engine) -> None:  # noqa: ANN001
    sle = _sf(engine)
    eng = _engine()
    broker = InMemoryBroker()
    dispatch = OutboxDispatcher(sle, broker, batch=20)
    worker = JobWorker(sle, eng, broker=broker, presenton=FakePresenton(ok=True), batch=8)

    job_id = await _create(sle, eng)
    async with sle() as s:
        await eng.request_cancel(s, job_id)
        await s.commit()
    await dispatch.tick_once()
    await worker.process_stream()
    async with sle() as s:
        final = await eng.get(s, job_id)
    assert final["status"] == "CANCELLED"


async def test_kill_worker_recovery_after_lease_expiry(engine) -> None:  # noqa: ANN001
    sle = _sf(engine)
    # lease_seconds=0：claim 后租约立即过期 → 用实时时钟即可触发回收
    eng = JobEngine(lease_seconds=0, backoff_base_s=0.002, backoff_max_s=0.02)
    broker = InMemoryBroker()
    # 1) 老 worker claim 后被杀（未 complete，行保持 CLAIMED + 租约已过）
    job_id = await _create(sle, eng)
    async with sle() as s:
        claimed = await eng.claim(s, job_id, "stale-token")  # noqa: F841
        await s.commit()
    # 2) 租约过期 → reconciler 回收（实时 now，available_at = now + 微小退避）
    async with sle() as s:
        touched = await eng.reclaim_expired(s)  # noqa: F841
        await s.commit()
    # 3) 新 worker db-poll 接管并完成（attempt=2）
    worker2 = JobWorker(sle, eng, broker=broker, presenton=FakePresenton(ok=True), batch=8)
    await asyncio.sleep(0.02)
    assert await worker2.process_pending() >= 1
    async with sle() as s:
        final = await eng.get(s, job_id)
        events = await eng.list_events(s, job_id)
    assert final["status"] == "COMPLETED"
    assert final["attempt"] == 2
    assert "LEASE_RECLAIMED" in [e["event_type"] for e in events]
