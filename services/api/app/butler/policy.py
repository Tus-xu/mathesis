"""Butler Kernel v2 PolicyGate（设计规格 §5 + 阶段 2 Task 4）

固定 10 步校验顺序（先失败先返回）：
1.  工具是否注册        → unknown_tool
2.  角色是否允许        → role_denied
3.  场景是否允许        → scene_denied
4.  参数是否合法        → invalid_arguments
5.  工具数量是否超预算  → budget_exceeded
6.  风险等级是否允许    → risk_denied（默认全部枚举允许，可显式排除）
7.  WRITE 是否要求幂等  → idempotency_required
8.  EXTERNAL 场景条件   → external_not_allowed
9.  联网搜索需服务端能力开启 → confirmation_required
    （以实际 Action 工具名为最终依据：needs_web_search 声明或
      *.web_search 工具二者任一命中即检查，不信任 Planner 布尔字段；
      全局 web_search_enabled 只是服务端能力硬前置，用户 opt-in 与
      本地拒答运行事实由 handler 在受信任 local-first 路径强制）
10. M2 范围排除        → m2_out_of_scope

错误响应一律使用稳定错误码 + 固定文案，不携带堆栈、内部类名、数据库信息或密钥。
F14（wf_verify_derivation / research.verify_derivation / lean.*）由统一函数
is_m2_denied_tool 在调用期兜底拒绝：即使工具绕过注册层进入 Registry，调用仍被拒绝。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

from app.butler.contracts import (
    ActionPlan,
    ButlerBudget,
    ButlerRequest,
    PlannedAction,
    ToolRisk,
)
from app.butler.registry import (
    M2_DENIED_TOOLS,
    ToolRegistry,
    UnknownToolError,
    is_m2_denied_tool,
)

#: 联网搜索工具家族（实际 Action 工具名为最终依据，不信任 Planner 布尔字段）。
WEB_SEARCH_TOOLS = frozenset({"xingchen.web_search"})


def is_web_search_tool(name: str) -> bool:
    """统一判断工具是否属于联网搜索（显式名单 + ``*.web_search`` 后缀）。"""
    return name in WEB_SEARCH_TOOLS or name.endswith(".web_search")

# 稳定错误码（对外契约，不得随内部实现变化）
ERROR_UNKNOWN_TOOL = "unknown_tool"
ERROR_ROLE_DENIED = "role_denied"
ERROR_SCENE_DENIED = "scene_denied"
ERROR_INVALID_ARGUMENTS = "invalid_arguments"
ERROR_BUDGET_EXCEEDED = "budget_exceeded"
ERROR_RISK_DENIED = "risk_denied"
ERROR_IDEMPOTENCY_REQUIRED = "idempotency_required"
ERROR_EXTERNAL_NOT_ALLOWED = "external_not_allowed"
ERROR_CONFIRMATION_REQUIRED = "confirmation_required"
ERROR_M2_OUT_OF_SCOPE = "m2_out_of_scope"


class PolicyDecision(BaseModel):
    """一次校验的裁决结果（稳定错误码 + 消毒后消息）。"""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    error_code: str | None = None
    message: str = ""
    blocked_tool: str | None = None


class PolicyGate:
    """确定性权限与预算门：LLM 计划与每次工具调用都必须通过本门。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        m2_denied_tools: frozenset[str] = M2_DENIED_TOOLS,
        allowed_risks: frozenset[ToolRisk] = frozenset(ToolRisk),
    ) -> None:
        self._registry = registry
        self._m2_denied_tools = m2_denied_tools
        self._allowed_risks = allowed_risks

    def validate_plan(
        self,
        request: ButlerRequest,
        plan: ActionPlan,
        *,
        budget: ButlerBudget | None = None,
        web_search_enabled: bool = False,
        external_allowed: bool = False,
    ) -> PolicyDecision:
        budget = budget or ButlerBudget()

        # 步骤 5：工具数量（计划级）
        if len(plan.actions) > budget.max_tool_calls:
            return self._deny(
                ERROR_BUDGET_EXCEEDED,
                "too many tool actions in plan",
                None,
            )

        # 步骤 9：联网搜索触发条件（计划级）——全局能力是硬前置。
        # web_search_enabled 只表示服务端能力已开启，不是用户授权；
        # 用户 opt-in 与本地拒答（运行事实）由 Action 级/执行期判定，
        # Planner 的 needs_web_search 声明不能替代用户授权。
        if plan.needs_web_search and not web_search_enabled:
            return self._deny(
                ERROR_CONFIRMATION_REQUIRED,
                "web search requires server capability enabled",
                None,
            )

        # 步骤 1-4 / 6-8 / 10：逐动作校验（把真实搜索授权状态传给每个 Action，
        # 搜索授权以实际 Action 工具名为最终依据）
        for action in plan.actions:
            decision = self._validate_action_core(
                request,
                action,
                external_allowed=external_allowed,
                web_search_enabled=web_search_enabled,
            )
            if not decision.allowed:
                return decision
        return PolicyDecision(allowed=True, message="ok")

    def validate_action(
        self,
        request: ButlerRequest,
        action: PlannedAction,
        *,
        external_allowed: bool = False,
        web_search_enabled: bool = False,
    ) -> PolicyDecision:
        return self._validate_action_core(
            request,
            action,
            external_allowed=external_allowed,
            web_search_enabled=web_search_enabled,
        )

    def _validate_action_core(
        self,
        request: ButlerRequest,
        action: PlannedAction,
        *,
        external_allowed: bool,
        web_search_enabled: bool,
    ) -> PolicyDecision:
        # 步骤 1：工具是否注册
        try:
            tool = self._registry.get(action.tool_name)
        except UnknownToolError:
            return self._deny(ERROR_UNKNOWN_TOOL, "tool is not registered", action.tool_name)

        # 步骤 2：角色是否允许
        if request.actor.role not in tool.allowed_roles:
            return self._deny(ERROR_ROLE_DENIED, "role is not allowed for tool", action.tool_name)

        # 步骤 3：场景是否允许
        if request.scene not in tool.allowed_scenes:
            return self._deny(ERROR_SCENE_DENIED, "scene is not allowed for tool", action.tool_name)

        # 步骤 4：参数是否合法（不把 ValidationError 详情泄漏给调用方）
        try:
            self._registry.validate_arguments(action.tool_name, action.arguments)
        except ValidationError:
            return self._deny(ERROR_INVALID_ARGUMENTS, "invalid tool arguments", action.tool_name)

        # 步骤 6：风险等级是否允许
        if tool.risk not in self._allowed_risks:
            return self._deny(ERROR_RISK_DENIED, "tool risk is not allowed", action.tool_name)

        # 步骤 7：WRITE 必须声明幂等
        if tool.risk == ToolRisk.WRITE and not tool.idempotency_required:
            return self._deny(
                ERROR_IDEMPOTENCY_REQUIRED,
                "write tool requires idempotency",
                action.tool_name,
            )

        # 步骤 8：EXTERNAL 必须满足场景授权（先于搜索确认返回）
        if tool.risk == ToolRisk.EXTERNAL and not external_allowed:
            return self._deny(
                ERROR_EXTERNAL_NOT_ALLOWED,
                "external tool is not authorized in this scene",
                action.tool_name,
            )

        # 步骤 9（Action 级）：实际工具是搜索工具时，全局联网能力是硬前置。
        # 不信任 Planner 的 needs_web_search 布尔字段，以 Action 工具名为最终依据；
        # 用户 opt-in 与本地拒答（运行事实）由 handler 在受信任 local-first
        # 路径强制（本地拒答后远程才被允许），不在此处用全局布尔冒充运行结果。
        if is_web_search_tool(action.tool_name) and not web_search_enabled:
            return self._deny(
                ERROR_CONFIRMATION_REQUIRED,
                "web search requires server capability enabled",
                action.tool_name,
            )

        # 步骤 10：M2 范围排除（统一函数 is_m2_denied_tool 覆盖 F14 名单与
        # lean.* 前缀；self._m2_denied_tools 保留自定义注入名单能力）
        if action.tool_name in self._m2_denied_tools or is_m2_denied_tool(action.tool_name):
            return self._deny(ERROR_M2_OUT_OF_SCOPE, "tool is outside M2 scope", action.tool_name)

        return PolicyDecision(allowed=True, message="ok")

    @staticmethod
    def _deny(error_code: str, message: str, blocked_tool: str | None) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            error_code=error_code,
            message=message,
            blocked_tool=blocked_tool,
        )
