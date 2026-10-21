"""ReliabilityIdempotency 单测：五元键 / request_hash 重放 / 409 冲突 / 窗口 / 失败释放。"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.reliability.idempotency import (
    COMPLETED,
    RESERVED,
    IdempotencyConflict,
    ReliabilityIdempotency,
    request_hash_of,
)
from app.reliability.models import ReliabilityIdempotency as IdemRow

OPS = {"owner_service": "tp", "operation": "deck.generate", "principal": "user-1", "idempotency_key": "k-1"}


async def test_reserve_then_complete_single_row(db) -> None:
    reserved, existing = await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h1")
    assert reserved is True and existing is None
    await ReliabilityIdempotency.complete(db, **OPS, response={"deck_id": "d1"})
    await db.commit()
    rows = (await db.execute(select(IdemRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == COMPLETED
    assert rows[0].response == {"deck_id": "d1"}


async def test_replay_same_hash(db) -> None:
    await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h1")
    await ReliabilityIdempotency.complete(db, **OPS, response={"deck_id": "d1"})
    await db.commit()
    reserved, existing = await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h1")
    assert reserved is False
    assert existing is not None and existing.response == {"deck_id": "d1"}


async def test_same_key_different_payload_returns_409(db) -> None:
    await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h1")
    await ReliabilityIdempotency.complete(db, **OPS, response={"deck_id": "d1"})
    await db.commit()
    with pytest.raises(IdempotencyConflict) as ei:
        await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h2")
    assert ei.value.status_code == 409  # 同 key 不同 payload → HTTP 409（绝不放错响应）


async def test_in_progress_busy_then_complete(db) -> None:
    """首者在途（IN_PROGRESS）→ 次者拿到（reserved=False, existing=IN_PROGRESS）不直接 409。"""
    reserved, _ = await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h1")
    assert reserved is True
    reserved2, existing = await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h1")
    assert reserved2 is False and existing.status == RESERVED


async def test_fail_releases_key_for_retry(db) -> None:
    reserved, _ = await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h1")
    assert reserved is True
    await ReliabilityIdempotency.fail(db, **OPS)  # 业务失败 → 释放键
    await db.commit()
    rows = (await db.execute(select(IdemRow))).scalars().all()
    assert len(rows) == 0  # 键已释放，可重试


async def test_principal_scope_isolates_keys(db) -> None:
    ops_b = dict(OPS, principal="user-2")
    r1, _ = await ReliabilityIdempotency.reserve(db, **OPS, request_hash="h")
    r2, _ = await ReliabilityIdempotency.reserve(db, **ops_b, request_hash="h")
    assert r1 is True and r2 is True  # 不同 principal 互不冲突


def test_request_hash_canonicalizes_body_order() -> None:
    assert request_hash_of("POST", "/p", {"a": 1, "b": [1, 2]}) == request_hash_of(
        "POST", "/p", {"b": [1, 2], "a": 1}
    )
    assert request_hash_of("POST", "/p", {"a": 1}) != request_hash_of("PUT", "/p", {"a": 1})
    assert request_hash_of("POST", "/p", None) != request_hash_of("POST", "/p", {})
