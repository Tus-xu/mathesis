"""InboxDedupe 单测：首次消费 / 重复投递 / 内容冲突 / ACK 前崩溃语义。"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.reliability.inbox import InboxConsumeResult, InboxDedupe
from app.reliability.models import ReliabilityInbox


def _hash(payload: dict) -> str:
    return InboxDedupe.payload_hash_of(payload)


async def test_first_consume_reserves_key(db) -> None:
    res = await InboxDedupe.consume(
        db, owner_service="tp", dedupe_key="job-1", payload_hash=_hash({"a": 1})
    )
    assert isinstance(res, InboxConsumeResult)
    assert res.duplicate is False
    assert res.conflict is False
    await db.commit()
    # 事务外可见
    rows = (await db.execute(select(ReliabilityInbox))).scalars().all()
    assert len(rows) == 1
    assert rows[0].dedupe_key == "job-1"


async def test_duplicate_delivery_skipped(db) -> None:
    await InboxDedupe.consume(db, owner_service="tp", dedupe_key="job-2", payload_hash=_hash({"a": 1}))
    await db.commit()
    # 重复投递（同 key 同 hash）→ duplicate=True，跳过副作用
    res = await InboxDedupe.consume(db, owner_service="tp", dedupe_key="job-2", payload_hash=_hash({"a": 1}))
    assert res.duplicate is True
    assert res.conflict is False
    rows = (await db.execute(select(ReliabilityInbox))).scalars().all()
    assert len(rows) == 1


async def test_same_key_different_payload_conflict(db) -> None:
    await InboxDedupe.consume(db, owner_service="tp", dedupe_key="job-3", payload_hash=_hash({"a": 1}))
    await db.commit()
    res = await InboxDedupe.consume(db, owner_service="tp", dedupe_key="job-3", payload_hash=_hash({"a": 2}))
    assert res.duplicate is True
    assert res.conflict is True


async def test_scope_isolation_between_owners(db) -> None:
    r1 = await InboxDedupe.consume(db, owner_service="tp8100", dedupe_key="k", payload_hash=_hash({}))
    r2 = await InboxDedupe.consume(db, owner_service="ps8200", dedupe_key="k", payload_hash=_hash({}))
    assert r1.duplicate is False
    assert r2.duplicate is False  # 不同 owner_service 互不干扰


async def test_crash_before_commit_rolls_back_reservation(db) -> None:
    """副作用后 ACK 前崩溃：事务回滚 → 去重键未占 → 重放可再次消费（不丢副作用）。"""
    class _Crash(RuntimeError):
        pass

    with pytest.raises(_Crash):
        async with db.begin():
            await InboxDedupe.consume(
                db, owner_service="tp", dedupe_key="job-crash", payload_hash=_hash({"x": 1})
            )
            await db.flush()
            raise _Crash("模拟 ACK 前崩溃")  # 不 commit → 整体回滚
    # 崩溃后：键未占，重放首次消费成功
    res = await InboxDedupe.consume(db, owner_service="tp", dedupe_key="job-crash", payload_hash=_hash({"x": 1}))
    assert res.duplicate is False


async def test_payload_hash_stable(db) -> None:
    m1 = {"job_id": str(uuid.uuid4()), "status": "done", "nested": {"a": [1, 2]}}
    m2 = {"nested": {"a": [1, 2]}, "status": "done", "job_id": m1["job_id"]}
    assert _hash(m1) == _hash(m2)  # 键序无关


async def test_mark_failed_sets_status(db) -> None:
    await InboxDedupe.consume(db, owner_service="tp", dedupe_key="job-f", payload_hash=_hash({}))
    await db.commit()
    await InboxDedupe.mark_failed(db, owner_service="tp", dedupe_key="job-f", error="boom")
    await db.commit()
    row = (await db.execute(select(ReliabilityInbox))).scalars().one()
    assert row.status == "FAILED"
    assert row.error == "boom"
