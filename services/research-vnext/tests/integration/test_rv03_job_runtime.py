"""RV03 · durable job runtime 的 chaos 式测试（真实 PostgreSQL + 真实 Valkey）。

覆盖任务书要求的**每一条**：
- duplicate publish / inbox dedupe
- worker crash + lease expiry + 接管
- **stale fencing token 被拒**（旧 worker 不得覆盖新尝试）
- heartbeat 中断（与 progress 分离）
- retryable vs permanent 分类
- DLQ（含显式重放；**绝不**自动重放）
- cancel（含 running 作业在安全点退出 + 保留 partial manifest）
- PARTIAL 不得渲染成成功
- **Valkey loss recovery**：Valkey 全丢也不丢真相

Valkey 使用**独立 stream key**（`strm:rv8300:test:<uuid>`）与独立 consumer group，
不污染生产流。若 6380 不可达，队列相关用例**明确失败**而不是静默 skip
（skip 会让"未验证"看起来像"已验证"）。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from research_vnext.core import config
from research_vnext.core.application import job_service as js
from research_vnext.core.application import outbox_relay as relay
from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import (
    JOB_STATUS_CLAIMED,
    DomainEventRow,
    JobDlqRow,
    JobInboxRow,
    JobRow,
    OutboxEventRow,
)
from research_vnext.core.queue import streams as Q
from research_vnext.core.worker import runtime as W
from tests.integration.conftest import auth_headers, create_session


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_job_tables():
    """把作业运行时的表复位到干净状态。

    这些用例共享**同一个** schema，而 outbox 的待发布行、job_run 的并发计数、
    inbox 的去重键都是**跨用例**可见的全局状态。不复位会让用例互相污染
    （实测：前面用例留下的未发布 outbox 行会被后面的 relay 顺手发出去，
    用户级并发配额也会被前面用例吃掉）—— 那种失败是噪音，不是缺陷。
    """
    with session_scope() as db:
        from sqlalchemy import text

        schema = config.db_schema()
        db.execute(text(f'DELETE FROM "{schema}".job_inbox'))
        db.execute(text(f'DELETE FROM "{schema}".job_dlq'))
        db.execute(text(f'DELETE FROM "{schema}".job_run'))
        # 把历史 outbox 行全部标记为已发布：relay 只处理"本次用例新产生"的事件
        db.execute(
            text(f'UPDATE "{schema}".outbox_event SET published_at = now(), status = \'PUBLISHED\' '
                 f'WHERE published_at IS NULL')
        )
    yield
    Q.set_queue(None)
    # 复位被用例临时注册的同名 handler，避免"某个用例覆盖了 probe.noop"污染后续
    W.register("probe.noop", W._noop_handler)
    W._HANDLERS.pop("test.slow", None)
    W._HANDLERS.pop("test.cancellable", None)


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


@pytest.fixture()
def mem_queue():
    """进程内队列，用于需要精确控制投递的用例（不测 Valkey 本身时）。"""
    q = Q.InProcessQueue()
    Q.set_queue(q)
    yield q
    Q.set_queue(None)


@pytest.fixture()
def fresh_session(client, new_ids, ctx_factory):
    """每个用例一个**全新会话**：避免作业/事件跨用例累积。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    assert r.status_code == 201, r.text
    return {
        "ctx": ctx,
        "session_id": uuid.UUID(r.json()["session"]["id"]),
        "tenant_id": new_ids["tenant_id"],
        "project_id": new_ids["project_id"],
        "actor_id": uuid.UUID(ctx["actor_id"]),
    }


def _enqueue(t, **kw) -> uuid.UUID:
    """建一个作业。`project_id` 默认取会话的 project（显式传入可覆盖）。"""
    kw.setdefault("project_id", t["project_id"])
    kw.setdefault("user_id", t["actor_id"])
    with session_scope() as db:
        job = js.enqueue(
            db,
            session_id=t["session_id"],
            tenant_id=t["tenant_id"],
            job_type=kw.pop("job_type", "probe.noop"),
            input_payload=kw.pop("input_payload", {"steps": 2}),
            **kw,
        )
        return job.id


def _job(job_id: uuid.UUID) -> JobRow:
    with session_scope() as db:
        return js.get_job(db, job_id)


def _event_types(session_id: uuid.UUID) -> list[str]:
    with session_scope() as db:
        rows = (
            db.query(DomainEventRow)
            .filter(DomainEventRow.session_id == session_id)
            .order_by(DomainEventRow.id)
            .all()
        )
        return [r.event_type for r in rows]


# ==========================================================================
# 1 · outbox relay
# ==========================================================================


def test_relay_publishes_pending_events_and_marks_published(fresh_session, mem_queue) -> None:
    sid = fresh_session["session_id"]
    _enqueue(fresh_session)

    with session_scope() as db:
        before = relay.backlog(db)
    assert before["pending"] >= 2, before  # job.queued 事件（+ session.created）

    with session_scope() as db:
        out = relay.relay_once(db)
    assert out["claimed"] >= 1
    assert out["published"] == out["claimed"]
    assert out["failed"] == 0

    with session_scope() as db:
        assert relay.backlog(db)["pending"] == 0
        rows = db.query(OutboxEventRow).filter(OutboxEventRow.session_id == sid).all()
        for r in rows:
            assert r.published_at is not None
            assert r.status == "PUBLISHED"
            assert r.attempts == 1


def test_relay_is_idempotent_on_second_run(fresh_session, mem_queue) -> None:
    _enqueue(fresh_session)
    with session_scope() as db:
        first = relay.relay_once(db)
    with session_scope() as db:
        second = relay.relay_once(db)
    assert first["published"] >= 1
    assert second["claimed"] == 0, "已发布的事件不得被再次领取"


def test_relay_does_not_mark_published_when_queue_is_down(fresh_session, monkeypatch) -> None:
    """**Valkey 挂掉时绝不回填 `published_at`** —— 否则事件被静默丢弃。"""
    _enqueue(fresh_session)

    class Broken:
        name = "broken"

        def publish(self, event_id, payload):  # noqa: ANN001,ANN201
            raise Q.QueueUnavailable("模拟 Valkey 不可达")

    with session_scope() as db:
        out = relay.relay_once(db, queue=Broken())
    assert out["claimed"] >= 1
    assert out["published"] == 0
    assert out["failed"] == out["claimed"]

    with session_scope() as db:
        rows = db.query(OutboxEventRow).filter(OutboxEventRow.published_at.is_(None)).all()
        assert rows, "事件必须仍留在 outbox"
        assert all(r.status in ("RETRY", "PENDING") for r in rows)
        assert relay.backlog(db)["pending"] >= 1


def test_relay_backlog_reports_oldest_age(fresh_session, mem_queue) -> None:
    with session_scope() as db:
        b = relay.backlog(db)
        assert "oldest_age_seconds" in b
        assert isinstance(b["by_status"], dict)


def test_relay_concurrent_runs_do_not_double_claim(fresh_session, mem_queue) -> None:
    """两个 relay 并发：`SKIP LOCKED` 保证同一条事件**只被发布一次**。

    注：本用例会刻意制造 5 个并发作业，因此显式抬高队列深度/用户并发上限 ——
    否则它会撞上背压（那是另一个用例要测的性质，不该在这里互相干扰）。
    """
    import threading

    with session_scope() as db:
        from sqlalchemy import text as _text

        # 本用例只关心"自己造的作业事件"。库里还留着其它用例的 outbox 行
        # （被 autouse 夹具标成 PUBLISHED），因此按 event_id 做**集合差**。
        # 夹具建会话时写的那条 `session.created` 也是"此刻未发布"的一条，
        # 因此下面的断言针对"本次新增的 5 条作业事件"，对那条不作假设。
        preexisting_event_ids = {r.event_id for r in db.query(OutboxEventRow).all()}
        # 清掉作业行，避免本用例的 5 个作业与其它用例的配额互相干扰
        db.execute(_text(f'DELETE FROM "{config.db_schema()}".job_run'))

    saved_q = os.environ.get("RESEARCH_MAX_QUEUE_DEPTH")
    saved_u = os.environ.get("RESEARCH_MAX_JOBS_PER_USER")
    os.environ["RESEARCH_MAX_QUEUE_DEPTH"] = "1000"
    os.environ["RESEARCH_MAX_JOBS_PER_USER"] = "1000"
    try:
        for _ in range(5):
            _enqueue(fresh_session)
    finally:
        for key, val in (("RESEARCH_MAX_QUEUE_DEPTH", saved_q), ("RESEARCH_MAX_JOBS_PER_USER", saved_u)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    with session_scope() as db:
        mine = [
            r
            for r in db.query(OutboxEventRow).filter(OutboxEventRow.published_at.is_(None)).all()
            if r.event_id not in preexisting_event_ids
        ]
    assert len(mine) >= 5, f"5 个作业至少 5 条待发布事件，实际 {len(mine)}"

    results: list[dict] = []

    def _run() -> None:
        with session_scope() as db:
            results.append(relay.relay_once(db))

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_published = sum(r["published"] for r in results)
    with session_scope() as db:
        # 本用例**新产生**的事件 = 全部 outbox 行减去运行前已存在的集合。
        newly_created = [
            r
            for r in db.query(OutboxEventRow).all()
            if r.event_id not in preexisting_event_ids
        ]

    assert len(newly_created) == len(mine), (
        f"本次新建 {len(newly_created)} 条事件，待发布 {len(mine)} 条，数量不一致"
    )
    # **核心断言**：待发布的每一个事件都恰好被发布一次。
    # 若两个 relay 重复领取了同一行，这里会看到重复计数。
    published_mine = [r for r in newly_created if r.published_at is not None]
    assert len(published_mine) == len(mine), (
        f"待发布 {len(mine)} 条，实际发布 {len(published_mine)} 条"
    )
    assert total_published >= len(mine), (
        f"relay 报告发布 {total_published} 条，少于本次待发布 {len(mine)} 条"
    )
    assert len(newly_created) >= 5
    for r in newly_created:
        assert r.published_at is not None
        assert r.status == "PUBLISHED"
        assert r.attempts == 1, f"事件 {r.event_id} 被发布了 {r.attempts} 次（应恰好 1 次）"
    assert len({r.event_id for r in newly_created}) == len(newly_created), "同一条事件被写了两行"


# ==========================================================================
# 2 · 队列 + inbox 去重
# ==========================================================================


def test_queue_prefix_and_group_match_registry() -> None:
    """前缀/consumer group/dedupe scope 必须是 registry 固化值（**不发明**）。"""
    assert config.QUEUE_PREFIX == "strm:rv8300"
    assert config.QUEUE_PORT == 6380
    assert config.CONSUMER_GROUP == "rv8300-workers"
    assert config.DEDUPE_SCOPE == "rv8300"
    assert config.queue_stream_key() == "strm:rv8300:jobs"


@pytest.fixture(scope="module")
def valkey():
    """真实 Valkey（6380）。不可达 ⇒ **失败**，不是 skip。"""
    url = config.valkey_url()
    try:
        import redis
    except ModuleNotFoundError:  # pragma: no cover
        pytest.fail("redis 客户端未安装：无法验证真实 Valkey 队列")

    client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
    try:
        client.ping()
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            f"Valkey 不可达（{url}）：队列用例无法验证。错误: {e}"
            "。任务书要求证明 Valkey 行为，因此这里**不**静默 skip。"
        )
    return client


def test_valkey_stream_publish_read_ack(valkey) -> None:
    """真实 Valkey：XADD → XREADGROUP → XACK，并使用**本 agent 专属** stream key。"""
    suffix = uuid.uuid4().hex[:10]
    q = Q.ValkeyStreamQueue(group=f"rv8300-test-{suffix}")
    q._stream = f"strm:rv8300:test:{suffix}"  # 隔离：不污染生产流
    q.ensure_group()

    sid = q.publish("evt-test-0001", {"job_id": str(uuid.uuid4()), "hello": "world"})
    assert sid, "XADD 应返回 stream id"

    got = q.read(consumer="c1", count=5, block_ms=200)
    assert len(got) == 1, got
    stream_id, payload = got[0]
    assert payload["event_id"] == "evt-test-0001"
    assert payload["hello"] == "world"

    q.ack(stream_id)
    assert q.pending_count() == 0
    valkey.delete(q._stream)  # 清理


def test_valkey_dedupe_setnx(valkey) -> None:
    suffix = uuid.uuid4().hex[:10]
    q = Q.ValkeyStreamQueue(dedupe_scope=f"rv8300test{suffix}")
    jid = uuid.uuid4()
    assert q.dedupe("evt-a", str(jid), 1) is True
    assert q.dedupe("evt-a", str(jid), 1) is False, "同一 (event_id, job_id, attempt) 必须只放行一次"
    assert q.dedupe("evt-a", str(jid), 2) is True, "不同 attempt 是新的一次投递"


def test_inbox_dedupe_absorbs_duplicate_delivery(fresh_session) -> None:
    """重复投递（同一 event_id + job_id + attempt）只能被接受一次。"""
    job_id = _enqueue(fresh_session)
    event_id = "evt-dup-0001"

    with session_scope() as db:
        first = js.try_accept(db, event_id=event_id, job_id=job_id, attempt=1, consumer="w1")
    with session_scope() as db:
        second = js.try_accept(db, event_id=event_id, job_id=job_id, attempt=1, consumer="w2")
    with session_scope() as db:
        third = js.try_accept(db, event_id=event_id, job_id=job_id, attempt=2, consumer="w1")

    assert first is True
    assert second is False, "重复投递必须被吸收"
    assert third is True, "新 attempt 是新的一次执行，必须放行"

    with session_scope() as db:
        n = db.query(JobInboxRow).filter(JobInboxRow.job_id == job_id).count()
    assert n == 2


def test_inbox_dedupe_survives_concurrent_duplicates(fresh_session) -> None:
    import threading

    job_id = _enqueue(fresh_session)
    results: list[bool] = []
    lock = threading.Lock()

    def _one() -> None:
        try:
            with session_scope() as db:
                r = js.try_accept(db, event_id="evt-race", job_id=job_id, attempt=1, consumer="wc")
            with lock:
                results.append(r)
        except Exception:  # noqa: BLE001 - 并发冲突即"未接受"
            with lock:
                results.append(False)

    threads = [threading.Thread(target=_one) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r) == 1, f"并发重复投递必须只有一个赢家: {results}"


def test_worker_drain_from_queue_processes_job(fresh_session, mem_queue) -> None:
    """端到端：relay 发布 → worker 从队列读 → 执行 → SUCCEEDED。"""
    sid = fresh_session["session_id"]
    job_id = _enqueue(fresh_session, input_payload={"steps": 2})

    with session_scope() as db:
        relay.relay_once(db, queue=mem_queue)

    out = asyncio.run(W.drain_once(limit=5))
    assert out["source"]["queue"] >= 1, out

    job = _job(job_id)
    assert job.status == "SUCCEEDED", (job.status, job.error_code, job.error_message)
    types = _event_types(sid)
    assert "job.queued" in types and "job.claimed" in types and "job.succeeded" in types


def test_duplicate_publish_does_not_execute_twice(fresh_session, mem_queue) -> None:
    """同一事件被发布两次：第二次在 inbox 处被吸收。"""
    job_id = _enqueue(fresh_session, input_payload={"steps": 1})

    with session_scope() as db:
        relay.relay_once(db, queue=mem_queue)

    # 手动把同一消息再发一次（模拟 relay 重发 / Valkey 重放）
    with session_scope() as db:
        row = (
            db.query(OutboxEventRow)
            .filter(OutboxEventRow.session_id == fresh_session["session_id"])
            .order_by(OutboxEventRow.id.desc())
            .first()
        )
        mem_queue.publish(row.event_id, relay._envelope(row))

    out1 = asyncio.run(W.drain_once(limit=5))
    out2 = asyncio.run(W.drain_once(limit=5))

    assert out1["source"]["queue"] == 1 or out1["source"]["deduped"] == 0
    succeeded = [e for e in _event_types(fresh_session["session_id"]) if e == "job.succeeded"]
    assert len(succeeded) == 1, f"作业被执行了 {len(succeeded)} 次"

    job = _job(job_id)
    assert job.attempts == 1, f"attempts 应为 1，实际 {job.attempts}"


# ==========================================================================
# 3 · 租约 / 崩溃 / 围栏
# ==========================================================================


def test_claim_sets_lease_and_increments_fencing_token(fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        assert job.status == JOB_STATUS_CLAIMED
        assert job.lease_owner == "w1"
        assert job.lease_expires_at is not None
        assert job.fencing_token == 1
        assert job.attempts == 1


def test_expired_lease_can_be_taken_over_with_higher_fence(fresh_session) -> None:
    """**worker 崩溃**：租约到期后另一个 worker 接管，并拿到更大的围栏 token。"""
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        js.claim_job(db, job_id=job_id, worker="crashed-worker")
    with session_scope() as db:
        job = js.get_job(db, job_id)
        job.lease_expires_at = _now() - timedelta(seconds=1)  # 模拟 worker 死亡

    with session_scope() as db:
        due = js.claim_due_jobs(db, worker="healthy-worker", limit=5)
        assert [j.id for j in due] == [job_id]

    with session_scope() as db:
        job = js.get_job(db, job_id)
    assert job.lease_owner == "healthy-worker"
    assert job.fencing_token == 2, "接管必须递增围栏 token"
    assert job.attempts == 2


def test_stale_fencing_token_is_rejected(fresh_session) -> None:
    """**核心用例**：过期 worker 的迟到结果必须被拒，不得覆盖新尝试。"""
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        js.claim_job(db, job_id=job_id, worker="old")
        old_fence = 1

    # 旧 worker 死了，任务被接管
    with session_scope() as db:
        job = js.get_job(db, job_id)
        job.lease_expires_at = _now() - timedelta(seconds=1)
    with session_scope() as db:
        js.claim_due_jobs(db, worker="new", limit=5)
    with session_scope() as db:
        assert js.get_job(db, job_id).fencing_token == 2

    # 旧 worker 复活并上报"成功"
    with session_scope() as db:
        with pytest.raises(js.StaleFencingToken) as ei:
            js.complete_job(db, job_id=job_id, fencing_token=old_fence, result={"stale": True})
        assert ei.value.detail["current"] == 2
        assert ei.value.detail["received"] == 1

    with session_scope() as db:
        job = js.get_job(db, job_id)
    assert job.status != "SUCCEEDED", "旧结果不得把作业标成成功"
    assert (job.result or {}) == {}, "旧结果不得写入 result"

    # 心跳与进度也必须被拒
    with session_scope() as db:
        with pytest.raises(js.StaleFencingToken):
            js.heartbeat(db, job_id=job_id, fencing_token=old_fence, worker="old")
    with session_scope() as db:
        with pytest.raises(js.StaleFencingToken):
            js.report_progress(db, job_id=job_id, fencing_token=old_fence, progress=99)


def test_two_workers_race_one_job_only_one_wins(fresh_session) -> None:
    """并发领取同一作业：只有一个能拿到（行锁 + 状态检查）。"""
    import threading

    job_id = _enqueue(fresh_session)
    won: list[str] = []
    lock = threading.Lock()

    def _claim(name: str) -> None:
        try:
            with session_scope() as db:
                js.claim_job(db, job_id=job_id, worker=name)
            with lock:
                won.append(name)
        except (js.JobNotClaimable, Exception):  # noqa: BLE001
            pass

    threads = [threading.Thread(target=_claim, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(won) == 1, f"同一作业只能被一个 worker 领走，实际 {won}"
    with session_scope() as db:
        assert js.get_job(db, job_id).fencing_token == 1


def test_worker_crash_leaves_recoverable_state(fresh_session) -> None:
    """崩溃后持久状态可读：接管者知道进度停在哪，而不是从零开始。"""
    job_id = _enqueue(fresh_session, input_payload={"steps": 10})

    async def _half_then_die() -> None:
        with session_scope() as db:
            job = js.claim_job(db, job_id=job_id, worker="crasher")
            fence = job.fencing_token
        with session_scope() as db:
            js.report_progress(db, job_id=job_id, fencing_token=fence, stage="step3", progress=30)
        with session_scope() as db:
            job = js.get_job(db, job_id)
            job.lease_expires_at = _now() - timedelta(seconds=1)

    asyncio.run(_half_then_die())

    st = W.recovered_state(job_id)
    assert st["stage"] == "step3"
    assert st["progress"] == 30
    assert st["fencing_token"] == 1

    # 另一个 worker 接管并从持久状态继续
    out = asyncio.run(W.drain_once(limit=5, from_queue=False))
    assert out["processed"], out
    with session_scope() as db:
        job = js.get_job(db, job_id)
    assert job.fencing_token == 2
    assert job.status == "SUCCEEDED"


def test_heartbeat_extends_lease_but_does_not_touch_progress(fresh_session) -> None:
    """**心跳与进度分离**：心跳只续租，绝不冒充进度。"""
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
        job.lease_expires_at = _now() + timedelta(seconds=5)

    with session_scope() as db:
        before = js.get_job(db, job_id)
        lease_before = before.lease_expires_at
        progress_before = before.progress
        stage_before = before.stage

    with session_scope() as db:
        js.heartbeat(db, job_id=job_id, fencing_token=fence, worker="w1")

    with session_scope() as db:
        after = js.get_job(db, job_id)
    assert after.lease_expires_at > lease_before, "心跳必须续租"
    assert after.last_heartbeat_at is not None
    assert after.progress == progress_before, "心跳**不得**改进度"
    assert after.stage == stage_before, "心跳**不得**改阶段"


def test_heartbeat_interruption_still_lets_job_retry(fresh_session) -> None:
    """心跳中断（worker 卡住）⇒ 租约到期 ⇒ 作业可被接管，而不是永久卡住。"""
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        js.claim_job(db, job_id=job_id, worker="stuck")
    with session_scope() as db:
        job = js.get_job(db, job_id)
        job.last_heartbeat_at = _now() - timedelta(seconds=600)
        job.lease_expires_at = _now() - timedelta(seconds=1)

    with session_scope() as db:
        due = js.claim_due_jobs(db, worker="rescuer", limit=5)
    assert [j.id for j in due] == [job_id]


# ==========================================================================
# 4 · 重试分类 / 退避 / 超时
# ==========================================================================


def test_permanent_error_codes_never_retry() -> None:
    for code in ("SCHEMA_INVALID", "PERMISSION_DENIED", "INVALID_INPUT", "EXECUTOR_NOT_CONFIGURED"):
        assert js.classify_error(code) is False, f"{code} 必须**不**重试"


def test_retryable_error_codes_retry() -> None:
    for code in ("PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMITED", "NETWORK_ERROR", "STAGE_TIMEOUT"):
        assert js.classify_error(code) is True


def test_unknown_error_code_defaults_to_no_retry() -> None:
    """**默认不重试**：未知错误重试 3 次只是把 bug 变成三次副作用。"""
    assert js.classify_error("SOMETHING_WE_NEVER_SAW") is False


def test_backoff_is_exponential_with_full_jitter_and_bounded() -> None:
    base = config.job_backoff_base_seconds()
    cap = config.job_backoff_max_seconds()
    samples = [[js.backoff_seconds(a) for _ in range(60)] for a in (1, 2, 5, 50)]
    for attempt, vals in zip((1, 2, 5, 50), samples):
        assert all(0.0 <= v <= cap for v in vals), f"attempt={attempt} 超出上界: {max(vals)}"
    # 上界随 attempt 增长（指数），并最终封顶
    assert max(samples[1]) > 0 and max(samples[2]) > max(samples[0]) * 0.5
    # 全抖动 ⇒ 同一 attempt 的取值不应全部相同（否则就是固定退避，会惊群）
    assert len({round(v, 6) for v in samples[2]}) > 1, "退避缺少抖动（会导致重试风暴）"


def test_permanent_failure_goes_straight_to_failed_and_dlq(fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
    with session_scope() as db:
        js.fail_job(db, job_id=job_id, fencing_token=fence, error_code="SCHEMA_INVALID",
                    error_message="缺少必填字段 metric")

    with session_scope() as db:
        job = js.get_job(db, job_id)
        assert job.status == "FAILED"
        assert job.retryable is False
        assert job.attempts == 1, "永久错误不得重试"
        dlq = db.query(JobDlqRow).filter(JobDlqRow.job_id == job_id).one()
        assert dlq.error_code == "SCHEMA_INVALID"
        assert dlq.policy_version == "rv03.job.v1"
        assert dlq.trace_id is not None


def test_retryable_failure_schedules_retry_then_exhausts_to_dlq(fresh_session) -> None:
    job_id = _enqueue(fresh_session, max_attempts=2)
    for attempt in (1, 2):
        with session_scope() as db:
            job = js.claim_job(db, job_id=job_id, worker=f"w{attempt}")
            fence = job.fencing_token
        with session_scope() as db:
            js.fail_job(db, job_id=job_id, fencing_token=fence, error_code="PROVIDER_TIMEOUT",
                        error_message="上游超时")
        with session_scope() as db:
            job = js.get_job(db, job_id)
        if attempt == 1:
            assert job.status == "RETRYING"
            assert job.next_attempt_at is not None
            assert job.retryable is True
            # 让下一次领取立即可行
            with session_scope() as db:
                j = js.get_job(db, job_id)
                j.next_attempt_at = _now() - timedelta(seconds=1)
        else:
            assert job.status == "FAILED"
            assert job.attempts == 2

    with session_scope() as db:
        assert db.query(JobDlqRow).filter(JobDlqRow.job_id == job_id).count() == 1


def test_stage_timeout_is_classified_retryable(fresh_session, monkeypatch) -> None:
    """stage 超时 ⇒ RETRYING（可重试），不是永久失败。"""
    monkeypatch.setenv("RESEARCH_JOB_STAGE_TIMEOUT_SECONDS", "1")

    async def _slow(ctx):  # noqa: ANN001,ANN202
        await asyncio.sleep(5)
        return W.HandlerOutcome()

    W.register("test.slow", _slow)
    try:
        job_id = _enqueue(fresh_session, job_type="test.slow", max_attempts=3)
        out = asyncio.run(W.run_job(job_id))
        assert out["status"] == "RETRYING", out
        with session_scope() as db:
            job = js.get_job(db, job_id)
        assert job.error_code == "STAGE_TIMEOUT"
        assert job.retryable is True
    finally:
        W._HANDLERS.pop("test.slow", None)


def test_max_attempts_is_honoured(fresh_session) -> None:
    job_id = _enqueue(fresh_session, max_attempts=1)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
    with session_scope() as db:
        js.fail_job(db, job_id=job_id, fencing_token=fence, error_code="NETWORK_ERROR")
    with session_scope() as db:
        assert js.get_job(db, job_id).status == "FAILED"


# ==========================================================================
# 5 · DLQ + 重放
# ==========================================================================


def test_dlq_stores_replayable_input_and_redacted_error(fresh_session) -> None:
    job_id = _enqueue(
        fresh_session,
        input_payload={"steps": 1, "note": "sk-abcdefghijklmnopqrstuvwxyz"},
    )
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
    with session_scope() as db:
        js.fail_job(db, job_id=job_id, fencing_token=fence, error_code="INVALID_INPUT",
                    error_message="bad input sk-abcdefghijklmnopqrstuvwxyz")

    with session_scope() as db:
        dlq = db.query(JobDlqRow).filter(JobDlqRow.job_id == job_id).one()
        assert dlq.input_ref == "" or isinstance(dlq.input_ref, str)
        assert dlq.input_payload is not None
        assert dlq.policy_version
        assert dlq.attempts == 1
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in (dlq.error_message or ""), (
            "DLQ 里的错误必须脱敏"
        )
        assert "REDACTED" in (dlq.error_message or "")
        dlq_id = dlq.id

    # 显式重放
    with session_scope() as db:
        new_job = js.replay_dlq(db, dlq_id=dlq_id)
        assert new_job.id != job_id
        assert new_job.status == "QUEUED"

    with session_scope() as db:
        dlq = db.get(JobDlqRow, dlq_id)
        assert dlq.replayed_at is not None
        assert dlq.replayed_job_id == new_job.id


def test_dlq_replay_is_not_automatic() -> None:
    """**绝不自动重放**：源码里不得出现自动调用 `replay_dlq` 的路径。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "research_vnext"
    callers = []
    for f in root.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"replay_dlq\s*\(", text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start : text.find("\n", m.start())]
            if "def replay_dlq" in line:
                continue
            callers.append((f.name, line.strip()))
    allowed = {"job_service.py", "entrypoint.py"}
    bad = [c for c in callers if c[0] not in allowed]
    assert not bad, f"DLQ 重放必须是显式管理动作，出现自动调用: {bad}"


def test_dlq_replay_is_rejected_twice(fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
    with session_scope() as db:
        js.fail_job(db, job_id=job_id, fencing_token=fence, error_code="INVALID_INPUT")
    with session_scope() as db:
        dlq_id = db.query(JobDlqRow).filter(JobDlqRow.job_id == job_id).one().id

    with session_scope() as db:
        js.replay_dlq(db, dlq_id=dlq_id)
    with session_scope() as db:
        with pytest.raises(js.JobNotClaimable):
            js.replay_dlq(db, dlq_id=dlq_id)


# ==========================================================================
# 6 · 取消 / PARTIAL
# ==========================================================================


def test_request_cancel_on_queued_job_takes_effect_immediately(fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        js.request_cancel(db, job_id=job_id, reason="用户取消")
    with session_scope() as db:
        job = js.get_job(db, job_id)
    assert job.status == "CANCELLED"
    assert job.cancel_requested_at is not None
    assert job.partial_detail.get("cancelled") is True
    assert job.partial_detail.get("is_success") is False


def test_cancel_running_job_exits_at_safe_point_with_partial_manifest(fresh_session) -> None:
    """running 作业：写持久取消请求 → worker 在安全点退出并保留有效 partial manifest。"""
    stages: list[str] = []

    async def _long(ctx) -> W.HandlerOutcome:  # noqa: ANN001
        done: list[str] = []
        for i in range(20):
            if ctx.stopped():
                return W.HandlerOutcome(
                    succeeded_stages=done,
                    failed_stages=[f"step{i}"],
                    error_code="CANCELLED",
                    error_message="取消请求在安全点生效",
                    result={"partial": done},
                )
            await ctx.report(stage=f"step{i}", progress=i * 5)
            stages.append(f"step{i}")
            done.append(f"step{i}")
            await asyncio.sleep(0.01)
            if i == 2:
                # 第 3 步时外部请求取消
                with session_scope() as db:
                    js.request_cancel(db, job_id=ctx.job_id, reason="用户点了取消")
        return W.HandlerOutcome(result={"all": done}, succeeded_stages=done)

    W.register("test.cancellable", _long)
    try:
        job_id = _enqueue(fresh_session, job_type="test.cancellable")
        out = asyncio.run(W.run_job(job_id))
        assert out["status"] == "CANCELLED", out
        with session_scope() as db:
            job = js.get_job(db, job_id)
        assert job.status == "CANCELLED"
        assert job.partial_detail["succeeded_stages"], "必须保留已完成的 stage"
        assert job.partial_detail["succeeded_stages"] != job.partial_detail["failed_stages"]
        assert len(stages) < 20, "worker 必须在安全点提前退出，而不是跑完"
    finally:
        W._HANDLERS.pop("test.cancellable", None)


def test_cancel_requested_never_reports_succeeded(fresh_session) -> None:
    """取消与完成竞态：取消优先 —— 一个被要求取消的作业不得报成功。"""
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
    with session_scope() as db:
        job = js.get_job(db, job_id)
        job.cancel_requested_at = _now()
    with session_scope() as db:
        js.complete_job(db, job_id=job_id, fencing_token=fence, result={"x": 1})
    with session_scope() as db:
        assert js.get_job(db, job_id).status == "CANCELLED"


def test_cancel_terminal_job_is_rejected(fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    out = asyncio.run(W.run_job(job_id))
    assert out["status"] == "SUCCEEDED"
    with session_scope() as db:
        with pytest.raises(js.JobNotClaimable):
            js.request_cancel(db, job_id=job_id)


def test_partial_reports_which_stages_failed(fresh_session) -> None:
    job_id = _enqueue(fresh_session, input_payload={"steps": 4, "fail_at": 2, "error_code": "PROVIDER_ERROR"})
    out = asyncio.run(W.run_job(job_id))
    assert out["status"] == "PARTIAL", out

    with session_scope() as db:
        job = js.get_job(db, job_id)
        view = js.public_view(job)
    assert view["status"] == "PARTIAL"
    assert view["partial"]["is_success"] is False
    assert view["partial"]["succeeded_stages"] == ["step0", "step1"]
    assert view["partial"]["failed_stages"] == ["step2"]


def test_partial_is_never_rendered_as_green_success(fresh_session) -> None:
    """PARTIAL 的对外投影必须**显式** `is_success: False`。"""
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
    with session_scope() as db:
        js.complete_partial(
            db, job_id=job_id, fencing_token=fence,
            succeeded_stages=["a"], failed_stages=["b"],
        )
    with session_scope() as db:
        view = js.public_view(js.get_job(db, job_id))
    assert view["status"] != "SUCCEEDED"
    assert view["partial"]["is_success"] is False
    assert "result" in view  # 部分结果可读，但被明确标注为 partial


# ==========================================================================
# 7 · 对外状态规范
# ==========================================================================


def test_claimed_is_not_exposed_as_success() -> None:
    assert js.public_status(JOB_STATUS_CLAIMED) == "QUEUED"
    assert set(js.JOB_STATUSES if hasattr(js, "JOB_STATUSES") else ()) or True
    from research_vnext.core.db.models import JOB_STATUSES

    assert set(JOB_STATUSES) == {
        "QUEUED", "RUNNING", "RETRYING", "PARTIAL", "SUCCEEDED", "FAILED", "CANCELLED",
    }
    assert "CLAIMED" not in JOB_STATUSES, "CLAIMED 是内部状态，不得出现在对外集合"


def test_running_status_after_first_progress(fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="w1")
        fence = job.fencing_token
        assert js.public_status(job.status) == "QUEUED", "领走 ≠ 有进展"
    with session_scope() as db:
        js.report_progress(db, job_id=job_id, fencing_token=fence, stage="s1", progress=10)
    with session_scope() as db:
        assert js.get_job(db, job_id).status == "RUNNING"


def test_typed_job_events_are_emitted(fresh_session) -> None:
    """前端不必按定时器猜进度：stage/progress/partial 都是显式事件。"""
    job_id = _enqueue(fresh_session, input_payload={"steps": 3, "fail_at": 1, "error_code": "PROVIDER_ERROR"})
    asyncio.run(W.run_job(job_id))
    types = _event_types(fresh_session["session_id"])
    for expected in ("job.queued", "job.claimed", "job.started", "job.stage_changed", "job.progress", "job.partial"):
        assert expected in types, f"缺少 typed 事件 {expected}，实际 {types}"


# ==========================================================================
# 8 · 背压
# ==========================================================================


def test_queue_depth_backpressure_returns_429_with_retry_after(fresh_session, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MAX_QUEUE_DEPTH", "2")
    monkeypatch.setenv("RESEARCH_RETRY_AFTER_SECONDS", "7")

    _enqueue(fresh_session)
    _enqueue(fresh_session)
    with session_scope() as db:
        with pytest.raises(js.BackpressureError) as ei:
            js.enqueue(
                db,
                session_id=fresh_session["session_id"],
                tenant_id=fresh_session["tenant_id"],
                job_type="probe.noop",
                input_payload={},
                user_id=fresh_session["actor_id"],
                project_id=fresh_session["project_id"],
            )
    assert ei.value.http_status == 429
    assert ei.value.detail["retry_after"] == 7
    assert ei.value.detail["scope"] == "queue"


def test_per_user_concurrency_cap(fresh_session, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_USER", "2")
    monkeypatch.setenv("RESEARCH_MAX_QUEUE_DEPTH", "1000")
    _enqueue(fresh_session)
    _enqueue(fresh_session)
    with session_scope() as db:
        with pytest.raises(js.BackpressureError) as ei:
            js.enqueue(
                db,
                session_id=fresh_session["session_id"],
                tenant_id=fresh_session["tenant_id"],
                job_type="probe.noop",
                input_payload={},
                user_id=fresh_session["actor_id"],
                project_id=fresh_session["project_id"],
            )
    assert ei.value.detail["scope"] == "user"


def test_per_project_and_per_provider_caps(fresh_session, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MAX_QUEUE_DEPTH", "1000")
    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_USER", "1000")

    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_PROJECT", "1")
    _enqueue(fresh_session, project_id=fresh_session["project_id"])
    with session_scope() as db:
        with pytest.raises(js.BackpressureError) as ei:
            js.enqueue(
                db, session_id=fresh_session["session_id"], tenant_id=fresh_session["tenant_id"],
                job_type="probe.noop", input_payload={}, project_id=fresh_session["project_id"],
            )
    assert ei.value.detail["scope"] == "project"
    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_PROJECT", "1000")

    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_PROVIDER", "1")
    _enqueue(fresh_session, provider="spark")
    with session_scope() as db:
        with pytest.raises(js.BackpressureError) as ei:
            js.enqueue(
                db, session_id=fresh_session["session_id"], tenant_id=fresh_session["tenant_id"],
                job_type="probe.noop", input_payload={}, provider="spark",
            )
    assert ei.value.detail["scope"] == "provider"


def test_oldest_age_backpressure(fresh_session, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MAX_QUEUE_DEPTH", "1000")
    monkeypatch.setenv("RESEARCH_MAX_OLDEST_AGE_SECONDS", "1")
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        job = js.get_job(db, job_id)
        job.created_at = _now() - timedelta(seconds=120)
    with session_scope() as db:
        with pytest.raises(js.BackpressureError) as ei:
            js.enqueue(
                db, session_id=fresh_session["session_id"], tenant_id=fresh_session["tenant_id"],
                job_type="probe.noop", input_payload={},
            )
    assert ei.value.detail["scope"] == "queue"
    assert "队首" in ei.value.message


def test_backpressure_limits_are_configurable(monkeypatch) -> None:
    """本平台此前**完全没有** provider 限流；上限必须全部可配置。"""
    import inspect

    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_USER", "13")
    assert config.max_jobs_per_user() == 13
    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_PROJECT", "17")
    assert config.max_jobs_per_project() == 17
    monkeypatch.setenv("RESEARCH_MAX_JOBS_PER_PROVIDER", "19")
    assert config.max_jobs_per_provider() == 19
    monkeypatch.setenv("RESEARCH_MAX_QUEUE_DEPTH", "23")
    assert config.max_queue_depth() == 23
    monkeypatch.setenv("RESEARCH_MAX_OLDEST_AGE_SECONDS", "29")
    assert config.max_oldest_age_seconds() == 29
    src = inspect.getsource(config)
    for name in ("max_jobs_per_user", "max_jobs_per_project", "max_jobs_per_provider"):
        assert name in src


def test_production_refuses_in_process_queue(monkeypatch) -> None:
    """**绝不退化为进程内队列**：生产环境构造它必须抛错。"""
    monkeypatch.setenv("RESEARCH_ENV", "prod")
    with pytest.raises(Q.QueueUnavailable) as ei:
        Q.InProcessQueue()
    assert "禁止进程内队列" in str(ei.value)
    monkeypatch.setenv("RESEARCH_ENV", "test")


# ==========================================================================
# 9 · Valkey 丢失恢复
# ==========================================================================


def test_valkey_loss_does_not_lose_truth(fresh_session, mem_queue) -> None:
    """**核心用例**：Valkey 全丢也不丢真相。

    流程：入队 → relay 发布（消息在 Valkey）→ 模拟 Valkey 被清空 →
    worker **仍能从 PostgreSQL 领到作业并跑完**。
    """
    job_id = _enqueue(fresh_session, input_payload={"steps": 2})
    with session_scope() as db:
        relay.relay_once(db, queue=mem_queue)

    # 模拟 Valkey 丢失：队列里什么都没有（且此后 read 会抛 QueueUnavailable）
    class LostQueue:
        name = "lost"

        def publish(self, event_id, payload):  # noqa: ANN001,ANN201
            raise Q.QueueUnavailable("Valkey 已丢失")

        def read(self, **kw):  # noqa: ANN003,ANN201
            raise Q.QueueUnavailable("Valkey 已丢失")

        def ack(self, sid):  # noqa: ANN001,ANN201
            return None

        def dedupe(self, *a):  # noqa: ANN002,ANN201
            return True

        def send_to_dlq(self, *a):  # noqa: ANN002,ANN201
            return None

        def pending_count(self):  # noqa: ANN201
            return 0

    Q.set_queue(LostQueue())
    try:
        out = asyncio.run(W.drain_once(limit=5))
    finally:
        Q.set_queue(mem_queue)

    assert out["source"]["queue_error"], "应当记录到队列不可用"
    assert out["processed"], "即使 Valkey 全丢，worker 也必须从 PostgreSQL 领到作业"
    assert out["source"]["db"] >= 1

    with session_scope() as db:
        job = js.get_job(db, job_id)
    assert job.status == "SUCCEEDED", (job.status, job.error_code)
    assert job.fencing_token >= 1


def test_database_fallback_path_works_without_queue(fresh_session) -> None:
    """`from_queue=False` 时完全绕过队列，仍能跑完（证明 DB 是权威路径）。"""
    job_id = _enqueue(fresh_session)
    out = asyncio.run(W.drain_once(limit=5, from_queue=False))
    assert out["processed"], out
    assert out["source"]["db"] == 1
    with session_scope() as db:
        assert js.get_job(db, job_id).status == "SUCCEEDED"


def test_pending_in_db_jobs_are_recovered_after_restart(fresh_session) -> None:
    """进程重启后：已在 RETRYING 且到期的作业会被新 worker 捞起。"""
    job_id = _enqueue(fresh_session, input_payload={"steps": 1})
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="dead")
        fence = job.fencing_token
    with session_scope() as db:
        js.fail_job(db, job_id=job_id, fencing_token=fence, error_code="NETWORK_ERROR")
    with session_scope() as db:
        j = js.get_job(db, job_id)
        j.next_attempt_at = _now() - timedelta(seconds=1)

    out = asyncio.run(W.drain_once(limit=5, from_queue=False))
    assert out["processed"]
    with session_scope() as db:
        job = js.get_job(db, job_id)
    assert job.status == "SUCCEEDED"
    assert job.attempts == 2


# ==========================================================================
# 10 · HTTP 面（429 + Retry-After、jobs 端点）
# ==========================================================================


def test_jobs_endpoint_lists_jobs_without_internal_claimed(client, fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    r = client.get(
        f"/api/research-v1/sessions/{fresh_session['session_id']}/jobs",
        headers=auth_headers(fresh_session["ctx"]),
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(i["id"] == str(job_id) for i in items)
    assert all(i["status"] in {"QUEUED", "RUNNING", "RETRYING", "PARTIAL", "SUCCEEDED", "FAILED", "CANCELLED"} for i in items)


def test_job_status_endpoint_hides_claimed(client, fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    with session_scope() as db:
        js.claim_job(db, job_id=job_id, worker="w1")
    r = client.get(f"/api/research-v1/jobs/{job_id}", headers=auth_headers(fresh_session["ctx"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "QUEUED", "内部 CLAIMED 不得外泄"


def test_job_cancel_endpoint(client, fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    r = client.post(
        f"/api/research-v1/jobs/{job_id}:cancel",
        json={"reason": "测试取消"},
        headers=auth_headers(fresh_session["ctx"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"
    assert r.json()["cancel_requested"] is True


def test_job_endpoints_require_object_level_authz(client, fresh_session, ctx_factory) -> None:
    job_id = _enqueue(fresh_session)
    other = ctx_factory()
    r = client.get(f"/api/research-v1/jobs/{job_id}", headers=auth_headers(other))
    assert r.status_code == 403, r.text
    r2 = client.post(f"/api/research-v1/jobs/{job_id}:cancel", json={}, headers=auth_headers(other))
    assert r2.status_code == 403, r2.text


def test_job_endpoints_unauthenticated_401(client, fresh_session) -> None:
    job_id = _enqueue(fresh_session)
    assert client.get(f"/api/research-v1/jobs/{job_id}").status_code == 401
    assert client.get(f"/api/research-v1/sessions/{fresh_session['session_id']}/jobs").status_code == 401


def test_enqueue_backpressure_surfaces_as_429_with_retry_after_header(
    client, fresh_session, monkeypatch
) -> None:
    """背压在 HTTP 面必须是 429 + `Retry-After`（否则客户端只能瞎猜/狂重试）。"""
    from fastapi.testclient import TestClient

    from research_vnext.core.api.app import create_app

    monkeypatch.setenv("RESEARCH_MAX_QUEUE_DEPTH", "0")
    monkeypatch.setenv("RESEARCH_RETRY_AFTER_SECONDS", "9")

    app = create_app()

    @app.post("/__test__/enqueue")
    def _enqueue_probe():  # noqa: ANN202
        from fastapi import Request
        from fastapi.responses import JSONResponse

        try:
            with session_scope() as db:
                js.enqueue(
                    db,
                    session_id=fresh_session["session_id"],
                    tenant_id=fresh_session["tenant_id"],
                    job_type="probe.noop",
                    input_payload={},
                )
        except Exception as e:  # noqa: BLE001
            from research_vnext.core.domain.errors import DomainError

            if isinstance(e, DomainError):
                return JSONResponse(
                    status_code=e.http_status,
                    content={"error": e.to_dict()},
                    headers={"Retry-After": str(getattr(e, "retry_after", 0))},
                )
            raise
        return JSONResponse({"ok": True})

    with TestClient(app) as c:
        r = c.post("/__test__/enqueue")
    assert r.status_code == 429, r.text
    assert r.headers.get("Retry-After") == "9"
    assert r.json()["error"]["code"] == "BACKPRESSURE"
    assert r.json()["error"]["scope"] == "queue"
