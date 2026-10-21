"""stu-r2 管线通电：对话 → Butler 内核桥接（app/butler/chat_bridge.py）测试。

覆盖（对齐定稿方案 testPlan ①⑤ + 验收 A/B）：
- data_intent_of：学情/记忆数据意图识别；视频/跳转/纯聊天不劫持；v2 关闭全走旧链；
- build_fixed_plan：确定性 ActionPlan（不依赖 LLM），且能过 PolicyGate 十步校验；
- 卡片组装（learning_viz_tools.card_for_tool_result）：数字全部来自 ToolResult.data
  （R(t) 曲线点 == fsrs.retrievability(t, S) 现算）、空集诚实空态、失败降级、
  非可视化工具返回 None；
- Runtime + fixed_plan：plan 阶段跳过（planner 不被调用、model_request_count=0），
  AgentRun status=succeeded、tool_call_count>=1（executed 口径）、ToolInvocation 落行；
- run_data_intent_events：空库 → 诚实空态卡（empty_reason），system 注入段生成，
  账本落库；
- POST /api/butler/chat（floating-butler 轨）：数据消息 → envelope.blocks 数据卡 +
  agent_runs/tool_invocations 新行。
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.butler.chat_bridge import (
    build_fixed_plan,
    client_request_id_for,
    data_intent_of,
    run_data_intent_events,
    system_message_for_cards,
)
from app.butler.contracts import (
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerRequest,
    ToolResult,
    ToolRisk,
)
from app.butler.executor import ButlerExecutor
from app.butler.learning_viz_tools import (
    TOOL_FSRS_RETENTION,
    TOOL_MASTERY_TREND,
    card_for_tool_result,
)
from app.butler.policy import PolicyGate
from app.butler.registry import ToolRegistry
from app.butler.runtime import ButlerRuntime
from app.butler.tools import register_student_chat_tools
from app.config import settings
from app.main import app
from app.models.agent_run import AgentRun, ToolInvocation
from app.models.database import get_db
from app.services import fsrs
from tests._m3_helpers import make_user, token

_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def _override_get_db():
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def _student_request(user_id: uuid.UUID) -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=user_id, role=ActorRole.STUDENT),
        message="分析一下我的数学薄弱点",
        scene="student.chat",
        client_request_id=f"test-{uuid.uuid4().hex[:12]}",
    )


def _chat_registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_student_chat_tools(reg)
    return reg


# ==================== 1. 意图识别（B：数据意图必进内核；纯聊天不受影响） ====================


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("分析一下我的数学薄弱点", "mastery_bkt"),
        ("帮我分析一下我的学情可以吗", "mastery_bkt"),
        ("我的掌握度怎么样了", "mastery_bkt"),
        ("看看我的记忆状态", "fsrs_retention"),
        ("哪些错题快忘了", "fsrs_retention"),
        ("我快忘了哪些知识点？", "fsrs_retention"),
        ("推荐几个二次函数的讲解视频", None),  # 视频链路互斥护栏
        ("帮我打开错题本", None),  # 跳转动词走 action 事件
        ("你好", None),  # 纯聊天
        ("解方程 x^2 = 4", None),  # 解题
    ],
)
def test_data_intent_of(message, expected, monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", True)
    assert data_intent_of(message) == expected


def test_data_intent_of_disabled_falls_back(monkeypatch):
    """v2 关闭（conftest/生产默认）→ None：既有 chat 行为完全不变。"""
    monkeypatch.setattr(settings, "butler_v2_enabled", False)
    assert data_intent_of("分析一下我的数学薄弱点") is None
    assert data_intent_of("看看我的记忆状态") is None


# ==================== 2. 确定性计划 + PolicyGate 放行 ====================


def test_build_fixed_plan_mastery():
    plan = build_fixed_plan("mastery_bkt", "分析一下我的数学薄弱点")
    assert plan.intent == "mastery_bkt"
    assert plan.response_mode == "cards"
    assert [a.tool_name for a in plan.actions] == [TOOL_MASTERY_TREND]
    assert plan.actions[0].arguments == {"days": 14, "limit": 6}


def test_build_fixed_plan_fsrs_due_only_by_message():
    due = build_fixed_plan("fsrs_retention", "哪些错题快忘了")
    assert [a.tool_name for a in due.actions] == [TOOL_FSRS_RETENTION]
    assert due.actions[0].arguments["due_only"] is True

    general = build_fixed_plan("fsrs_retention", "看看我的记忆状态")
    assert general.actions[0].arguments["due_only"] is False


def test_fixed_plan_passes_policy_gate():
    """确定性计划仍走 PolicyGate 十步校验 + Registry 输入校验（不得绕过权限层）。"""
    reg = _chat_registry()
    for name in (TOOL_FSRS_RETENTION, TOOL_MASTERY_TREND):
        tool = reg.get(name)
        assert tool.risk == ToolRisk.READ
        assert "student.chat" in tool.allowed_scenes
    policy = PolicyGate(reg, allowed_risks=frozenset({ToolRisk.READ, ToolRisk.LEARNING_ACTION}))
    request = _student_request(uuid.uuid4())
    for intent in ("mastery_bkt", "fsrs_retention"):
        plan = build_fixed_plan(intent, "看看我的记忆状态，分析我的薄弱点")
        decision = policy.validate_plan(request, plan, budget=ButlerBudget())
        assert decision.allowed, decision.error_code
        # Registry 输入校验通过（参数由代码构造，LLM 无参数可编）
        for action in plan.actions:
            reg.validate_arguments(action.tool_name, action.arguments)


# ==================== 3. 卡片组装：grounding 与诚实空态 ====================


def _ok_result(data) -> ToolResult:
    return ToolResult(ok=True, data=data, execution_status="executed", latency_ms=3)


def test_fsrs_card_curve_is_computed_from_real_stability():
    """曲线点必须等于 fsrs.retrievability(t, S) 现算值——禁止合成序列。"""
    stability, wrong, reviews = 3.0, 1, 2
    item = {
        "record_id": str(uuid.uuid4()),
        "kp_code": "MATH-FN-01",
        "kp_name": "函数",
        "question_preview": "已知函数 f(x)=x^2+1，求…",
        "review_count": reviews,
        "wrong_count": wrong,
        "stability": stability,
        "retrievability": fsrs.retrievability(1.5, stability),
        "level": fsrs.fsrs_level(stability),
        "is_due": True,
        "hours_to_forget": 24.0,
        "next_review_at": None,
        "current_t": 1.5,
        "curve": None,  # 由下面真实构造
    }
    from app.butler.learning_viz_tools import _curve_for

    item["curve"] = _curve_for(stability, wrong, reviews).model_dump()
    card = card_for_tool_result(TOOL_FSRS_RETENTION, _ok_result({"items": [item]}))
    assert card is not None
    assert card["type"] == "fsrs_retention"
    assert card["source"] == f"butler.{TOOL_FSRS_RETENTION}"
    assert card["degraded"] is False
    assert card["items"][0]["level"] == "lv2"
    curve = card["items"][0]["curve"]
    assert curve["thresholds"] == {"due": fsrs.DUE_THRESHOLD, "decay": fsrs.DECAY_THRESHOLD}
    for point in curve["points"]:
        assert point["r"] == fsrs.retrievability(float(point["t"]), stability)
    # 阶梯 = 0..当前复习档，值 = estimate_stability（如实标注按复习次数推导）
    ladder = curve["stability_ladder"]
    assert [s["review_count"] for s in ladder] == [0, 1, 2]
    assert all(s["stability"] == fsrs.estimate_stability(s["review_count"], wrong) for s in ladder)


def test_fsrs_card_empty_is_honest():
    card = card_for_tool_result(TOOL_FSRS_RETENTION, _ok_result({"items": []}))
    assert card["empty_reason"] == "no_error_records"
    assert card["items"] == []
    assert "错题" in card["message"]
    # 空态卡不得出现任何百分比/曲线数字
    assert "%" not in card["message"]


def test_tool_failure_card_is_degraded_not_fabricated():
    failed = ToolResult(
        ok=False,
        error_code="tool_error",
        user_message="tool execution failed",
        execution_status="executed",
    )
    for name in (TOOL_FSRS_RETENTION, TOOL_MASTERY_TREND):
        card = card_for_tool_result(name, failed)
        assert card["degraded"] is True
        assert card["items"] == []
        assert card["type"] in ("fsrs_retention", "mastery_bkt")


def test_mastery_card_summary_and_delta_from_tool_data():
    trend = [
        {"date": "2026-10-01", "mastery": 0.2},
        {"date": "2026-10-02", "mastery": None},
        {"date": "2026-10-03", "mastery": 0.35},
    ]
    data = {
        "series": [
            {"kp_code": "MATH-FN-01", "kp_name": "函数", "points": trend},
            {"kp_code": "MATH-SEQ-02", "kp_name": "数列", "points": []},
        ],
        "items": [
            {
                "kp_code": "MATH-FN-01",
                "kp_name": "函数",
                "mastery": 0.35,
                "practice_count": 8,
                "correct_count": 3,
            },
            {
                "kp_code": "MATH-SEQ-02",
                "kp_name": "数列",
                "mastery": 0.75,
                "practice_count": 25,
                "correct_count": 20,
            },
        ],
        "summary": {"avg_mastery": 0.55, "mastered_count": 1, "total_kps": 2},
        "snapshot_days": 5,
        "max_practice_count": 25,
    }
    card = card_for_tool_result(TOOL_MASTERY_TREND, _ok_result(data))
    assert card["type"] == "mastery_bkt"
    # max practice 25 >= BKT_MIN_ANSWERS(20) → 如实标注标准 BKT
    assert card["model"] == "bkt"
    assert card["min_answers_threshold"] == 20
    items = card["items"]
    # 掌握度升序（薄弱在前）；三态阈值对齐 growth_router 0.4/0.7
    assert [it["kp_code"] for it in items] == ["MATH-FN-01", "MATH-SEQ-02"]
    assert items[0]["state"] == "weak"  # 0.35 < 0.4
    assert items[1]["state"] == "mastered"  # 0.75 >= 0.7
    assert items[0]["delta"] == round(0.35 - 0.2, 4)  # 最后两个非空点
    assert items[0]["trend"] == trend
    summary = card["summary"]
    assert summary["total_kps"] == 2
    assert summary["mastered_count"] == 1
    assert summary["weak_top1"]["kp_code"] == "MATH-FN-01"
    assert "薄弱" in card["message"]


def test_mastery_card_bkt_lite_label_below_threshold():
    data = {
        "series": [],
        "items": [],
        "summary": {"avg_mastery": 0.0, "mastered_count": 0, "total_kps": 0},
        "snapshot_days": 0,
        "max_practice_count": 2,
    }
    card = card_for_tool_result(TOOL_MASTERY_TREND, _ok_result(data))
    # 答题量远低于 20 → 如实标注 BKT-lite + 诚实空态
    assert card["model"] == "bkt_lite"
    assert card["empty_reason"] == "no_mastery_data"


def test_card_builder_ignores_non_viz_tools():
    assert card_for_tool_result("student.profile.read", _ok_result({"ok": 1})) is None


def test_system_message_for_cards():
    cards = [{"message": "共 2 个知识点，平均掌握度 55%", "type": "mastery_bkt"}]
    text = system_message_for_cards(cards, degraded=False)
    assert "禁止编造" in text and "55%" in text
    assert system_message_for_cards([], degraded=False) == ""
    assert "降级" in system_message_for_cards(cards, degraded=True)


# ==================== 4. Runtime + fixed_plan（账本即证据） ====================


class _BoomPlanner:
    """fixed_plan 提供时 planner 不得被调用；一旦调用即失败暴露。"""

    async def run(self, *args, **kwargs):
        raise AssertionError("planner must not be called when fixed_plan is provided")


def _fixed_plan_runtime():
    reg = _chat_registry()
    policy = PolicyGate(reg, allowed_risks=frozenset({ToolRisk.READ, ToolRisk.LEARNING_ACTION}))
    executor = ButlerExecutor(reg, policy, budget=ButlerBudget())
    return ButlerRuntime(
        registry=reg,
        policy=policy,
        executor=executor,
        planner=_BoomPlanner(),
        adapter=object(),  # 若跳过失败 → _plan 会触发 BoomPlanner
    )


@pytest.mark.asyncio
async def test_runtime_fixed_plan_skips_planner_and_writes_ledger():
    user_id = uuid.uuid4()
    runtime = _fixed_plan_runtime()
    plan = build_fixed_plan("mastery_bkt", "分析一下我的数学薄弱点")
    crid = client_request_id_for(uuid.uuid4().hex)
    request = ButlerRequest(
        actor=ActorContext(user_id=user_id, role=ActorRole.STUDENT),
        message="分析一下我的数学薄弱点",
        scene="student.chat",
        client_request_id=crid,
    )
    async with _session_factory() as db:
        # AgentRun.user_id FK users.id → 需真实用户行
        uid = await make_user(db)
        await db.commit()
        request = request.model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT)}
        )
        envelope = await runtime.run(request, db, fixed_plan=plan)
        await db.commit()

        assert envelope.intent == "mastery_bkt"
        assert envelope.degraded is False  # 空库也是诚实空态卡，不是失败
        cards = [
            b["data"] for b in envelope.blocks if isinstance(b, dict) and b.get("type") == "card"
        ]
        assert cards and cards[0]["type"] == "mastery_bkt"
        assert cards[0]["empty_reason"] == "no_mastery_data"

        run = (
            await db.execute(select(AgentRun).where(AgentRun.client_request_id == crid))
        ).scalar_one()
        assert run.status == "succeeded"
        assert run.scene == "student.chat"
        assert run.intent == "mastery_bkt"
        assert run.model_request_count == 0  # plan 阶段跳过：0 模型请求
        assert run.tool_call_count == 1  # executed 口径
        assert run.degraded is False
        invocations = (
            (await db.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id)))
            .scalars()
            .all()
        )
        assert len(invocations) == 1
        assert invocations[0].tool_name == TOOL_MASTERY_TREND
        assert invocations[0].status == "executed"


@pytest.mark.asyncio
async def test_runtime_fixed_plan_fsrs_empty_db_empty_state_card():
    runtime = _fixed_plan_runtime()
    plan = build_fixed_plan("fsrs_retention", "看看我的记忆状态")
    crid = client_request_id_for(uuid.uuid4().hex)
    async with _session_factory() as db:
        uid = await make_user(db)
        await db.commit()
        request = ButlerRequest(
            actor=ActorContext(user_id=uid, role=ActorRole.STUDENT),
            message="看看我的记忆状态",
            scene="student.chat",
            client_request_id=crid,
        )
        envelope = await runtime.run(request, db, fixed_plan=plan)
        await db.commit()
        cards = [
            b["data"] for b in envelope.blocks if isinstance(b, dict) and b.get("type") == "card"
        ]
        assert cards and cards[0]["type"] == "fsrs_retention"
        assert cards[0]["empty_reason"] == "no_error_records"


# ==================== 5. 桥接事件流（chat 技能路径） ====================


@pytest.mark.asyncio
async def test_run_data_intent_events_yields_card_and_ledger(monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", True)
    async with _session_factory() as db:
        uid = await make_user(db)
        await db.commit()

    request_id = uuid.uuid4().hex
    messages: list[dict] = []
    events: list[dict] = []
    with patch(
        "app.providers.router.get_model_router_for_user",
        new=AsyncMock(return_value=AsyncMock()),
    ):
        async for evt in run_data_intent_events(
            question="分析一下我的数学薄弱点",
            user_id=uid,
            request_id=request_id,
            messages=messages,
        ):
            events.append(evt)

    types = [e["type"] for e in events]
    assert "status" in types and "card" in types
    card = next(e["data"] for e in events if e["type"] == "card")
    assert card["type"] == "mastery_bkt"
    assert card.get("empty_reason") == "no_mastery_data"
    assert any(m.get("role") == "system" and "禁止编造" in m.get("content", "") for m in messages)

    # 账本：独立 session 已 commit，agent_runs/tool_invocations 真实落行
    async with _session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.client_request_id == client_request_id_for(request_id)
                )
            )
        ).scalar_one()
        assert run.tool_call_count == 1
        assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_run_data_intent_events_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", False)
    events = [
        e
        async for e in run_data_intent_events(
            question="分析一下我的数学薄弱点",
            user_id=uuid.uuid4(),
            request_id=uuid.uuid4().hex,
            messages=[],
        )
    ]
    assert events == []


# ==================== 7. /api/agent/chat 全链冒烟（kernel→chat skill→桥→SSE card） ====================


class _FakeChatLLM:
    """主链路 LLM 替身：chat（标题任务）+ chat_stream（正文 token）。"""

    intended_provider = "fake"

    async def chat(self, messages, **kwargs):
        return {
            "content": "学情分析",
            "provider": "fake",
            "latency_ms": 1,
            "input_tokens": 1,
            "output_tokens": 1,
        }

    async def chat_stream(self, messages, **kwargs):
        yield {"token": "你的薄弱点已在上面的卡片里，我们先攻最弱的一项。"}


@pytest.mark.asyncio
async def test_agent_chat_sse_data_intent_emits_card(client, monkeypatch):
    """端到端（真实 chat 技能 + 真实桥接）：数据意图 → SSE card 事件 + 账本落库。"""
    from app.gateway import agent_router as ar
    from app.kernel.router import RouteDecision

    monkeypatch.setattr(settings, "butler_v2_enabled", True)

    class _FakeRouter:
        async def route(self, *args, **kwargs):
            return RouteDecision(skill_id="chat", confidence=0.95, params={})

    async def _noop_bg(*args, **kwargs):
        return None

    async def _fake_router(user_id, db):
        return _FakeChatLLM()

    phone = f"138{str(uuid.uuid4().int)[:8]}"
    with (
        patch.object(ar, "get_intent_router", return_value=_FakeRouter()),
        patch.object(ar, "get_model_router_for_user", side_effect=_fake_router),
        patch.object(ar, "_bg_episodic_extract", side_effect=_noop_bg),
        patch.object(ar, "_bg_summary", side_effect=_noop_bg),
        patch(
            "app.providers.router.get_model_router_for_user",
            new=AsyncMock(return_value=_FakeChatLLM()),
        ),
    ):
        await client.post("/api/auth/sms-code", json={"phone": phone})
        login = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        tok = login.json()["data"]["token"]
        resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "分析一下我的数学薄弱点",
                "context": {"client_msg_id": str(uuid.uuid4()), "workspace": "student"},
            },
            headers=_auth(tok),
        )
    assert resp.status_code == 200
    lines = resp.text.split("\n")
    events = [ln[7:] for ln in lines if ln.startswith("event: ")]
    assert "card" in events, events
    card = None
    for i, ln in enumerate(lines):
        if ln == "event: card" and i + 1 < len(lines) and lines[i + 1].startswith("data: "):
            card = json.loads(lines[i + 1][6:])
            break
    assert card is not None and card["type"] == "mastery_bkt"
    # 空库 → 诚实空态卡，不编造数字
    assert card.get("empty_reason") == "no_mastery_data"
    assert "token" in events  # LLM 正文照常流式输出（卡片不阻塞主链路）

    # 账本：agent_runs 新行（scene=student.chat、tool executed）
    async with _session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AgentRun)
                    .where(AgentRun.scene == "student.chat")
                    .order_by(AgentRun.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .all()
        )
        assert rows
        run = rows[0]
        assert run.tool_call_count == 1
        assert run.status == "succeeded"
        inv = (
            (await db.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id)))
            .scalars()
            .all()
        )
        assert inv and inv[0].tool_name == TOOL_MASTERY_TREND
        assert inv[0].status == "executed"


# ==================== 6. POST /api/butler/chat（floating-butler 轨） ====================


@pytest.mark.asyncio
async def test_butler_chat_data_message_returns_blocks_and_ledger(client, monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", True)
    async with _session_factory() as db:
        student_id = await make_user(db)
        await db.commit()
    crid = f"stu-r2-{uuid.uuid4().hex}"
    with patch(
        "app.providers.router.get_model_router_for_user",
        new=AsyncMock(return_value=AsyncMock()),
    ):
        response = await client.post(
            "/api/butler/chat",
            json={"message": "分析一下我的数学薄弱点", "client_request_id": crid},
            headers=_auth(token(student_id, "student")),
        )
    assert response.status_code == 200
    envelope = response.json()["data"]["envelope"]
    assert envelope["degraded"] is False
    assert envelope["blocks"], "数据消息必须携带结构化数据卡"
    card = envelope["blocks"][0]["data"]
    assert card["type"] == "mastery_bkt"
    assert card["empty_reason"] == "no_mastery_data"  # 空库诚实空态
    assert card["source"] == f"butler.{TOOL_MASTERY_TREND}"

    async with _session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.client_request_id == crid))
        ).scalar_one()
        assert run.user_id == student_id
        assert run.scene == "student.chat"
        assert run.status == "succeeded"
        assert run.tool_call_count == 1
        assert run.model_request_count == 0
        inv = (
            (await db.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id)))
            .scalars()
            .all()
        )
        assert inv and inv[0].status == "executed"


@pytest.mark.asyncio
async def test_butler_chat_pure_chat_intent_keeps_planner_path(client, monkeypatch):
    """非数据意图（任务下单）不受桥接影响：仍走 planner → 后台任务工具。"""
    from app.services import task_runner

    monkeypatch.setattr(settings, "butler_v2_enabled", True)
    async with _session_factory() as db:
        student_id = await make_user(db)
        await db.commit()
    prev = task_runner._HANDLERS.get("practice.generate")

    async def handler(task, db, progress):
        await progress("处理中", 50)
        return {"artifact_type": "practice", "quiz_id": str(uuid.uuid4()), "jump": "/tasks"}

    task_runner._HANDLERS["practice.generate"] = handler
    try:
        crid = f"stu-r2-chat-{uuid.uuid4().hex}"
        plan = {
            "intent": "practice_request",
            "goal": "为你生成专属练习",
            "actions": [
                {
                    "tool_name": "student.practice.generate",
                    "arguments": {"kp_code": "函数", "count": 5},
                    "reason": "学生要练函数题",
                }
            ],
            "response_mode": "direct",
            "needs_web_search": False,
        }

        class _PlannerRouter:
            async def chat(self, *args, **kwargs):
                import json as _json

                return {"content": _json.dumps(plan, ensure_ascii=False)}

        with patch(
            "app.providers.router.get_model_router_for_user",
            new=AsyncMock(return_value=_PlannerRouter()),
        ):
            response = await client.post(
                "/api/butler/chat",
                json={"message": "帮我出5道函数的练习题", "client_request_id": crid},
                headers=_auth(token(student_id, "student")),
            )
        assert response.status_code == 200
        envelope = response.json()["data"]["envelope"]
        assert envelope["actions"], "任务下单意图仍走 planner 路径"
        assert "blocks" not in envelope  # 非数据意图不产卡
    finally:
        if prev is None:
            task_runner._HANDLERS.pop("practice.generate", None)
        else:
            task_runner._HANDLERS["practice.generate"] = prev
