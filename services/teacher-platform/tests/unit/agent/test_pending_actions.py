"""R11.5-B：PendingAction 持久化 store 单测（InMemory 语义：状态机/过期/确认/拒绝/重启前状态）。"""
from __future__ import annotations

import time

import pytest

from app.agent.pending_actions import (
    APPROVED,
    EXPIRED,
    PENDING,
    REJECTED,
    InMemoryPendingStore,
    PendingAction,
)


def _action(action_id: str, teacher_id: str = "t1", *, expires_in: float | None = 100) -> PendingAction:
    return PendingAction(
        action_id=action_id,
        session_id="s1",
        teacher_id=teacher_id,
        tool="quiz_import_question",
        card={"type": "action", "id": action_id},
        expires_at=(time.time() + expires_in) if expires_in is not None else None,
    )


async def test_create_and_get_roundtrip() -> None:
    store = InMemoryPendingStore()
    await store.create(_action("a1"))
    got = await store.get("a1")
    assert got is not None and got.status == PENDING
    assert got.tool == "quiz_import_question"


async def test_confirm_approves_and_rejects() -> None:
    store = InMemoryPendingStore()
    await store.create(_action("a1"))
    ok = await store.confirm("a1", approved=True, params={"kp": "椭圆"}, by="t1")
    assert ok is not None and ok.status == APPROVED and ok.params_overrides == {"kp": "椭圆"}
    # 已处理：二次确认/拒绝 → None
    assert await store.confirm("a1", approved=False, params=None, by="t1") is None
    assert await store.mark_expired("a1") is None


async def test_expired_action_is_rejected_and_swept() -> None:
    store = InMemoryPendingStore()
    await store.create(_action("a2", expires_in=-10))  # 已过期
    # get() 触发惰性过期
    got = await store.get("a2")
    assert got is not None and got.status == EXPIRED
    # confirm 过期项 → None（不执行写工具）
    assert await store.confirm("a2", approved=True, params=None, by="t1") is None
    # sweep 后 list 不含过期 PENDING
    store2 = InMemoryPendingStore()
    await store2.create(_action("a3", expires_in=-1))
    await store2.sweep_expired()
    assert await store2.list_by_teacher("t1") == []


async def test_list_filters_by_teacher_and_pending() -> None:
    store = InMemoryPendingStore()
    await store.create(_action("a1", teacher_id="t1"))
    await store.create(_action("a2", teacher_id="t2"))
    await store.confirm("a1", approved=True, params=None, by="t1")
    assert [a.action_id for a in await store.list_by_teacher("t1")] == []  # 已处理不列出
    assert [a.action_id for a in await store.list_by_teacher("t1", include_handled=True)] == ["a1"]
    assert [a.action_id for a in await store.list_by_teacher("t2")] == ["a2"]


async def test_restart_survival_via_same_store_recreation() -> None:
    """服务重启恢复：store 生命周期外重建（模拟重启），状态仍可查/可操作。"""
    store = InMemoryPendingStore()
    await store.create(_action("a-keep", expires_in=1000))
    # 模拟重启：新 store 实例从「持久层」恢复——用 dict 直传模拟 PG 持久化语义
    recovered = InMemoryPendingStore()
    recovered._rows = dict(store._rows)
    got = await recovered.get("a-keep")
    assert got is not None and got.status == PENDING
    confirmed = await recovered.confirm("a-keep", approved=True, params=None, by="t1")
    assert confirmed is not None and confirmed.status == APPROVED


async def test_mark_expired_sets_status() -> None:
    store = InMemoryPendingStore()
    await store.create(_action("a4", expires_in=None))  # 永不过期
    await store.mark_expired("a4")
    assert (await store.get("a4")).status == EXPIRED