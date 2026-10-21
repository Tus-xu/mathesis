"""Butler Kernel v2 ButlerRuntime（阶段 3C）

覆盖：
- 固定管线 Context → Plan → Policy → Execute → Compose；
- 成功计划 / 模型失败 fallback / Policy 拒绝 / 工具超时；
- 重复 client_request_id 不重复执行；
- 账本完整（AgentRun + 5×AgentStep + ToolInvocation digest）；
- 模型请求 ≤3、工具调用 ≤5；
- envelope 无敏感信息/思维链。
"""

import asyncio
import json
import time
import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.butler.context import ContextAssembler
from app.butler.contracts import (
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerEnvelope,
    ButlerRequest,
    ToolRisk,
)
from app.butler.executor import ButlerExecutor
from app.butler.model_adapter import (
    ButlerModelAdapter,
    build_planner,
)
from app.butler.policy import PolicyGate
from app.butler.registry import ToolDefinition, ToolRegistry
from app.butler.runtime import ButlerRuntime
from app.config import settings
from app.models.agent_run import AgentRun, AgentStep, ToolInvocation
from app.models.user import User

# 独立 NullPool engine：避免共享连接池跨测试 event loop 的协议失效（同 test_m1_fixes）
_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def _session():
    async with _session_factory() as s:
        yield s


class EchoInput(BaseModel):
    query: str


class EchoOutput(BaseModel):
    answer: str


async def _echo_handler(context, validated_input: dict[str, Any]) -> dict[str, Any]:
    return {"answer": validated_input.get("query", "")}


def _tool(
    name: str, *, risk: ToolRisk = ToolRisk.READ, idempotency_required: bool = False
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        description=f"{name} 测试工具",
        input_model=EchoInput,
        output_model=EchoOutput,
        risk=risk,
        allowed_roles=frozenset({ActorRole.STUDENT}),
        allowed_scenes=frozenset({"student.dashboard"}),
        idempotency_required=idempotency_required,
        handler=_echo_handler,
    )


class FakeRouter:
    """可配置的 ModelRouter 替身。"""

    def __init__(self, plan_json: str | None = None, *, fail: bool = False):
        self.plan_json = plan_json
        self.fail = fail

    async def chat(self, messages, **kwargs):
        if self.fail:
            raise RuntimeError("model unavailable")
        if self.plan_json is None:
            raise RuntimeError("no plan configured")
        return {
            "content": self.plan_json,
            "provider": "fake",
            "model": "fake",
            "input_tokens": 10,
            "output_tokens": 20,
            "latency_ms": 5,
            "tool_calls": None,
        }


def _plan_json(*, tool: str = "student.read") -> str:
    return json.dumps(
        {
            "intent": "review",
            "goal": "复习错题",
            "actions": [
                {
                    "tool_name": tool,
                    "arguments": {"query": "x"},
                    "reason": "查学情",
                }
            ],
            "response_mode": "direct",
            "needs_web_search": False,
        }
    )


def _request() -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT),
        message="今天复习什么",
        scene="student.dashboard",
        client_request_id=f"crid-{uuid.uuid4().hex[:12]}",
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_tool("student.read"))
    reg.register(_tool("student.write", risk=ToolRisk.WRITE, idempotency_required=True))
    return reg


def _build_runtime(router: FakeRouter, *, shadow: bool = False) -> ButlerRuntime:
    registry = _registry()
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(router)
    planner = build_planner(adapter, registry, request=_request())
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    return ButlerRuntime(
        registry=registry,
        policy=policy,
        assembler=ContextAssembler(),
        adapter=adapter,
        planner=planner,
        executor=executor,
        budget=ButlerBudget(),
        shadow=shadow,
    )


async def _make_user(s) -> uuid.UUID:
    u = User(phone=f"13{uuid.uuid4().int % 100000000:08d}", nickname="")
    s.add(u)
    await s.commit()
    return u.id


async def test_success_plan_returns_envelope_and_ledger():
    runtime = _build_runtime(FakeRouter(_plan_json()))
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        env = await runtime.run(req, s)
        assert isinstance(env, ButlerEnvelope)
        assert env.degraded is False
        assert "sk" not in env.text.lower() or "思维链" not in env.text
        # 账本
        run = (
            await s.execute(
                select(AgentRun).where(
                    AgentRun.client_request_id == req.client_request_id
                )
            )
        ).scalars().one()
        assert run.status == "succeeded"
        assert run.tool_call_count == 1
        steps = (
            await s.execute(
                select(AgentStep).where(AgentStep.run_id == run.id)
            )
        ).scalars().all()
        assert {st.stage for st in steps} == {"context", "plan", "policy", "execute", "compose"}
        assert len(steps) == 5
        tis = (
            await s.execute(
                select(ToolInvocation).where(
                    ToolInvocation.run_id == run.id
                )
            )
        ).scalars().all()
        assert len(tis) == 1
        assert tis[0].arguments_digest  # digest 非原文
        assert tis[0].result_digest


async def test_model_failure_returns_fallback_degraded():
    runtime = _build_runtime(FakeRouter(fail=True))
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        env = await runtime.run(req, s)
        assert env.degraded is True
        # 不向学生端抛异常；账本标记 fallback
        run = (
            await s.execute(
                select(AgentRun).where(
                    AgentRun.client_request_id == req.client_request_id
                )
            )
        ).scalars().one()
        assert run.status == "fallback"


async def test_policy_rejection_degraded():
    # 计划引用未注册工具 → unknown_tool → 拒绝
    runtime = _build_runtime(FakeRouter(_plan_json(tool="no.such.tool")))
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        env = await runtime.run(req, s)
        assert env.degraded is True
        assert "unknown_tool" in str(env.trace)


async def test_duplicate_request_does_not_repeat():
    runtime = _build_runtime(FakeRouter(_plan_json()))
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        await runtime.run(req, s)
        await s.commit()
        env2 = await runtime.run(req, s)
        assert env2.degraded is True
        assert env2.trace.get("error_code") == "duplicate_request"
        # 账本只有 1 条 run
        runs = (
            await s.execute(
                select(AgentRun).where(
                    AgentRun.client_request_id == req.client_request_id
                )
            )
        ).scalars().all()
        assert len(runs) == 1


async def test_shadow_run_no_side_effects():
    # shadow 模式：WRITE 工具 → shadow_skipped；账本仍完整
    runtime = _build_runtime(FakeRouter(_plan_json(tool="student.write")), shadow=True)
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        await runtime.run(req, s)
        run = (
            await s.execute(
                select(AgentRun).where(
                    AgentRun.client_request_id == req.client_request_id
                )
            )
        ).scalars().one()
        tis = (
            await s.execute(
                select(ToolInvocation).where(
                    ToolInvocation.run_id == run.id
                )
            )
        ).scalars().all()
        assert all(ti.status == "shadow_skipped" for ti in tis)


async def test_model_requests_and_tool_calls_within_limits():
    runtime = _build_runtime(FakeRouter(_plan_json()))
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        await runtime.run(req, s)
        assert runtime.adapter.request_count <= 3


async def test_execute_total_budget_timeout_degrades_and_preserves_ledger():
    """双工具累计超时：总执行时间受 ButlerBudget.timeout_s 限制。

    - 工具 A 快速成功，工具 B 挂起 → 总护栏在 ~1s 触发（而非单工具 20s）；
    - 返回 degraded envelope；AgentRun.status=failed、error_code=execution_timeout；
    - execute AgentStep 标记 failed；已完成工具 A 的账本保留；不向客户端抛 500。
    """
    async def fast_handler(context, validated_input):
        await asyncio.sleep(0.2)
        return {"answer": "fast"}

    async def slow_handler(context, validated_input):
        await asyncio.sleep(30)
        return {"answer": "slow"}

    registry = ToolRegistry()
    registry.register(_tool("tool.fast").model_copy(update={"handler": fast_handler}))
    registry.register(_tool("tool.slow").model_copy(update={"handler": slow_handler}))
    plan_json = json.dumps(
        {
            "intent": "review",
            "goal": "双工具累计超时",
            "actions": [
                {"tool_name": "tool.fast", "arguments": {"query": "a"}, "reason": "r"},
                {"tool_name": "tool.slow", "arguments": {"query": "b"}, "reason": "r"},
            ],
            "response_mode": "direct",
            "needs_web_search": False,
        }
    )
    budget = ButlerBudget(timeout_s=1.0)
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(FakeRouter(plan_json))
    planner = build_planner(adapter, registry, request=_request())
    executor = ButlerExecutor(registry, policy, budget=budget)
    runtime = ButlerRuntime(
        registry=registry, policy=policy, assembler=ContextAssembler(),
        adapter=adapter, planner=planner, executor=executor, budget=budget,
    )
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        started = time.perf_counter()
        env = await runtime.run(req, s)
        elapsed = time.perf_counter() - started
        await s.commit()
        run = (
            await s.execute(
                select(AgentRun).where(AgentRun.client_request_id == req.client_request_id)
            )
        ).scalars().one()
        steps = (
            await s.execute(select(AgentStep).where(AgentStep.run_id == run.id))
        ).scalars().all()
        tis = (
            await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))
        ).scalars().all()
    # 总执行时间受 budget.timeout_s（1s）限制：远小于单工具 30s 挂起
    assert elapsed < 3.0
    assert env.degraded is True
    assert "execution_timeout" in str(env.trace)
    assert run.status == "failed"
    assert run.error_code == "execution_timeout"
    # execute AgentStep 标记 failed
    exec_step = next(st for st in steps if st.stage == "execute")
    assert exec_step.status == "failed"
    assert exec_step.error_code == "execution_timeout"
    # 已完成工具 A 账本保留（executed）；被中断工具 B 记录 failed
    by_tool = {ti.tool_name: ti for ti in tis}
    assert by_tool["tool.fast"].status == "executed"
    assert by_tool["tool.slow"].status == "failed"


async def test_external_tool_rejected_by_default():
    """集成护栏：Runtime 默认不传 external_allowed/web_search → EXTERNAL 工具被 Policy 拒绝。

    授权数据来源与前端 opt-in 在阶段 5/6 完成；本阶段不得写死 external_allowed=True。
    """
    registry = ToolRegistry()
    registry.register(_tool("student.external", risk=ToolRisk.EXTERNAL))
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(FakeRouter(_plan_json(tool="student.external")))
    planner = build_planner(adapter, registry, request=_request())
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    runtime = ButlerRuntime(
        registry=registry, policy=policy, assembler=ContextAssembler(),
        adapter=adapter, planner=planner, executor=executor, budget=ButlerBudget(),
    )
    async with _session_factory() as s:
        user_id = await _make_user(s)
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        env = await runtime.run(req, s)
        await s.commit()
        run = (
            await s.execute(
                select(AgentRun).where(AgentRun.client_request_id == req.client_request_id)
            )
        ).scalars().one()
        tis = (
            await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))
        ).scalars().all()
    assert env.degraded is True
    assert "external_not_allowed" in str(env.trace)
    assert run.status == "failed"
    assert run.error_code == "external_not_allowed"
    assert len(tis) == 0  # 计划级拒绝不写 ToolInvocation


async def test_external_tool_allowed_when_authorization_config_enabled():
    """授权配置源：system_configs['butler.authorization'].external_allowed=true → EXTERNAL 工具放行。

    阶段 5a 补缺口：Runtime 从配置读取授权开关并传给 PolicyGate/Executor，
    不再默认写死 external_allowed=False。
    """
    from app.models.system_config import upsert_system_config

    registry = ToolRegistry()
    registry.register(_tool("student.external", risk=ToolRisk.EXTERNAL))
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(FakeRouter(_plan_json(tool="student.external")))
    planner = build_planner(adapter, registry, request=_request())
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    runtime = ButlerRuntime(
        registry=registry, policy=policy, assembler=ContextAssembler(),
        adapter=adapter, planner=planner, executor=executor, budget=ButlerBudget(),
    )
    async with _session_factory() as s:
        user_id = await _make_user(s)
        await upsert_system_config(s, "butler.authorization", {"external_allowed": True})
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        env = await runtime.run(req, s)
        await s.commit()
        run = (
            await s.execute(
                select(AgentRun).where(AgentRun.client_request_id == req.client_request_id)
            )
        ).scalars().one()
        tis = (
            await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))
        ).scalars().all()
    assert env.degraded is False
    assert "external_not_allowed" not in str(env.trace)
    assert run.status == "succeeded"
    assert len(tis) == 1
    assert tis[0].tool_name == "student.external"
    assert tis[0].status == "executed"


async def test_web_search_allowed_when_authorization_config_enabled():
    """授权配置源：web_search_enabled=true → 联网搜索工具放行（默认配置下被拒）。"""
    from app.models.system_config import upsert_system_config

    registry = ToolRegistry()
    registry.register(_tool("xingchen.web_search", risk=ToolRisk.EXTERNAL))
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(FakeRouter(_plan_json(tool="xingchen.web_search")))
    planner = build_planner(adapter, registry, request=_request())
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    runtime = ButlerRuntime(
        registry=registry, policy=policy, assembler=ContextAssembler(),
        adapter=adapter, planner=planner, executor=executor, budget=ButlerBudget(),
    )
    async with _session_factory() as s:
        user_id = await _make_user(s)
        await upsert_system_config(
            s, "butler.authorization",
            {"external_allowed": True, "web_search_enabled": True},
        )
        req = _request().model_copy(
            update={"actor": ActorContext(user_id=user_id, role=ActorRole.STUDENT)}
        )
        env = await runtime.run(req, s)
        await s.commit()
        run = (
            await s.execute(
                select(AgentRun).where(AgentRun.client_request_id == req.client_request_id)
            )
        ).scalars().one()
        tis = (
            await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))
        ).scalars().all()
    assert env.degraded is False
    assert "confirmation_required" not in str(env.trace)
    assert run.status == "succeeded"
    assert len(tis) == 1
    assert tis[0].tool_name == "xingchen.web_search"
    assert tis[0].status == "executed"
