"""Butler Kernel v2 Executor（设计规格 §5 + 阶段 3B）

- 先 Policy 后执行；输入先 Registry 校验，输出后 Registry 校验；
- 总工具数 ≤ 预算（Policy 计划级检查 + 防御）；
- 每个工具独立 timeout_s，超时 → degraded ToolResult；
- WRITE 按 user_id + client_request_id + tool_name + canonical arguments 生成
  幂等键，重复写调用直接返回已记录结果，不执行第二次 handler；
- 顺序执行（第一版全顺序，保证正确；无并发副作用）；
- Shadow 模式：WRITE/EXTERNAL 返回 shadow_skipped，handler 调用次数为 0；
- EXTERNAL 超时/失败 → degraded ToolResult；
- 异常不泄漏堆栈 / SQL / 文件路径 / 密钥（稳定文案）。

handler 协议（不使用 eval/getattr）：
    async def handler(context: ToolExecutionContext, validated_input: dict) -> dict
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.butler.contracts import (
    ActionPlan,
    ButlerBudget,
    ButlerRequest,
    PlannedAction,
    ToolResult,
    ToolRisk,
)
from app.butler.policy import PolicyGate
from app.butler.registry import ToolRegistry


@dataclass
class WebSearchAuthorization:
    """联网搜索类型化授权上下文（两种输入事实分离，禁止全局布尔冒充运行结果）。

    - global_enabled  管理员全局能力开关（服务端是否允许联网搜索）；
    - user_opt_in     用户本次请求 opt-in（ButlerRequest.web_search_opt_in）。
    本地拒答（local_refused）是 handler 在受信任 local-first 路径执行后产生的
    运行事实，以 handler 局部变量为唯一事实源，不在此上下文声明字段（避免
    “声明有字段但从未赋值”的假闭环）。
    """

    global_enabled: bool
    user_opt_in: bool


@dataclass
class ToolExecutionContext:
    """handler 协议上下文：``handler(context, validated_input) -> dict``。"""

    run_id: uuid.UUID
    request: ButlerRequest
    db: AsyncSession | None
    idempotency_key: str
    web_search_auth: WebSearchAuthorization | None = None


def _digest(value: Any) -> str:
    """确定性脱敏摘要（账本/幂等键用，不存原始输入输出）。"""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _elapsed_ms(started: float) -> int:
    """实际调用耗时（毫秒）：真实执行过至少记 1ms，禁止账本固定为 0。"""
    return max(1, int((time.perf_counter() - started) * 1000))


class ButlerExecutor:
    """受限、幂等、可审计的工具执行器（第一版全顺序执行）。"""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyGate,
        *,
        budget: ButlerBudget | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._budget = budget or ButlerBudget()
        # 幂等重放缓存：idempotency_key -> ToolResult（同进程内 WRITE 不重放）
        self._replay: dict[str, ToolResult] = {}

    async def execute_plan(
        self,
        run_id: uuid.UUID,
        request: ButlerRequest,
        plan: ActionPlan,
        db: AsyncSession | None,
        *,
        shadow: bool = False,
        budget: ButlerBudget | None = None,
        external_allowed: bool = False,
        web_search_enabled: bool = False,
    ) -> list[ToolResult]:
        budget = budget or self._budget
        # 1. 计划级 Policy（含工具数量 ≤5、搜索能力、M2 范围）
        decision = self._policy.validate_plan(
            request,
            plan,
            budget=budget,
            web_search_enabled=web_search_enabled,
            external_allowed=external_allowed,
        )
        if not decision.allowed:
            return [
                ToolResult(
                    ok=False, error_code=decision.error_code, user_message=decision.message,
                    execution_status="not_executed",
                )
            ]
        # 2. 顺序执行（第一版全顺序，保证正确性），受总预算 timeout_s 护栏约束：
        #    即使每个工具独立 timeout_s ≤20，多工具累计也不得超过单次运行总预算。
        #    超时 → 返回已完成的工具结果 + 一个 degraded 超时 ToolResult（账本保留）。
        results: list[ToolResult] = []
        started = time.perf_counter()
        try:
            async with asyncio.timeout(budget.timeout_s):
                for action in plan.actions:
                    result = await self.invoke(
                        run_id,
                        request,
                        action,
                        db,
                        shadow=shadow,
                        external_allowed=external_allowed,
                        web_search_enabled=web_search_enabled,
                    )
                    results.append(result)
        except TimeoutError:
            results.append(
                ToolResult(
                    ok=False,
                    error_code="execution_timeout",
                    user_message="execution timed out",
                    degraded=True,
                    latency_ms=_elapsed_ms(started),
                    execution_status="executed",
                )
            )
        return results

    async def invoke(
        self,
        run_id: uuid.UUID,
        request: ButlerRequest,
        action: PlannedAction,
        db: AsyncSession | None,
        *,
        shadow: bool = False,
        external_allowed: bool = False,
        web_search_enabled: bool = False,
    ) -> ToolResult:
        # 1. Policy 先于执行（角色/场景/参数/风险/幂等/外部/搜索/M2）
        decision = self._policy.validate_action(
            request,
            action,
            external_allowed=external_allowed,
            web_search_enabled=web_search_enabled,
        )
        if not decision.allowed:
            return ToolResult(
                ok=False, error_code=decision.error_code, user_message=decision.message,
                execution_status="not_executed",
            )

        tool = self._registry.get(action.tool_name)
        # 2. 输入校验（Policy 已校验，双保险）
        try:
            validated = self._registry.validate_arguments(action.tool_name, action.arguments)
        except Exception:  # noqa: BLE001
            return ToolResult(
                ok=False, error_code="invalid_arguments", user_message="invalid tool arguments",
                execution_status="not_executed",
            )

        # 3. Shadow：WRITE/EXTERNAL 无副作用
        if shadow and tool.risk in (ToolRisk.WRITE, ToolRisk.EXTERNAL):
            return ToolResult(
                ok=False, error_code="shadow_skipped", user_message="shadow run skipped side effect",
                execution_status="shadow_skipped",
            )

        # 4. 幂等键（含 user_id 防跨用户冲突；canonical args 保证确定性）
        idem_key = self._idempotency_key(request, action, validated)

        # 5. WRITE 幂等重放：同键已成功 → 直接返回已记录结果，不执行 handler
        #    （进程内 dict：仅本 Executor 实例生命周期内有效，非跨进程持久幂等）
        #    latency_ms 归零：重放未进 handler，不得继承首次执行耗时（账本真实性）
        if tool.risk == ToolRisk.WRITE and tool.idempotency_required:
            replay = self._replay.get(idem_key)
            if replay is not None:
                return replay.model_copy(
                    update={"execution_status": "replayed", "latency_ms": 0}
                )

        context = ToolExecutionContext(
            run_id=run_id,
            request=request,
            db=db,
            idempotency_key=idem_key,
            web_search_auth=WebSearchAuthorization(
                global_enabled=web_search_enabled,
                user_opt_in=request.web_search_opt_in,
            ),
        )
        # 6. 执行（每个工具独立超时；记录实际耗时供账本使用）
        started = time.perf_counter()
        try:
            async with asyncio.timeout(tool.timeout_s):
                raw = await tool.handler(context, validated)
        except TimeoutError:
            return ToolResult(
                ok=False,
                error_code="tool_timeout",
                user_message="tool timed out",
                retryable=True,
                degraded=True,
                latency_ms=_elapsed_ms(started),
                idempotency_key=idem_key if tool.risk == ToolRisk.WRITE else None,
                execution_status="executed",
            )
        except Exception:  # noqa: BLE001 —— 稳定文案，不泄漏内部细节
            return ToolResult(
                ok=False,
                error_code="tool_error",
                user_message="tool execution failed",
                retryable=True,
                degraded=tool.risk in (ToolRisk.EXTERNAL, ToolRisk.WRITE),
                latency_ms=_elapsed_ms(started),
                idempotency_key=idem_key if tool.risk == ToolRisk.WRITE else None,
                execution_status="executed",
            )

        # 7. 输出校验
        try:
            validated_out = self._registry.validate_output(action.tool_name, raw)
        except Exception:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error_code="invalid_output",
                user_message="invalid tool output",
                latency_ms=_elapsed_ms(started),
                idempotency_key=idem_key if tool.risk == ToolRisk.WRITE else None,
                execution_status="executed",
            )

        result = ToolResult(
            ok=True,
            data=validated_out,
            latency_ms=_elapsed_ms(started),
            # WRITE 一律携带幂等键（= 交给 handler 的 ToolExecutionContext.idempotency_key）
            idempotency_key=idem_key if tool.risk == ToolRisk.WRITE else None,
            execution_status="executed",
        )
        if tool.risk == ToolRisk.WRITE and tool.idempotency_required:
            self._replay[idem_key] = result
        return result

    def _idempotency_key(
        self, request: ButlerRequest, action: PlannedAction, validated: dict[str, Any]
    ) -> str:
        canonical = _digest(validated)
        return f"{request.actor.user_id}:{request.client_request_id}:{action.tool_name}:{canonical}"
