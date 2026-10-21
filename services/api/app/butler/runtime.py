"""Butler Kernel v2 ButlerRuntime（设计规格 §5 + 阶段 3C）

固定管线：Context → Plan → Policy → Execute → Compose，每阶段记录 AgentStep。

- 模型失败 / 超时 / 修复失败 → deterministic fallback（degraded），不向学生端抛异常；
- Policy 拒绝 → 拒绝 envelope（不静默越权）；
- 重复 client_request_id（账本唯一约束）→ duplicate envelope，不重复执行；
- 模型请求数受 UsageLimits(request_limit=max_model_requests) 约束，工具调用 ≤5；
- Shadow 模式 WRITE/EXTERNAL 一律 shadow_skipped；
- 账本只存脱敏摘要（digest），不存密钥/完整文本/思维链。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.butler.composer import ResultComposer
from app.butler.config import resolve_butler_authorization
from app.butler.context import ContextAssembler, context_assembler
from app.butler.contracts import (
    ActionPlan,
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerContextSnapshot,
    ButlerEnvelope,
    ButlerRequest,
    ToolResult,
)
from app.butler.executor import ButlerExecutor, _digest
from app.butler.model_adapter import (
    ButlerDeps,
    ButlerModelAdapter,
    build_planning_prompt,
    deterministic_fallback_plan,
)
from app.butler.policy import PolicyDecision, PolicyGate
from app.butler.registry import ToolRegistry
from app.models.agent_run import AgentRun, AgentStep, ToolInvocation
from app.models.database import background_session_factory

STAGES = ("context", "plan", "policy", "execute", "compose")


class ButlerRuntime:
    """统一入口：组装上下文 → 计划 → 校验 → 执行 → 组合，并完整记账。"""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyGate,
        assembler: ContextAssembler | None = None,
        adapter: ButlerModelAdapter | None = None,
        planner: Agent[ButlerDeps, ActionPlan] | None = None,
        executor: ButlerExecutor | None = None,
        composer: ResultComposer | None = None,
        budget: ButlerBudget | None = None,
        shadow: bool = False,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._assembler = assembler or context_assembler
        self.adapter = adapter
        self._planner = planner
        self._executor = executor
        self._composer = composer or ResultComposer()
        self._budget = budget or ButlerBudget()
        self._shadow = shadow

    async def run(
        self,
        request: ButlerRequest,
        db: AsyncSession,
        *,
        fixed_plan: ActionPlan | None = None,
    ) -> ButlerEnvelope:
        """执行一次完整管线。

        fixed_plan（stu-r2 管线通电）：调用方（chat_bridge / butler_router）对数据类
        意图构造的确定性 ActionPlan。提供时跳过 LLM planner 阶段（模型请求数恒 0、
        plan 即 fixed_plan），其余阶段（Context/Policy/Execute/Compose/记账）与常规
        路径完全一致——数据类意图不存在「LLM 没选工具」的 0 动作路径。不提供时行为
        与现状一致（教师端与全部既有调用方零影响）。
        """
        run_id = uuid.uuid4()
        started = time.perf_counter()
        # run_id 单一事实源：envelope、AgentRun、AgentStep、ToolInvocation 共用同一 ID
        run_row = AgentRun(
            id=run_id,
            user_id=request.actor.user_id,
            role=request.actor.role.value,
            scene=request.scene,
            client_request_id=request.client_request_id,
            intent="",
            status="running",
        )
        db.add(run_row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return await self._duplicate_envelope(request, db)

        # ---- 1. Context（失败 → 最小类型化 Snapshot 继续）----
        snapshot, ctx_error = await self._run_stage(
            "context", self._assembler.build(request, db)
        )
        if snapshot is None:
            snapshot = _empty_snapshot(request)

        # ---- 2. Plan（失败 → deterministic fallback；fixed_plan 提供时跳过 LLM planner）----
        if fixed_plan is not None:
            plan, model_requests = fixed_plan, 0
            plan_error = None
        else:
            # 模型计数经返回值传递（协程局部），不落实例属性：并发 run 不串扰
            plan_result, plan_error = await self._run_stage("plan", self._plan(request, snapshot))
            if plan_result is None:
                plan = deterministic_fallback_plan(request, reason="plan_failed")
                model_requests = 0
            else:
                plan, model_requests = plan_result

        # ---- 3. Policy（同步：异常 → 降级；拒绝 → 不执行工具）----
        # 管理员能力开关来自 system_configs["butler.authorization"]（env ← 全局覆盖），
        # 与 Executor 共用同一来源，避免计划级/动作级校验不一致。
        # 用户本次请求 opt-in 由 request.web_search_opt_in 承载（默认 false），
        # 本地拒答是 handler 运行事实，均不在此处持久化/伪造。
        authorization = await resolve_butler_authorization(db)
        try:
            decision = self._policy.validate_plan(
                request,
                plan,
                budget=self._budget,
                external_allowed=authorization["external_allowed"],
                web_search_enabled=authorization["web_search_enabled"],
            )
            policy_error = None
        except Exception:  # noqa: BLE001
            decision = None
            policy_error = "policy_error"
        policy_allowed = decision is not None and decision.allowed
        policy_rejected = decision is not None and not decision.allowed

        # ---- 4. Execute（Policy 拒绝/异常 → 不执行工具）----
        results: list[ToolResult] = []
        exec_error = None
        if policy_allowed and decision is not None and self._executor is not None:
            results, exec_error = await self._run_stage(
                "execute",
                self._executor.execute_plan(
                    run_id, request, plan, db, shadow=self._shadow, budget=self._budget,
                    external_allowed=authorization["external_allowed"],
                    web_search_enabled=authorization["web_search_enabled"],
                ),
            )
            if results is None:
                results = []
            # 总预算超时（executor 返回 degraded 超时 ToolResult）：execute 阶段标记 failed，
            # 已完成工具账本保留；不向客户端抛 500。
            if any(r.error_code == "execution_timeout" for r in results):
                exec_error = "execution_timeout"

        # ---- 5. Compose（失败 → Runtime 自身最小 envelope，不再调 Composer）----
        degraded = (
            ctx_error is not None
            or plan_error is not None
            or policy_error is not None
            or exec_error is not None
            or plan.response_mode == "degraded"
            or not policy_allowed
            or any(not r.ok for r in results)
        )
        compose_error = None
        envelope: ButlerEnvelope | None = None
        if self._composer is not None:
            envelope, compose_error = await self._run_stage(
                "compose",
                _compose_safe(self._composer, run_id=run_id, request=request, plan=plan,
                              results=results, degraded=degraded),
            )
        if envelope is None:
            compose_error = compose_error or "compose_error"
            # Composer 已失败，Runtime 自身产生最小安全 Envelope（不再调 Composer）
            degraded = True
            envelope = _minimal_envelope(run_id, request, plan, degraded=True)

        # Policy 拒绝码进入 trace（Composer 不感知 Policy；拒绝原因需对学生端可见）
        if not policy_allowed and decision is not None and decision.error_code:
            trace = dict(envelope.trace)
            codes = list(trace.get("error_codes") or [])
            if decision.error_code not in codes:
                codes.append(decision.error_code)
            trace["error_codes"] = codes
            trace["error_code"] = decision.error_code
            envelope = envelope.model_copy(update={"trace": trace})

        # ---- 记账 ----
        latency_ms = int((time.perf_counter() - started) * 1000)
        run_row.intent = plan.intent
        run_row.status = self._resolve_status(
            plan, results, ctx_error, plan_error, policy_error, exec_error, compose_error,
            policy_rejected,
        )
        run_row.degraded = degraded
        run_row.model_request_count = model_requests
        # 实际工具调用计数：只有真正进入 handler（executed）才计入；
        # replayed/shadow_skipped/not_executed 不虚增
        run_row.tool_call_count = sum(1 for r in results if r.execution_status == "executed")
        run_row.finished_at = _now()
        run_row.latency_ms = latency_ms
        run_row.error_code = self._resolve_error_code(
            ctx_error, plan_error, policy_error, exec_error, compose_error, decision, results
        )

        # Policy 拒绝（非异常）也标记为该阶段失败，并携带具体拒绝码
        policy_step_error = policy_error
        if policy_step_error is None and policy_rejected and decision is not None:
            policy_step_error = decision.error_code
        stage_errors = {"context": ctx_error, "plan": plan_error, "policy": policy_step_error,
                        "execute": exec_error, "compose": compose_error}
        for seq, stage in enumerate(STAGES):
            err = stage_errors[stage]
            db.add(
                AgentStep(
                    run_id=run_row.id,
                    sequence=seq,
                    stage=stage,
                    status="ok" if err is None else "failed",
                    error_code=err,
                )
            )

        # ToolInvocation：仅实际执行的工具（Policy 计划级拒绝不写）
        if policy_allowed:
            for action, result in zip(plan.actions, results, strict=False):
                db.add(
                    ToolInvocation(
                        run_id=run_row.id,
                        tool_name=action.tool_name,
                        tool_version=self._tool_version(action.tool_name),
                        status=_invocation_status(result),
                        latency_ms=result.latency_ms,
                        idempotency_key=result.idempotency_key,
                        arguments_digest=_digest(action.arguments),
                        result_digest=_digest(result.data) if result.ok else None,
                        error_code=result.error_code,
                    )
                )

        await db.flush()
        return envelope

    @staticmethod
    def _resolve_status(
        plan: ActionPlan,
        results: list[ToolResult],
        ctx_error: str | None,
        plan_error: str | None,
        policy_error: str | None,
        exec_error: str | None,
        compose_error: str | None,
        policy_rejected: bool = False,
    ) -> str:
        # Policy 计划级拒绝是硬失败：不得记 succeeded
        if policy_rejected:
            return "failed"
        if ctx_error or plan_error or policy_error or exec_error or compose_error:
            if plan.response_mode == "degraded" and not results:
                return "fallback"
            return "failed"
        if plan.response_mode == "degraded":
            return "fallback"
        if any(not r.ok for r in results):
            return "failed"
        return "succeeded"

    @staticmethod
    def _resolve_error_code(
        ctx_error: str | None,
        plan_error: str | None,
        policy_error: str | None,
        exec_error: str | None,
        compose_error: str | None,
        decision: PolicyDecision | None,
        results: list[ToolResult],
    ) -> str | None:
        for err in (ctx_error, plan_error, policy_error, exec_error, compose_error):
            if err:
                return err
        if decision is not None and not decision.allowed:
            return decision.error_code
        for r in results:
            if not r.ok and r.error_code:
                return r.error_code
        return None

    def _tool_version(self, tool_name: str) -> str:
        try:
            return self._registry.get(tool_name).version
        except Exception:  # noqa: BLE001 —— 未注册工具（policy 已拦）不打断记账
            return ""

    async def _plan(
        self, request: ButlerRequest, snapshot: ButlerContextSnapshot
    ) -> tuple[ActionPlan, int]:
        """返回 (计划, 本次运行模型请求数)；计数禁止共享实例状态。"""
        planner, adapter = self._planner, self.adapter
        if planner is None or adapter is None:
            return deterministic_fallback_plan(request, reason="no_planner"), 0
        prompt = build_planning_prompt(request, snapshot, self._registry)
        deps = ButlerDeps(
            request=request, snapshot=snapshot, registry=self._registry, budget=self._budget
        )
        adapter.reset_count()
        try:
            async with asyncio.timeout(self._budget.timeout_s):
                result = await planner.run(
                    prompt,
                    deps=deps,
                    usage_limits=UsageLimits(
                        request_limit=self._budget.max_model_requests
                    ),
                )
            # per-run 计数：PydanticAI RunUsage 自动隔离，连续/并发 run 不串扰
            return result.output, int(result.usage.requests or 0)
        except Exception:  # noqa: BLE001 —— 模型全失败/超时 → 规则降级
            # 失败路径读 ContextVar 计数（同一 Task 上下文，未被其他 run 累计）
            return (
                deterministic_fallback_plan(request, reason="planner_failed"),
                adapter.request_count,
            )

    async def _run_stage(
        self, stage: str, coro: Coroutine[Any, Any, Any]
    ) -> tuple[Any, str | None]:
        try:
            value = await coro
            return value, None
        except Exception:  # noqa: BLE001 —— 稳定内部错误码，不泄漏异常文本
            return None, f"{stage}_error"

    async def _duplicate_envelope(self, request: ButlerRequest, db: AsyncSession) -> ButlerEnvelope:
        """重复 client_request_id：查询已有 AgentRun，返回其 run_id（非随机生成）。"""
        existing = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.user_id == request.actor.user_id,
                    AgentRun.client_request_id == request.client_request_id,
                )
            )
        ).scalars().first()
        run_id = existing.id if existing is not None else uuid.uuid4()
        return ButlerEnvelope(
            run_id=run_id,
            intent="",
            text="重复请求已忽略",
            blocks=[],
            actions=[],
            sources=[],
            degraded=True,
            trace={"error_code": "duplicate_request"},
        )


def _invocation_status(result: ToolResult) -> str:
    """ToolInvocation.status 直接由 ToolResult.execution_status 派生。

    区分 executed（实际执行成功）/ failed（实际执行失败）/ replayed（幂等重放，
    未进 handler）/ shadow_skipped（影子跳过）/ not_executed（前置拒绝）。
    """
    if result.execution_status == "replayed":
        return "replayed"
    if result.execution_status == "shadow_skipped":
        return "shadow_skipped"
    if result.execution_status == "not_executed":
        return "not_executed"
    if result.ok:
        return "executed"
    return "failed"


def _empty_snapshot(request: ButlerRequest) -> ButlerContextSnapshot:
    """Context 失败时的最小类型化 Snapshot（空结构，不抛 500）。"""
    return ButlerContextSnapshot(
        actor=request.actor,
        scene=request.scene,
        profile={},
        conversation={},
        assignments=(),
        effective_config={},
        feature_flags=frozenset(),
    )


def _minimal_envelope(run_id: uuid.UUID, request: ButlerRequest, plan: ActionPlan, degraded: bool) -> ButlerEnvelope:
    """Compose 失败时由 Runtime 自身产生的最小安全 Envelope（不再调已失败的 Composer）。"""
    return ButlerEnvelope(
        run_id=run_id,
        intent=plan.intent,
        text=plan.goal or "处理完成",
        blocks=[],
        actions=[],
        sources=[],
        degraded=degraded,
        trace={"error_code": "compose_error", "intent": plan.intent},
    )


async def _compose_safe(
    composer: ResultComposer,
    *,
    run_id: uuid.UUID,
    request: ButlerRequest,
    plan: ActionPlan,
    results: list[ToolResult],
    degraded: bool,
) -> ButlerEnvelope:
    """把同步 compose 包成 coroutine 供 _run_stage 统一捕获。"""
    return composer.compose(
        run_id=run_id, request=request, plan=plan, results=results, degraded=degraded
    )


def _now() -> datetime:
    return datetime.now(UTC)


def build_shadow_runtime() -> ButlerRuntime:
    """无副作用影子运行实例：空 Registry → fallback 计划 → 无动作。"""
    registry = ToolRegistry()
    policy = PolicyGate(registry)
    executor = ButlerExecutor(registry, policy, budget=ButlerBudget())
    return ButlerRuntime(
        registry=registry,
        policy=policy,
        executor=executor,
        budget=ButlerBudget(),
        shadow=True,
    )


async def run_v2_shadow(user_id: uuid.UUID, scene: str, message: str) -> None:
    """独立后台 session 的 v2 影子运行（best-effort，异常吞掉；无业务副作用）。"""
    try:
        async with background_session_factory() as db:
            runtime = build_shadow_runtime()
            req = ButlerRequest(
                actor=ActorContext(user_id=user_id, role=ActorRole.STUDENT),
                message=message,
                scene=scene,
                client_request_id=f"shadow-{uuid.uuid4().hex[:16]}",
            )
            await runtime.run(req, db)
            await db.commit()
    except Exception:  # noqa: BLE001 —— 影子运行绝不影响主链路
        pass
