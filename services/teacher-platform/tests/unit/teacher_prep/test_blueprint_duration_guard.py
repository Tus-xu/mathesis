"""时长一致性修复单测：蓝图提案落库前归一化 + 编辑守卫只拦「加重超额」。

背景（真实可用性事故）：LLM 生成的蓝图 lesson_flow 时长合计可能 ≠ 课时预算
（如 Σ=50 > 45），而旧的编辑守卫「改后总和 > 预算即 422」会让教师在已超额蓝图上
连改标题都被锁死。本文件覆盖两处修复：

A. 蓝图提案落库前归一化（handler._finish → normalize_flow_durations）：
   Σ == 预算、保序、保文字、保 source_ref、教师编辑过的节点结构不动、确定性；
B. 编辑守卫 `_check_time_budget` 新语义：只拦「改后 Σ > 预算 且 改后 Σ > 改前 Σ」。
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

import app.teacher_prep.conversation.router as conv_router
from app.core.deps import get_current_user, get_session
from app.core.errors import ApiError, ErrorCode
from app.core.security import UserToken
from app.teacher_prep.conversation.node_ops import _check_time_budget, lesson_budget_minutes
from app.teacher_prep.conversation.orchestrator import ConversationService
from app.teacher_prep.conversation.service_bridge import set_conversation_service_factory
from app.teacher_prep.models import BlueprintRevisionRow, TeachingBlueprintRow
from app.teacher_prep.specs.durations import normalize_flow_durations
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

from .conftest import CLASS_A, TEACHER, MembershipSim
from .tp_context_fixtures import LESSON1, SRC, seed_synthetic_textbook

BASE = "/api/teacher-v3/prep/conversations"

# 6 环节合计 50 分钟（> 单课时预算 45）——复现 LLM 超额蓝图
_FLOW_50 = [
    {"stage": "introduction", "title": "情境导入", "items": [{"text": "从生活现象引入"}], "duration_minutes": 5},
    {"stage": "new_content", "title": "新授概念", "items": [{"text": "给出定义"}], "duration_minutes": 10},
    {"stage": "example", "title": "例题讲解", "items": [{"text": "示范解法"}], "duration_minutes": 10},
    {"stage": "exercise", "title": "随堂练习", "items": [{"text": "学生独立完成"}], "duration_minutes": 10},
    {"stage": "summary", "title": "课堂小结", "items": [{"text": "归纳要点"}], "duration_minutes": 10},
    {"stage": "homework", "title": "作业布置", "items": [{"text": "布置课后作业"}], "duration_minutes": 5},
]


# ── A. 归一化纯函数语义（落库点接入的既有函数）─────────────────────────────


def test_normalize_sums_to_budget_and_preserves_order_text():
    """Σ == 预算；环节顺序 / 标题 / 条目文字 / source_ref 全部保留；不就地修改入参。"""
    body = {"title": "keep", "lesson_flow": json.loads(json.dumps(_FLOW_50, ensure_ascii=False))}
    out = normalize_flow_durations(body, 45)
    flow = out["lesson_flow"]
    assert sum(s["duration_minutes"] for s in flow) == 45
    assert all(s["duration_minutes"] >= 1 for s in flow)
    # 保序（stage/title 逐项不变）
    assert [s["stage"] for s in flow] == [s["stage"] for s in _FLOW_50]
    assert [s["title"] for s in flow] == [s["title"] for s in _FLOW_50]
    # 保文字（items 逐项不变）
    assert [s["items"] for s in flow] == [s["items"] for s in _FLOW_50]
    assert out["title"] == "keep"
    # 不就地修改入参（防污染已落库蓝图）
    assert sum(s["duration_minutes"] for s in body["lesson_flow"]) == 50


def test_normalize_preserves_teacher_edited_structure():
    """教师编辑过的节点：仅时长按比例归一，文字/结构/edited_by 一律不动。"""
    body = {
        "lesson_flow": [
            {
                "stage": "introduction",
                "title": "导入（教师定稿）",
                "items": [{"text": "教师补充的例子", "source_ref": None}],
                "teacher_questions": [{"text": "你见过哪些集合？"}],
                "duration_minutes": 20,
                "edited_by": "teacher",
                "edited_at": "2026-09-20T00:00:00+00:00",
            },
            {"stage": "homework", "title": "作业", "items": [], "duration_minutes": 30},
        ]
    }
    out = normalize_flow_durations(body, 45)
    first = out["lesson_flow"][0]
    assert sum(s["duration_minutes"] for s in out["lesson_flow"]) == 45
    assert first["title"] == "导入（教师定稿）"
    assert first["teacher_questions"] == [{"text": "你见过哪些集合？"}]
    assert first["edited_by"] == "teacher"
    assert first["edited_at"] == "2026-09-20T00:00:00+00:00"


def test_normalize_deterministic():
    """同输入同输出（确定性；同一蓝图重复落库不抖）。"""
    body = {"lesson_flow": json.loads(json.dumps(_FLOW_50, ensure_ascii=False))}
    a = normalize_flow_durations(body, 45)
    b = normalize_flow_durations(body, 45)
    assert a["lesson_flow"] == b["lesson_flow"]
    # 幂等：再归一化不变
    assert normalize_flow_durations(a, 45)["lesson_flow"] == a["lesson_flow"]


def test_normalize_skips_when_stages_more_than_budget():
    """环节数 > 预算（无法每环节 ≥1 分钟）→ 原样返回（既有语义，不硬塞）。"""
    body = {"lesson_flow": [{"stage": "exercise", "title": f"环节{i}", "items": [], "duration_minutes": 10} for i in range(50)]}
    assert normalize_flow_durations(body, 45) is body


# ── B. 守卫新语义（只拦加重超额）────────────────────────────────────────────


class _BudgetSession:
    """预算口径最小桩：lesson_budget_minutes 只读 srow.intent.lesson_hours。"""

    def __init__(self, lesson_hours: int = 1) -> None:
        self.intent = {"lesson_hours": lesson_hours}


def _guard_flow(*durations: int) -> dict:
    return {
        "lesson_flow": [
            {"stage": "introduction", "title": f"环节{i}", "items": [{"text": f"文本{i}"}], "duration_minutes": d}
            for i, d in enumerate(durations)
        ]
    }


def test_guard_allows_non_duration_edit_when_already_over_budget():
    """current_total=50（已超额）只改标题（node_index=-1，未改时长）→ 放行。"""
    body = _guard_flow(30, 20)
    assert lesson_budget_minutes(_BudgetSession()) == 45
    _check_time_budget(body, srow=_BudgetSession(), node_index=-1, new_duration=None)


def test_guard_allows_unchanged_total():
    """current_total=50, new_total=50（时长原样填回）→ 放行。"""
    body = _guard_flow(30, 20)
    _check_time_budget(body, srow=_BudgetSession(), node_index=0, new_duration=30)


def test_guard_blocks_aggravating_over_budget_edit_fields():
    """current_total=50, new_total=52 → 422 time_budget_exceeded，字段名与口径不变。"""
    body = _guard_flow(30, 20)
    with pytest.raises(ApiError) as exc:
        _check_time_budget(body, srow=_BudgetSession(), node_index=1, new_duration=22)
    err = exc.value
    assert err.code == ErrorCode.VALIDATION
    assert err.status_code == 422
    data = err.data
    assert data["error_key"] == "time_budget_exceeded"
    assert data["over_minutes"] == 52 - 45
    assert data["total_minutes"] == 52
    assert data["budget_minutes"] == 45


def test_guard_allows_reducing_into_budget():
    """current_total=50, new_total=45（削减到预算内）→ 放行。"""
    body = _guard_flow(30, 20)
    _check_time_budget(body, srow=_BudgetSession(), node_index=1, new_duration=15)


def test_guard_allows_reducing_but_still_over_budget():
    """current_total=50, new_total=48（削减但仍超额）→ 放行（允许逐步削减）。"""
    body = _guard_flow(30, 20)
    _check_time_budget(body, srow=_BudgetSession(), node_index=1, new_duration=18)


def test_guard_blocks_new_over_budget_from_within_budget():
    """current_total=40（未超额）, new_total=46 → 422。"""
    body = _guard_flow(30, 10)
    with pytest.raises(ApiError) as exc:
        _check_time_budget(body, srow=_BudgetSession(), node_index=1, new_duration=16)
    assert exc.value.data["over_minutes"] == 1
    assert exc.value.data["total_minutes"] == 46


# ── C. 端到端：conversation run 落库的蓝图 body 已归一化 ─────────────────────


class _FlowLlm:
    """意图 / 蓝图双 prompt 的确定性假 LLM（蓝图 lesson_flow 合计 50 > 45）。"""

    async def __call__(self, prompt: str) -> str:
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
                "lesson_flow": json.loads(json.dumps(_FLOW_50, ensure_ascii=False)),
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
            lambda: ConversationService(llm_generate=llm or _FlowLlm(), session_factory=db_factory)
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


@pytest.fixture(autouse=True)
def _restore_service_factory():
    yield
    set_conversation_service_factory(None)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _auth():
    return {"Authorization": "Bearer jwt-teacher-token"}


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
        jobs = await JobEngine.claim(s, owner_service="teacher_prep", worker_name="dur-guard-test", limit=10)
        await s.commit()
        for job in jobs:
            await fenced_run(s, job_id=job.id, claim_token=job.claim_token or "", handler=handler)


async def _seeded_session(api_factory, db_factory) -> tuple[object, str]:
    """真实链路造数：新建（同步）→ worker 投影蓝图 → 返回 (app, session_id)。"""
    llm = _FlowLlm()
    app, members = api_factory(llm)
    async with db_factory() as s:
        await seed_synthetic_textbook(s)
        await s.commit()
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            BASE,
            json={"class_id": CLASS_A, "message": "备1.1集合", "title": "集合备课", "idempotency_key": "dur-key-0001"},
            params={"wait": "true"},
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        sid = r.json()["data"]["session"]["session_id"]
    await _process_jobs(db_factory, llm)
    return app, sid


async def _latest_blueprint(db_factory) -> TeachingBlueprintRow:
    async with db_factory() as s:
        return (
            await s.execute(
                select(TeachingBlueprintRow).order_by(TeachingBlueprintRow.created_at.desc()).limit(1)
            )
        ).scalar_one()


async def _get_ws(app, sid: str) -> dict:
    async with _client(app) as c:
        r = await c.get(f"{BASE}/{sid}", headers=_auth())
        assert r.status_code == 200, r.text
        return r.json()["data"]


async def test_blueprint_proposal_persisted_normalized(api_factory, db_factory):
    """LLM 蓝图合计 50 → 落库提案 body / revision body / 工作区均 Σ == 45（保序保文字）。"""
    app, sid = await _seeded_session(api_factory, db_factory)

    brow = await _latest_blueprint(db_factory)
    flow = brow.body["lesson_flow"]
    assert sum(s["duration_minutes"] for s in flow) == 45, "蓝图本体必须归一化到课时预算"
    assert [s["stage"] for s in flow] == [s["stage"] for s in _FLOW_50]
    assert [s["title"] for s in flow] == [s["title"] for s in _FLOW_50]
    # 保文字：条目文本逐项不变（source_ref 由 schema 序列化补 None，非归一化改动）
    assert [[it["text"] for it in s["items"]] for s in flow] == [
        [it["text"] for it in s["items"]] for s in _FLOW_50
    ]

    # proposed revision 快照同样归一化（与蓝图本体一致）
    async with db_factory() as s:
        rev = (
            await s.execute(
                select(BlueprintRevisionRow).where(BlueprintRevisionRow.blueprint_id == brow.id)
            )
        ).scalars().first()
        assert rev is not None
        assert sum(x["duration_minutes"] for x in rev.body["lesson_flow"]) == 45

    # 工作区视图（教师所见）与顶栏预算一致
    ws = await _get_ws(app, sid)
    ws_flow = ws["artifact"]["blueprint"]["body"]["lesson_flow"]
    assert sum(s["duration_minutes"] for s in ws_flow) == 45


async def test_title_edit_on_legacy_over_budget_not_blocked(api_factory, db_factory):
    """历史超额蓝图（直接改库模拟 Σ=50）上：只改标题放行；加重 422；削减放行。"""
    app, sid = await _seeded_session(api_factory, db_factory)

    # 直接改库模拟历史脏数据：Σ=30+20=50 > 45
    async with db_factory() as s:
        brow = (
            await s.execute(
                select(TeachingBlueprintRow).order_by(TeachingBlueprintRow.created_at.desc()).limit(1)
            )
        ).scalar_one()
        body = dict(brow.body)
        body["lesson_flow"] = [
            {"stage": "introduction", "title": "情境导入", "items": [], "duration_minutes": 30},
            {"stage": "new_content", "title": "新授概念", "items": [], "duration_minutes": 20},
        ]
        brow.body = body
        await s.commit()

    ws = await _get_ws(app, sid)
    assert sum(s["duration_minutes"] for s in ws["artifact"]["blueprint"]["body"]["lesson_flow"]) == 50

    # 只改标题（时长不动）→ 放行（修复前 422 锁死）
    async with _client(app) as c:
        r = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "dur-edit-title-1",
                "expected_revision": ws["session"]["revision"],
                "node_index": 0,
                "title": "情境导入（教师版）",
            },
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        ws1 = r.json()["data"]
        assert ws1["artifact"]["blueprint"]["body"]["lesson_flow"][0]["title"] == "情境导入（教师版）"

        # 加重超额（20 → 25，Σ=55）→ 422，字段齐全
        r2 = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "dur-edit-aggravate-1",
                "expected_revision": ws1["session"]["revision"],
                "node_index": 1,
                "duration_minutes": 25,
            },
            headers=_auth(),
        )
        assert r2.status_code == 422, r2.text
        data = r2.json()["data"]
        assert r2.json()["code"] == 42200
        assert data["error_key"] == "time_budget_exceeded"
        assert data["over_minutes"] == 55 - 45
        assert data["total_minutes"] == 55
        assert data["budget_minutes"] == 45

        # 削减到预算内（20 → 15，Σ=45）→ 放行
        r3 = await c.patch(
            f"{BASE}/{sid}/blueprint/node",
            json={
                "idempotency_key": "dur-edit-reduce-1",
                "expected_revision": ws1["session"]["revision"],
                "node_index": 1,
                "duration_minutes": 15,
            },
            headers=_auth(),
        )
        assert r3.status_code == 200, r3.text
        flow3 = r3.json()["data"]["artifact"]["blueprint"]["body"]["lesson_flow"]
        assert sum(s["duration_minutes"] for s in flow3) == 45


async def test_revisions_list_shows_normalized_total(api_factory, db_factory):
    """历史 revision 列表 total_minutes 与预算一致（教师查看历史不再自相矛盾）。"""
    app, sid = await _seeded_session(api_factory, db_factory)
    async with _client(app) as c:
        rh = await c.get(f"{BASE}/{sid}/blueprint/revisions", headers=_auth())
        assert rh.status_code == 200, rh.text
        item = rh.json()["data"]["items"][0]
        assert item["total_minutes"] == 45