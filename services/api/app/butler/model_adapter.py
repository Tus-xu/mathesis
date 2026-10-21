"""Butler Kernel v2 ModelAdapter（设计规格 §6 + 阶段 3A）

- 实现 PydanticAI 所需生成函数，内部只调用现有 ``ModelRouter.chat()``，
  保留主备降级、熔断与 ``log_ai_call`` 审计；
- 接收已解析的 ModelRouter 实例，不创建 Spark/DeepSeek，不读取/保存/日志输出
  任何 API Key，不连接 Pydantic Gateway；
- 不依赖模型原生 Function Calling（``functions=None``，只消费 content 文本）；
- 使用 PydanticAI 公开 API（Agent / FunctionModel / UsageLimits）完成
  结构化 ActionPlan 校验，输出非法时最多修复 1 次（retries=1）；
- 模型全部失败 / 超时 / 修复失败 → ``deterministic_fallback_plan()``，
  不向学生端抛异常；
- 单次 Planner 请求数由 ``UsageLimits(request_limit=max_model_requests)`` 约束；
- Planner 只能看到 ``Registry.visible_to(role, scene)`` 返回的工具摘要。
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.butler.contracts import (
    ActionPlan,
    ActorRole,
    ButlerBudget,
    ButlerContextSnapshot,
    ButlerRequest,
)
from app.butler.registry import ToolDefinition, ToolRegistry, UnknownToolError
from app.providers.base import ChatMessage
from app.providers.router import ModelRouter

PLANNER_SCENE = "butler.planner"

#: 单次运行的模型请求计数（ContextVar：asyncio.Task 边界天然隔离，
#: 同一 Runtime/Adapter 连续/并发 run 不累计、不串扰，禁止共享整数方案）
_request_count_var: ContextVar[int] = ContextVar("butler_planner_request_count", default=0)

#: Prompt 中近期对话最多条数与单条消息截断长度（有界脱敏）
_PROMPT_MAX_MESSAGES = 5
_PROMPT_MSG_LIMIT = 120
#: Prompt 总长度上限（防止完整隐私文本进入模型）
_PROMPT_TOTAL_LIMIT = 4000
#: 工具摘要单行有界：描述截断 + 字段数上限（禁止完整 Schema/handler 进 Prompt）
_TOOL_DESC_LIMIT = 80
_TOOL_MAX_FIELDS = 8


def _truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"

_DEFAULT_PLANNER_PROMPT = (
    "你是智学数研学生端 AI 管家。只输出 ActionPlan JSON："
    '{"intent": str, "goal": str, "actions": [{"tool_name": str, "arguments": {"query": "..."}, "reason": str}], '
    '"response_mode": "direct"|"cards"|"socratic"|"degraded", "needs_web_search": bool}。'
)


@dataclass
class ButlerDeps:
    """Planner 依赖（Agent 泛型参数，供 Runtime 传入请求/快照/注册表/预算）。"""

    request: ButlerRequest
    snapshot: ButlerContextSnapshot | None
    registry: ToolRegistry
    budget: ButlerBudget


class ButlerModelAdapter:
    """PydanticAI ↔ ModelRouter 隔离层（内部只调现有路由，不持有密钥）。"""

    def __init__(self, router: ModelRouter, *, budget: ButlerBudget | None = None) -> None:
        self._router = router
        self._budget = budget or ButlerBudget()

    @property
    def request_count(self) -> int:
        return _request_count_var.get()

    def reset_count(self) -> None:
        """重置本次运行的请求计数（同一 asyncio.Task 上下文内有效）。"""
        _request_count_var.set(0)

    async def generate(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """PydanticAI FunctionModel 生成函数：消息转文本 → ModelRouter.chat() → 文本响应。"""
        _request_count_var.set(_request_count_var.get() + 1)
        prompt = _messages_to_prompt(messages)
        chat_messages: list[ChatMessage] = [{"role": "user", "content": prompt}]
        result = await self._router.chat(
            chat_messages,
            temperature=0.2,
            max_tokens=4096,
            functions=None,  # 不依赖模型原生 Function Calling
            thinking=False,
            request_id=str(uuid.uuid4()),
            scene=PLANNER_SCENE,
        )
        return ModelResponse(parts=[TextPart(content=_extract_json_object(result["content"]))])


def _extract_json_object(text: str) -> str:
    """从模型输出中提取首个平衡的 JSON 对象（小模型常见：前后散文/markdown 栅栏/结尾注解）。

    提取不到（无花括号）时原样返回，交给 PydanticAI 的校验重试与降级路径。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    start = t.find("{")
    if start < 0:
        return t
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return t[start : i + 1]
    return t[start:]


def _messages_to_prompt(messages: list[ModelMessage]) -> str:
    """把 PydanticAI 消息序列合并为单一文本 prompt。"""
    parts: list[str] = []
    for msg in messages:
        for part in msg.parts:
            text = getattr(part, "content", None)
            if text:
                parts.append(str(text))
    return "\n\n".join(parts)


def _tool_summary_line(tool: ToolDefinition) -> str:
    """单个可见工具的有界摘要：name + description + 输入字段名。

    禁止进入 Prompt：handler、密钥、内部类路径、完整 JSON Schema。
    """
    desc = _truncate(tool.description, _TOOL_DESC_LIMIT)
    fields = list(tool.input_model.model_fields)[:_TOOL_MAX_FIELDS]
    field_summary = "; ".join(fields) if fields else "无"
    return f"- {tool.name}：{desc}；输入字段：{field_summary}"


def _tools_block(registry: ToolRegistry, role: ActorRole, scene: str) -> str:
    """仅当前角色/场景可见工具的摘要块（不可见工具的名称/描述/Schema 一律不出现）。"""
    visible = registry.visible_to(role, scene)
    if not visible:
        return "- (无可用工具)"
    lines = []
    for name in visible:
        try:
            tool = registry.get(name)
        except UnknownToolError:  # 名称来自 Registry 本身，仅防御性兜底
            continue
        lines.append(_tool_summary_line(tool))
    return "\n".join(lines) if lines else "- (无可用工具)"


def build_planning_prompt(
    request: ButlerRequest,
    snapshot: ButlerContextSnapshot | None,
    registry: ToolRegistry,
) -> str:
    """组装本次运行的 Planner user prompt：有界脱敏的上下文 + 可见工具摘要。

    - system_prompt 由 build_planner 静态设置（不绑定 request/snapshot）；
    - 每次 run 只通过本函数注入本次上下文，连续服务多用户不残留；
    - 上下文经 _truncate/_redact 脱敏，禁止完整隐私文本/密钥进入模型。
    """
    tools_block = _tools_block(registry, request.actor.role, request.scene)

    sections: list[str] = [
        f"用户消息：{request.message}",
        f"场景：{request.scene}；角色：{request.actor.role.value}",
        f"可用工具（仅这些，禁止使用未列出的工具）：\n{tools_block}",
    ]
    if snapshot is not None:
        profile = snapshot.profile or {}
        if profile:
            sections.append(
                "学情：学习风格={style}；阶段={stage}；薄弱点={weak}".format(
                    style=profile.get("learning_style", ""),
                    stage=profile.get("current_stage", ""),
                    weak=profile.get("weak_point_rank", []),
                )
            )
        conv = snapshot.conversation or {}
        if conv:
            msgs = conv.get("recent_messages", [])[:_PROMPT_MAX_MESSAGES]
            sections.append(
                "近期对话：标题={title}；摘要={summary}；最近消息={msgs}".format(
                    title=conv.get("title", ""),
                    summary=conv.get("summary", ""),
                    msgs=[
                        {"role": m.get("role", ""), "content": _truncate(m.get("content", ""), _PROMPT_MSG_LIMIT)}
                        for m in msgs
                    ],
                )
            )
        if snapshot.assignments:
            sections.append("当前作业：" + ", ".join(a.get("title", "") for a in snapshot.assignments))
        if snapshot.feature_flags:
            sections.append("特性开关：" + ", ".join(sorted(snapshot.feature_flags)))

    sections.append(
        "ActionPlan 结构："
        '{"intent": str, "goal": str, "actions": [{"tool_name": str, "arguments": {"query": "..."}, "reason": str}], '
        '"response_mode": "direct"|"cards"|"socratic"|"degraded", "needs_web_search": bool}。'
        "actions 最多 5 个。"
    )
    # 总长度上限：防止完整隐私文本进入模型
    return "\n".join(sections)[:_PROMPT_TOTAL_LIMIT]


def build_planner(
    adapter: ButlerModelAdapter,
    registry: ToolRegistry,
    *,
    budget: ButlerBudget | None = None,
    request: ButlerRequest | None = None,  # noqa: ARG001 —— 兼容旧签名，不再绑定到 system_prompt
    system_prompt: str | None = None,
) -> Agent[ButlerDeps, ActionPlan]:
    """构造通用、可复用的 PydanticAI Agent。

    - system_prompt 为静态格式说明，不绑定任何 request/snapshot；
    - 每次 run 的上下文与可见工具通过 build_planning_prompt 生成的 user_prompt 注入；
    - 最多修复 1 次（retries=1）；单次请求上限由 UsageLimits 在 run 时约束。
    """
    return Agent(
        model=FunctionModel(adapter.generate),
        deps_type=ButlerDeps,
        output_type=ActionPlan,
        retries=1,  # 模型输出非法时最多修复 1 次
        system_prompt=system_prompt or _DEFAULT_PLANNER_PROMPT,
    )


def deterministic_fallback_plan(
    request: ButlerRequest, *, reason: str = "model_unavailable"
) -> ActionPlan:
    """模型全部失败 / 超时 / 修复失败时的规则型降级计划（不向学生端抛异常）。"""
    return ActionPlan(
        intent="fallback",
        goal="模型暂不可用，返回规则型降级响应",
        actions=[],
        response_mode="degraded",
        needs_web_search=False,
    )
