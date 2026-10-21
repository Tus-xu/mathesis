"""Butler Kernel v2 ModelAdapter（阶段 3A）

覆盖：
- 合法 JSON / markdown 代码块包裹 → ActionPlan；
- 非法 JSON / Schema 错误 → 最多修复 1 次（retries=1）；
- 主备模型全部失败 / 超时 → deterministic fallback（不向学生端抛异常）；
- 单次 Planner 请求数 ≤ 3（UsageLimits + adapter 计数双护栏）；
- 不调用模型原生 Function Calling；
- 不连接 Pydantic Gateway（复用传入 ModelRouter 实例，不新建 provider）；
- 不可见工具不进入 Planner 工具摘要。
"""

import asyncio
import json
import uuid

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import UsageLimits

from app.butler.contracts import (
    ActionPlan,
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerRequest,
    ToolRisk,
)
from app.butler.model_adapter import (
    ButlerDeps,
    ButlerModelAdapter,
    build_planner,
    build_planning_prompt,
    deterministic_fallback_plan,
)
from app.butler.registry import ToolDefinition, ToolRegistry


class EchoInput(BaseModel):
    query: str


class EchoOutput(BaseModel):
    answer: str


async def _echo_handler(*_args, **_kwargs):
    return {"answer": "ok"}


def _tool_def(name: str, *, roles: frozenset[ActorRole], scenes: frozenset[str]) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        description=f"{name} 测试工具",
        input_model=EchoInput,
        output_model=EchoOutput,
        risk=ToolRisk.READ,
        allowed_roles=roles,
        allowed_scenes=scenes,
        handler=_echo_handler,
    )


class FakeRouter:
    """模拟 ModelRouter.chat：按序消费 responses，记录调用；可注入异常/延迟。"""

    def __init__(
        self,
        responses: list[object] | None = None,
        *,
        exception: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.responses = list(responses or [])
        self.exception = exception
        self.delay = delay
        self.calls: list[dict] = []

    async def chat(self, messages: list[dict], **kwargs) -> dict:
        self.calls.append({"messages": messages, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exception:
            raise self.exception
        if not self.responses:
            raise RuntimeError("FakeRouter: no more responses")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return {
            "content": resp,
            "provider": "fake",
            "model": "fake",
            "input_tokens": 10,
            "output_tokens": 20,
            "latency_ms": 5,
            "tool_calls": None,
        }


def _plan_json(**overrides) -> str:
    base = {
        "intent": "review",
        "goal": "复习错题",
        "actions": [
            {
                "tool_name": "student.read",
                "arguments": {"query": "x"},
                "reason": "查学情",
            }
        ],
        "response_mode": "direct",
        "needs_web_search": False,
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def _student_request() -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT),
        message="今天复习什么",
        scene="student.dashboard",
        client_request_id="crid-1",
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        _tool_def(
            "student.read",
            roles=frozenset({ActorRole.STUDENT}),
            scenes=frozenset({"student.dashboard"}),
        )
    )
    reg.register(
        _tool_def(
            "teacher.grade.read",
            roles=frozenset({ActorRole.TEACHER}),
            scenes=frozenset({"teacher.dashboard"}),
        )
    )
    return reg


async def _run(
    agent: Agent,
    request: ButlerRequest,
    registry: ToolRegistry,
    budget: ButlerBudget | None = None,
) -> object:
    budget = budget or ButlerBudget()
    prompt = build_planning_prompt(request, None, registry)
    deps = ButlerDeps(request=request, snapshot=None, registry=registry, budget=budget)
    return await agent.run(
        prompt,
        deps=deps,
        usage_limits=UsageLimits(request_limit=budget.max_model_requests),
    )


# ---------- 结构化输出 ----------


async def test_legal_json_to_action_plan():
    router = FakeRouter([_plan_json()])
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    result = await _run(agent, _student_request(), _registry())
    assert isinstance(result.output, ActionPlan)
    assert result.output.intent == "review"
    assert result.output.actions[0].tool_name == "student.read"


async def test_markdown_code_block_wrapped_json():
    router = FakeRouter([f"```json\n{_plan_json()}\n```"])
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    result = await _run(agent, _student_request(), _registry())
    assert isinstance(result.output, ActionPlan)


async def test_invalid_json_retried_once():
    router = FakeRouter(["this is not json", _plan_json()])
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    result = await _run(agent, _student_request(), _registry())
    assert isinstance(result.output, ActionPlan)
    assert len(router.calls) == 2  # 修复 1 次


async def test_schema_error_retried_once():
    bad = _plan_json(actions=[{"tool_name": f"t{i}", "reason": "x"} for i in range(6)])
    router = FakeRouter([bad, _plan_json()])
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    result = await _run(agent, _student_request(), _registry())
    assert isinstance(result.output, ActionPlan)
    assert len(router.calls) == 2


# ---------- 失败降级 ----------


async def test_all_models_failed_returns_fallback():
    router = FakeRouter(exception=RuntimeError("upstream boom"))
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    with pytest.raises(RuntimeError):
        await _run(agent, _student_request(), _registry())
    plan = deterministic_fallback_plan(_student_request(), reason="model_unavailable")
    assert isinstance(plan, ActionPlan)
    assert plan.response_mode == "degraded"
    assert plan.actions == []


async def test_timeout_returns_fallback():
    router = FakeRouter(delay=5.0, responses=[_plan_json()])
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await _run(agent, _student_request(), _registry())
    plan = deterministic_fallback_plan(_student_request(), reason="timeout")
    assert plan.response_mode == "degraded"


# ---------- 请求预算 ----------


async def test_request_count_within_three():
    """单次 Planner 请求数受 UsageLimits(request_limit=3) 约束，且 ≤3。"""
    router = FakeRouter(["bad", "also bad", "still bad", "nope"])
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    with pytest.raises(UnexpectedModelBehavior):
        await _run(agent, _student_request(), _registry())
    # retries=1 时最多 2 次；即便模型持续坏，请求数也绝不会超过 3
    assert len(router.calls) <= 3
    assert adapter.request_count <= 3


# ---------- 边界：无 Function Calling / 无 Gateway / 不泄漏 ----------


async def test_no_function_calling():
    router = FakeRouter([_plan_json()])
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    await _run(agent, _student_request(), _registry())
    assert router.calls
    assert all(call["functions"] is None for call in router.calls)


def test_reuses_injected_router_no_gateway():
    router = FakeRouter()
    adapter = ButlerModelAdapter(router)
    assert adapter._router is router  # 不自行创建 Spark/DeepSeek
    assert not hasattr(adapter, "_gateway")


async def test_no_leak_of_model_errors_or_keys():
    """对外输出（fallback 计划）不携带模型异常文本与密钥。"""
    router = FakeRouter(exception=RuntimeError("spark api_key=sk-12345 boom"))
    adapter = ButlerModelAdapter(router)
    agent = build_planner(adapter, _registry(), request=_student_request())
    with pytest.raises(RuntimeError):
        await _run(agent, _student_request(), _registry())
    # 降级计划是唯一对外产物：不含异常文本、不含密钥
    plan = deterministic_fallback_plan(_student_request(), reason="model_unavailable")
    assert "sk-12345" not in plan.model_dump_json()
    assert "boom" not in plan.model_dump_json()
    assert "Traceback" not in plan.model_dump_json()
    # adapter 不保存任何密钥字段
    assert not hasattr(adapter, "_api_key")
    assert not hasattr(adapter, "_secret")


# ---------- 工具可见性 ----------


def test_invisible_tools_not_in_planner_prompt():
    registry = _registry()
    request = _student_request()
    prompt = build_planning_prompt(request, None, registry)
    assert "student.read" in prompt
    assert "teacher.grade.read" not in prompt  # 学生看不到教师工具


def test_deterministic_fallback_plan_shape():
    plan = deterministic_fallback_plan(_student_request())
    assert isinstance(plan, ActionPlan)
    assert plan.response_mode == "degraded"
    assert plan.actions == []
