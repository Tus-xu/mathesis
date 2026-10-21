"""TransactionalOutbox 单测：同事务入队 / 发布成功 / 发布失败重试 / 崩溃恢复 / DLQ / 背压。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.reliability.models import ReliabilityOutbox
from app.reliability.outbox import OutboxDispatcher, OutboxReconciler, OutboxService

TOPIC = "rt:demo"


class FakePublisher:
    """可注入故障的流发布器：fail_times = 连续失败次数（模拟 Valkey 短暂不可用/发布失败）。"""

    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.published: list[dict] = []

    async def publish(self, *, topic, message_id, payload, headers=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("stream unavailable (simulated)")
        self.published.append({"topic": topic, "message_id": message_id, "payload": payload})


async def _enqueue(session, *, dedupe_key=None, payload=None):
    return await OutboxService.enqueue(
        session, topic=TOPIC, payload=payload or {"op": "demo"}, dedupe_key=dedupe_key
    )


async def test_enqueue_is_transactional_with_caller(db) -> None:
    async with db.begin():
        row = await _enqueue(db)
        rid = row.id
        # 未提交前不可见（同事务语义：业务状态+jobs+outbox 一起落库）
        visible = (
            await db.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.id == rid))
        ).scalar_one_or_none()
        assert visible is not None  # 同 session 可见（未跨连接），commit 后才对外原子可见
    rows = (await db.execute(select(ReliabilityOutbox))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "PENDING"


async def test_dedupe_key_conflict_raises_on_duplicate_enqueue(db) -> None:
    await _enqueue(db, dedupe_key="k1")
    await db.commit()
    with pytest.raises(IntegrityError):  # UNIQUE(topic, dedupe_key) → 去重冲突
        await _enqueue(db, dedupe_key="k1")
        await db.commit()


async def test_dispatcher_publishes_and_marks_published(session_factory) -> None:
    pub = FakePublisher()
    async with session_factory() as s:
        await _enqueue(s)
        await s.commit()
    d = OutboxDispatcher(session_factory, pub, poll_interval_s=0.01)
    n = await d.poll_once()
    assert n == 1
    assert len(pub.published) == 1
    msg = pub.published[0]
    assert msg["topic"] == TOPIC
    async with session_factory() as s:
        row = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        assert row.status == "PUBLISHED"
        assert row.published_at is not None


async def test_commit_then_publish_failure_retries_to_success(session_factory) -> None:
    """故障注入①：DB commit 后发布失败 → 行保 PENDING（不丢事件）→ 退避后重试 → 终成 PUBLISHED。"""
    pub = FakePublisher(fail_times=1)  # 第 1 次发布失败
    async with session_factory() as s:
        row = await _enqueue(s)
        await s.commit()  # DB commit 成功、发布未发生
    d = OutboxDispatcher(session_factory, pub, poll_interval_s=0.01)
    await d.poll_once()  # 发布失败 → available_at 退避
    async with session_factory() as s:
        row2 = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.id == row.id))).scalar_one()
        assert row2.status == "PENDING"
        assert row2.attempt == 1
        assert row2.available_at is not None
    # 退避到期后（把 available_at 拨回过去模拟时间）→ 重发成功
    async with session_factory() as s:
        r = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.id == row.id))).scalar_one()
        r.available_at = datetime.now(UTC) - timedelta(seconds=5)
        await s.commit()
    await d.poll_once()
    assert len(pub.published) == 1
    async with session_factory() as s:
        row3 = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.id == row.id))).scalar_one()
        assert row3.status == "PUBLISHED"


async def test_publish_ok_but_state_mark_crash_republishes_same_message_id(session_factory) -> None:
    """故障注入②：发布成功但标记 PUBLISHED 前崩溃 → 行保 PENDING → 重发同 message_id（幂等投递）。"""
    pub = FakePublisher()
    async with session_factory() as s:
        row = await _enqueue(s)
        await s.commit()
    d = OutboxDispatcher(session_factory, pub, poll_interval_s=0.01)
    await d.poll_once()
    # 模拟「发布成功后、标记前崩溃」：行仍 PENDING（等价于首次标记失败）
    async with session_factory() as s:
        r = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.id == row.id))).scalar_one()
        r.status = "PENDING"
        r.published_at = None
        await s.commit()
    await d.poll_once()  # 重新投递
    ids = [m["message_id"] for m in pub.published]
    assert len(ids) == 2 and ids[0] == ids[1] == str(row.id)  # 同 message_id 重发（幂等 XADD）


async def test_valkey_down_dlq_after_max_attempts(session_factory) -> None:
    """故障注入③：Valkey 短暂不可用——渐进失败至 DLQ（不静默丢，入 DLQ 可审计）。"""
    pub = FakePublisher(fail_times=10 ** 9)  # 一直失败
    async with session_factory() as s:
        await _enqueue(s)
        await s.commit()
    d = OutboxDispatcher(session_factory, pub, poll_interval_s=0.0, batch=1)
    for _ in range(30):  # 退避前进 + 轮询直到 DLQ（attempt >= max_attempts=8）
        async with session_factory() as s:
            r = (await s.execute(select(ReliabilityOutbox))).scalars().one()
            if r.status == "DLQ":
                break
            r.available_at = datetime.now(UTC) - timedelta(seconds=5)  # 拨回退避时间
            await s.commit()
        await d.poll_once()
    async with session_factory() as s:
        row = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        assert row.status == "DLQ"
        assert row.attempt >= row.max_attempts
        assert row.last_error
    report = await OutboxReconciler(session_factory).report()
    assert report["dlq"] == 1
    assert report["pending"] == 0


async def test_backpressure_backoff_schedules_available_at(session_factory) -> None:
    pub = FakePublisher(fail_times=3)
    async with session_factory() as s:
        await _enqueue(s)
        await s.commit()
    d = OutboxDispatcher(session_factory, pub, poll_interval_s=0.0)
    await d.poll_once()  # 失败1
    async with session_factory() as s:
        r = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        r.available_at = datetime.now(UTC) - timedelta(seconds=5)  # 模拟退避到期
        await s.commit()
    await d.poll_once()  # 失败2
    async with session_factory() as s:
        row = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        assert row.attempt == 2
    # 指数退避：attempt=2 → backoff=2s（未来；SQLite 读回朴素时间）
    assert row.available_at is not None
    assert row.available_at > datetime.now(UTC).replace(tzinfo=None)


async def test_dispatcher_reconciler_report_counts(session_factory) -> None:
    pub = FakePublisher()
    async with session_factory() as s:
        await _enqueue(s, dedupe_key="a")
        await _enqueue(s, dedupe_key="b")
        await s.commit()
    d = OutboxDispatcher(session_factory, pub, poll_interval_s=0.01)
    await d.poll_once()
    report = await OutboxReconciler(session_factory).report()
    assert report == {"pending": 0, "published": 2, "dlq": 0}


async def test_restart_dispatcher_recovers_pending(file_db) -> None:
    """故障注入④：dispatcher 崩溃后重启（全新 session_factory）→ 未发布事件被新实例接管。"""
    f1 = file_db()
    async with f1() as s:
        await _enqueue(s)
        await s.commit()
    # 旧 dispatcher 崩溃（未 poll）；重启 = 新 factory + 新 dispatcher
    f2 = file_db()
    pub = FakePublisher()
    d = OutboxDispatcher(f2, pub, poll_interval_s=0.01)
    n = await d.poll_once()
    assert n == 1
    async with f2() as s:
        row = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        assert row.status == "PUBLISHED"


async def test_multi_dispatcher_no_double_publish(session_factory) -> None:
    """收敛性：一条消息被认领即出队（第二次轮询不重复发布）。真实并发互斥由 PG
    FOR UPDATE SKIP LOCKED 承担（SQLite 单连接下退化为顺序语义，见 R11.5-C 报告）。"""
    pub = FakePublisher()
    async with session_factory() as s:
        for i in range(6):
            await _enqueue(s, dedupe_key=f"k{i}")
        await s.commit()
    d1, d2 = OutboxDispatcher(session_factory, pub, poll_interval_s=0.01), OutboxDispatcher(session_factory, pub, poll_interval_s=0.01)
    n1 = await d1.poll_once()
    n2 = await d2.poll_once()
    assert n1 == 6
    assert n2 == 0  # 第二批无待发（同一批已被认领/发布，不重复）
    assert len(pub.published) == 6
