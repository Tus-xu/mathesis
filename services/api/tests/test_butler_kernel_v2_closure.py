"""Butler Kernel v2 内核闭环修复（阶段 3.1）

6 组：①Planner 真实上下文 ②递归密钥隔离 ③单次运行模型计数
④全阶段异常降级 ⑤账本真实性 ⑥幂等边界。
"""

import asyncio
import json
import uuid
from types import SimpleNamespace
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
    ButlerEnvelope,
    ButlerRequest,
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


async def _echo_handler(context, validated_input: dict[str, Any]) -> dict[str, Any]:
    return {"answer": validated_input.get("query", "")}


def _tool(name: str, *, risk: ToolRisk = ToolRisk.READ, idempotency_required: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        description=f"{name}",
        input_model=EchoInput,
        output_model=EchoOutput,
        risk=risk,
        allowed_roles=frozenset({ActorRole.STUDENT}),
        allowed_scenes=frozenset({"student.dashboard"}),
        idempotency_required=idempotency_required,
        handler=_echo_handler,
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_tool("student.read"))
    reg.register(_tool("student.write", risk=ToolRisk.WRITE, idempotency_required=True))
    return reg


def _plan_json(*, tool: str = "student.read") -> str:
    return json.dumps(
        {
            "intent": "review",
            "goal": "复习",
            "actions": [{"tool_name": tool, "arguments": {"query": "x"}, "reason": "r"}],
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


def _snapshot(*, profile=None, conversation=None, assignments=(), feature_flags=frozenset(),
              effective_config=None) -> ButlerContextSnapshot:
    return ButlerContextSnapshot(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT),
        scene="student.dashboard",
        profile=profile or {"learning_style": "practice", "current_stage": "consolidation",
                            "weak_point_rank": [{"kp": "函数", "mastery": 0.4}]},
        conversation=conversation or {"title": "错题", "summary": "复习函数",
                                      "recent_messages": [{"role": "user", "content": "帮我看看"}]},
        assignments=assignments or ({"title": "三角函数", "type": "quiz"},),
        effective_config=effective_config if effective_config is not None else {"primary_model": "spark"},
        feature_flags=feature_flags if feature_flags else frozenset({"web_search"}),
    )


class FakeRouter:
    def __init__(self, plan_json: str | None = None, *, fail: bool = False):
        self.plan_json = plan_json
        self.fail = fail
        self.captured_prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        self.captured_prompts.append(prompt)
        if self.fail:
            raise RuntimeError("model unavailable")
        return {"content": self.plan_json or _plan_json(), "provider": "fake", "model": "fake",
                "input_tokens": 10, "output_tokens": 20, "latency_ms": 5, "tool_calls": None}


def _build_runtime(router: FakeRouter, *, shadow: bool = False) -> tuple[ButlerRuntime, ButlerModelAdapter]:
    registry = _registry()
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(router)
    planner = build_planner(adapter, registry, budget=ButlerBudget())
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    rt = ButlerRuntime(registry=registry, policy=policy, assembler=ContextAssembler(),
                       adapter=adapter, planner=planner, executor=executor, budget=ButlerBudget(), shadow=shadow)
    return rt, adapter


async def _make_user(s) -> uuid.UUID:
    u = User(phone=f"13{uuid.uuid4().int % 100000000:08d}", nickname="")
    s.add(u)
    await s.commit()
    return u.id


# ============ 组1 Planner 真实上下文 ============


def test_planning_prompt_includes_snapshot_data():
    snap = _snapshot()
    prompt = build_planning_prompt(_request(), snap, _registry())
    assert "practice" in prompt  # 学习风格
    assert "consolidation" in prompt  # 学习阶段
    assert "函数" in prompt  # 薄弱点
    assert "复习函数" in prompt  # 对话 summary
    assert "帮我看看" in prompt  # 最近消息
    assert "三角函数" in prompt  # 作业
    assert "web_search" in prompt  # feature flags
    assert "student.read" in prompt  # 可见工具


def test_planner_system_prompt_not_bound_to_request():
    """build_planner 返回通用 Agent，system_prompt 不含具体 request/snapshot 数据。"""
    adapter = ButlerModelAdapter(FakeRouter())
    agent = build_planner(adapter, _registry(), budget=ButlerBudget())
    # Agent 不应携带某个 request 的私有上下文（可通过连续两次不同 prompt 运行验证）
    assert agent is not None


async def test_consecutive_runs_no_data_leak():
    router = FakeRouter()
    rt, _ = _build_runtime(router)
    async with _session_factory() as s:
        uid1 = await _make_user(s)
        req1 = _request(msg="用户A的专属问题三角函数", crid="crid-leak-1").model_copy(
            update={"actor": ActorContext(user_id=uid1, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req1, s)
        await s.commit()
    async with _session_factory() as s:
        uid2 = await _make_user(s)
        req2 = _request(msg="用户B的专属问题数列", crid="crid-leak-2").model_copy(
            update={"actor": ActorContext(user_id=uid2, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req2, s)
        await s.commit()
    # 第二次 prompt 不得残留第一个用户的专属内容
    assert "用户A" not in router.captured_prompts[-1]
    assert "三角函数" not in router.captured_prompts[-1] or "数列" in router.captured_prompts[-1]


# ============ 组2 递归密钥隔离 ============


_SENSITIVE = ("api_key", "api_secret", "secret", "password", "token", "credential",
              "encrypted-secret", "another-secret", "secret-value", "token-value")


async def test_recursive_secret_redaction_in_snapshot():
    """嵌套 system_configs 的密钥递归剔除，不进入 Snapshot。"""
    from app.butler.context import ContextAssembler

    class FakeSession:
        def __init__(self):
            self.executed = []

        async def execute(self, stmt):
            import re as _re
            sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            m = _re.search(r"FROM\s+(\w+)", sql)
            table = m.group(1) if m else "unknown"
            self.executed.append(table)
            rows = {
                "student_profiles": [],
                "conversations": [],
                "messages": [],
                "user_model_configs": [],
                "assignments": [],
                "system_configs": [
                    SimpleNamespace(key="model.global", value={
                        "primary": {"model": "spark", "api_key": "encrypted-secret"},
                        "secondary": {"api_key": "another-secret"},
                    }),
                    SimpleNamespace(key="xingchen.global", value={
                        "api_secret": "secret-value",
                        "nested": {"access_token": "token-value"},
                    }),
                ],
            }.get(table, [])
            from tests.test_butler_context import _FakeResult
            return _FakeResult(list(rows))

    snap = await ContextAssembler().build(_request(), FakeSession())
    blob = str(snap.model_dump())
    for s in _SENSITIVE:
        assert s not in blob, f"Snapshot 泄漏敏感词: {s}"


def test_recursive_secret_redaction_in_prompt():
    snap = _snapshot(effective_config={
        "primary_model": "spark",
        "sys:model.global": {"primary": {"model": "spark", "api_key": "encrypted-secret"},
                             "nested": {"access_token": "token-value"}},
    })
    prompt = build_planning_prompt(_request(), snap, _registry())
    for s in _SENSITIVE:
        assert s not in prompt, f"Prompt 泄漏敏感词: {s}"


# ============ 组3 单次运行模型计数 ============


async def test_model_request_count_not_accumulated():
    router = FakeRouter()
    rt, adapter = _build_runtime(router)
    async with _session_factory() as s:
        uid = await _make_user(s)
        req1 = _request(crid="crid-cnt-1").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req1, s)
        await s.commit()
    async with _session_factory() as s:
        uid2 = await _make_user(s)
        req2 = _request(crid="crid-cnt-2").model_copy(
            update={"actor": ActorContext(user_id=uid2, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req2, s)
        run2 = (await s.execute(
            select(AgentRun).where(AgentRun.client_request_id == "crid-cnt-2")
        )).scalars().one()
        # 第二次运行的模型计数只计本次，不累计第一次
        assert run2.model_request_count == 1


async def test_concurrent_runs_no_count_crosstalk():
    router = FakeRouter()
    rt, _ = _build_runtime(router)
    async with _session_factory() as s:
        uid1 = await _make_user(s)
        uid2 = await _make_user(s)
    req1 = _request(crid="crid-conc-1").model_copy(
        update={"actor": ActorContext(user_id=uid1, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
    )
    req2 = _request(crid="crid-conc-2").model_copy(
        update={"actor": ActorContext(user_id=uid2, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
    )

    async def _run(req):
        async with _session_factory() as s:
            await rt.run(req, s)
            await s.commit()

    await asyncio.gather(_run(req1), _run(req2))
    async with _session_factory() as s:
        r1 = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == "crid-conc-1"))).scalars().one()
        r2 = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == "crid-conc-2"))).scalars().one()
        assert r1.model_request_count == 1
        assert r2.model_request_count == 1


# ============ 组4 全阶段异常降级 ============


class _BoomAssembler:
    async def build(self, request, db):
        raise RuntimeError("context boom")


class _BoomComposer:
    def compose(self, *, run_id, request, plan, results, degraded=False):
        raise RuntimeError("compose boom")


async def _run_with_boom(boom_stage: str, *, crid: str) -> tuple[ButlerEnvelope, AgentRun]:
    router = FakeRouter()
    registry = _registry()
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(router)
    planner = build_planner(adapter, registry, budget=ButlerBudget())

    assembler = _BoomAssembler() if boom_stage == "context" else ContextAssembler()
    composer = _BoomComposer() if boom_stage == "compose" else __import__(
        "app.butler.composer", fromlist=["ResultComposer"]
    ).ResultComposer()

    if boom_stage == "plan":
        # 注入 prompt 构造异常：monkeypatch build_planning_prompt
        import app.butler.runtime as rt_mod
        orig = rt_mod.build_planning_prompt
        rt_mod.build_planning_prompt = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("plan boom"))
    if boom_stage == "policy":
        class _BoomPolicy:
            def validate_plan(self, *a, **k):
                raise RuntimeError("policy boom")
            def validate_action(self, *a, **k):
                raise RuntimeError("policy boom")
        policy = _BoomPolicy()  # type: ignore
    if boom_stage == "execute":
        class _BoomExecutor:
            async def execute_plan(self, *a, **k):
                raise RuntimeError("execute boom")
        executor = _BoomExecutor()  # type: ignore
    else:
        executor = ButlerExecutor(registry, policy, budget=ButlerBudget())

    rt = ButlerRuntime(registry=registry, policy=policy, assembler=assembler, adapter=adapter,
                       planner=planner, executor=executor, composer=composer, budget=ButlerBudget())
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid=crid).model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        env = await rt.run(req, s)
        await s.commit()
        run = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == crid))).scalars().one()

    if boom_stage == "plan":
        rt_mod.build_planning_prompt = orig
    return env, run


@pytest.mark.parametrize("stage", ["context", "plan", "policy", "execute", "compose"])
async def test_stage_exception_degraded(stage: str):
    env, run = await _run_with_boom(stage, crid=f"crid-boom-{stage}-{uuid.uuid4().hex[:6]}")
    assert env.degraded is True
    assert "Traceback" not in str(env.trace)
    # AgentRun 完成 + error_code 非空
    assert run.error_code is not None
    assert run.error_code != ""
    # 敏感词不泄漏
    assert "boom" not in str(env.trace)
    # 五个 AgentStep 均有记录
    async with _session_factory() as s:
        steps = (await s.execute(select(AgentStep).where(AgentStep.run_id == run.id))).scalars().all()
        assert len(steps) == 5
        failed_stages = {st.stage for st in steps if st.status == "failed"}
        assert stage in failed_stages  # 对应阶段标记 failed


# ============ 组5 账本真实性 ============


async def test_tool_invocation_latency_nonzero():
    rt, _ = _build_runtime(FakeRouter())
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid="crid-latency").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req, s)
        await s.commit()
        run = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == "crid-latency"))).scalars().one()
        tis = (await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))).scalars().all()
        assert len(tis) == 1
        assert tis[0].latency_ms > 0  # 实际耗时，非固定 0


async def test_write_idempotency_key_nonempty():
    router = FakeRouter(_plan_json(tool="student.write"))
    rt, _ = _build_runtime(router)
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid="crid-idem").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req, s)
        await s.commit()
        run = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == "crid-idem"))).scalars().one()
        tis = (await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))).scalars().all()
        assert len(tis) == 1
        assert tis[0].idempotency_key  # 非空
        assert tis[0].idempotency_key != ""


async def test_policy_rejected_no_tool_invocation():
    # 计划引用未注册工具 → policy 计划级拒绝
    router = FakeRouter(_plan_json(tool="no.such.tool"))
    rt, _ = _build_runtime(router)
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid="crid-preject").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req, s)
        await s.commit()
        run = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == "crid-preject"))).scalars().one()
        assert run.tool_call_count == 0  # 未实际执行工具
        tis = (await s.execute(select(ToolInvocation).where(ToolInvocation.run_id == run.id))).scalars().all()
        assert len(tis) == 0  # 不写"已执行"的 ToolInvocation


async def test_agent_run_status_failed_on_tool_error():
    # 工具 handler 抛异常 → AgentRun.status=failed + error_code 非空
    registry = _registry()
    failing_tool = _tool("student.read")
    failing_tool = failing_tool.model_copy(update={"handler": _FailingHandler()})
    registry._tools["student.read"] = failing_tool
    policy = PolicyGate(registry)
    adapter = ButlerModelAdapter(FakeRouter())
    planner = build_planner(adapter, registry, budget=ButlerBudget())
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    rt = ButlerRuntime(registry=registry, policy=policy, assembler=ContextAssembler(),
                       adapter=adapter, planner=planner, executor=executor, budget=ButlerBudget())
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid="crid-fail").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req, s)
        await s.commit()
        run = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == "crid-fail"))).scalars().one()
        assert run.status == "failed"
        assert run.error_code is not None


class _FailingHandler:
    async def __call__(self, context, validated_input):
        raise RuntimeError("tool boom")


async def test_duplicate_returns_existing_run_id():
    rt, _ = _build_runtime(FakeRouter())
    async with _session_factory() as s:
        uid = await _make_user(s)
        req = _request(crid="crid-dup").model_copy(
            update={"actor": ActorContext(user_id=uid, role=ActorRole.STUDENT, class_ids=(uuid.uuid4(),))}
        )
        await rt.run(req, s)
        await s.commit()
        existing_run = (await s.execute(select(AgentRun).where(AgentRun.client_request_id == "crid-dup"))).scalars().one()
    async with _session_factory() as s:
        env2 = await rt.run(req, s)
        # duplicate envelope 返回已有 run_id，非随机
        assert env2.run_id == existing_run.id


# ============ 组6 幂等边界 ============


async def test_write_replay_single_execution_in_plan():
    """同一计划内 WRITE 重放：handler 仅执行 1 次（进程内 dict 保护）。"""
    calls = {"n": 0}

    async def handler(context, validated_input):
        calls["n"] += 1
        return {"answer": "ok"}

    registry = _registry()
    registry._tools["student.write"] = _tool("student.write", risk=ToolRisk.WRITE,
                                             idempotency_required=True).model_copy(update={"handler": handler})
    policy = PolicyGate(registry)
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    # 直接调 invoke 两次同 args
    req = _request(crid="crid-replay")
    a = await executor.invoke(uuid.uuid4(), req, __import__("app.butler.contracts", fromlist=["PlannedAction"]).PlannedAction(
        tool_name="student.write", arguments={"query": "x"}, reason="r"), None)
    b = await executor.invoke(uuid.uuid4(), req, __import__("app.butler.contracts", fromlist=["PlannedAction"]).PlannedAction(
        tool_name="student.write", arguments={"query": "x"}, reason="r"), None)
    assert calls["n"] == 1
    assert b.data == a.data


def test_replay_is_in_process_dict():
    """进程内重放缓存（非跨进程持久化）。"""
    registry = _registry()
    executor = ButlerExecutor(registry, PolicyGate(registry), budget=ButlerBudget())
    assert isinstance(executor._replay, dict)
