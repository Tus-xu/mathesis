"""长会话 /messages 尾窗口：必须返回**最新** N 条（含最后一条）。

缺陷回归（2026-10-04）：list_messages 原为 order_by(seq) 升序 + limit——长会话
（seq 超过 limit）时返回的是**最早** N 条，老师刚发的消息反而查不到。修复后：
先按 seq 降序取尾部窗口，再反转为升序返回（前端按 seq 归并，见 useConversation）。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

import app.teacher_prep.conversation.router as conv_router
from app.core.deps import get_current_user, get_session
from app.core.security import UserToken
from app.teacher_prep.conversation.models import ConversationMessageRow
from app.teacher_prep.conversation.orchestrator import ConversationService
from app.teacher_prep.conversation.service_bridge import set_conversation_service_factory
from app.teacher_prep.models import TeachingSessionRow

from .conftest import CLASS_A, TEACHER

BASE = "/api/teacher-v3/prep/conversations"


@pytest.fixture
def api(db, session_factory, members, adapter):
    from main import create_app

    app = create_app()
    conv_router._adapter = lambda: adapter  # type: ignore[assignment]
    set_conversation_service_factory(lambda: ConversationService(session_factory=session_factory))

    async def _override_db():
        yield db

    def _override_user():
        return UserToken(user_id=TEACHER, name="张老师", role="teacher", school_id=None)

    app.dependency_overrides[get_session] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    return app, members


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _auth():
    return {"Authorization": "Bearer jwt-teacher-token"}


async def _seed_long_conversation(db, *, total: int = 210) -> str:
    """种 200+ 条消息的会话（seq 1..total 全序；不走发消息命令——本轮只验查询窗口）。"""
    srow = TeachingSessionRow(
        teacher_id=uuid.UUID(TEACHER), class_id=uuid.UUID(CLASS_A), status="active", title="长会话"
    )
    db.add(srow)
    await db.flush()
    for seq in range(1, total + 1):
        db.add(
            ConversationMessageRow(
                session_id=srow.id,
                seq=seq,
                role="user" if seq % 2 == 1 else "assistant",
                kind="text",
                text=f"第 {seq} 条消息",
            )
        )
    await db.commit()
    return str(srow.id)


async def test_messages_returns_latest_window_including_last(api, db):
    """210 条消息、limit=100：返回 seq 111..210 升序，且**含最后一条**（seq=210）。"""
    members = api[1]
    members.set(CLASS_A, user_id=TEACHER)
    sid = await _seed_long_conversation(db, total=210)
    async with _client(api[0]) as c:
        r = await c.get(f"{BASE}/{sid}/messages", params={"limit": 100}, headers=_auth())
        assert r.status_code == 200, r.text
        items = r.json()["data"]
        assert len(items) == 100
        seqs = [m["seq"] for m in items]
        assert seqs == list(range(111, 211))  # 最新 100 条，升序返回
        assert seqs[-1] == 210  # 必须含最后一条（老师刚发的消息）


async def test_messages_limit_one_returns_only_last(api, db):
    """limit=1：只返回最新 1 条（seq=210），不是最早 1 条。"""
    members = api[1]
    members.set(CLASS_A, user_id=TEACHER)
    sid = await _seed_long_conversation(db, total=210)
    async with _client(api[0]) as c:
        r = await c.get(f"{BASE}/{sid}/messages", params={"limit": 1}, headers=_auth())
        assert r.status_code == 200, r.text
        items = r.json()["data"]
        assert [m["seq"] for m in items] == [210]
