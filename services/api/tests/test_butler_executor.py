"""Butler Kernel v2 Executor（阶段 3B）

覆盖：
- 先 Policy 后执行：Policy 拒绝时 handler 不执行；
- 输入/输出 Schema 校验（前后 Registry 校验）；
- 每个工具独立超时 → degraded ToolResult；
- 总工具数 ≤5（budget 防御）；
- WRITE 幂等重放：同幂等键只执行一次 handler；
- 不同用户相同 client_request_id 不冲突；
- Shadow 模式 WRITE/EXTERNAL 返回 shadow_skipped，handler 调用 0 次；
- EXTERNAL 失败 → degraded；
- 异常不泄漏堆栈/SQL/文件路径/密钥。
"""

import asyncio
import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from app.butler.contracts import (
    ActionPlan,
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerRequest,
    PlannedAction,
    ToolResult,
    ToolRisk,
)
from app.butler.executor import ButlerExecutor, ToolExecutionContext
from app.butler.policy import PolicyGate
from app.butler.registry import ToolDefinition, ToolRegistry


class EchoInput(BaseModel):
    query: str
    limit: int = 3


class EchoOutput(BaseModel):
    answer: str


class CountingHandler:
    """可计数的 handler（验证执行次数）。"""

    def __init__(self, *, fail: Exception | None = None, delay: float = 0.0):
        self.count = 0
        self.fail = fail
        self.delay = delay

    async def __call__(self, context: ToolExecutionContext, validated_input: dict[str, Any]) -> dict[str, Any]:
        self.count += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        return {"answer": validated_input.get("query", "")}


def _tool(
    name: str,
    *,
    roles: frozenset[ActorRole],
    scenes: frozenset[str],
    risk: ToolRisk = ToolRisk.READ,
    idempotency_required: bool = False,
    handler: Any = None,
    timeout_s: float = 20.0,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        description=f"{name} 测试工具",
        input_model=EchoInput,
        output_model=EchoOutput,
        risk=risk,
        allowed_roles=roles,
        allowed_scenes=scenes,
        idempotency_required=idempotency_required,
        handler=handler or CountingHandler(),
        timeout_s=timeout_s,
    )


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        _tool(
            "student.read",
            roles=frozenset({ActorRole.STUDENT}),
            scenes=frozenset({"student.dashboard"}),
        )
    )
    reg.register(
        _tool(
            "teacher.grade.read",
            roles=frozenset({ActorRole.TEACHER}),
            scenes=frozenset({"teacher.dashboard"}),
        )
    )
    reg.register(
        _tool(
            "test.write",
            roles=frozenset({ActorRole.STUDENT}),
            scenes=frozenset({"student.dashboard"}),
            risk=ToolRisk.WRITE,
            idempotency_required=True,
        )
    )
    reg.register(
        _tool(
            "test.external",
            roles=frozenset({ActorRole.STUDENT}),
            scenes=frozenset({"student.dashboard"}),
            risk=ToolRisk.EXTERNAL,
        )
    )
    return reg


@pytest.fixture
def executor(registry: ToolRegistry) -> ButlerExecutor:
    policy = PolicyGate(registry)
    return ButlerExecutor(registry, policy, budget=ButlerBudget())


@pytest.fixture
def butler_request() -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT),
        message="今天复习什么",
        scene="student.dashboard",
        client_request_id="crid-1",
    )


def _action(tool_name: str, **args) -> PlannedAction:
    return PlannedAction(tool_name=tool_name, arguments={"query": "x", **args}, reason="test")


def _plan(*tool_names: str) -> ActionPlan:
    return ActionPlan(
        intent="review",
        goal="复习",
        actions=[_action(t) for t in tool_names],
        response_mode="cards",
    )


def _ctx(executor: ButlerExecutor, butler_request: ButlerRequest, run_id: uuid.UUID | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=run_id or uuid.uuid4(),
        butler_request=butler_request,
        db=None,  # 本阶段 handler 协议不强制使用 db
        idempotency_key="",
    )


# ---------- Policy 先于执行 ----------


async def test_policy_denied_handler_not_called(executor: ButlerExecutor, butler_request: ButlerRequest, registry: ToolRegistry):
    handler = registry.get("teacher.grade.read").handler
    result = await executor.invoke(uuid.uuid4(), butler_request, _action("teacher.grade.read"), None)
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert result.error_code == "role_denied"
    assert handler.count == 0


# ---------- Schema 校验 ----------


async def test_input_schema_validated(executor: ButlerExecutor, butler_request: ButlerRequest, registry: ToolRegistry):
    handler = registry.get("student.read").handler
    bad = PlannedAction(tool_name="student.read", arguments={"query": 123}, reason="x")
    result = await executor.invoke(uuid.uuid4(), butler_request, bad, None)
    assert result.ok is False
    assert result.error_code == "invalid_arguments"
    assert handler.count == 0


async def test_output_schema_validated(executor: ButlerExecutor, butler_request: ButlerRequest, registry: ToolRegistry):
    # handler 返回与 EchoOutput 不符的 shape → invalid_output
    class BadOut:
        async def __call__(self, context, validated_input):
            return {"wrong_key": 1}

    registry._tools["student.read"] = registry._tools["student.read"].model_copy(
        update={"handler": BadOut()}
    )
    result = await executor.invoke(uuid.uuid4(), butler_request, _action("student.read"), None)
    assert result.ok is False
    assert result.error_code == "invalid_output"


# ---------- 超时 ----------


async def test_tool_timeout_degraded(registry: ToolRegistry, butler_request: ButlerRequest):
    slow = CountingHandler(delay=1.0)
    registry._tools["test.external"] = _tool(
        "test.external",
        roles=frozenset({ActorRole.STUDENT}),
        scenes=frozenset({"student.dashboard"}),
        risk=ToolRisk.EXTERNAL,
        handler=slow,
        timeout_s=0.1,
    )
    policy = PolicyGate(registry)
    ex = ButlerExecutor(registry, policy, budget=ButlerBudget())
    result = await ex.invoke(
        uuid.uuid4(), butler_request, _action("test.external"), None, external_allowed=True
    )
    assert result.ok is False
    assert result.error_code == "tool_timeout"
    assert result.degraded is True


# ---------- 预算 ----------


async def test_execute_plan_budget_enforced(executor: ButlerExecutor, butler_request: ButlerRequest):
    plan = _plan("student.read", "student.read", "student.read")
    results = await executor.execute_plan(uuid.uuid4(), butler_request, plan, None, budget=ButlerBudget(max_tool_calls=2))
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error_code == "budget_exceeded"


async def test_execute_plan_success(executor: ButlerExecutor, butler_request: ButlerRequest):
    plan = _plan("student.read", "student.read")
    results = await executor.execute_plan(uuid.uuid4(), butler_request, plan, None)
    assert len(results) == 2
    assert all(r.ok for r in results)


# ---------- WRITE 幂等 ----------


async def test_write_idempotent_replay_single_execution(
    executor: ButlerExecutor, butler_request: ButlerRequest, registry: ToolRegistry
):
    handler = registry.get("test.write").handler
    run_id = uuid.uuid4()
    a = await executor.invoke(run_id, butler_request, _action("test.write"), None)
    b = await executor.invoke(run_id, butler_request, _action("test.write"), None)
    assert a.ok is True
    assert b.data == a.data  # 重放已记录结果
    assert handler.count == 1


async def test_different_users_same_client_request_id_no_conflict(
    executor: ButlerExecutor, butler_request: ButlerRequest, registry: ToolRegistry
):
    handler = registry.get("test.write").handler
    other = butler_request.model_copy(
        update={"actor": ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT)}
    )
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    await executor.invoke(run_a, butler_request, _action("test.write"), None)
    await executor.invoke(run_b, other, _action("test.write"), None)
    assert handler.count == 2  # 不同用户幂等键不冲突


async def test_replay_latency_not_inherited_from_first_execution(
    executor: ButlerExecutor, butler_request: ButlerRequest, registry: ToolRegistry
):
    """重放不得继承首次执行的 handler 耗时：replayed 的 latency_ms 必须为 0。

    首次 executed 真实执行过 → latency_ms > 0；第二次 replayed 未进 handler，
    不得复制首次执行耗时（账本真实性：replayed 无实际执行）。
    """
    handler = registry.get("test.write").handler
    run_id = uuid.uuid4()
    first = await executor.invoke(run_id, butler_request, _action("test.write"), None)
    second = await executor.invoke(run_id, butler_request, _action("test.write"), None)

    assert first.execution_status == "executed"
    assert first.latency_ms > 0, "首次真实执行必须记录实际耗时"
    assert second.execution_status == "replayed"
    assert second.latency_ms == 0, "重放未进 handler，不得继承首次执行耗时"
    assert second.latency_ms != first.latency_ms
    # 幂等语义不变：data / idempotency_key / digest 一致
    assert second.data == first.data
    assert second.idempotency_key == first.idempotency_key
    assert handler.count == 1


# ---------- Shadow ----------


async def test_shadow_no_side_effects(executor: ButlerExecutor, butler_request: ButlerRequest, registry: ToolRegistry):
    write_handler = registry.get("test.write").handler
    external_handler = registry.get("test.external").handler
    plan = _plan("test.write", "test.external")
    results = await executor.execute_plan(
        uuid.uuid4(), butler_request, plan, None, shadow=True, external_allowed=True
    )
    assert len(results) == 2
    assert all(r.error_code == "shadow_skipped" for r in results)
    assert write_handler.count == 0
    assert external_handler.count == 0


# ---------- EXTERNAL 失败降级 ----------


async def test_external_failure_degraded(registry: ToolRegistry, butler_request: ButlerRequest):
    failing = CountingHandler(fail=RuntimeError("external provider down"))
    registry._tools["test.external"] = _tool(
        "test.external",
        roles=frozenset({ActorRole.STUDENT}),
        scenes=frozenset({"student.dashboard"}),
        risk=ToolRisk.EXTERNAL,
        handler=failing,
    )
    policy = PolicyGate(registry)
    ex = ButlerExecutor(registry, policy, budget=ButlerBudget())
    result = await ex.invoke(
        uuid.uuid4(), butler_request, _action("test.external"), None, external_allowed=True
    )
    assert result.ok is False
    assert result.degraded is True
    assert result.retryable is True


# ---------- 异常不泄漏 ----------


async def test_exception_no_leak(registry: ToolRegistry, butler_request: ButlerRequest):
    leaky = CountingHandler(fail=RuntimeError("db error at D:/secret/path.sql: key=sk-999"))
    registry._tools["student.read"] = _tool(
        "student.read",
        roles=frozenset({ActorRole.STUDENT}),
        scenes=frozenset({"student.dashboard"}),
        handler=leaky,
    )
    policy = PolicyGate(registry)
    ex = ButlerExecutor(registry, policy, budget=ButlerBudget())
    result = await ex.invoke(uuid.uuid4(), butler_request, _action("student.read"), None)
    assert result.ok is False
    assert result.error_code == "tool_error"
    assert "D:/secret/path.sql" not in (result.user_message or "")
    assert "sk-999" not in (result.user_message or "")
    assert "Traceback" not in (result.user_message or "")
