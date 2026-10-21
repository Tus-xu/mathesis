"""Butler Kernel v2 阶段 3.2：账本语义与 Planner 工具摘要收口。

4 组：①Run ID 单一事实源 ②Policy 拒绝账本状态
③实际工具调用计数 ④Planner 可见工具摘要。
"""

import asyncio
import json
import uuid
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.butler.context import ContextAssembler
from app.butler.contracts import (
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerContextSnapshot,
    ButlerRequest,
    PlannedAction,
    ToolRisk,
)
from app.butler.executor import ButlerExecutor
from app.butler.model_adapter import (
    ButlerModelAdapter,
    build_planner,
    build_planning_prompt,
)
from app.butler.policy import PolicyGate
from app.butler.registry import ToolDefinition, ToolRegistry
from app.butler.runtime import ButlerRuntime
from app.config import settings
from app.models.agent_run import AgentRun, AgentStep, ToolInvocation
from app.models.user import User

_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


class EchoInput(BaseModel):
    query: str


class EchoOutput(BaseModel):
    answer: str


class _CountingHandler:
    """计数 handler：记录进入次数（含进入后异常/超时的调用）。"""

    def __init__(self, *, fail: bool = False, sleep: float = 0.0, bad_output: bool = False):
        self.calls = 0
        self.fail = fail
        self.sleep = sleep
        self.bad_output = bad_output

    async def __call__(self, context, validated_input: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.fail:
            raise RuntimeError("tool boom")
        if self.bad_output:
            return {"wrong_field": 1}
        return {"answer": validated_input.get("query", "")}


def _tool(
    name: str,
    *,
    description: str = "测试工具",
    risk: ToolRisk = ToolRisk.READ,
    idempotency_required: bool = False,
    handler: Any = None,
    roles: frozenset[ActorRole] = frozenset({ActorRole.STUDENT}),
    scenes: frozenset[str] = frozenset({"student.dashboard"}),
    timeout_s: float = 20.0,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        description=description,
        input_model=EchoInput,
        output_model=EchoOutput,
        risk=risk,
        allowed_roles=roles,
        allowed_scenes=scenes,
        timeout_s=timeout_s,
        idempotency_required=idempotency_required,
        handler=handler or _CountingHandler(),
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_tool("student.read", description="读取学生掌握度与薄弱知识点画像"))
    reg.register(
        _tool("student.write", risk=ToolRisk.WRITE, idempotency_required=True,
              description="保存学生学习笔记")
    )
    return reg


def _plan_json(*tools: str) -> str:
    """按工具名列表生成计划 JSON（默认单工具）。"""
    names = tools or ("student.read",)
    return json.dumps(
        {
            "intent": "review",
            "goal": "复习",
            "actions": [
                {"tool_name": t, "arguments": {"query": "x"}, "reason": "r"} for t in names
            ],
            "response_mode": "direct",
            "needs_web_search": False,
        }
    )


def _request(*, msg: str = "今天复习什么", crid: str | None = None) -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),)),
        message=msg,
        scene="student.dashboard",
        client_request_id=crid or f"crid-{uuid.uuid4().hex[:8]}",
    )


def _snapshot() -> ButlerContextSnapshot:
    return ButlerContextSnapshot(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT),
        scene="student.dashboard",
        profile={"learning_style": "practice", "current_stage": "consolidation",
                 "weak_point_rank": [{"kp": "函数", "mastery": 0.4}]},
        conversation={"title": "错题", "summary": "复习函数",
                      "recent_messages": [{"role": "user", "content": "帮我看看"}]},
        assignments=({"title": "三角函数", "type": "quiz"},),
        effective_config={"primary_model": "spark"},
        feature_flags=frozenset({"web_search"}),
    )


class FakeRouter:
    def __init__(self, plan_json: str | None = None):
        self.plan_json = plan_json

    async def chat(self, messages, **kwargs):
        return {"content": self.plan_json or _plan_json(), "provider": "fake", "model": "fake",
                "input_tokens": 10, "output_tokens": 20, "latency_ms": 5, "tool_calls": None}


def _build_runtime(
    router: FakeRouter,
    *,
    registry: ToolRegistry | None = None,
    shadow: bool = False,
    handlers: dict[str, _CountingHandler] | None = None,
) -> tuple[ButlerRuntime, dict[str, _CountingHandler]]:
    """构建 Runtime；handlers 返回各工具的计数 handler 供断言。"""
    reg = registry or _registry()
    handlers = handlers if handlers is not None else {}
    if registry is None:
        # 注入计数 handler 到默认注册表
        for name in ("student.read", "student.write"):
            tool = reg.get(name)
            h = _CountingHandler()
            handlers[name] = h
            reg._tools[name] = tool.model_copy(update={"handler": h})
    policy = PolicyGate(reg)
    adapter = ButlerModelAdapter(router)
    planner = build_planner(adapter, reg, budget=ButlerBudget())
    executor = ButlerExecutor(reg, policy, budget=ButlerBudget())
    rt = ButlerRuntime(registry=reg, policy=policy, assembler=ContextAssembler(),
                       adapter=adapter, planner=planner, executor=executor,
                       budget=ButlerBudget(), shadow=shadow)
    return rt, handlers


async def _make_user(s) -> uuid.UUID:
    u = User(phone=f"13{uuid.uuid4().int % 100000000:08d}", nickname="")
    s.add(u)
    await s.commit()
    return u.id


async def _run_once(
    rt: ButlerRuntime, *, crid: str
) -> tuple[Any, Any, Any]:
    """跑一次完整 run 并返回 (AgentRun, [AgentStep], [ToolInvocation])。"""
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid=crid).model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req, s)
        await s.commit()
        run = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == crid))).scalars().one()
        steps = (await s.execute(select(AgentStep).where(AgentStep.run_id == run.id))).scalars().all()
        tis = (await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))).scalars().all()
    return run, steps, tis


# ============ 组1 Run ID 单一事实源 ============


async def test_run_id_single_source_of_truth():
    """first envelope.run_id == ledger.id == duplicate.run_id。"""
    rt, _ = _build_runtime(FakeRouter())
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid="crid-runid-32").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        env1 = await rt.run(req, s)
        await s.commit()
        ledger = (await s.execute(
            select(AgentRun).where(AgentRun.client_request_id == "crid-runid-32")
        )).scalars().one()
        steps = (await s.execute(select(AgentStep).where(AgentStep.run_id == ledger.id))).scalars().all()
        tis = (await s.execute(
            select(ToolInvocation).where(ToolInvocation.run_id == ledger.id)
        )).scalars().all()
    async with _session_factory() as s:
        env2 = await rt.run(req, s)
    # 三者同一事实源
    assert env1.run_id == ledger.id, "envelope.run_id 必须等于 AgentRun.id"
    assert env2.run_id == ledger.id, "duplicate envelope 必须返回已有 run_id"
    # 账本外键均指向该 run
    assert len(steps) == 5 and all(st.run_id == ledger.id for st in steps)
    assert len(tis) == 1 and all(ti.run_id == ledger.id for ti in tis)


# ============ 组2 Policy 拒绝账本状态 ============


def _rejection_registry() -> tuple[ToolRegistry, dict[str, _CountingHandler]]:
    reg = _registry()
    handlers = {}
    for name, kwargs in (
        ("teacher.only.tool", {"roles": frozenset({ActorRole.TEACHER}),
                               "description": "教师专用工具"}),
        ("other.scene.tool", {"scenes": frozenset({"student.practice"}),
                              "description": "练习场景工具"}),
    ):
        h = _CountingHandler()
        handlers[name] = h
        reg.register(_tool(name, handler=h, **kwargs))
    return reg, handlers


@pytest.mark.parametrize(
    "planned_tool,expected_code",
    [
        ("no.such.tool", "unknown_tool"),
        ("teacher.only.tool", "role_denied"),
        ("other.scene.tool", "scene_denied"),
    ],
)
async def test_policy_rejection_ledger_status(planned_tool: str, expected_code: str):
    """计划级拒绝：AgentRun/AgentStep 状态、handler 0 次、无 ToolInvocation。"""
    reg, handlers = _rejection_registry()
    # visible 工具也换计数 handler，确认可见工具也未被调用
    for name in ("student.read", "student.write"):
        h = _CountingHandler()
        handlers[name] = h
        reg._tools[name] = reg.get(name).model_copy(update={"handler": h})
    rt, _ = _build_runtime(FakeRouter(_plan_json(planned_tool)), registry=reg)

    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid=f"crid-prej-{expected_code}").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        env = await rt.run(req, s)
        await s.commit()
        run = (await s.execute(
            select(AgentRun).where(AgentRun.client_request_id == f"crid-prej-{expected_code}")
        )).scalars().one()
        steps = (await s.execute(select(AgentStep).where(AgentStep.run_id == run.id))).scalars().all()
        tis = (await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))).scalars().all()

    # AgentRun：failed + 具体拒绝码
    assert run.status == "failed", "Policy 拒绝不得记 succeeded"
    assert run.error_code == expected_code
    assert run.tool_call_count == 0
    # Policy AgentStep：failed + 具体拒绝码
    policy_step = next(st for st in steps if st.stage == "policy")
    assert policy_step.status == "failed"
    assert policy_step.error_code == expected_code
    # 未实际执行任何 handler
    assert all(h.calls == 0 for h in handlers.values())
    # 无 ToolInvocation；envelope 降级且 trace 含稳定拒绝码
    assert len(tis) == 0
    assert env.degraded is True
    assert expected_code in str(env.trace)


# ============ 组3 实际工具调用计数 ============


async def test_tool_call_count_normal_success():
    """正常成功：handler 1 次、tool_call_count=1、ToolInvocation=executed。"""
    rt, handlers = _build_runtime(FakeRouter(_plan_json("student.read")))
    run, steps, tis = await _run_once(rt, crid="crid-cnt-normal")
    assert handlers["student.read"].calls == 1
    assert run.tool_call_count == 1
    assert len(tis) == 1
    assert tis[0].status == "executed"


async def test_tool_call_count_shadow_skipped():
    """shadow：handler 0 次、tool_call_count=0，保留 shadow_skipped 审计记录。"""
    rt, handlers = _build_runtime(FakeRouter(_plan_json("student.write")), shadow=True)
    run, steps, tis = await _run_once(rt, crid="crid-cnt-shadow")
    assert handlers["student.write"].calls == 0
    assert run.tool_call_count == 0
    assert len(tis) == 1
    assert tis[0].status == "shadow_skipped"


async def test_tool_call_count_write_replay():
    """同一计划两次相同 WRITE：handler 1 次、tool_call_count=1、replayed 有审计。"""
    rt, handlers = _build_runtime(FakeRouter(_plan_json("student.write", "student.write")))
    run, steps, tis = await _run_once(rt, crid="crid-cnt-replay")
    assert handlers["student.write"].calls == 1, "重放不得再次进入 handler"
    assert run.tool_call_count == 1, "重放不得虚增实际调用次数"
    assert len(tis) == 2
    assert tis[0].status == "executed"
    assert tis[1].status == "replayed"


async def test_tool_call_count_handler_error():
    """handler 异常：已实际调用，计数准确。"""
    reg = _registry()
    h = _CountingHandler(fail=True)
    reg._tools["student.read"] = reg.get("student.read").model_copy(update={"handler": h})
    rt, _ = _build_runtime(FakeRouter(_plan_json("student.read")), registry=reg)
    run, steps, tis = await _run_once(rt, crid="crid-cnt-error")
    assert h.calls == 1
    assert run.tool_call_count == 1
    assert tis[0].status == "failed"
    assert run.error_code == "tool_error"


async def test_tool_call_count_timeout():
    """timeout：handler 已进入，计数准确。"""
    reg = _registry()
    h = _CountingHandler(sleep=0.3)
    reg._tools["student.read"] = reg.get("student.read").model_copy(
        update={"handler": h, "timeout_s": 0.05}
    )
    rt, _ = _build_runtime(FakeRouter(_plan_json("student.read")), registry=reg)
    run, steps, tis = await _run_once(rt, crid="crid-cnt-timeout")
    assert h.calls == 1
    assert run.tool_call_count == 1
    assert tis[0].status == "failed"
    assert tis[0].error_code == "tool_timeout"


async def test_tool_call_count_invalid_output():
    """invalid_output：handler 已调用，计数准确。"""
    reg = _registry()
    h = _CountingHandler(bad_output=True)
    reg._tools["student.read"] = reg.get("student.read").model_copy(update={"handler": h})
    rt, _ = _build_runtime(FakeRouter(_plan_json("student.read")), registry=reg)
    run, steps, tis = await _run_once(rt, crid="crid-cnt-invout")
    assert h.calls == 1
    assert run.tool_call_count == 1
    assert tis[0].status == "failed"
    assert tis[0].error_code == "invalid_output"


async def test_tool_result_execution_status_metadata():
    """ToolResult 显式携带 executed/replayed/shadow_skipped/not_executed 元数据。"""
    reg = _registry()
    h = _CountingHandler()
    reg._tools["student.write"] = reg.get("student.write").model_copy(update={"handler": h})
    policy = PolicyGate(reg)
    executor = ButlerExecutor(reg, policy, budget=ButlerBudget())
    req = _request(crid="crid-execmeta")

    action = PlannedAction(tool_name="student.write", arguments={"query": "x"}, reason="r")
    # 正常执行 → executed
    r1 = await executor.invoke(uuid.uuid4(), req, action, None)
    assert r1.execution_status == "executed"
    # 重放 → replayed
    r2 = await executor.invoke(uuid.uuid4(), req, action, None)
    assert r2.execution_status == "replayed"
    # shadow → shadow_skipped
    r3 = await executor.invoke(uuid.uuid4(), req, action, None, shadow=True)
    assert r3.execution_status == "shadow_skipped"
    # 动作级拒绝（角色不允许）→ not_executed
    teacher_req = req.model_copy(update={
        "actor": ActorContext(user_id=uuid.uuid4(), role=ActorRole.TEACHER)
    })
    r4 = await executor.invoke(uuid.uuid4(), teacher_req, action, None)
    assert r4.execution_status == "not_executed"
    # 计数语义：executed 才算实际调用
    statuses = [r1.execution_status, r2.execution_status, r3.execution_status, r4.execution_status]
    assert statuses.count("executed") == 1


# ============ 组4 Planner 可见工具摘要 ============


def _summary_registry() -> ToolRegistry:
    reg = _registry()
    reg.register(_tool(
        "teacher.grade.read",
        roles=frozenset({ActorRole.TEACHER}),
        description="教师批改成绩查询",
    ))
    reg.register(_tool(
        "other.scene.tool",
        scenes=frozenset({"student.practice"}),
        description="练习场景专用工具",
    ))
    return reg


def test_planner_tool_summary_contains_name_description_fields():
    """可见工具摘要至少包含 name、description、输入字段摘要。"""
    prompt = build_planning_prompt(_request(), _snapshot(), _summary_registry())
    assert "student.read" in prompt
    assert "读取学生掌握度与薄弱知识点画像" in prompt  # description
    assert "query" in prompt  # 输入字段摘要
    assert "student.write" in prompt
    assert "保存学生学习笔记" in prompt


def test_planner_tool_summary_excludes_invisible_tools():
    """不可见工具的名称、描述均不得进入 Prompt。"""
    prompt = build_planning_prompt(_request(), _snapshot(), _summary_registry())
    assert "teacher.grade.read" not in prompt
    assert "教师批改成绩查询" not in prompt
    assert "other.scene.tool" not in prompt
    assert "练习场景专用工具" not in prompt


def test_planner_tool_summary_no_handler_secrets_or_full_schema():
    """摘要不含 handler、内部类路径、完整 JSON Schema。"""
    prompt = build_planning_prompt(_request(), _snapshot(), _summary_registry())
    assert "handler" not in prompt
    assert "ToolDefinition" not in prompt
    assert "_CountingHandler" not in prompt
    assert '"properties"' not in prompt
    assert '"type"' not in prompt


def test_planner_tool_summary_bounded():
    """多工具长描述下 Prompt 总长 ≤4000。"""
    reg = _summary_registry()
    for i in range(20):
        reg.register(_tool(
            f"student.bulk.{i}",
            description="很长的工具描述" * 40,  # 320 字符
        ))
    prompt = build_planning_prompt(_request(), _snapshot(), reg)
    assert len(prompt) <= 4000
    # 每个可见工具单行摘要有界（截断生效）
    assert "很长的工具描述" in prompt  # 仍含截断后的描述
    assert "很长的工具描述" * 40 not in prompt  # 但不含完整长文


def test_planner_system_prompt_static_generic():
    """system_prompt 保持静态通用，不绑定用户或 Snapshot。"""
    from app.butler.model_adapter import _DEFAULT_PLANNER_PROMPT

    adapter = ButlerModelAdapter(FakeRouter())
    agent = build_planner(adapter, _summary_registry(), budget=ButlerBudget())
    assert agent is not None
    # 静态提示不含任何用户/snapshot 专属内容与工具清单
    for token in ("student.read", "函数", "三角函数", "practice", "错题"):
        assert token not in _DEFAULT_PLANNER_PROMPT
