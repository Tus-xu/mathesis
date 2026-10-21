"""R3 课堂设计页节点能力单测：教师在线编辑 / AI 定向修改 / 历史版本 / 教材查看。

覆盖（ACCEPTANCE-MATRIX 3.3~3.7 的后端契约）：
- PATCH node：真实落库（新 revision，source=teacher，edited_by 落节点体）、幂等重放、
  409 revision 冲突、时长预算 Σ>45 → 422 time_budget_exceeded（data 带超额分钟数）；
- POST /messages target_node_id：只重写 target 节点（其它节点逐字节不动）、
  node_revision_card 落 timeline、blueprint.node.updated durable 事件、
  失败 → 产品化 error 事件（time_budget_exceeded / node_revision_invalid）；
- GET revisions：不可变快照列表（时间/来源/verdict/body）；
- GET textbook/view：授权切片原文（chunk 全文 + section 聚合 + fail closed）。
"""

from __future__ import annotations

import json
import uuid as uuid_mod

import httpx
import pytest
from sqlalchemy import select

import app.teacher_prep.conversation.router as conv_router
from app.core.deps import get_current_user, get_session
from app.core.security import UserToken
from app.teacher_prep.conversation.orchestrator import ConversationService
from app.teacher_prep.conversation.service_bridge import set_conversation_service_factory
from app.teacher_prep.models import BlueprintRevisionRow, SessionEventRow
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

from .conftest import CLASS_A, TEACHER, MembershipSim
from .tp_context_fixtures import LESSON1, SRC, seed_synthetic_textbook

BASE = "/api/teacher-v3/prep/conversations"

_NODE_JSON = {
    "title": "情境导入与轨迹感知",
    "duration_minutes": 6,
    "summary": "从生活现象引入，动点画椭圆",
    "teacher_questions": [
        {"text": "动点 $P$ 满足什么条件时，$|PF_1| + |PF_2|$ 的值保持不变？"},
        {"text": "这样得到的轨迹是什么图形？它与圆有什么不同？"},
    ],
    "student_activities": [
        {"text": "在几何画板中拖动点 $P$，观察 $|PF_1|+|PF_2|$ 是否变化，并尝试描述动点的轨迹。"}
    ],
    "board_notes": [
        {"text": "椭圆的定义：平面内与两个定点 $F_1$、$F_2$ 的距离之和等于常数 $2a$（$2a > |F_1F_2|$）的点的轨迹。"},
        {"text": "数学表达式：$|PF_1| + |PF_2| = 2a$（$2a > |F_1F_2|$）"},
    ],
    "hints": [{"text": "可借助几何画板动态演示，帮助学生建立直观认识。"}],
}

_BLUEPRINT_FLOW = [
    {"stage": "introduction", "title": "情境导入", "items": [{"text": "从生活现象引入"}], "duration_minutes": 5},
    {"stage": "new_content", "title": "椭圆的定义", "items": [{"text": "给出定义"}], "duration_minutes": 10},
]


class _NodeLlm:
    """意图 / 蓝图 / 节点修订三 prompt 的确定性假 LLM（按 prompt 锚点分发）。"""

    def __init__(self, node_raw: str | None = None, flow: list[dict] | None = None) -> None:
        self.node_raw = node_raw
        self.flow = flow or _BLUEPRINT_FLOW
        self.node_prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        if "【待修改环节】" in prompt:
            self.node_prompts.append(prompt)
            return self.node_raw if self.node_raw is not None else json.dumps(_NODE_JSON, ensure_ascii=False)
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
                "lesson_flow": self.flow,
                "warnings_ack": [],
            },
            ensure_ascii=False,
        )


@pytest.fixture
def db_factory(file_db):
    return file_db()


@pytest.fixture
def api_factory(db_factory):
    def _make(llm=None):
        from main import create_app

        members = MembershipSim()
        transport = FakeApiTransport()
        transport.handler = members.handler
        adapter = Api8000Adapter(transport, InMemoryMappingRegistry())

        app = create_app()
        conv_router._adapter = lambda: adapter  # type: ignore[assignment]
        set_conversation_service_factory(
            lambda: ConversationService(llm_generate=llm or _NodeLlm(), session_factory=db_factory)
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


async def _process_jobs(factory, llm) -> None:
    """模拟 worker：claim 全部 PENDING 作业并 fenced_run（同既有会话测试法）。"""
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
        jobs = await JobEngine.claim(s, owner_service="teacher_prep", worker_name="node-ops-test", limit=10)
        await s.commit()
        for job in jobs:
            await fenced_run(s, job_id=job.id, claim_token=job.claim_token or "", handler=handler)


async def _confirmed_session(api_factory, db_factory, llm) -> str:
    """真实链路造数：新建（同步）→ worker 投影蓝图 → confirm → 返回 session_id。"""
    app, members = api_factory(llm)
    await _seed(db_factory)
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "title": "集合备课", "idempotency_key": "node-key-0001"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        sid = r.json()["data"]["session"]["session_id"]
        await _process_jobs(db_factory, llm)
        r2 = await c.get(f"{BASE}/{sid}", headers=_auth())
        assert r2.status_code == 200, r2.text
        ws = r2.json()["data"]
        bp = ws["artifact"]["blueprint"]
        assert bp is not None and bp["status"] == "proposal"
        r3 = await c.post(
            f"{BASE}/{sid}/actions",
            json={
                "session_id": sid,
                "action": "confirm_proposal",
                "target": bp["blueprint_id"],
                "expected_revision": ws["session"]["revision"],
                "idempotency_key": "node-confirm-0001",
                "payload": {"note": "确认"},
            },
            headers=_auth(),
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["data"]["artifact"]["blueprint"]["status"] == "confirmed"
        return sid


async def _get_ws(app, sid: str) -> dict:
    async with _client(app) as c:
        r = await c.get(f"{BASE}/{sid}", headers=_auth())
        assert r.status_code == 200, r.text
        return r.json()["data"]


def _fresh_app(api_factory, llm=None):
    """测试体自建 app（与 _confirmed_session 的 DB 共享；成员模拟需重新置位）。"""
    app, members = api_factory(llm)
    members.set(CLASS_A, user_id=TEACHER)
    return app


async def _events(db_factory, sid: str) -> list[SessionEventRow]:
    async with db_factory() as s:
        rows = (
            (
                await s.execute(
                    select(SessionEventRow)
                    .where(SessionEventRow.session_id == uuid_mod.UUID(sid))
                    .order_by(SessionEventRow.seq)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def _revisions_of_session(db_factory, sid: str) -> list[BlueprintRevisionRow]:
    from app.teacher_prep.models import TeachingBlueprintRow

    async with db_factory() as s:
        bp = (
            (
                await s.execute(
                    select(TeachingBlueprintRow)
                    .where(TeachingBlueprintRow.session_id == uuid_mod.UUID(sid))
                    .order_by(TeachingBlueprintRow.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        assert bp is not None
        rows = (
            (
                await s.execute(
                    select(BlueprintRevisionRow)
                    .where(BlueprintRevisionRow.blueprint_id == bp.id)
                    .order_by(BlueprintRevisionRow.revision_no)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


def _flow_of(ws: dict) -> list[dict]:
    return ws["artifact"]["blueprint"]["body"]["lesson_flow"]


# ── 3.3 教师在线编辑 ─────────────────────────────────────────────────────────


async def test_edit_node_persists_teacher_revision(api_factory, db_factory):
    """PATCH node → 落库 + 新 revision(source=teacher, edited_by=teacher) + 重进还原。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    bp_rev_before = ws0["artifact"]["blueprint"]["revision"]

    async with _client(app) as c:
        r = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-0001",
                "expected_revision": ws0["session"]["revision"],
                "node_index": 1,
                "title": "椭圆的定义（教师定稿）",
                "duration_minutes": 8,
                "teacher_questions": [{"text": "动点 $P$ 满足什么条件时 $|PF_1|+|PF_2|$ 不变？"}],
                "student_activities": [{"text": "小组讨论并汇报"}],
                "board_notes": [{"text": "$|PF_1| + |PF_2| = 2a$"}],
            },
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        ws = r.json()["data"]

    flow = _flow_of(ws)
    node = flow[1]
    assert node["title"] == "椭圆的定义（教师定稿）"
    assert node["duration_minutes"] == 8
    assert node["edited_by"] == "teacher"
    assert node["teacher_questions"][0]["text"].startswith("动点")
    # 其它节点不动
    assert flow[0]["title"] == "情境导入"
    assert ws["artifact"]["blueprint"]["revision"] == bp_rev_before + 1
    assert ws["session"]["revision"] > ws0["session"]["revision"]

    # 重进还原（权威快照）
    ws2 = await _get_ws(app, sid)
    assert _flow_of(ws2)[1]["title"] == "椭圆的定义（教师定稿）"

    # revision 行：source=teacher、note 指明环节
    revs = await _revisions_of_session(db_factory, sid)
    assert revs[-1].source == "teacher"
    assert "环节 2" in (revs[-1].note or "")


async def test_edit_node_idempotent_replay(api_factory, db_factory):
    """同 idempotency_key 同载荷 → 重放首响应，不产生新 revision。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    payload = {
        "idempotency_key": "edit-node-idem-01",
        "expected_revision": ws0["session"]["revision"],
        "node_index": 0,
        "duration_minutes": 4,
    }
    async with _client(app) as c:
        r1 = await c.patch(f"{BASE}/{sid}/blueprint/node", json=payload, headers=_auth())
        assert r1.status_code == 200, r1.text
        rev1 = r1.json()["data"]["artifact"]["blueprint"]["revision"]
        r2 = await c.patch(f"{BASE}/{sid}/blueprint/node", json=payload, headers=_auth())
        assert r2.status_code == 200, r2.text
        rev2 = r2.json()["data"]["artifact"]["blueprint"]["revision"]
    assert rev1 == rev2
    assert len(await _revisions_of_session(db_factory, sid)) == rev1  # 每 revision 一行


async def test_edit_node_conflict_and_bounds(api_factory, db_factory):
    """expected_revision 过期 → 409；node_index 越界 → 422 node_target_invalid。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    async with _client(app) as c:
        r = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-conf-01",
                "expected_revision": ws0["session"]["revision"] - 1,
                "node_index": 0,
                "duration_minutes": 4,
            },
            headers=_auth(),
        )
        assert r.status_code == 409, r.text
        r2 = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-bounds-1",
                "expected_revision": ws0["session"]["revision"],
                "node_index": 99,
                "duration_minutes": 4,
            },
            headers=_auth(),
        )
        assert r2.status_code == 422, r2.text
        body = r2.json()
        assert body["data"]["error_key"] == "prep.error.node_target_invalid"


async def test_edit_node_time_budget_exceeded(api_factory, db_factory):
    """Σ时长 > 45 → 422 time_budget_exceeded，data 带超额分钟数。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    async with _client(app) as c:
        # 蓝图提案落库前已时长归一化（Σ=45：node0=15 + node1=30）。
        # node0 → 40 属「加重超额」（40 > 15）→ 422（守卫新语义只拦加重，见时长守卫单测）。
        r = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-budget-1",
                "expected_revision": ws0["session"]["revision"],
                "node_index": 0,
                "duration_minutes": 40,
            },
            headers=_auth(),
        )
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["code"] == 42200
        assert body["data"]["error_key"] == "time_budget_exceeded"
        assert body["data"]["over_minutes"] == body["data"]["total_minutes"] - body["data"]["budget_minutes"]
        assert body["data"]["total_minutes"] == 70 and body["data"]["budget_minutes"] == 45


async def test_edit_node_duration_within_budget_then_history(api_factory, db_factory):
    """合法时长保存 → 历史 revisions 列表（3.7）含 teacher/ai 来源与完整 body。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    async with _client(app) as c:
        r = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-budget-2",
                "expected_revision": ws0["session"]["revision"],
                "node_index": 0,
                "duration_minutes": 40,
            },
            headers=_auth(),
        )
        # node1 原时长 10 → Σ=50 超预算；先把 node1 改小再保存 node0
        assert r.status_code == 422
        r2 = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-budget-3",
                "expected_revision": ws0["session"]["revision"],
                "node_index": 1,
                "duration_minutes": 5,
            },
            headers=_auth(),
        )
        assert r2.status_code == 200, r2.text
        ws1 = r2.json()["data"]
        r3 = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-budget-4",
                "expected_revision": ws1["session"]["revision"],
                "node_index": 0,
                "duration_minutes": 40,
            },
            headers=_auth(),
        )
        assert r3.status_code == 200, r3.text

        # 历史：新→旧，含 confirmed（teacher）/ 编辑（teacher）/ 提案（ai_suggestion）
        rh = await c.get(f"{BASE}/{sid}/blueprint/revisions", headers=_auth())
        assert rh.status_code == 200, rh.text
        hist = rh.json()["data"]
        assert hist["blueprint_id"]
        nos = [it["revision_no"] for it in hist["items"]]
        assert nos == sorted(nos, reverse=True)
        assert hist["items"][0]["revision_no"] == hist["current_revision"]
        srcs = {it["source"] for it in hist["items"]}
        assert "teacher" in srcs and "ai_suggestion" in srcs
        assert all("body" in it and "created_at" in it for it in hist["items"])
        # 每条 body 都可只读查看（含 lesson_flow）
        assert all(isinstance(it["body"].get("lesson_flow"), list) for it in hist["items"])


# ── 3.4 AI 定向修改 ──────────────────────────────────────────────────────────


async def test_ai_node_revision_updates_only_target_node(api_factory, db_factory):
    """target_node_id=1 → 只重写节点 1；其它节点逐字节不动；卡片/事件/revision 齐全。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    flow_before = json.loads(json.dumps(_flow_of(ws0), ensure_ascii=False))

    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/{sid}/messages",
            json={
                "idempotency_key": "node-rev-0001",
                "expected_revision": ws0["session"]["revision"],
                "message": "先让学生观察轨迹再归纳定义，并补上板书公式",
                "target_node_id": "1",
            },
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        ws = r.json()["data"]

    flow = _flow_of(ws)
    assert flow[1]["title"] == _NODE_JSON["title"]
    assert flow[1]["duration_minutes"] == 6
    assert flow[1]["teacher_questions"][0]["text"].startswith("动点")
    assert flow[1]["board_notes"][1]["text"].startswith("数学表达式")
    assert flow[1]["edited_by"] == "ai"
    # 其它节点逐字节不动
    assert flow[0] == flow_before[0]
    # timeline 有 node_revision_card
    cards = [m for m in ws["timeline"] if m["component"] and m["component"]["type"] == "node_revision_card"]
    assert len(cards) == 1
    props = cards[0]["component"]["props"]
    assert props["node_index"] == 1 and props["node_title"] == _NODE_JSON["title"]
    assert props["source"] == "ai_suggestion"
    # durable 事件
    events = await _events(db_factory, sid)
    node_ev = [e for e in events if e.event_type == "blueprint.node.updated"]
    assert len(node_ev) == 1
    assert node_ev[0].payload["node_index"] == 1
    assert node_ev[0].payload["source"] == "ai_suggestion"
    assert any(e.event_type == "ai.stage" and e.payload.get("stage") == "node_revising" for e in events)
    # revision 行
    revs = await _revisions_of_session(db_factory, sid)
    assert revs[-1].source == "ai_suggestion"


async def test_ai_node_revision_is_idempotent_on_replay(api_factory, db_factory):
    """同 key 重放：返回已落库结果（revision 不再推进、事件不重复）。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    payload = {
        "idempotency_key": "node-rev-idem-1",
        "expected_revision": ws0["session"]["revision"],
        "message": "重排该环节活动顺序",
        "target_node_id": "1",
    }
    async with _client(app) as c:
        r1 = await c.post(f"{BASE}/{sid}/messages", json=payload, params={"wait": "true"}, headers=_auth())
        assert r1.status_code == 200, r1.text
        rev1 = r1.json()["data"]["artifact"]["blueprint"]["revision"]
        r2 = await c.post(f"{BASE}/{sid}/messages", json=payload, params={"wait": "true"}, headers=_auth())
        assert r2.status_code == 200, r2.text
        rev2 = r2.json()["data"]["artifact"]["blueprint"]["revision"]
    assert rev1 == rev2
    events = await _events(db_factory, sid)
    assert len([e for e in events if e.event_type == "blueprint.node.updated"]) == 1


async def test_ai_node_revision_time_budget_error(api_factory, db_factory):
    """AI 建议时长超剩余预算 → error 事件 time_budget_exceeded + wait 路径 409。"""
    bad = dict(_NODE_JSON)
    bad["duration_minutes"] = 44  # node0 已占 5 → 44+5=49 > 45
    llm = _NodeLlm(node_raw=json.dumps(bad, ensure_ascii=False))
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/{sid}/messages",
            json={
                "idempotency_key": "node-rev-budget-1",
                "expected_revision": ws0["session"]["revision"],
                "message": "把这一环节加长",
                "target_node_id": "1",
            },
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 409, r.text
    events = await _events(db_factory, sid)
    errs = [e for e in events if e.event_type == "error"]
    assert errs and errs[-1].payload["error_key"] == "prep.error.time_budget_exceeded"


async def test_ai_node_revision_invalid_llm_output(api_factory, db_factory):
    """LLM 输出非 JSON → error 事件 node_revision_invalid，蓝图不被修改。"""
    llm = _NodeLlm(node_raw="这不是 JSON")
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    rev_before = ws0["artifact"]["blueprint"]["revision"]
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/{sid}/messages",
            json={
                "idempotency_key": "node-rev-bad-1",
                "expected_revision": ws0["session"]["revision"],
                "message": "改成探究式",
                "target_node_id": "0",
            },
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 409, r.text
    ws = await _get_ws(app, sid)
    assert ws["artifact"]["blueprint"]["revision"] == rev_before
    events = await _events(db_factory, sid)
    errs = [e for e in events if e.event_type == "error"]
    assert errs and errs[-1].payload["error_key"] == "prep.error.node_revision_invalid"


async def test_teacher_edits_survive_ai_targeted_revision(api_factory, db_factory):
    """教师块不被静默覆盖：AI 只动 target 节点；教师编辑过的其它节点保持 edited_by=teacher。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    ws0 = await _get_ws(app, sid)
    async with _client(app) as c:
        # 教师先编辑节点 0
        r = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "edit-node-t-1",
                "expected_revision": ws0["session"]["revision"],
                "node_index": 0,
                "title": "情境导入（教师版）",
                "teacher_questions": [{"text": "你能举出生活中的椭圆吗？"}],
            },
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        ws1 = r.json()["data"]
        # AI 定向修改节点 1
        r2 = await c.post(
            f"{BASE}/{sid}/messages",
            json={
                "idempotency_key": "node-rev-t-2",
                "expected_revision": ws1["session"]["revision"],
                "message": "让学生先观察轨迹再归纳定义",
                "target_node_id": "1",
            },
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r2.status_code == 200, r2.text
        ws2 = r2.json()["data"]
    flow = _flow_of(ws2)
    assert flow[0]["title"] == "情境导入（教师版）"
    assert flow[0]["edited_by"] == "teacher"
    assert flow[0]["teacher_questions"][0]["text"] == "你能举出生活中的椭圆吗？"
    assert flow[1]["edited_by"] == "ai"


# ── 3.6 教材查看 ─────────────────────────────────────────────────────────────


async def test_textbook_view_chunk_and_section(api_factory, db_factory):
    """chunk ref → 切片原文全文；section ref → 章节 chunks 聚合；非法/未知 → fail closed。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    app = _fresh_app(api_factory, llm)
    from app.textbook_v2.models import TextbookChunk

    async with db_factory() as s:
        chunk = (
            (await s.execute(select(TextbookChunk).where(TextbookChunk.section_id == LESSON1, TextbookChunk.order == 10)))
            .scalars()
            .first()
        )
        chunk_id = chunk.id
    chunk_ref = f"textbook_v2/locator/v1/source/{SRC}/section/{LESSON1}/chunk/{chunk_id}"
    section_ref = f"textbook_v2/locator/v1/source/{SRC}/section/{LESSON1}"

    async with _client(app) as c:
        r = await c.get(f"{BASE}/{sid}/textbook/view", params={"ref": chunk_ref}, headers=_auth())
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["kind"] == "chunk"
        assert "A \\subseteq B" in data["text"]  # 真实切片原文（含 LaTeX）
        assert data["section"]["title"].startswith("1.1")
        assert data["page_range"] == [1, 12]  # 种子 chunk 无独立页码 → 回落章节真实区间

        r2 = await c.get(f"{BASE}/{sid}/textbook/view", params={"ref": section_ref}, headers=_auth())
        assert r2.status_code == 200, r2.text
        d2 = r2.json()["data"]
        assert d2["kind"] == "section"
        assert len(d2["chunks"]) == 12  # 1.1 共 12 个 chunk
        assert any("列举法" in ch["text"] for ch in d2["chunks"])

        r3 = await c.get(f"{BASE}/{sid}/textbook/view", params={"ref": "not-a-locator"}, headers=_auth())
        assert r3.status_code == 422
        r4 = await c.get(
            f"{BASE}/{sid}/textbook/view",
            params={"ref": f"textbook_v2/locator/v1/source/{SRC}/section/{LESSON1}/chunk/{uuid_mod.uuid4()}"},
            headers=_auth(),
        )
        assert r4.status_code == 404


async def test_blueprint_revisions_requires_ownership(api_factory, db_factory):
    """非本人会话 → 403（对象级授权）。"""
    llm = _NodeLlm()
    sid = await _confirmed_session(api_factory, db_factory, llm)
    from main import create_app

    app2 = create_app()
    members = MembershipSim()

    class _OtherUser:
        pass

    async def _override_db():
        async with db_factory() as s:
            yield s

    def _override_user():
        return UserToken(user_id="other-teacher-0000-0000-0000-000000000000", name="别人", role="teacher", school_id=None)

    transport = FakeApiTransport()
    transport.handler = members.handler
    conv_router._adapter = lambda: Api8000Adapter(transport, InMemoryMappingRegistry())  # type: ignore[assignment]
    set_conversation_service_factory(lambda: ConversationService(llm_generate=llm, session_factory=db_factory))
    app2.dependency_overrides[get_session] = _override_db
    app2.dependency_overrides[get_current_user] = _override_user
    async with _client(app2) as c:
        r = await c.get(f"{BASE}/{sid}/blueprint/revisions", headers=_auth())
        assert r.status_code == 403, r.text


@pytest.fixture(autouse=True)
def _restore_service_factory():
    yield
    set_conversation_service_factory(None)


async def test_parse_node_revision_tolerant_shapes():
    """LLM 越界形态的确定性收纳：lesson_flow 包裹 / 数组 / 未知键丢弃 / 条目多余键过滤。"""
    import json as _json

    from app.teacher_prep.conversation.node_ops import parse_node_revision

    # 整个 lesson_flow 包裹 → 取 node_index 元素
    full = {"lesson_flow": [
        {"title": "A", "items": []},
        {"title": "B", "duration_minutes": 9, "teacher_questions": [{"text": "问题1", "note": "多余键"}], "key_points": [{"text": "x"}]},
    ], "warnings_ack": []}
    p = parse_node_revision(_json.dumps(full, ensure_ascii=False), node_index=1)
    assert p.title == "B" and p.duration_minutes == 9
    assert p.teacher_questions[0].text == "问题1"

    # 数组包裹
    p2 = parse_node_revision(_json.dumps([{"title": "X"}, {"title": "Y", "student_activities": ["活动"]}]), node_index=1)
    assert p2.title == "Y" and p2.student_activities[0].text == "活动"

    # node 解包 + 顶层未知键丢弃
    p3 = parse_node_revision(_json.dumps({"node": {"title": "Z"}, "context_snapshot_hash": "abc"}, ensure_ascii=False))
    assert p3.title == "Z"

    # 实测形态（doubao-seed-2.0-lite，2026-09-23 真实失败样例）：合法 JSON 之后多吐一个
    # 闭合括号（`Extra data: line 1 column 568`）→ 取首个完整 JSON 值（只取值，不改正文）
    from app.teacher_prep.conversation.node_ops import NodeRevisionError as _NRE

    p4 = parse_node_revision(
        '{"title":"探究椭圆范围与对称性","duration_minutes":15,"summary":"先画图观察再代数推导",'
        '"board_notes":[{"text":"范围：$|x| \\\\leq a$"}]}}',
        node_index=1,
    )
    assert p4.title == "探究椭圆范围与对称性" and p4.duration_minutes == 15
    assert p4.board_notes[0].text.startswith("范围")

    # 真截断（首个 JSON 值本身不完整）仍必须 fail-closed（不放宽闸门）
    try:
        parse_node_revision('{"title":"x","summary":', node_index=1)
        raise AssertionError("should reject truncated json")
    except _NRE:
        pass

    # 越界/非法 → 显式失败
    try:
        parse_node_revision(_json.dumps({"lesson_flow": [{"title": "A"}]}), node_index=5)
        raise AssertionError("should reject out-of-range")
    except _NRE:
        pass
    try:
        parse_node_revision("not json at all")
        raise AssertionError("should reject non-json")
    except _NRE:
        pass
