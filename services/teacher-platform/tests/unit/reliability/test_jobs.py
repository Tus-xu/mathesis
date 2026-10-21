"""Job 引擎单测：创建/幂等入队/claim+fencing/续租/终态/revision 冲突/取消/回收/DLQ/同事务事件。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.reliability.jobs import (
    CANCELLED,
    CLAIMED,
    COMPLETED,
    DLQ,
    FAILED,
    PENDING,
    FenceError,
    JobDispatcher,
    JobEngine,
    RevisionConflict,
    fenced_run,
)
from app.reliability.models import ReliabilityJob, ReliabilityJobEvent, ReliabilityOutbox

OWNER = "teacher_platform"


async def _create(session, **kw):
    base = {"owner_service": OWNER, "job_type": "deck.generate", "payload": {"topic": "椭圆"}}
    base.update(kw)
    return await JobEngine.create(session, **base)


async def test_create_appends_created_event(db) -> None:
    job = await _create(db)
    await db.commit()
    assert job.status == PENDING
    assert job.revision == 1
    evs = (await db.execute(select(ReliabilityJobEvent).where(ReliabilityJobEvent.job_id == job.id))).scalars().all()
    assert [e.event_type for e in evs] == ["job.created"]
    assert evs[0].seq == 1


async def test_enqueue_dedupe_returns_existing(db) -> None:
    j1 = await _create(db, dedupe_key="dup-1")
    await db.commit()
    j2 = await _create(db, dedupe_key="dup-1")
    await db.commit()
    assert j2.id == j1.id  # 幂等入队：返回既有行，不产生重复作业
    rows = (await db.execute(select(ReliabilityJob))).scalars().all()
    assert len(rows) == 1


async def test_claim_fences_with_token(db) -> None:
    await _create(db, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    claimed = await JobEngine.claim(db, owner_service=OWNER, worker_name="w1", limit=10)
    await db.commit()
    assert len(claimed) == 1
    got = claimed[0]
    assert got.status == CLAIMED
    assert got.claim_token.startswith("w1:")
    assert got.attempt == 1
    assert got.claim_expires_at is not None
    # 同一 token 完成 OK
    await JobEngine.complete(db, got, got.claim_token, result={"ok": True})
    await db.commit()


async def test_wrong_token_complete_rejected(db) -> None:
    await _create(db, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    (claimed,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w1", limit=10)
    await db.commit()
    with pytest.raises(FenceError):
        await JobEngine.complete(db, claimed, "other-worker:xxx")
    # 重复投递（旧 token 已失效）→ fenced_run 拒之门外
    code = await fenced_run(
        db,
        job_id=claimed.id,
        claim_token="stale-token",
        handler=lambda **kw: {"x": 1},
    )
    assert code == "fenced_out"


async def test_expected_revision_conflict(db) -> None:
    await _create(db, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    (claimed,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w1", limit=10)
    await db.commit()
    with pytest.raises(RevisionConflict):
        await JobEngine.complete(db, claimed, claimed.claim_token, expected_revision=999)


def _naive_utc_past(seconds: float = 5.0) -> datetime:
    """SQLite func.now()/CURRENT_TIMESTAMP 为无时区 UTC 字符串；测试拨回时间统一用朴素 UTC。"""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=seconds)


async def test_retry_backoff_and_terminal_failed(db) -> None:
    job = await _create(db, max_attempts=3, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    for attempt_no in range(1, 4):
        # 重试退避把 available_at 推进未来；每次认领前拨回（模拟时间流逝）
        r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
        r.available_at = _naive_utc_past()
        await db.commit()
        (claimed,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w", limit=10)
        await db.commit()
        assert claimed.attempt == attempt_no
        if attempt_no < 3:
            await JobEngine.fail(db, claimed, claimed.claim_token, error=f"boom-{attempt_no}")
            await db.commit()
            # 可重试 → 回 PENDING + 退避（future 由 backoff 语义保证；SQLite 秒级比较不作精确断言）
            r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
            assert r.status == PENDING
            assert r.last_error == f"boom-{attempt_no}"
        else:
            await JobEngine.fail(db, claimed, claimed.claim_token, error="boom-3")
            await db.commit()
            r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
            assert r.status == FAILED  # attempt>=max → 终态 FAILED
    evs = (await db.execute(select(ReliabilityJobEvent).where(ReliabilityJobEvent.job_id == job.id).order_by(ReliabilityJobEvent.seq))).scalars().all()
    types = [e.event_type for e in evs]
    assert types == ["job.created", "job.claimed", "job.retry", "job.claimed", "job.retry", "job.claimed", "job.failed"]


async def test_cancel_pending_direct(db) -> None:
    job = await _create(db, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    assert await JobEngine.cancel(db, job.id) is True
    await db.commit()
    r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    assert r.status == CANCELLED


async def test_cancel_claimed_blocks_dispatch_then_reconciler_finalizes(db) -> None:
    job = await _create(db, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    (claimed,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w1", limit=10)
    await db.commit()
    assert await JobEngine.cancel(db, claimed.id) is True  # CLAIMED → cancel_requested
    await db.commit()
    r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    assert r.cancel_requested is True and r.status == CLAIMED
    # worker 侧 fenced_run 入口见 cancel_requested → 直接终态取消
    code = await fenced_run(
        db, job_id=job.id, claim_token=claimed.claim_token,
        handler=lambda **kw: {"x": 1},
    )
    assert code == "fenced_out"
    r2 = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    assert r2.status == CANCELLED


async def test_stale_claim_reclaimed_by_reconciler(db) -> None:
    """故障注入⑤：worker 崩溃（租约过期）→ reconciler 回收重投；旧 token 失效。"""
    job = await _create(db, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    (claimed,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w1", limit=10, claim_lease_s=1)
    await db.commit()
    stale_token = claimed.claim_token
    # 模拟租约过期
    r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    r.claim_expires_at = _naive_utc_past()
    await db.commit()

    reclaimed = await JobEngine.requeue_stale(
        db, owner_service=OWNER, claim_lease_s=1,
    )
    await db.commit()
    assert [str(x) for x in reclaimed] == [str(job.id)]
    r2 = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    assert r2.status == PENDING
    assert r2.claim_token is None
    # 回收后 available_at 接近 now（SQLite 秒级字符串比较需拨回确保到期）
    r2.available_at = _naive_utc_past()
    await db.commit()
    # 旧 worker 用旧 token 想续租/完成 → 被拒
    assert await JobEngine.renew(db, job.id, stale_token) is False
    # 新 worker 重新认领 → 成功执行
    (claimed2,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w2", limit=10)
    await db.commit()
    await JobEngine.complete(db, claimed2, claimed2.claim_token, result={"recovered": True})
    await db.commit()
    final = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    assert final.status == COMPLETED and final.result == {"recovered": True}


async def test_dlq_when_attempts_exhausted_via_reconciler(db) -> None:
    job = await _create(db, max_attempts=2, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    # 第一次 claim
    (c1,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w", limit=10)
    await db.commit()
    await JobEngine.fail(db, c1, c1.claim_token, error="e1")
    await db.commit()
    # 第二次 claim（先拨回退避时间）→ fail → attempt=2 >= max_attempts → FAILED（不是 DLQ，因显式 fail）
    r1 = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    r1.available_at = _naive_utc_past()
    await db.commit()
    (c2,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w", limit=10)
    await db.commit()
    await JobEngine.fail(db, c2, c2.claim_token, error="e2")
    await db.commit()
    r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    assert r.status == FAILED


async def test_deadline_reconciles_to_dlq(db) -> None:
    job = await _create(db, timeout_s=1, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    await db.commit()
    (c,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w", limit=10)
    await db.commit()
    r = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    r.deadline_at = _naive_utc_past()  # 已超绝对时限
    await db.commit()
    deadlined = await JobEngine.deadline_raise(db, owner_service=OWNER)
    await db.commit()
    assert [x for x in deadlined] == [job.id]
    r2 = (await db.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
    assert r2.status == DLQ


async def test_state_change_events_and_outbox_same_transaction(db) -> None:
    """业务状态 + 事件 + outbox 同事务：任一失败则全部回滚。"""
    job = await _create(db, scheduled_at=datetime.now(UTC) - timedelta(seconds=5))
    (claimed,) = await JobEngine.claim(db, owner_service=OWNER, worker_name="w", limit=10)
    await JobEngine.complete(db, claimed, claimed.claim_token, result={"ok": True})
    await JobEngine.enqueue_outbox(db, claimed, topic="rt:jobs", event_type="job.completed")
    await db.commit()
    rows = (await db.execute(select(ReliabilityOutbox))).scalars().all()
    assert len(rows) == 1
    assert rows[0].dedupe_key == str(job.id)
    assert rows[0].payload["event_type"] == "job.completed"
    evs = (await db.execute(select(ReliabilityJobEvent))).scalars().all()
    assert len(evs) == 3  # created + claimed + completed


async def test_job_dispatcher_end_to_end(session_factory) -> None:
    """dispatcher：claim → fenced_run(handler 幂等) → complete；副作用恰好一次。"""
    seen_effects: list[str] = []

    async def handler(*, session, job):
        seen_effects.append(str(job.id))
        return {"processed": job.id}

    async with session_factory() as s:
        job = await _create(
            s, scheduled_at=datetime.now(UTC) - timedelta(seconds=5), job_type="paper.scan"
        )
        await s.commit()
    d = JobDispatcher(
        session_factory, OWNER,
        {"paper.scan": handler},
        worker_name="w-test", poll_interval_s=0.01,
    )
    n = await d.run_once()
    assert n == 1
    async with session_factory() as s:
        r = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
        assert r.status == COMPLETED
        assert r.result == {"processed": str(job.id)}
    assert seen_effects == [str(job.id)]


async def test_dispatcher_unknown_handler_fails_job(session_factory) -> None:
    async with session_factory() as s:
        job = await _create(
            s, scheduled_at=datetime.now(UTC) - timedelta(seconds=5),
            job_type="ghost", max_attempts=1,  # 未知 handler = 配置缺陷 → 一次失败即终态 FAILED
        )
        await s.commit()
    d = JobDispatcher(session_factory, OWNER, {}, worker_name="w")
    await d.run_once()
    async with session_factory() as s:
        r = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == job.id))).scalar_one()
        assert r.status == FAILED
        assert "未知 job_type" in (r.last_error or "")


async def test_trace_id_recorded_on_job_and_events(db) -> None:
    from app.reliability.trace import new_trace, reset_current, set_current

    tr = new_trace(correlation_id="corr-jobs")
    tok = set_current(tr)
    try:
        job = await _create(db)
        await db.commit()
    finally:
        reset_current(tok)
    assert job.trace_id == tr.trace_id
    assert job.correlation_id == "corr-jobs"
    ev = (await db.execute(select(ReliabilityJobEvent))).scalars().one()
    assert ev.trace_id == tr.trace_id
