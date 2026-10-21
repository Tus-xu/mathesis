"""JobEngine 状态机单元测试（取消/重试/租约回收/超时/DLQ/幂等去重）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.errors import ApiError, ErrorCode
from app.reliability.jobs import JobEngine

ENGINE = JobEngine(lease_seconds=60, backoff_base_s=0.001, backoff_max_s=0.01)


async def _create(session, **kw):
    return await ENGINE.create(
        session,
        job_type="ppt.deck.prepare",
        payload={"presenton_probe": False, "deck_id": "d-1"},
        max_attempts=kw.get("max_attempts", 5),
        timeout_s=kw.get("timeout_s", 300),
        dedupe_key=kw.get("dedupe_key"),
    )


async def test_create_then_complete(session) -> None:  # noqa: ANN001
    job = await _create(session)
    await session.commit()
    assert job["status"] == "PENDING"
    claimed = await ENGINE.claim(session, job["id"], "tok-1")
    assert claimed["status"] == "CLAIMED"
    assert claimed["attempt"] == 1
    assert await ENGINE.complete(session, job["id"], "tok-1", {"ok": True})
    await session.commit()
    final = await ENGINE.get(session, job["id"])
    assert final["status"] == "COMPLETED"


async def test_claim_fencing_wrong_token(session) -> None:  # noqa: ANN001
    job = await _create(session)
    await session.commit()
    await ENGINE.claim(session, job["id"], "tok-a")
    # 错误 token 无法 complete/fail
    assert not await ENGINE.complete(session, job["id"], "tok-b", {})
    assert not await ENGINE.fail(session, job["id"], "tok-b", "x", retriable=True)


async def test_dedupe_key_conflict(session) -> None:  # noqa: ANN001
    await _create(session, dedupe_key="dup-key")
    await session.commit()
    with pytest.raises(ApiError) as ei:
        await _create(session, dedupe_key="dup-key")
    assert ei.value.code == ErrorCode.CONFLICT


async def test_cancel_requested_then_cancelled_at_claim(session) -> None:  # noqa: ANN001
    job = await _create(session)
    await session.commit()
    await ENGINE.request_cancel(session, job["id"])
    await session.commit()
    claimed = await ENGINE.claim(session, job["id"], "tok-c")
    assert claimed["status"] == "CANCELLED"


async def test_retriable_failure_backoff_then_success(session) -> None:  # noqa: ANN001
    job = await _create(session)
    await session.commit()
    claimed = await ENGINE.claim(session, job["id"], "t1")
    st = await ENGINE.fail(session, job["id"], "t1", "boom", retriable=True)
    await session.commit()
    assert st == "PENDING"
    assert claimed is not None  # claim 成功进入 CLAIMED
    # 退避后重试成功
    claimed2 = await ENGINE.claim(session, job["id"], "t2")
    assert claimed2["attempt"] == 2
    await ENGINE.complete(session, job["id"], "t2", {"ok": True})
    await session.commit()
    assert (await ENGINE.get(session, job["id"]))["status"] == "COMPLETED"


async def test_max_attempts_goes_dlq(session) -> None:  # noqa: ANN001
    job = await _create(session, max_attempts=2)
    await session.commit()
    for t in ("a", "b"):
        c = await ENGINE.claim(session, job["id"], t)
        assert c is not None
        st = await ENGINE.fail(session, job["id"], t, "boom", retriable=True)
        await session.commit()
    assert st == "DLQ"
    assert (await ENGINE.get(session, job["id"]))["status"] == "DLQ"


async def test_lease_expiry_reclaim(session) -> None:  # noqa: ANN001
    job = await _create(session)
    await session.commit()
    await ENGINE.claim(session, job["id"], "tok-old")
    await session.commit()
    # 直接构造过期时刻
    later = datetime.now(UTC) + timedelta(seconds=61)
    touched = await ENGINE.reclaim_expired(session, now=later)
    await session.commit()
    assert job["id"] in [str(t) for t in touched]
    assert (await ENGINE.get(session, job["id"]))["status"] == "PENDING"
    # 旧 token 失效
    assert not await ENGINE.complete(session, job["id"], "tok-old", {})


async def test_deadline_dlq(session) -> None:  # noqa: ANN001
    job = await _create(session, timeout_s=10)
    await session.commit()
    now = datetime.now(UTC)
    touched = await ENGINE.dlq_expired_deadlines(session, now=now + timedelta(seconds=11))
    await session.commit()
    assert job["id"] in [str(t) for t in touched]
    assert (await ENGINE.get(session, job["id"]))["status"] == "DLQ"


async def test_events_recorded(session) -> None:  # noqa: ANN001
    job = await _create(session)
    await session.commit()
    await ENGINE.claim(session, job["id"], "t")
    await ENGINE.complete(session, job["id"], "t", {})
    await session.commit()
    events = await ENGINE.list_events(session, job["id"])
    kinds = [e["event_type"] for e in events]
    assert kinds == ["CREATED", "CLAIMED", "COMPLETED"]


async def test_dlq_listing(session) -> None:  # noqa: ANN001
    job = await _create(session, max_attempts=1)
    await session.commit()
    await ENGINE.claim(session, job["id"], "t")
    await ENGINE.fail(session, job["id"], "t", "boom", retriable=True)
    await session.commit()
    rows = await ENGINE.list_dlq(session)
    assert any(r["id"] == job["id"] for r in rows)
