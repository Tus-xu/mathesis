"""W1 决策 D4 单测：会话消息异步受理（202）+ SSE 进度事件契约。

覆盖：
- 受理立即返回（LLM 慢时 POST 不等待，<2s）+ 受理字段（conversation_id/message_id/status/revision）
  且既有 workspace 字段不破坏；
- wait=true 兼容（既有 test_conversation_api 同步形状覆盖）；
- 事件序列（durable，tp_session_events 可重放）：message.accepted → ai.stage(analyzing
  → locate_textbook → blueprint_proposing → drafting → done) → assistant.message.completed
  （含 blueprint 引用）→ conversation.updated；
- 幂等：同 idempotency_key 重复提交重放同一受理结果（不重复落消息、不重复 spawn 处理）；
- 错误事件：意外异常 → error（error_key + 产品化文案，无堆栈）；意图解析失败 →
  basic mode 卡 completed + error；
- 并发串行化：处理中再发 → 409（产品化文案）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid as uuid_mod

import httpx
import pytest
from sqlalchemy import select

import app.teacher_prep.conversation.router as conv_router
from app.core.deps import get_current_user, get_session
from app.core.security import UserToken
from app.teacher_prep.conversation import background as bg
from app.teacher_prep.conversation.orchestrator import ConversationService
from app.teacher_prep.conversation.service_bridge import set_conversation_service_factory
from app.teacher_prep.models import SessionEventRow
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

from .conftest import CLASS_A, TEACHER, MembershipSim
from .tp_context_fixtures import LESSON1, SRC, seed_synthetic_textbook

BASE = "/api/teacher-v3/prep/conversations"


class _FakeLlm:
    """意图 + 蓝图双 prompt 的确定性假 LLM；可注入延迟/失败。

    `entered`：进入（慢）LLM 调用即置位——测试据此确认后台任务已停在
    LLM 等待上（不持任何 DB 事务），再做并发/落库断言（消除 SQLite 文件锁竞争）。
    """

    def __init__(self, *, delay: float = 0.0, intent_raw: str | None = None) -> None:
        self.delay = delay
        self.intent_raw = intent_raw
        self.entered = asyncio.Event()

    async def __call__(self, prompt: str) -> str:
        self.entered.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.intent_raw is not None and "教师消息" in prompt:
            return self.intent_raw
        if "教师消息" in prompt:
            return json.dumps(
                {
                    "intent_type": "modify",
                    "textbook_id": str(SRC),
                    "section_id": str(LESSON1),
                    "class_id": CLASS_A,
                    "knowledge_points": ["集合"],
                    "lesson_scope": "第一课时",
                    "lesson_hours": 1,
                    "objectives": [{"text": "理解集合概念", "source_ref": None}],
                    "note": None,
                    "confidence": 0.9,
                    "warnings": [],
                }
            )
        import re as _re

        m = _re.search(r"context_snapshot_hash：([0-9a-f]{64})", prompt)
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


class _BoomSendService(ConversationService):
    """send_message 必炸：验证后台任务异常 → 产品化 error 事件（进程不崩）。"""

    async def send_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("boom-db-down")


@pytest.fixture
def db_factory(file_db):
    """file_db() 返回 async_sessionmaker；解包一层 → 直接可调用的 sessionmaker（NullPool 独立连接）。"""
    return file_db()


@pytest.fixture
def api_factory(db_factory):
    """可注入 LLM / 服务实现的 API 装配。

    用 file_db（文件 SQLite + NullPool，每 session 独立连接）：受理后台任务与
    请求 session 并发共享同一持久库（StaticPool 单连接无法承载并发）。
    """

    def _make(llm=None, service_factory=None):
        from main import create_app

        members = MembershipSim()
        transport = FakeApiTransport()
        transport.handler = members.handler
        adapter = Api8000Adapter(transport, InMemoryMappingRegistry())

        app = create_app()
        conv_router._adapter = lambda: adapter  # type: ignore[assignment]
        set_conversation_service_factory(
            service_factory or (lambda: ConversationService(llm_generate=llm or _FakeLlm(), session_factory=db_factory))
        )

        async def _override_db():
            async with db_factory() as s:
                yield s

        def _override_user():
            return UserToken(user_id=TEACHER, name="张老师", role="teacher", school_id=None)

        app.dependency_overrides[get_session] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        return app, members

    return _make


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _auth():
    return {"Authorization": "Bearer jwt-teacher-token"}


async def _seed(db_factory) -> None:
    async with db_factory() as s:
        await seed_synthetic_textbook(s)
        await s.commit()


async def _process_jobs(factory, llm):
    """模拟 worker：claim 全部 PENDING 作业并 fenced_run（同 test_conversation_api 法）。"""
    from datetime import UTC, datetime, timedelta

    from app.reliability.jobs import JobEngine, fenced_run
    from app.reliability.models import ReliabilityJob
    from app.teacher_prep.conversation.handler import conversation_run_handler

    handler = await conversation_run_handler(factory, llm, lease_s=5.0)
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


async def _events(make_session, session_id: str) -> list[SessionEventRow]:
    async with make_session() as s:
        rows = (
            (
                await s.execute(
                    select(SessionEventRow)
                    .where(SessionEventRow.session_id == uuid_mod.UUID(session_id))
                    .order_by(SessionEventRow.seq)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


# ── 受理立即返回（R6.2：首响 ≤ 数秒）─────────────────────────────────────────


async def test_accept_returns_202_without_waiting_llm(api_factory, db_factory):
    """LLM 需 2s 时，受理响应必须立即返回（<1.5s），消息已落库且状态=processing。"""
    app, members = api_factory()
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        t0 = time.monotonic()
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "title": "集合备课", "idempotency_key": "async-key-0001"},
            headers=_auth(),
        )
        elapsed = time.monotonic() - t0
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    # 受理字段
    assert data["status"] == "processing"
    assert data["conversation_id"] == data["session"]["session_id"]
    assert data["message_id"]
    assert data["revision"] == data["session"]["revision"]
    # 既有 workspace 字段不破坏
    for key in ("session", "timeline", "artifact", "evidence", "available_actions", "job_summary", "basic_mode"):
        assert key in data, f"受理响应缺失既有字段 {key}"
    # timeline 已含刚落库的用户消息（立即可见）
    assert any(m["kind"] == "text" and m["role"] == "user" for m in data["timeline"])
    # 未等待 LLM（2s）即返回
    assert elapsed < 1.5, f"受理响应耗时 {elapsed:.2f}s，不应等待 LLM"
    await bg.drain_background_tasks()


async def test_accept_persists_message_and_accepted_event(api_factory, db_factory):
    """受理即落库：tp_conversation_messages 有 accepted 用户消息 + message.accepted durable 事件。"""
    llm = _FakeLlm(delay=1.5)
    app, members = api_factory(llm)
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0002"},
            headers=_auth(),
        )
        assert r.status_code == 202, r.text
        data = r.json()["data"]
    # LLM 在途（1.5s）：等任务真正停在 LLM 等待上（无 DB 事务）再断言受理即落库
    llm.entered.set() if llm.entered.is_set() else await llm.entered.wait()
    from app.teacher_prep.conversation.models import ConversationMessageRow

    async with db_factory() as s:
        msg = (
            await s.execute(
                select(ConversationMessageRow).where(ConversationMessageRow.id == uuid_mod.UUID(data["message_id"]))
            )
        ).scalar_one()
        assert msg.status == "accepted" and msg.role == "user" and msg.text == "备1.1集合"
        ev = (
            (
                await s.execute(
                    select(SessionEventRow)
                    .where(
                        SessionEventRow.session_id == uuid_mod.UUID(data["conversation_id"]),
                        SessionEventRow.event_type == "message.accepted",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(ev) == 1 and ev[0].payload["message_id"] == data["message_id"]
    await bg.drain_background_tasks()


# ── wait=true 兼容（旧 FE / 回归）────────────────────────────────────────────


async def test_wait_true_keeps_sync_shape(api_factory, db_factory):
    """?wait=true：200 + 同步版 workspace 形状（等待 LLM 完成）。"""
    app, members = api_factory(_FakeLlm())
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0003"},
            params={"wait": "true"},
            headers=_auth(),
        )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["session"]["status"] == "active"
    assert data["job_summary"][0]["status"] == "PENDING"
    assert "conversation_id" not in data and "status" not in data  # 旧形状无受理字段


# ── 事件序列（durable 可重放）────────────────────────────────────────────────


async def test_event_sequence_reaches_completed(api_factory, db_factory):
    """受理 → 后台解析 → 蓝图入队 → worker 完成：完整事件序列 + 终态事件载荷。"""
    app, members = api_factory(_FakeLlm())
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0004"},
            headers=_auth(),
        )
        assert r.status_code == 202, r.text
        sid = r.json()["data"]["conversation_id"]
    await bg.drain_background_tasks()
    await _process_jobs(db_factory, _FakeLlm())

    rows = await _events(db_factory, sid)
    types = [e.event_type for e in rows]
    seqs = [e.seq for e in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # 全序递增（replay 依据）

    # 阶段序列：analyzing → locate_textbook → blueprint_proposing → drafting → done
    stages = [e.payload["stage"] for e in rows if e.event_type == "ai.stage"]
    assert stages == ["analyzing", "locate_textbook", "blueprint_proposing", "drafting", "done"]
    assert all(e.payload.get("stage_label") for e in rows if e.event_type == "ai.stage")

    # 受理与终态
    assert "message.accepted" in types
    assert types.count("assistant.message.completed") == 1
    assert "conversation.updated" in types
    assert "error" not in types
    # 受理制不重复发 message.created（用户消息以 message.accepted 发布）
    assert "conversation.message.created" not in types

    completed = next(e for e in rows if e.event_type == "assistant.message.completed")
    primary = completed.payload["messages"][0]
    assert primary["kind"] == "proposal"
    assert primary["component"]["type"] == "blueprint_card"
    assert completed.payload["blueprint_id"] == primary["component"]["props"]["blueprint_id"]
    assert completed.payload["evidence"]["kind"] == "evidence"

    updated = [e for e in rows if e.event_type == "conversation.updated"]
    assert updated[-1].payload["revision"] >= 2
    assert updated[-1].payload["status"] in ("draft", "active")


async def test_sse_replay_after_disconnect_delivers_terminal_events(api_factory, db_factory):
    """断线重连（Last-Event-ID）：accept 后立刻重连 → replay 补齐 message.accepted 等错过事件。"""
    from starlette.requests import Request

    app, members = api_factory(_FakeLlm(delay=0.8))
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0005"},
            headers=_auth(),
        )
        assert r.status_code == 202, r.text
        sid = r.json()["data"]["conversation_id"]

    async def _replay_after(after_seq: int) -> list[dict]:
        async def _receive():
            return {"type": "http.disconnect"}

        request = Request(
            scope={
                "type": "http",
                "method": "GET",
                "path": f"{BASE}/{sid}/events",
                "headers": [(b"authorization", b"Bearer jwt")],
                "query_string": f"lastEventId={after_seq}".encode(),
            },
            receive=_receive,
        )
        user = UserToken(user_id=TEACHER, name="张老师", role="teacher", school_id=None)
        resp = await conv_router.conversation_events(
            sid, request, user=user, session=db_factory()  # type: ignore[arg-type]
        )
        out: list[dict] = []
        async for f in resp.body_iterator:
            if f.get("event") == "event":
                out.append(json.loads(f["data"]))
        return out

    # 模拟客户端在受理前已连过（Last-Event-ID=0 之前的旧 seq 不存在）→ 重连补齐
    frames = await _replay_after(0)
    event_types = [f["event_type"] for f in frames]
    assert "message.accepted" in event_types
    assert "conversation.updated" in event_types
    await bg.drain_background_tasks()


# ── 幂等 ─────────────────────────────────────────────────────────────────────


async def test_idempotent_accept_replays_same_receipt(api_factory, db_factory):
    """同 idempotency_key 重复提交：同一受理结果（不重复落消息），且不重复处理。"""
    app, members = api_factory(_FakeLlm())
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        payload = {"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0006"}
        r1 = await c.post(BASE, json=payload, headers=_auth())
        r2 = await c.post(BASE, json=payload, headers=_auth())
    assert r1.status_code == 202 and r2.status_code == 202
    d1, d2 = r1.json()["data"], r2.json()["data"]
    assert d2["message_id"] == d1["message_id"]
    assert d2["conversation_id"] == d1["conversation_id"]
    assert d2["revision"] == d1["revision"]

    await bg.drain_background_tasks()
    await _process_jobs(db_factory, _FakeLlm())
    rows = await _events(db_factory, d1["conversation_id"])
    assert [e.event_type for e in rows].count("message.accepted") == 1
    assert [e.event_type for e in rows].count("assistant.message.completed") == 1


# ── 错误事件 ─────────────────────────────────────────────────────────────────


async def test_processing_crash_emits_productized_error(api_factory, db_factory):
    """后台意外异常：error 事件（error_key + 产品化文案，无堆栈）；受理消息落 failed。"""
    app, members = api_factory(service_factory=lambda: _BoomSendService(llm_generate=_FakeLlm(), session_factory=db_factory))
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0007"},
            headers=_auth(),
        )
        assert r.status_code == 202, r.text
        data = r.json()["data"]
    await bg.drain_background_tasks()

    rows = await _events(db_factory, data["conversation_id"])
    errors = [e for e in rows if e.event_type == "error"]
    assert errors, "后台异常必须发布 error 事件"
    payload = errors[0].payload
    assert payload["error_key"] == "prep.error.processing_failed"
    assert payload["message"] == bg.PROCESSING_FAILED_MESSAGE
    assert payload["retryable"] is True
    assert "Traceback" not in json.dumps(payload, ensure_ascii=False)
    assert payload["message_id"] == data["message_id"]

    from app.teacher_prep.conversation.models import ConversationMessageRow

    async with db_factory() as s:
        msg = (
            await s.execute(
                select(ConversationMessageRow).where(ConversationMessageRow.id == uuid_mod.UUID(data["message_id"]))
            )
        ).scalar_one()
        assert msg.status == "failed"


async def test_intent_failure_basic_mode_completed_and_error_events(api_factory, db_factory):
    """意图解析失败：basic mode 卡（最终 assistant 消息）completed + error（intent_invalid）。"""
    app, members = api_factory(_FakeLlm(intent_raw="这不是JSON"))
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0008"},
            headers=_auth(),
        )
        assert r.status_code == 202, r.text
        sid = r.json()["data"]["conversation_id"]
    await bg.drain_background_tasks()

    rows = await _events(db_factory, sid)
    types = [e.event_type for e in rows]
    completed = [e for e in rows if e.event_type == "assistant.message.completed"]
    assert completed and completed[0].payload["messages"][0]["kind"] == "basic_mode"
    errors = [e for e in rows if e.event_type == "error"]
    assert errors and errors[0].payload["error_key"] == "prep.error.intent_invalid"
    assert "conversation.updated" in types
    # 失败态不发 done（done 只属于成功完成）
    assert "done" not in [e.payload.get("stage") for e in rows if e.event_type == "ai.stage"]


# ── 并发串行化 ───────────────────────────────────────────────────────────────


async def test_second_message_409_while_processing(api_factory, db_factory):
    """上一条处理中：新消息 409（明确文案）；处理完成并跑完 worker 后可再发。"""
    llm = _FakeLlm(delay=2.0)
    app, members = api_factory(llm)
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r1 = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "idempotency_key": "async-key-0009"},
            headers=_auth(),
        )
        assert r1.status_code == 202, r1.text
        rev = r1.json()["data"]["revision"]
        sid = r1.json()["data"]["conversation_id"]
        # 任务已停在 LLM 等待（不持 DB 事务）→ 409 判定确定性（不与进度写竞争文件锁）
        await llm.entered.wait()

        r2 = await c.post(
            f"{BASE}/{sid}/messages",
            json={"expected_revision": rev, "message": "第二条", "idempotency_key": "async-key-0010"},
            headers=_auth(),
        )
        assert r2.status_code == 409
        assert r2.json()["code"] == 40900
        assert "仍在处理中" in r2.json()["message"]

    await bg.drain_background_tasks()
    await _process_jobs(db_factory, _FakeLlm())

    async with _client(app) as c:
        from app.teacher_prep.models import TeachingSessionRow

        async with db_factory() as s:
            srow = (
                await s.execute(
                    select(TeachingSessionRow).where(TeachingSessionRow.id == uuid_mod.UUID(sid))
                )
            ).scalar_one()
            rev2 = srow.revision
        r3 = await c.post(
            f"{BASE}/{sid}/messages",
            json={"expected_revision": rev2, "message": "继续", "idempotency_key": "async-key-0011"},
            headers=_auth(),
        )
        assert r3.status_code == 202, r3.text
    await bg.drain_background_tasks()
