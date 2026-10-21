# coding: utf-8
"""WP-E / E-2 回归：确认卡登记不得 NameError，且必须持久化 900s TTL。

修复前：butler/router.py 用了未定义名 _DEFAULT_TTL_SECONDS（只 import 了 DEFAULT_TTL_SECONDS），
任何产出 action 卡的 /butler/chat 轮次都在 SSE 生成器内抛 NameError，
PendingAction / 人工确认流（Proposal→PendingAction→Confirm→重鉴权→变更）整体不可用（P0）。
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.agent.pending_actions import DEFAULT_TTL_SECONDS, PENDING, InMemoryPendingStore
from app.domains.butler import router as butler_router


@pytest.fixture
def store(monkeypatch):
    """只需替换 store 获取：其余走 register_pending_action 真实代码路径（含 TTL 表达式）。"""
    mem = InMemoryPendingStore()

    async def _get_store():
        return mem

    monkeypatch.setattr(butler_router, "_get_pending_store", _get_store)
    monkeypatch.setattr(butler_router, "_pending_store", None, raising=False)
    return mem


def test_default_ttl_constant_is_900_seconds():
    """TTL 契约值：app/agent/pending_actions.py 定义 15 分钟（不得被硬编码改写）。"""
    assert DEFAULT_TTL_SECONDS == 15 * 60 == 900


def test_module_has_no_undefined_dangling_ttl_name():
    """修复前遗留的 _DEFAULT_TTL_SECONDS 不得存在（防止再次被误用）。"""
    assert not hasattr(butler_router, "_DEFAULT_TTL_SECONDS")


async def test_register_pending_action_persists_with_900s_ttl(store):
    """修复前此处抛 NameError: name '_DEFAULT_TTL_SECONDS' is not defined。"""
    action_id = f"act-{uuid.uuid4().hex[:10]}"
    teacher_id = str(uuid.uuid4())
    card = {"type": "action", "id": action_id, "tool": "quiz.create"}

    before = time.time()
    await butler_router.register_pending_action(action_id, {
        "session_id": "bs-e2", "teacher_id": teacher_id, "tool": "quiz.create", "card": card,
    })
    after = time.time()

    persisted = await store.get(action_id)
    assert persisted is not None, "register_pending_action 未落库"
    assert persisted.status == PENDING
    assert persisted.tool == "quiz.create"
    assert persisted.session_id == "bs-e2"
    assert persisted.teacher_id == teacher_id
    assert persisted.card == card
    assert persisted.expires_at is not None
    # expires_at ≈ now + 900（容差覆盖调用前后的时钟漂移）
    assert before + 900 <= persisted.expires_at <= after + 900
    assert abs(persisted.expires_at - (persisted.created_at + 900)) < 5
