"""M2 对话系统根本性重构测试（规格 §2.12）

覆盖：
1. resolve_thread：regenerate 兄弟版本 supersede / activate 切换 / edit 截断分支
2. regenerate 端点：产生兄弟版本、老版本 superseded、SSE 契约（meta/done.message_id）
3. edit 端点：edited 首事件、新 user 兄弟、原消息 supersede、分支截断
4. stop：置位后流中断（49901）且落 interrupted assistant
5. feedback 持久化并在 messages 返回
6. conversations 分页/pin/rename/q
7. messages 返回 versions/attachments/thinking
8. 主通道熔断（§2.11.1）
9. clarify 落 assistant 消息（消除幽灵消息）

需要 PostgreSQL + Redis 运行中（与 test_iter05_chat 同环境/同模式）。
"""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.background import BackgroundTasks

from app.config import settings
from app.gateway import agent_router as ar
from app.kernel.router import RouteDecision
from app.kernel.thread import versions_of, walk_active_thread
from app.main import app
from app.models.conversation import Conversation
from app.models.database import get_db
from app.models.file import File, FileAsset
from app.models.message import Message
from app.providers.router import _CIRCUIT, ModelRouter

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


@asynccontextmanager
async def _db():
    async with _test_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_client(client):
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = login_resp.json()["data"]
    return client, data["token"], data["user"]["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _sse_events(text: str) -> list[str]:
    return [ln[7:] for ln in text.split("\n") if ln.startswith("event: ")]


def _sse_datas(text: str, event_type: str) -> list[dict]:
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        if ln == f"event: {event_type}" and i + 1 < len(lines) and lines[i + 1].startswith("data: "):
            out.append(json.loads(lines[i + 1][6:]))
    return out


def _sse_data(text: str, event_type: str) -> dict | None:
    datas = _sse_datas(text, event_type)
    return datas[0] if datas else None


def _chat_body(client_msg_id: str, **extra) -> dict:
    body = {
        "message": "解释一下导数的定义",
        "context": {"client_msg_id": client_msg_id, "workspace": "student"},
    }
    body.update(extra)
    return body


# ==================== 测试替身 ====================


class _FakeSkill:
    """假 skill：输出 thinking + 两段 token + _result_meta（文本可配）"""

    text: str = "回答内容"
    thinking: str = "思考一下"

    async def run(self, params, ctx):
        yield {"type": "thinking", "data": {"text": _FakeSkill.thinking}}
        yield {"type": "token", "data": {"text": _FakeSkill.text}}
        yield {
            "type": "_result_meta",
            "data": {
                "full_text": _FakeSkill.text,
                "provider": "fake",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        }


class _SlowSkill:
    """假 skill：发一个 token 后长时间挂起（stop 测试用）"""

    async def run(self, params, ctx):
        yield {"type": "token", "data": {"text": "开始回答"}}
        await asyncio.sleep(60)
        yield {"type": "token", "data": {"text": "不该出现"}}


class _FakeRegistry:
    skill = _FakeSkill
    slow = _SlowSkill

    def __init__(self, slow: bool = False):
        self._slow = slow

    def get(self, skill_id):
        return _SlowSkill() if self._slow else _FakeSkill()


class _FakeRouter:
    async def route(self, *args, **kwargs):
        return RouteDecision(skill_id="chat", confidence=0.95, params={})


class _FakeModelRouter:
    """假 ModelRouter：chat 直接回标题（标题并发任务用），不走真实 HTTP"""

    intended_provider = "fake"

    async def chat(self, messages, **kwargs):
        return {
            "content": "导数概念答疑",
            "provider": "fake",
            "latency_ms": 1,
            "input_tokens": 1,
            "output_tokens": 1,
        }


async def _fake_get_model_router(user_id, db):
    return _FakeModelRouter()


async def _noop_bg(*args, **kwargs):
    """后台任务替身（情景记忆/摘要会真实调 LLM，测试一律静默跳过）"""
    return None


def _patch_chat(slow: bool = False):
    return (
        patch.object(ar, "get_intent_router", return_value=_FakeRouter()),
        patch.object(ar, "get_skill_registry", return_value=_FakeRegistry(slow=slow)),
        patch.object(ar, "get_model_router_for_user", side_effect=_fake_get_model_router),
        patch.object(ar, "_bg_episodic_extract", side_effect=_noop_bg),
        patch.object(ar, "_bg_summary", side_effect=_noop_bg),
    )


async def _send_chat(client, token, message: str = "解释一下导数的定义") -> tuple[str, str]:
    """发一条 chat（fake skill），返回 (conversation_id, response_text)"""
    _FakeSkill.text = "初版回答"
    cmid = str(uuid.uuid4())
    ps = _patch_chat()
    with ps[0], ps[1], ps[2], ps[3], ps[4]:
        resp = await client.post(
            "/api/agent/chat", json=_chat_body(cmid, message=message), headers=_headers(token)
        )
    assert resp.status_code == 200
    meta = _sse_data(resp.text, "meta")
    return meta["conversation_id"], resp.text


async def _get_messages(client, token, conv_id: str, **params) -> dict:
    resp = await client.get(
        f"/api/agent/conversations/{conv_id}/messages",
        params=params,
        headers=_headers(token),
    )
    assert resp.status_code == 200
    return resp.json()["data"]


# ==================== 1. resolve_thread 纯函数 ====================


def _msg(mid: str, parent: str | None, ts: int, superseded: bool = False):
    return SimpleNamespace(
        id=mid,
        parent_id=parent,
        created_at=datetime.fromtimestamp(ts, UTC),
        superseded_at=datetime.fromtimestamp(ts + 1, UTC) if superseded else None,
    )


class TestWalkActiveThread:
    def test_linear_thread(self):
        msgs = [_msg("a", None, 1), _msg("b", "a", 2), _msg("c", "b", 3)]
        thread, _ = walk_active_thread(msgs)
        assert [m.id for m in thread] == ["a", "b", "c"]

    def test_sibling_superseded_branch(self):
        """regenerate 场景：b1 被 supersede，b2 活动 → 线程走 b2"""
        msgs = [
            _msg("a", None, 1),
            _msg("b1", "a", 2, superseded=True),
            _msg("b2", "a", 3),
        ]
        thread, children = walk_active_thread(msgs)
        assert [m.id for m in thread] == ["a", "b2"]
        v = versions_of(thread[1], children)
        assert v["count"] == 2 and v["index"] == 2

    def test_activate_older_version(self):
        """activate 切回旧版本：b2 被 supersede，b1 活动 → 线程走 b1 及其子链"""
        msgs = [
            _msg("a", None, 1),
            _msg("b1", "a", 2),
            _msg("b2", "a", 3, superseded=True),
            _msg("c1", "b1", 4),
        ]
        thread, _ = walk_active_thread(msgs)
        assert [m.id for m in thread] == ["a", "b1", "c1"]

    def test_edit_truncates_branch(self):
        """edit 场景：原 user 消息 b 被 supersede，新兄弟 b' 活动，
        b 的旧子链 c 不再进入活动线程"""
        msgs = [
            _msg("a", None, 1),
            _msg("b", "a", 2, superseded=True),
            _msg("b2", "a", 3),
            _msg("c", "b", 4),
            _msg("c2", "b2", 5),
        ]
        thread, children = walk_active_thread(msgs)
        assert [m.id for m in thread] == ["a", "b2", "c2"]
        v = versions_of(thread[1], children)
        assert v["count"] == 2 and v["index"] == 2


# ==================== 2. regenerate ====================


class TestRegenerate:
    async def test_regenerate_creates_sibling_version(self, auth_client):
        """重新生成 → 新兄弟版本、旧版本 superseded、versions.count=2、内容不同"""
        client, token, _ = auth_client
        conv_id, chat_text = await _send_chat(client, token)
        old_done = _sse_data(chat_text, "done")
        assert old_done and old_done.get("message_id")

        items = (await _get_messages(client, token, conv_id))["items"]
        old_assistant = next(m for m in items if m["role"] == "assistant")
        assert old_assistant["versions"]["count"] == 1

        # 重新生成（假 skill 输出不同内容）
        _FakeSkill.text = "重生回答"
        ps = _patch_chat()
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = await client.post(
                "/api/agent/chat/regenerate",
                json={"conversation_id": conv_id, "message_id": old_assistant["id"]},
                headers=_headers(token),
            )
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        assert "meta" in events and "done" in events
        new_done = _sse_data(resp.text, "done")
        assert new_done["message_id"] != old_assistant["id"]
        tokens = "".join(d["text"] for d in _sse_datas(resp.text, "token"))
        assert "重生回答" in tokens

        # 活动线程：新版本 index=2/count=2，旧版本不在线程中且已 superseded
        items = (await _get_messages(client, token, conv_id))["items"]
        assistants = [m for m in items if m["role"] == "assistant"]
        assert len(assistants) == 1
        assert assistants[0]["id"] == new_done["message_id"]
        assert assistants[0]["versions"]["count"] == 2
        assert assistants[0]["versions"]["index"] == 2
        async with _db() as s:
            old_msg = await s.get(Message, uuid.UUID(old_assistant["id"]))
            assert old_msg.superseded_at is not None

    async def test_regenerate_validates_target(self, auth_client):
        """非 assistant 消息/不存在 → 错误"""
        client, token, _ = auth_client
        conv_id, _ = await _send_chat(client, token)
        items = (await _get_messages(client, token, conv_id))["items"]
        user_msg = next(m for m in items if m["role"] == "user")
        ps = _patch_chat()
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = await client.post(
                "/api/agent/chat/regenerate",
                json={"conversation_id": conv_id, "message_id": user_msg["id"]},
                headers=_headers(token),
            )
        assert "error" in _sse_events(resp.text)

    async def test_activate_switches_version(self, auth_client):
        """activate 切回旧版本 → resolve_thread 变化，versions.index 跟随"""
        client, token, _ = auth_client
        conv_id, chat_text = await _send_chat(client, token)
        old_id = _sse_data(chat_text, "done")["message_id"]

        _FakeSkill.text = "重生回答"
        ps = _patch_chat()
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = await client.post(
                "/api/agent/chat/regenerate",
                json={"conversation_id": conv_id, "message_id": old_id},
                headers=_headers(token),
            )
        new_id = _sse_data(resp.text, "done")["message_id"]

        resp = await client.post(
            f"/api/agent/messages/{old_id}/activate", headers=_headers(token)
        )
        assert resp.json()["data"]["ok"] is True

        items = (await _get_messages(client, token, conv_id))["items"]
        assistants = [m for m in items if m["role"] == "assistant"]
        assert len(assistants) == 1
        assert assistants[0]["id"] == old_id
        assert assistants[0]["versions"]["index"] == 1
        assert assistants[0]["versions"]["count"] == 2
        async with _db() as s:
            new_msg = await s.get(Message, uuid.UUID(new_id))
            assert new_msg.superseded_at is not None


# ==================== 3. edit ====================


class TestEdit:
    async def test_edit_creates_branch_and_truncates(self, auth_client):
        """编辑重发 → edited 首事件、新 user 兄弟（parent 相同、attachments 继承）、
        原消息 supersede、活动线程截断走新分支"""
        client, token, _ = auth_client
        conv_id, _ = await _send_chat(client, token)
        items = (await _get_messages(client, token, conv_id))["items"]
        user_msg = next(m for m in items if m["role"] == "user")

        _FakeSkill.text = "改后回答"
        ps = _patch_chat()
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = await client.post(
                "/api/agent/chat/edit",
                json={
                    "conversation_id": conv_id,
                    "message_id": user_msg["id"],
                    "message": "换个问法：导数的几何意义",
                },
                headers=_headers(token),
            )
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        # edited 为流首个事件（规格 §3）
        assert events[0] == "edited"
        edited = _sse_data(resp.text, "edited")
        assert edited["user_msg_id"]
        done = _sse_data(resp.text, "done")
        assert done.get("message_id")

        # 活动线程：新 user（版本 2/2）+ 新 assistant；原 user 已 supersede
        items = (await _get_messages(client, token, conv_id))["items"]
        users = [m for m in items if m["role"] == "user"]
        assert len(users) == 1
        assert users[0]["id"] == edited["user_msg_id"]
        assert users[0]["versions"]["count"] == 2
        assert users[0]["versions"]["index"] == 2
        assert "几何意义" in (users[0]["envelope"]["blocks"][0]["content"])
        async with _db() as s:
            old_msg = await s.get(Message, uuid.UUID(user_msg["id"]))
            assert old_msg.superseded_at is not None
            new_msg = await s.get(Message, uuid.UUID(edited["user_msg_id"]))
            assert new_msg.parent_id == old_msg.parent_id


# ==================== 4. stop ====================


class TestStop:
    async def test_stop_endpoint_sets_cancel_keys(self, auth_client):
        """停止端点语义：按会话前缀置位全部取消键；已置位的不重复计数；
        指定 client_msg_id 时只置位对应键（httpx ASGITransport 不做增量流式，
        端到端断流行为由 test_stopped_stream_49901_and_persists 覆盖）"""
        client, token, _ = auth_client
        conv_id = str(uuid.uuid4())
        ev1, ev2 = asyncio.Event(), asyncio.Event()
        key1 = f"{conv_id}:cmid_a"
        key2 = f"{conv_id}:cmid_b"
        other_key = f"{uuid.uuid4()}:cmid_x"
        ev_other = asyncio.Event()
        ar._ACTIVE[key1], ar._ACTIVE[key2], ar._ACTIVE[other_key] = ev1, ev2, ev_other
        try:
            resp = await client.post(
                "/api/agent/chat/stop",
                json={"conversation_id": conv_id},
                headers=_headers(token),
            )
            assert resp.json()["data"]["stopped"] == 2
            assert ev1.is_set() and ev2.is_set()
            assert not ev_other.is_set()  # 其他会话不受影响

            # 已置位的键不重复计数
            resp = await client.post(
                "/api/agent/chat/stop",
                json={"conversation_id": conv_id},
                headers=_headers(token),
            )
            assert resp.json()["data"]["stopped"] == 0

            # client_msg_id 定向停止
            ev3 = asyncio.Event()
            ar._ACTIVE[f"{conv_id}:cmid_c"] = ev3
            resp = await client.post(
                "/api/agent/chat/stop",
                json={"conversation_id": conv_id, "client_msg_id": "cmid_c"},
                headers=_headers(token),
            )
            assert resp.json()["data"]["stopped"] == 1
            assert ev3.is_set()
        finally:
            for k in (key1, key2, other_key, f"{conv_id}:cmid_c"):
                ar._ACTIVE.pop(k, None)

    async def test_stopped_stream_49901_and_persists(self, auth_client):
        """流中断行为：取消键置位后 1s 级断流、error 49901、部分回答落 interrupted
        （直接驱动 _reply_events 生成器——ASGITransport 不增量流式，无法走 HTTP 断言）"""
        client, token, user_id = auth_client
        async with _db() as s:
            conv = Conversation(user_id=uuid.UUID(user_id), active_role="student", title="停止")
            s.add(conv)
            await s.flush()
            um = Message(
                conversation_id=conv.id,
                client_msg_id="cmid_stop",
                role="user",
                content="会被停止的问题",
            )
            s.add(um)
            await s.commit()
            conv_id, um_id = str(conv.id), um.id

        cancel_event = asyncio.Event()
        ps = _patch_chat(slow=True)
        chunks: list[str] = []
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            async with _test_session_factory() as s2:
                agen = ar._reply_events(
                    log=ar.logger,
                    db=s2,
                    model_router=_FakeModelRouter(),
                    user_id=user_id,
                    request_id="req-stop-test",
                    active_role="student",
                    conversation_id=conv_id,
                    user_message="会被停止的问题",
                    user_msg=SimpleNamespace(id=um_id),
                    ai_client_msg_id="ai_cmid_stop",
                    decision=RouteDecision(skill_id="chat", confidence=0.9, params={}),
                    attachment_texts=[],
                    parsed_events=[],
                    has_attachments=False,
                    thinking_pref=None,
                    cancel_event=cancel_event,
                    supersede_ids=[],
                    background_tasks=BackgroundTasks(),
                    title_task=None,
                    t_start=time.monotonic(),
                )
                # meta → 首个 token 后置位取消键
                chunks.append(await agen.__anext__())  # meta
                chunks.append(await agen.__anext__())  # token 开始回答
                assert "开始回答" in chunks[-1]
                cancel_event.set()
                t0 = time.monotonic()
                chunks.append(await agen.__anext__())  # error 49901
                elapsed = time.monotonic() - t0
                with pytest.raises(StopAsyncIteration):
                    await agen.__anext__()
        assert elapsed < 2  # 1s 级断流（不等 60s skill / 30s 心跳）
        err = _sse_data(chunks[-1], "error")
        assert err["code"] == 49901

        # 部分回答落库 interrupted
        async with _db() as s:
            rows = (
                await s.execute(
                    select(Message).where(
                        Message.conversation_id == uuid.UUID(conv_id),
                        Message.role == "assistant",
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            meta = (rows[0].envelope or {}).get("meta", {})
            assert meta.get("interrupted") is True and meta.get("stopped") is True
            assert "开始回答" in rows[0].content
            assert rows[0].parent_id == um_id

    async def test_stop_without_active_stream(self, auth_client):
        """无进行中流 → stopped=0"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/agent/chat/stop",
            json={"conversation_id": str(uuid.uuid4())},
            headers=_headers(token),
        )
        assert resp.json()["data"]["stopped"] == 0


# ==================== 5. feedback ====================


class TestFeedback:
    async def test_feedback_persist_and_return(self, auth_client):
        """feedback 落库 + messages 返回 + value='' 清除"""
        client, token, _ = auth_client
        conv_id, chat_text = await _send_chat(client, token)
        assistant_id = _sse_data(chat_text, "done")["message_id"]

        resp = await client.post(
            "/api/agent/feedback",
            json={"message_id": assistant_id, "value": "down", "reason": "步骤跳跃"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 0

        items = (await _get_messages(client, token, conv_id))["items"]
        assistant = next(m for m in items if m["id"] == assistant_id)
        assert assistant["feedback"] == "down"
        assert assistant["feedbackReason"] == "步骤跳跃"

        # 清除
        await client.post(
            "/api/agent/feedback",
            json={"message_id": assistant_id, "value": ""},
            headers=_headers(token),
        )
        items = (await _get_messages(client, token, conv_id))["items"]
        assistant = next(m for m in items if m["id"] == assistant_id)
        assert assistant["feedback"] is None
        assert assistant["feedbackReason"] is None

    async def test_feedback_legacy_contract(self, auth_client):
        """旧契约 {target_msg_id, reason: 'up'} 自动归位"""
        client, token, _ = auth_client
        conv_id, chat_text = await _send_chat(client, token)
        assistant_id = _sse_data(chat_text, "done")["message_id"]
        resp = await client.post(
            "/api/agent/feedback",
            json={"target_msg_id": assistant_id, "reason": "up"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 0
        async with _db() as s:
            msg = await s.get(Message, uuid.UUID(assistant_id))
            assert msg.feedback == "up"


# ==================== 6. conversations 分页/pin/rename/q ====================


class TestConversations:
    async def _create(self, client, token, n: int) -> list[str]:
        ids = []
        for _ in range(n):
            resp = await client.post(
                "/api/agent/conversations", json={}, headers=_headers(token)
            )
            ids.append(resp.json()["data"]["id"])
        return ids

    async def test_patch_rename_and_pin(self, auth_client):
        client, token, _ = auth_client
        ids = await self._create(client, token, 2)
        resp = await client.patch(
            f"/api/agent/conversations/{ids[0]}",
            json={"title": "导数专题", "pinned": True},
            headers=_headers(token),
        )
        data = resp.json()["data"]
        assert data["title"] == "导数专题" and data["pinned"] is True

        # pinned 优先排序
        resp = await client.get("/api/agent/conversations", headers=_headers(token))
        items = resp.json()["data"]["items"]
        assert items[0]["id"] == ids[0]

    async def test_search_q(self, auth_client):
        client, token, _ = auth_client
        ids = await self._create(client, token, 2)
        await client.patch(
            f"/api/agent/conversations/{ids[0]}",
            json={"title": "三角函数复习"},
            headers=_headers(token),
        )
        resp = await client.get(
            "/api/agent/conversations", params={"q": "三角"}, headers=_headers(token)
        )
        data = resp.json()["data"]
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == ids[0]

    async def test_pagination(self, auth_client):
        client, token, _ = auth_client
        await self._create(client, token, 3)
        resp = await client.get(
            "/api/agent/conversations", params={"limit": 2}, headers=_headers(token)
        )
        data = resp.json()["data"]
        assert len(data["items"]) == 2 and data["hasMore"] is True
        cursor = data["items"][-1]["updatedAt"]
        resp = await client.get(
            "/api/agent/conversations",
            params={"limit": 2, "before": cursor},
            headers=_headers(token),
        )
        page2 = resp.json()["data"]
        page1_ids = {i["id"] for i in data["items"]}
        assert all(i["id"] not in page1_ids for i in page2["items"])

    async def test_backward_compatible_no_args(self, auth_client):
        """无参调用形态不变：items/total 字段保留"""
        client, token, _ = auth_client
        await self._create(client, token, 1)
        resp = await client.get("/api/agent/conversations", headers=_headers(token))
        data = resp.json()["data"]
        assert "items" in data and "total" in data and "hasMore" in data
        item = data["items"][0]
        for key in ("id", "title", "activeRole", "summary", "messageCount", "createdAt", "updatedAt"):
            assert key in item
        assert item["pinned"] is False


# ==================== 7. messages 字段（versions/attachments/thinking） ====================


class TestMessageFields:
    async def test_thinking_and_fields(self, auth_client):
        """assistant 落 thinking（含 envelope.blocks 追加 thinking 块）、
        interrupted 标记、attachments 缺省 []"""
        client, token, _ = auth_client
        conv_id, chat_text = await _send_chat(client, token)
        # 流式中有 thinking 事件
        assert "thinking" in _sse_events(chat_text)

        items = (await _get_messages(client, token, conv_id))["items"]
        assistant = next(m for m in items if m["role"] == "assistant")
        assert assistant["thinking"] == "思考一下"
        assert assistant["attachments"] == []
        assert assistant["interrupted"] is False
        assert assistant["versions"]["count"] == 1
        block_types = [b["type"] for b in assistant["envelope"]["blocks"]]
        assert "thinking" in block_types  # 老前端兼容块

        user_msg = next(m for m in items if m["role"] == "user")
        assert user_msg["attachments"] == []
        assert user_msg["feedback"] is None

    async def test_attachments_persisted(self, auth_client):
        """发送带附件消息 → user 消息 attachments 随消息持久化"""
        client, token, user_id = auth_client
        # 造一个 parsed 文件
        async with _db() as s:
            f = File(
                user_id=uuid.UUID(user_id),
                filename="题目截图.png",
                mime="image/png",
                size_bytes=12345,
                sha256=uuid.uuid4().hex,
                file_type="image",
                status="parsed",
                parse_engine="ocr",
            )
            s.add(f)
            await s.flush()
            s.add(FileAsset(file_id=f.id, asset_type="markdown", page_no=1, content="已知 x>0"))
            await s.commit()
            file_id = str(f.id)

        ps = _patch_chat()
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = await client.post(
                "/api/agent/chat",
                json=_chat_body(
                    str(uuid.uuid4()),
                    attachments=[{"file_id": file_id, "kind": "image"}],
                ),
                headers=_headers(token),
            )
        assert resp.status_code == 200
        conv_id = _sse_data(resp.text, "meta")["conversation_id"]
        items = (await _get_messages(client, token, conv_id))["items"]
        user_msg = next(m for m in items if m["role"] == "user")
        assert len(user_msg["attachments"]) == 1
        att = user_msg["attachments"][0]
        assert att["file_id"] == file_id
        assert att["name"] == "题目截图.png"
        assert att["mime"] == "image/png"
        assert att["size"] == 12345
        assert att["kind"] == "image"

    async def test_messages_pagination(self, auth_client):
        """活动线程开窗：limit/before 翻页"""
        client, token, user_id = auth_client
        # 直接种 25 条线性消息
        async with _db() as s:
            conv = Conversation(user_id=uuid.UUID(user_id), active_role="student", title="分页")
            s.add(conv)
            await s.flush()
            parent = None
            for i in range(25):
                m = Message(
                    conversation_id=conv.id,
                    client_msg_id=f"m{i}",
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"第{i}条",
                    parent_id=parent,
                )
                s.add(m)
                await s.flush()
                parent = m.id
            await s.commit()
            conv_id = str(conv.id)

        data = await _get_messages(client, token, conv_id, limit=20)
        assert len(data["items"]) == 20 and data["hasMore"] is True
        before = data["items"][-1]["id"]
        page2 = await _get_messages(client, token, conv_id, limit=20, before=before)
        assert len(page2["items"]) == 5 and page2["hasMore"] is False


# ==================== 8. 主通道熔断（§2.11.1） ====================


class _FakeProvider:
    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self._model = f"{name}-model"
        self._api_url = f"https://{name}.test/{uuid.uuid4().hex[:6]}"
        self._fail = fail
        self.calls = 0

    @property
    def available(self):
        return True

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self._fail:
            raise RuntimeError(f"{self.name} down")
        return {
            "content": "ok",
            "provider": self.name,
            "latency_ms": 1,
            "input_tokens": 1,
            "output_tokens": 1,
        }


class TestCircuitBreaker:
    def setup_method(self):
        _CIRCUIT.clear()

    async def test_opens_after_two_failures(self):
        """连续失败 ≥2 次 → 熔断 5min 直接走备用；成功后自动恢复"""
        spark = _FakeProvider("spark", fail=True)
        deepseek = _FakeProvider("deepseek")
        router = ModelRouter(spark=spark, deepseek=deepseek)

        r1 = await router.chat([{"role": "user", "content": "hi"}], request_id="t1", scene="test")
        assert r1["provider"] == "deepseek"
        assert router._primary_usable() is True  # 1 次失败未熔断

        await router.chat([{"role": "user", "content": "hi"}], request_id="t2", scene="test")
        assert router._primary_usable() is False  # 2 次失败 → 熔断

        # 熔断期：不再调用主通道（白等超时消失）
        calls_before = spark.calls
        await router.chat([{"role": "user", "content": "hi"}], request_id="t3", scene="test")
        assert spark.calls == calls_before

        # 主通道恢复 → 成功后自动闭合
        spark._fail = False
        router._circuit_state()["open_until"] = 0.0  # 模拟熔断窗口过期
        r4 = await router.chat([{"role": "user", "content": "hi"}], request_id="t4", scene="test")
        assert r4["provider"] == "spark"
        assert router._circuit_state()["failures"] == []

    def test_intended_provider_respects_circuit(self):
        spark = _FakeProvider("spark")
        deepseek = _FakeProvider("deepseek")
        router = ModelRouter(spark=spark, deepseek=deepseek)
        assert router.intended_provider == "spark"
        router._record_primary_failure()
        router._record_primary_failure()
        assert router.intended_provider == "deepseek"


# ==================== 9. clarify 落库（消除幽灵消息） ====================


class TestClarifyPersist:
    async def test_attachment_clarify_persists_assistant(self, auth_client):
        """附件未就绪 clarify → assistant 消息落库（历史不再只有孤零零 user 消息）"""
        client, token, user_id = auth_client
        async with _db() as s:
            f = File(
                user_id=uuid.UUID(user_id),
                filename="解析中.pdf",
                mime="application/pdf",
                size_bytes=100,
                sha256=uuid.uuid4().hex,
                file_type="pdf",
                status="parsing",
            )
            s.add(f)
            await s.commit()
            file_id = str(f.id)

        resp = await client.post(
            "/api/agent/chat",
            json=_chat_body(
                str(uuid.uuid4()),
                attachments=[{"file_id": file_id, "kind": "doc"}],
            ),
            headers=_headers(token),
        )
        assert "clarify" in _sse_events(resp.text)
        done = _sse_data(resp.text, "done")
        assert done.get("message_id")  # clarify 也带 message_id

        # assistant 澄清消息已落库（幽灵消息消除），线性链挂在 user 消息下
        async with _db() as s:
            clarify_msg = await s.get(Message, uuid.UUID(done["message_id"]))
            assert clarify_msg is not None
            assert clarify_msg.envelope["meta"].get("clarify") is True
            assert "解析" in clarify_msg.content
            assert clarify_msg.parent_id is not None


# ==================== 10. 标题 SSE 事件 ====================


class TestTitleEvent:
    async def test_title_event_and_done_fields(self, auth_client):
        """首轮对话：title 事件在 done 前发出，done 带 message_id 与 title"""
        client, token, _ = auth_client
        conv_id, chat_text = await _send_chat(client, token)
        events = _sse_events(chat_text)
        assert "title" in events
        assert events.index("title") < events.index("done")
        title_data = _sse_data(chat_text, "title")
        assert title_data["title"] == "导数概念答疑"
        done = _sse_data(chat_text, "done")
        assert done["title"] == "导数概念答疑"
        assert done.get("message_id")


# ==================== 11. 幂等修正 ====================


class TestIdempotency:
    async def test_replay_only_for_completed(self, auth_client):
        """同 cmid 重放仅限已完成 assistant：完成态重放内容一致"""
        client, token, _ = auth_client
        cmid = str(uuid.uuid4())
        _FakeSkill.text = "幂等回答"
        ps = _patch_chat()
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp1 = await client.post(
                "/api/agent/chat", json=_chat_body(cmid), headers=_headers(token)
            )
            resp2 = await client.post(
                "/api/agent/chat", json=_chat_body(cmid), headers=_headers(token)
            )
        tokens1 = "".join(d["text"] for d in _sse_datas(resp1.text, "token"))
        tokens2 = "".join(d["text"] for d in _sse_datas(resp2.text, "token"))
        assert tokens1 == tokens2 == "幂等回答"

    async def test_interrupted_resend_creates_sibling(self, auth_client):
        """同 cmid 且已有 interrupted assistant → 视为重新发送，新建兄弟版本"""
        client, token, user_id = auth_client
        # 先种一个 interrupted 现场（user + interrupted assistant）
        async with _db() as s:
            conv = Conversation(user_id=uuid.UUID(user_id), active_role="student", title="重发")
            s.add(conv)
            await s.flush()
            um = Message(
                conversation_id=conv.id,
                client_msg_id="cmid_resend",
                role="user",
                content="中断过的问题",
            )
            s.add(um)
            await s.flush()
            am = Message(
                conversation_id=conv.id,
                client_msg_id="ai_cmid_resend",
                role="assistant",
                content="半截回答",
                envelope={
                    "msg_id": str(uuid.uuid4()),
                    "role": "assistant",
                    "blocks": [{"type": "markdown", "content": "半截回答"}],
                    "meta": {"interrupted": True},
                },
                parent_id=um.id,
            )
            s.add(am)
            await s.commit()
            conv_id, old_am_id = str(conv.id), str(am.id)

        _FakeSkill.text = "重发完整回答"
        ps = _patch_chat()
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = await client.post(
                "/api/agent/chat",
                json=_chat_body("cmid_resend", conversation_id=conv_id, message="中断过的问题"),
                headers=_headers(token),
            )
        assert resp.status_code == 200
        assert "error" not in _sse_events(resp.text)  # 不再 40901
        tokens = "".join(d["text"] for d in _sse_datas(resp.text, "token"))
        assert "重发完整回答" in tokens
        done = _sse_data(resp.text, "done")
        assert done["message_id"] != old_am_id

        # 旧 interrupted 版本已 supersede，兄弟组 count=2
        async with _db() as s:
            old_msg = await s.get(Message, uuid.UUID(old_am_id))
            assert old_msg.superseded_at is not None
        items = (await _get_messages(client, token, conv_id))["items"]
        assistant = next(m for m in items if m["role"] == "assistant")
        assert assistant["versions"]["count"] == 2


# ==================== 10. M2.2 tutor 上下文恢复（chat/regenerate/edit 决策层） ====================


class _CaptureSkill:
    """捕获 params 的假 skill（决策参数断言用）"""

    last_params: dict | None = None

    async def run(self, params, ctx):
        _CaptureSkill.last_params = dict(params)
        yield {"type": "token", "data": {"text": "占位回答"}}
        yield {
            "type": "_result_meta",
            "data": {"full_text": "占位回答", "provider": "fake", "usage": {}},
        }


class _CaptureRegistry:
    """记录被请求的 skill_id，统一返回 _CaptureSkill"""

    def __init__(self):
        self.requested: list[str] = []

    def get(self, skill_id):
        self.requested.append(skill_id)
        return _CaptureSkill()


def _patch_capture(registry: _CaptureRegistry):
    return (
        patch.object(ar, "get_skill_registry", return_value=registry),
        patch.object(ar, "get_model_router_for_user", side_effect=_fake_get_model_router),
        patch.object(ar, "_bg_episodic_extract", side_effect=_noop_bg),
        patch.object(ar, "_bg_summary", side_effect=_noop_bg),
    )


class TestTutorContextDecision:
    """用户实测 bug 的决策层回归：动作消息/重新生成绝不掉出 socratic 状态机"""

    async def test_chat_action_without_active_session_stays_socratic(self, auth_client):
        """无 active tutor_session（会话刚结束）时发 tutor_action：
        决策仍锁 socratic_solver（此前掉意图路由成 chat → 模型幻觉自编题目）"""
        client, token, _ = auth_client
        registry = _CaptureRegistry()
        ps = _patch_capture(registry)
        with patch.object(ar, "_find_active_tutor_session", return_value=None):  # noqa: SIM117
            with ps[0], ps[1], ps[2], ps[3]:
                resp = await client.post(
                    "/api/agent/chat",
                    json=_chat_body(
                        str(uuid.uuid4()),
                        message="直接看答案",
                        context={
                            "client_msg_id": str(uuid.uuid4()),
                            "workspace": "student",
                            "tutor_action": "answer",
                        },
                    ),
                    headers=_headers(token),
                )
        assert resp.status_code == 200
        assert registry.requested == ["socratic_solver"]
        assert _CaptureSkill.last_params["tutor_action"] == "answer"

    async def _seed_socratic_messages(
        self, user_id: str, user_content: str, extra: dict | None, skill_id: str
    ) -> tuple[str, str]:
        """直接落库一对 user/assistant 消息（committed，端点可读），返回 (conv_id, asst_id)"""
        async with _test_session_factory() as s:
            conv = Conversation(
                user_id=uuid.UUID(user_id), active_role="student", title="已命名"
            )
            s.add(conv)
            await s.flush()
            user_msg = Message(
                conversation_id=conv.id,
                client_msg_id=f"u_{uuid.uuid4().hex[:12]}",
                role="user",
                content=user_content,
                envelope={"msg_id": str(uuid.uuid4()), "role": "user", "blocks": []},
                skill_id="chat",
                parent_id=None,
            )
            s.add(user_msg)
            await s.flush()
            meta = {"extra": extra} if extra is not None else {}
            asst = Message(
                conversation_id=conv.id,
                client_msg_id=f"a_{uuid.uuid4().hex[:12]}",
                role="assistant",
                content="完整解答…",
                envelope={
                    "msg_id": str(uuid.uuid4()),
                    "role": "assistant",
                    "blocks": [],
                    "meta": meta,
                },
                skill_id=skill_id,
                parent_id=user_msg.id,
            )
            s.add(asst)
            await s.commit()
            return str(conv.id), str(asst.id)

    async def test_regenerate_recovers_socratic_context(self, auth_client):
        """regenerate socratic 消息：tutor_action + tutor_session_id + _regenerate
        全部从原消息 envelope.meta.extra 恢复并透传给技能"""
        client, token, user_id = auth_client
        session_id = str(uuid.uuid4())
        conv_id, asst_id = await self._seed_socratic_messages(
            user_id,
            "确认查看完整解答",
            {"session_id": session_id, "tutor_action": "answer_confirm"},
            "socratic_solver",
        )

        registry = _CaptureRegistry()
        ps = _patch_capture(registry)
        with ps[0], ps[1], ps[2], ps[3]:
            resp = await client.post(
                "/api/agent/chat/regenerate",
                json={"conversation_id": conv_id, "message_id": asst_id},
                headers=_headers(token),
            )
        assert resp.status_code == 200
        assert "done" in _sse_events(resp.text)
        assert registry.requested == ["socratic_solver"]
        params = _CaptureSkill.last_params
        assert params["tutor_action"] == "answer_confirm"
        assert params["tutor_session_id"] == session_id
        assert params["_regenerate"] is True

    async def test_regenerate_infers_action_from_legacy_content(self, auth_client):
        """迭代10 前数据（extra 无 tutor_action）：按用户消息固定话术推断动作"""
        client, token, user_id = auth_client
        session_id = str(uuid.uuid4())
        conv_id, asst_id = await self._seed_socratic_messages(
            user_id, "来点提示", {"session_id": session_id}, "socratic_solver"
        )

        registry = _CaptureRegistry()
        ps = _patch_capture(registry)
        with ps[0], ps[1], ps[2], ps[3]:
            resp = await client.post(
                "/api/agent/chat/regenerate",
                json={"conversation_id": conv_id, "message_id": asst_id},
                headers=_headers(token),
            )
        assert resp.status_code == 200
        params = _CaptureSkill.last_params
        assert params["tutor_action"] == "hint"
        assert params["tutor_session_id"] == session_id

    async def test_regenerate_chat_hallucination_corrected_to_socratic(self, auth_client):
        """原消息是动作话术但当时掉路由成了 chat（幻觉答案）：会话内有引导会话时
        regenerate 纠正回 socratic 状态机"""
        client, token, user_id = auth_client
        conv_id, asst_id = await self._seed_socratic_messages(
            user_id, "直接看答案", None, "chat"
        )

        registry = _CaptureRegistry()
        ps = _patch_capture(registry)
        fake_session = SimpleNamespace(id=uuid.uuid4())
        with patch.object(ar, "_find_latest_tutor_session", return_value=fake_session):  # noqa: SIM117
            with ps[0], ps[1], ps[2], ps[3]:
                resp = await client.post(
                    "/api/agent/chat/regenerate",
                    json={"conversation_id": conv_id, "message_id": asst_id},
                    headers=_headers(token),
                )
        assert resp.status_code == 200
        assert registry.requested == ["socratic_solver"]
        assert _CaptureSkill.last_params["tutor_action"] == "answer"
        assert _CaptureSkill.last_params["_regenerate"] is True

    async def test_edit_sticks_to_active_tutor_session(self, auth_client):
        """active tutor_session 内编辑作答：决策锁 socratic_solver 重判（此前掉意图路由）"""
        client, token, _ = auth_client
        conv_id, _ = await _send_chat(client, token)
        items = (await _get_messages(client, token, conv_id))["items"]
        user_msg = next(m for m in items if m["role"] == "user")

        registry = _CaptureRegistry()
        ps = _patch_capture(registry)
        fake_session = SimpleNamespace(id=uuid.uuid4())
        with patch.object(ar, "_find_active_tutor_session", return_value=fake_session):  # noqa: SIM117
            with ps[0], ps[1], ps[2], ps[3]:
                resp = await client.post(
                    "/api/agent/chat/edit",
                    json={
                        "conversation_id": conv_id,
                        "message_id": user_msg["id"],
                        "message": "改后作答：$x=3$",
                    },
                    headers=_headers(token),
                )
        assert resp.status_code == 200
        assert registry.requested == ["socratic_solver"]
        assert _CaptureSkill.last_params["question"] == "改后作答：$x=3$"
