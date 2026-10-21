"""Butler Kernel v2 类型化工具注册表（设计规格 §7 + 阶段 2 Task 4）

- 每个工具声明：唯一名称、版本、描述、输入模型、输出模型、风险级别、
  角色集合、场景集合、默认超时、幂等策略与 handler；
- 重复名称拒绝；未知工具返回稳定错误（不携带堆栈/内部类名）；
- 输入/输出分别由 input_model / output_model 校验；
- F14（wf_verify_derivation / research.verify_derivation / Lean 工具）通过
  M2_DENIED_TOOLS 名单在注册层直接拒绝，无法进入 Registry；
- 本阶段不注册真实领域工具，仅提供类型化注册与校验；
- 不使用 eval / getattr 动态反射查找业务函数：handler 由 Executor 在
  后续阶段按工具名显式调用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.butler.contracts import ActorRole, ToolRisk

#: F14 / M2 范围外工具：注册层直接拒绝（PolicyGate 另持有一份做调用期兜底）。
M2_DENIED_TOOLS = frozenset(
    {
        "research.verify_derivation",
        "wf_verify_derivation",
        "lean.verify",
        "lean.prove",
        "lean.check",
    }
)


def is_m2_denied_tool(name: str) -> bool:
    """F14 / M2 范围外工具统一判断（单一逻辑源）。

    Registry.register 与 PolicyGate 第 10 步必须共同调用本函数，
    禁止复制两份不一致逻辑。规则 = 显式名单 + ``lean.*`` 前缀全家拦截，
    保证未来新增 lean 系列工具名无需改名单。
    """
    return name in M2_DENIED_TOOLS or name.startswith("lean.")


class ToolRegistryError(Exception):
    """注册表稳定错误基类（message 对外可见，不得含堆栈/密钥）。"""


class DuplicateToolError(ToolRegistryError):
    """工具名重复注册。"""


class UnknownToolError(ToolRegistryError):
    """工具名未注册。"""


class ToolForbiddenError(ToolRegistryError):
    """工具名命中 M2_DENIED_TOOLS / 注册层拒绝名单。"""


class ToolDefinition(BaseModel):
    """一个工具的完整声明（类型化 I/O + 权限 + 风险 + handler）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk: ToolRisk
    allowed_roles: frozenset[ActorRole]
    allowed_scenes: frozenset[str]
    timeout_s: float = 20.0
    idempotency_required: bool = False
    handler: Callable[..., Awaitable[Any]]
    # handler 协议（由 Executor 显式调用，不使用 eval/getattr 反射）：
    #   async def handler(context: ToolExecutionContext, validated_input: dict) -> dict
    # ToolExecutionContext 定义于 app/butler/executor.py；返回 dict 由 output_model 校验。


class ToolRegistry:
    """显式 allowlist 工具注册表（进程内单例由 Runtime 持有）。"""

    def __init__(self, denied_tools: frozenset[str] = M2_DENIED_TOOLS) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._denied_tools = denied_tools

    def register(self, definition: ToolDefinition) -> None:
        # 统一函数 is_m2_denied_tool 是 F14/lean.* 规则的唯一逻辑源；
        # self._denied_tools 保留调用方注入自定义拒绝名单的能力。
        if definition.name in self._denied_tools or is_m2_denied_tool(definition.name):
            raise ToolForbiddenError(f"tool is denied: {definition.name}")
        if definition.name in self._tools:
            raise DuplicateToolError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(f"unknown tool: {name}")
        return tool

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def visible_to(self, role: ActorRole, scene: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name, tool in self._tools.items()
                if role in tool.allowed_roles and scene in tool.allowed_scenes
            )
        )

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """按 input_model 校验并规范化参数；ValidationError 原样上抛由 Policy 捕获。"""
        tool = self.get(name)
        validated = tool.input_model.model_validate(arguments)
        return validated.model_dump()

    def validate_output(self, name: str, data: Any) -> Any:
        """按 output_model 校验工具输出。"""
        tool = self.get(name)
        validated = tool.output_model.model_validate(data)
        return validated.model_dump()


def build_m2_registry() -> ToolRegistry:
    """统一 M2 工具注册表工厂：9 个本地领域工具 + 7 个星辰远程工具 = 16 个。

    - 通过 register_domain_tools / register_workflow_tools 显式组合，不访问私有字段；
    - F14 / wf_intent_router / wf_socratic_chat / lean.* 由注册层拒绝，无法进入；
    - 本阶段只建立统一工厂，不挂载 butler_router、不启用 v2、不切流。
    """
    from app.butler.tools import register_domain_tools
    from app.butler.workflow_tools import register_workflow_tools

    reg = ToolRegistry()
    register_domain_tools(reg)
    register_workflow_tools(reg)
    return reg
