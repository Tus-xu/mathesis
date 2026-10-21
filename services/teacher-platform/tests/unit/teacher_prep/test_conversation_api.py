"""R17 API 层契约测试：真实 FastAPI 栈 + 假 8000 成员 + fake LLM。

覆盖：200 新建/续备/workspace/actions、401/403/409/404、SSE snapshot→replay
（Last-Event-ID）、无 Stepper/Wizard 字段、allowlist 组件契约。
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

import app.teacher_prep.conversation.router as conv_router
from app.core.deps import get_current_user, get_session
from app.core.security import UserToken
from app.teacher_prep.conversation.orchestrator import ConversationService
from app.teacher_prep.conversation.service_bridge import set_conversation_service_factory
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

from .conftest import CLASS_A, TEACHER
from .tp_context_fixtures import LESSON1, SRC, seed_synthetic_textbook

BASE = "/api/teacher-v3/prep/conversations"


class _MembershipSim:
    def __init__(self) -> None:
        self._members: dict[str, dict] = {}
        self._fail_5xx: set[str] = set()

    def set(self, class_id: str, *, user_id: str = TEACHER, role: str = "teacher", confirmed: bool = True) -> None:
        self._members[class_id] = {"class_id": class_id, "user_id": user_id, "member_role": role, "confirmed": confirmed}

    def remove(self, class_id: str) -> None:
        self._members.pop(class_id, None)

    def set_5xx(self, class_id: str) -> None:
        self._fail_5xx.add(class_id)

    def handler(self, method: str, path: str, token: str, json_body: dict | None):
        if path.startswith("/api/classes/"):
            cid = path.split("/")[-1]
            if cid in self._fail_5xx:
                return 500, {"detail": "boom"}
            member = self._members.get(cid)
            if member is None:
                return 404, {"detail": "member not found"}
            if member.get("confirmed") is False:
                return 403, {"detail": "unconfirmed"}
            return 200, member
        return 404, {"detail": "unknown path"}


class _FakeLlm:
    def __call__(self, prompt: str) -> str:
        if "教师消息" in prompt:
            return json.dumps(
                {
                    "intent_type": "modify",
                    "textbook_id": str(SRC),
                    "section_id": str(LESSON1),
                    "class_id": CLASS_A,
                    "knowledge_points": ["集合", "元素"],
                    "lesson_scope": "第一课时",
                    "lesson_hours": 1,
                    "objectives": [{"text": "理解集合概念", "source_ref": None}],
                    "note": None,
                    "confidence": 0.9,
                    "warnings": [],
                }
            )
        m = re.search(r"context_snapshot_hash：([0-9a-f]{64})", prompt)
        snap_hash = m.group(1) if m else "0" * 64
        return json.dumps(
            {
                "schema_version": "0.1.0",
                "context_snapshot_hash": snap_hash,
                "title": "合成集合 1.1 教学蓝图",
                "objectives": [{"text": "理解集合与元素的概念", "source_ref": None}],
                "prior_knowledge": [],
                "concepts": [],
                "theorems": [],
                "examples": [],
                "inquiries": [],
                "design_intent": [],
                "key_points": [],
                "difficult_points": [],
                "lesson_flow": [{"stage": "introduction", "title": "导入", "items": [], "duration_minutes": 5}],
                "warnings_ack": [],
            }
        )


class _FakeLlmWithoutClass(_FakeLlm):
    """覆盖模型漏填班级时的真实行为：会话路径已带 class_id。"""

    def __call__(self, prompt: str) -> str:
        data = json.loads(super().__call__(prompt))
        if "教师消息" in prompt:
            data["class_id"] = None
        return json.dumps(data)


@pytest.fixture
def api(db, session_factory):
    from main import create_app

    members = _MembershipSim()
    transport = FakeApiTransport()
    transport.handler = members.handler
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())

    app = create_app()
    conv_router._adapter = lambda: adapter  # type: ignore[assignment]
    # session_factory：受理制后台任务（D4）的短事务走测试库（不触碰生产 DB）
    set_conversation_service_factory(
        lambda: ConversationService(llm_generate=_FakeLlm(), session_factory=session_factory)
    )

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


async def _seed_and_create(api, db):
    await seed_synthetic_textbook(db)
    members = api[1]
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(api[0]) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "title": "集合备课", "idempotency_key": "api-key-0001"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]


async def test_api_new_conversation_200(api, db):
    data = await _seed_and_create(api, db)
    assert data["session"]["status"] == "active"
    assert data["artifact"]["context"] is not None
    assert data["job_summary"][0]["status"] == "PENDING"
    # Conversation-first：无强制步骤条
    assert "steps" not in data and "wizard" not in data and "current_step" not in data
    # timeline 组件全部来自 allowlist
    from app.teacher_prep.conversation.catalog import ALLOWED_COMPONENT_TYPES

    for m in data["timeline"]:
        if m["component"]:
            assert m["component"]["type"] in ALLOWED_COMPONENT_TYPES
    # 动态业务文案来自授权数据（教材名来自切片）；稳定 UI 文案走 copy_key
    ctx_card = next(m for m in data["timeline"] if m["kind"] == "context")
    assert ctx_card["component"]["props"]["source_title"].startswith("合成出版社")
    # 动作卡（confirm/reject）的稳定 UI 文案走 copy_key，不散落业务硬编码
    bp_card = next((m for m in data["timeline"] if m["kind"] == "proposal"), None)
    if bp_card is not None:
        assert bp_card["component"]["props"]["copy_keys"]["confirm"].startswith("prep.")


async def test_api_401_unauthenticated(api, db):
    from main import create_app

    app = create_app()
    async with _client(app) as c:
        r = await c.get(BASE)
        assert r.status_code == 401


async def test_api_403_cross_class(api, db):
    await seed_synthetic_textbook(db)
    members = api[1]
    members.set(CLASS_A, user_id=TEACHER)
    members.remove(CLASS_A)  # 当前用户无该班成员关系 → 40301（8000 404 → ContractMembershipDenied）
    async with _client(api[0]) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "api-key-0002"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 403 and r.json()["code"] == 40301


async def test_api_503_authority_down(api, db):
    await seed_synthetic_textbook(db)
    members = api[1]
    members.set(CLASS_A, user_id=TEACHER)
    members.set_5xx(CLASS_A)
    async with _client(api[0]) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "api-key-0003"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 503


async def test_api_404_unknown_session(api, db):
    await seed_synthetic_textbook(db)
    members = api[1]
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(api[0]) as c:
        r = await c.get(
            f"{BASE}/00000000-0000-0000-0000-000000000000",
            headers=_auth(),
        )
        assert r.status_code == 404


async def test_api_send_message_and_conflict(api, db):
    data = await _seed_and_create(api, db)
    sid = data["session"]["session_id"]
    rev = data["session"]["revision"]
    async with _client(api[0]) as c:
        r = await c.post(
            f"{BASE}/{sid}/messages",
            json={"session_id": sid, "expected_revision": rev, "message": "接着讲表示法", "idempotency_key": "api-key-0004"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        # 陈旧 revision → 409
        r2 = await c.post(
            f"{BASE}/{sid}/messages",
            json={"session_id": sid, "expected_revision": rev, "message": "用旧版本再发", "idempotency_key": "api-key-0005"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r2.status_code == 409 and r2.json()["code"] == 40900


async def test_api_send_message_uses_path_session_id(api, db):
    """前端按 REST 约定只传路径 session_id，不应被重复 body 字段卡成 422。"""
    data = await _seed_and_create(api, db)
    sid = data["session"]["session_id"]
    async with _client(api[0]) as c:
        r = await c.post(
            f"{BASE}/{sid}/messages",
            json={
                "expected_revision": data["session"]["revision"],
                "message": "继续补充课堂练习",
                "idempotency_key": "api-key-path-session",
            },
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text


async def test_api_session_class_is_kept_when_intent_omits_class(api, db):
    """首页已选班级时，模型漏填 class_id 也不能再次要求老师选班。"""
    await seed_synthetic_textbook(db)
    members = api[1]
    members.set(CLASS_A, user_id=TEACHER)
    set_conversation_service_factory(lambda: ConversationService(llm_generate=_FakeLlmWithoutClass()))
    async with _client(api[0]) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "api-key-class-fallback"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        ws = r.json()["data"]
        selection_cards = [m for m in ws["timeline"] if m["component"] and m["component"]["type"] == "selection_card"]
        assert ws["artifact"]["context"] is not None
        assert all(card["component"]["props"]["kind"] != "class" for card in selection_cards)


async def test_api_actions_confirm(api, db, session_factory):
    data = await _seed_and_create(api, db)
    sid = data["session"]["session_id"]
    async with _client(api[0]) as c:
        # 模拟 worker 完成提案（经 service 层 handler；用测试库的 session_factory）
        await _process_jobs(session_factory, _FakeLlm())
        # 读回 workspace → confirm
        r = await c.get(f"{BASE}/{sid}", headers=_auth())
        assert r.status_code == 200
        ws = r.json()["data"]
        assert ws["artifact"]["blueprint"] is not None and ws["artifact"]["blueprint"]["status"] == "proposal"
        bp_card = next(m for m in ws["timeline"] if m["kind"] == "proposal")
        r2 = await c.post(
            f"{BASE}/{sid}/actions",
            json={
                "session_id": sid,
                "action": "confirm_proposal",
                "target": bp_card["component"]["props"]["blueprint_id"],
                "expected_revision": ws["session"]["revision"],
                "idempotency_key": "api-key-0006",
                "payload": {"note": "确认"},
            },
            headers=_auth(),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["data"]["artifact"]["blueprint"]["status"] == "confirmed"


async def _process_jobs(factory, fake):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.reliability.jobs import JobEngine, fenced_run
    from app.reliability.models import ReliabilityJob
    from app.teacher_prep.conversation.handler import conversation_run_handler

    handler = await conversation_run_handler(factory, fake, lease_s=5.0)
    async with factory() as s:
        pending = (
            (await s.execute(select(ReliabilityJob).where(ReliabilityJob.status == "PENDING"))).scalars().all()
        )
        for j in pending:
            j.available_at = datetime.now(UTC) - timedelta(seconds=5)
        await s.commit()
        jobs = await JobEngine.claim(s, owner_service="teacher_prep", worker_name="api-test", limit=10)
        await s.commit()
        for job in jobs:
            await fenced_run(s, job_id=job.id, claim_token=job.claim_token or "", handler=handler)


async def test_api_sse_snapshot_and_replay_last_event_id(api, db):
    """SSE 帧契约：retry → snapshot → replay(带 id=seq)；Last-Event-ID 断点续传。

    注：仓库约定以函数级验证 SSE 契约（R11.5-C test_sse_contract 同法）；
    sse_starlette + ASGITransport 全流消费会挂起，故直接迭代响应 body_iterator。
    """
    from starlette.requests import Request

    from app.core.security import UserToken
    from app.teacher_prep.conversation.router import conversation_events

    data = await _seed_and_create(api, db)
    sid = data["session"]["session_id"]

    async def _collect(last_event_id: str | None = None) -> list[dict]:
        async def _receive():
            return {"type": "http.disconnect"}

        headers = [(b"authorization", b"Bearer jwt")]
        if last_event_id:
            headers.append((b"last-event-id", last_event_id.encode()))
        request = Request(
            scope={
                "type": "http",
                "method": "GET",
                "path": f"/api/teacher-v3/prep/conversations/{sid}/events",
                "headers": headers,
                "query_string": b"",
            },
            receive=_receive,
        )
        user = UserToken(user_id=TEACHER, name="张老师", role="teacher", school_id=None)
        resp = await conversation_events(sid, request, user=user, session=db)
        frames: list[dict] = []
        async for f in resp.body_iterator:
            frames.append(f)
        return frames  # 帧字典序列（retry/snapshot/event）

    frames = await _collect()
    assert frames[0] == {"retry": 3000}
    assert frames[1]["event"] == "snapshot"
    replay = [f for f in frames if f.get("event") == "event"]
    assert replay, "应有 durable 事件重放"
    replay_ids = [int(f.get("id", 0)) for f in replay]
    assert replay_ids == sorted(replay_ids) and replay_ids[0] >= 1
    event_types = [json.loads(f["data"])["event_type"] for f in replay]
    assert "command.applied" in event_types
    assert any("conversation" in e for e in event_types)
    # snapshot data 是 workspace（无步骤条）
    snap_data = json.loads(frames[1]["data"])
    assert "steps" not in snap_data and "session" in snap_data
    # Last-Event-ID 断点续传：只回放该 seq 之后的事件
    last = replay_ids[-1]
    after = [f for f in await _collect(last_event_id=str(last)) if f.get("event") == "event"]
    after_ids = [int(f.get("id", 0)) for f in after]
    assert all(x > last for x in after_ids) or after_ids == []


async def test_api_unknown_action_422(api, db):
    data = await _seed_and_create(api, db)
    sid = data["session"]["session_id"]
    async with _client(api[0]) as c:
        r = await c.post(
            f"{BASE}/{sid}/actions",
            json={
                "session_id": sid,
                "action": "evil_action",
                "expected_revision": data["session"]["revision"],
                "idempotency_key": "api-key-0007",
            },
            headers=_auth(),
        )
        assert r.status_code == 422
