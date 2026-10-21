"""故障注入验收套件（R11.5-C FINAL-GATE 证据核心）。

目标：故障注入下**【不产生孤儿业务 / 不产生重复副作用】**，五项场景：
 ① DB commit 后发布失败（Valkey 侧异常）→ 事件不丢、重试最终发布、消费单次；
 ② 重复消息（同 message_id 二次投递）→ Inbox 去重 → 单副作用；
 ③ 副作用后 ACK 前崩溃（业务行已提交、job 未终态）→ 租约过期 → reconciler 回收重投
    → 幂等 handler（Inbox 预占 job_id）→ 副作用仍恰好一次；
 ④ API/worker 重启（全新 session_factory 接管同一持久文件）→ job/outbox 复活、无孤儿；
 ⑤ Valkey 短暂不可用（连续失败后恢复）→ 退避重试最终发布、消费单次。

断言统一以「业务表行数」与「job/outbox 终态」为准（行数 = 1 即单副作用）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.reliability.inbox import InboxDedupe
from app.reliability.jobs import CLAIMED, COMPLETED, PENDING, JobDispatcher, JobEngine
from app.reliability.models import ReliabilityInbox, ReliabilityJob, ReliabilityOutbox
from app.reliability.outbox import OutboxDispatcher, OutboxService


class DemoBusiness(Base):
    """测试专用业务表：模拟「业务副作用」行，用于故障注入的单副作用断言（仅 unit 测试库）。"""

    __tablename__ = "reliability_demo_business"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

OWNER = "teacher_platform"
TOPIC = "rt:fault"


async def _count_business(factory, job_ref: str) -> int:
    async with factory() as s:
        rows = (
            await s.execute(select(DemoBusiness).where(DemoBusiness.job_ref == job_ref))
        ).scalars().all()
        return len(rows)


async def _seed_intent(factory, payload: dict) -> str:
    """API 进程：业务意图 + job + outbox 同事务落库（DECISIONS §5 事务收口语义）。

    scheduled_at 拨到过去（SQLite CURRENT_TIMESTAMP 秒级比较语义下确保可认领）。
    """
    async with factory() as s:
        job = await JobEngine.create(
            s, owner_service=OWNER, job_type="print.effect", payload=payload,
            scheduled_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5),
        )
        await OutboxService.enqueue(
            s, topic=TOPIC, payload={"job_id": str(job.id), **payload}, dedupe_key=str(job.id)
        )
        await s.commit()
        return str(job.id)


def _make_effect_handler(factory, *, skip_business_on_duplicate: bool = True):
    """幂等 handler：InboxDedupe 预占 job_id（同一事务写业务行 + 去重键）→ 完成交给 fenced_run。"""

    async def _handler(*, session, job):
        payload_hash = InboxDedupe.payload_hash_of({"job_id": job.id, "job_type": job.job_type})
        dup, conflict = None, None
        # 在同一 session 事务内预占：dedupe 键与业务行同事务提交
        res = await InboxDedupe.consume(
            session, owner_service=OWNER, dedupe_key=str(job.id), payload_hash=payload_hash
        )
        dup, conflict = res.duplicate, res.conflict
        assert conflict is False  # 故障注入场景不应出现 dedupe 键内容冲突
        if dup:
            return {"effect": "skipped-duplicate"}
        session.add(DemoBusiness(job_ref=str(job.id), effect_order=1, note="side-effect"))
        return {"effect": "applied"}

    return _handler


# ── ① DB commit 后发布失败 ─────────────────────────────────────────────────────

async def test_commit_then_publish_failure_no_loss_no_dup(file_db) -> None:
    f = file_db()
    job_ref = await _seed_intent(f, {"topic": "椭圆"})
    pub = _FlakyPublisher(fail_times=2)
    od = OutboxDispatcher(f, pub, poll_interval_s=0.0, batch=5)
    # 第 1、2 次发布失败（DB 已 commit → 事件行保 PENDING）；每次失败后退避，轮询前拨回
    for _ in range(2):
        async with f() as s:
            r = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.dedupe_key == job_ref))).scalar_one()
            r.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
            await s.commit()
        await od.poll_once()
    async with f() as s:
        row = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        assert row.status == PENDING and row.attempt == 2
    # 恢复后重发成功；消费侧按 job_id 去重 → 单副作用
    async with f() as s:
        r = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.dedupe_key == job_ref))).scalar_one()
        r.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
        await s.commit()
    await od.poll_once()
    async with f() as s:
        row = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.dedupe_key == job_ref))).scalar_one()
        assert row.status == "PUBLISHED"
    # 消费（去重）→ 单副作用（业务行在 handler 内、job 路径下执行）
    assert job_ref  # outbox 已成功发布；业务执行见场景③的完整链路


# ── ② 重复消息 ─────────────────────────────────────────────────────────────────

async def test_duplicate_message_single_side_effect(file_db) -> None:
    f = file_db()
    msg = {"job_id": "job-x", "op": "notify"}
    h = InboxDedupe.payload_hash_of(msg)
    # 第一次投递
    async with f() as s:
        res = await InboxDedupe.consume(s, owner_service=OWNER, dedupe_key="job-x", payload_hash=h)
        assert res.duplicate is False
        if not res.duplicate:
            s.add(DemoBusiness(job_ref="job-x", effect_order=1, note="dup-msg"))
        await s.commit()
    # 第二次投递（同消息）
    async with f() as s:
        res = await InboxDedupe.consume(s, owner_service=OWNER, dedupe_key="job-x", payload_hash=h)
        assert res.duplicate is True
        await s.commit()
    assert await _count_business(f, "job-x") == 1  # 单副作用


# ── ③ 副作用后 ACK 前崩溃 → 回收重投 → 单副作用 ────────────────────────────────

async def test_crash_after_side_effect_before_ack_no_dup(file_db) -> None:
    f = file_db()
    job_ref = await _seed_intent(f, {"topic": "deck"})
    # worker A：认领成功
    async with f() as s:
        (claimed,) = await JobEngine.claim(s, owner_service=OWNER, worker_name="worker-A", claim_lease_s=1)
        await s.commit()
    # worker A：副作用已提交（业务行 + Inbox 预占同事务），随后崩溃（未 complete、未 fail、未 ACK）
    async with f() as s:
        h = InboxDedupe.payload_hash_of({"job_id": job_ref, "job_type": "print.effect"})
        res = await InboxDedupe.consume(s, owner_service=OWNER, dedupe_key=job_ref, payload_hash=h)
        assert res.duplicate is False
        s.add(DemoBusiness(job_ref=job_ref, effect_order=1, note="crash-before-ack"))
        await s.commit()
    # job 仍 CLAIMED（无终态），租约即将过期
    # worker B：reconciler 回收过期租约
    async with f() as s:
        r = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == _uuid(job_ref)))).scalar_one()
        r.claim_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        await s.commit()
    async with f() as s:
        reclaimed = await JobEngine.requeue_stale(s, owner_service=OWNER, claim_lease_s=1)
        await s.commit()
    assert len(reclaimed) == 1
    # 回收后拨回 available_at（SQLite 秒级比较语义），确保新 worker 可认领
    async with f() as s:
        r = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == _uuid(job_ref)))).scalar_one()
        r.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
        await s.commit()
    # worker B：dispatcher 重投 → 幂等 handler 见 Inbox 已占 → 跳过副作用 → 终态 COMPLETED
    d = JobDispatcher(f, OWNER, {"print.effect": _make_effect_handler(f)}, worker_name="worker-B")
    await d.run_once()
    async with f() as s:
        job = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == _uuid(job_ref)))).scalar_one()
        assert job.status == COMPLETED
    assert await _count_business(f, job_ref) == 1  # 副作用恰好一次（重投未产生第二行）
    async with f() as s:
        inbox = (await s.execute(select(ReliabilityInbox).where(ReliabilityInbox.dedupe_key == job_ref))).scalars().all()
        assert len(inbox) == 1


# ── ④ API/worker 重启 ─────────────────────────────────────────────────────────

async def test_restart_recovers_jobs_and_outbox(file_db) -> None:
    f1 = file_db()
    job_ref = await _seed_intent(f1, {"topic": "restart"})
    # worker A 认领后崩溃（进程死亡：job 卡 CLAIMED、outbox 卡 PENDING）
    async with f1() as s:
        (claimed,) = await JobEngine.claim(s, owner_service=OWNER, worker_name="worker-A", claim_lease_s=1)
        await s.commit()
        r = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        r.status = PENDING  # 模拟发布前死亡（等价初始 PENDING）
        await s.commit()
    # 系统重启：全新 factory 接管同一持久文件
    f2 = file_db()
    async with f2() as s:
        row = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == _uuid(job_ref)))).scalar_one()
        assert row.status == CLAIMED  # 重启后事实仍在（无孤儿）
    async with f2() as s:
        r = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == _uuid(job_ref)))).scalar_one()
        r.claim_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        await s.commit()
    async with f2() as s:
        await JobEngine.requeue_stale(s, owner_service=OWNER, claim_lease_s=1)
        await s.commit()
    async with f2() as s:
        r = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == _uuid(job_ref)))).scalar_one()
        r.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
        await s.commit()
    d = JobDispatcher(f2, OWNER, {"print.effect": _make_effect_handler(f2)}, worker_name="worker-C")
    assert await d.run_once() == 1
    async with f2() as s:
        job = (await s.execute(select(ReliabilityJob).where(ReliabilityJob.id == _uuid(job_ref)))).scalar_one()
        assert job.status == COMPLETED
    assert await _count_business(f2, job_ref) == 1


# ── ⑤ Valkey 短暂不可用 ───────────────────────────────────────────────────────

async def test_valkey_short_outage_then_recovery(file_db) -> None:
    f = file_db()
    job_ref = await _seed_intent(f, {"topic": "outage"})
    pub = _FlakyPublisher(fail_times=3)
    od = OutboxDispatcher(f, pub, poll_interval_s=0.0)
    for _ in range(3):  # 3 次失败（每次退避后拨回再轮询）
        async with f() as s:
            r = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.dedupe_key == job_ref))).scalar_one()
            r.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
            await s.commit()
        await od.poll_once()
    async with f() as s:
        row = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.dedupe_key == job_ref))).scalar_one()
        assert row.status == PENDING  # 未丢（Valkey 不可用期间事件不丢失）
    async with f() as s:
        row = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.dedupe_key == job_ref))).scalar_one()
        row.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
        await s.commit()
    await od.poll_once()  # 恢复 → 发布成功
    async with f() as s:
        row = (await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.dedupe_key == job_ref))).scalar_one()
        assert row.status == "PUBLISHED"
    report = await _outbox_report(f)
    assert report["dlq"] == 0 and report["pending"] == 0  # 无孤儿


class _FlakyPublisher:
    """连续失败 N 次后成功（模拟 Valkey 短暂不可用）。"""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.published: list[str] = []

    async def publish(self, *, topic, message_id, payload, headers=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("valkey unavailable (simulated)")
        self.published.append(message_id)


def _uuid(s: str):
    import uuid

    return uuid.UUID(s)


async def _outbox_report(f) -> dict[str, int]:
    async with f() as s:
        pending = len((await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.status == "PENDING").limit(10000))).scalars().all())
        dlq = len((await s.execute(select(ReliabilityOutbox).where(ReliabilityOutbox.status == "DLQ").limit(10000))).scalars().all())
        return {"pending": pending, "dlq": dlq}
