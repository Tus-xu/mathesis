"""Butler Kernel v2 核心类型契约（阶段 2 Task 3）

覆盖（设计规格 §4 + 阶段 2 验收）：
- ActorRole / ToolRisk 枚举值；
- 各模型字段类型与默认值（ContextSnapshot 整体是类型化模型而非裸 dict）；
- 空字符串 ID / tool_name / scene / client_request_id 拒绝；
- 超过 5 个 Action 拒绝、非法 response_mode 拒绝；
- Planner-facing 模型 extra="forbid"（多余字段拒绝）。
"""

import uuid

import pytest
from pydantic import ValidationError

from app.butler.contracts import (
    ActionPlan,
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerContextSnapshot,
    ButlerEnvelope,
    ButlerRequest,
    PlannedAction,
    ToolResult,
    ToolRisk,
)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _actor(role: ActorRole = ActorRole.STUDENT) -> ActorContext:
    return ActorContext(user_id=_uid(), role=role, class_ids=(_uid(),), locale="zh-CN")


def _action(tool_name: str = "test.read") -> PlannedAction:
    return PlannedAction(tool_name=tool_name, arguments={"a": 1}, reason="unit test")


# ---------- ActorRole ----------


def test_actor_role_enum_values():
    assert ActorRole.STUDENT.value == "student"
    assert ActorRole.TEACHER.value == "teacher"
    assert ActorRole.RESEARCHER.value == "researcher"
    assert ActorRole.ADMIN.value == "admin"


# ---------- ActorContext ----------


def test_actor_context_defaults():
    ctx = ActorContext(user_id=_uid(), role=ActorRole.STUDENT)
    assert ctx.class_ids == ()
    assert ctx.locale == "zh-CN"


def test_actor_context_rejects_extra_field():
    with pytest.raises(ValidationError):
        ActorContext(user_id=_uid(), role=ActorRole.STUDENT, sneaky="x")


# ---------- ButlerRequest ----------


def test_butler_request_full():
    req = ButlerRequest(
        actor=_actor(),
        message="帮我看看今天该复习什么",
        scene="student.dashboard",
        conversation_id=_uid(),
        source_event_id=_uid(),
        client_request_id="crid-1",
    )
    assert req.scene == "student.dashboard"
    assert req.client_request_id == "crid-1"


def test_butler_request_rejects_empty_client_request_id():
    with pytest.raises(ValidationError):
        ButlerRequest(actor=_actor(), message="hi", scene="student.dashboard", client_request_id="")


def test_butler_request_rejects_empty_scene():
    with pytest.raises(ValidationError):
        ButlerRequest(actor=_actor(), message="hi", scene="", client_request_id="crid-1")


def test_butler_request_rejects_extra_field():
    with pytest.raises(ValidationError):
        ButlerRequest(
            actor=_actor(),
            message="hi",
            scene="student.dashboard",
            client_request_id="crid-1",
            unexpected=True,
        )


# ---------- ButlerContextSnapshot ----------


def test_butler_context_snapshot_is_typed_model_not_bare_dict():
    snap = ButlerContextSnapshot(
        actor=_actor(),
        scene="student.dashboard",
        profile={"streak_days": 3},
        conversation={"summary": "…"},
        assignments=({"id": "a1"},),
        effective_config={"model": "spark"},
        feature_flags=frozenset({"web_search"}),
    )
    assert isinstance(snap, ButlerContextSnapshot)
    assert isinstance(snap.profile, dict)
    assert isinstance(snap.assignments, tuple)
    assert isinstance(snap.feature_flags, frozenset)
    assert snap.scene == "student.dashboard"


def test_butler_context_snapshot_defaults():
    snap = ButlerContextSnapshot(actor=_actor(), scene="student.dashboard")
    assert snap.profile == {}
    assert snap.conversation == {}
    assert snap.assignments == ()
    assert snap.effective_config == {}
    assert snap.feature_flags == frozenset()


def test_butler_context_snapshot_rejects_extra_field():
    with pytest.raises(ValidationError):
        ButlerContextSnapshot(actor=_actor(), scene="student.dashboard", extra_key=1)


# ---------- PlannedAction ----------


def test_planned_action_rejects_empty_tool_name():
    with pytest.raises(ValidationError):
        PlannedAction(tool_name="", reason="x")


def test_planned_action_rejects_extra_field():
    with pytest.raises(ValidationError):
        PlannedAction(tool_name="test.read", reason="x", sneaky=True)


# ---------- ActionPlan ----------


def test_action_plan_allows_five_actions():
    plan = ActionPlan(
        intent="review",
        goal="复习错题",
        actions=[_action(f"t{i}") for i in range(5)],
        response_mode="cards",
    )
    assert len(plan.actions) == 5


def test_action_plan_rejects_six_actions():
    with pytest.raises(ValidationError):
        ActionPlan(
            intent="review",
            goal="复习错题",
            actions=[_action(f"t{i}") for i in range(6)],
            response_mode="cards",
        )


def test_action_plan_rejects_invalid_response_mode():
    with pytest.raises(ValidationError):
        ActionPlan(
            intent="review",
            goal="复习错题",
            actions=[_action()],
            response_mode="fire_and_forget",
        )


def test_action_plan_rejects_extra_field():
    with pytest.raises(ValidationError):
        ActionPlan(
            intent="review",
            goal="复习错题",
            actions=[_action()],
            response_mode="direct",
            plugin="x",
        )


def test_action_plan_needs_web_search_default_false():
    plan = ActionPlan(
        intent="review", goal="复习错题", actions=[_action()], response_mode="direct"
    )
    assert plan.needs_web_search is False


# ---------- ToolRisk ----------


def test_tool_risk_enum_values():
    assert ToolRisk.READ.value == "read"
    assert ToolRisk.LEARNING_ACTION.value == "learning_action"
    assert ToolRisk.WRITE.value == "write"
    assert ToolRisk.EXTERNAL.value == "external"
    assert ToolRisk.ROLE_RESTRICTED.value == "role_restricted"


# ---------- ToolResult ----------


def test_tool_result_defaults():
    r = ToolResult(ok=True)
    assert r.data is None
    assert r.error_code is None
    assert r.user_message is None
    assert r.retryable is False
    assert r.degraded is False


# ---------- ButlerEnvelope ----------


def test_butler_envelope_defaults():
    env = ButlerEnvelope(run_id=_uid(), intent="review", text="ok")
    assert env.blocks == []
    assert env.actions == []
    assert env.sources == []
    assert env.degraded is False
    assert env.trace == {}


def test_butler_envelope_rejects_extra_field():
    with pytest.raises(ValidationError):
        ButlerEnvelope(run_id=_uid(), intent="review", text="ok", surprise="x")


# ---------- ButlerBudget ----------


def test_butler_budget_defaults():
    b = ButlerBudget()
    assert b.max_model_requests == 3
    assert b.max_tool_calls == 5
    assert b.timeout_s == 20


# ---------- 阶段 2.1：ButlerBudget 边界 ----------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_model_requests": 0},
        {"max_model_requests": -1},
        {"max_model_requests": 4},
        {"max_tool_calls": 0},
        {"max_tool_calls": -5},
        {"max_tool_calls": 6},
        {"timeout_s": 0},
        {"timeout_s": -0.1},
        {"timeout_s": 20.1},
        {"timeout_s": 21},
    ],
)
def test_butler_budget_rejects_out_of_bounds(kwargs: dict):
    with pytest.raises(ValidationError):
        ButlerBudget(**kwargs)


def test_butler_budget_allows_minimal():
    b = ButlerBudget(max_model_requests=1, max_tool_calls=1, timeout_s=0.1)
    assert b.max_model_requests == 1
    assert b.max_tool_calls == 1
    assert b.timeout_s == 0.1
