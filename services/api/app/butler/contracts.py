"""Butler Kernel v2 核心类型契约（设计规格 §4 + 阶段 2 扩展）

角色分层：student / teacher / researcher / admin，为 M3 教师端、M4 科研端
提供同一套类型化契约；Planner-facing 模型全部 extra="forbid"，防止模型输出
携带未声明字段；字符串 ID / tool_name / scene / client_request_id 拒绝空串。
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActorRole(StrEnum):
    """平台角色：决定工具可见性与权限边界。"""

    STUDENT = "student"
    TEACHER = "teacher"
    RESEARCHER = "researcher"
    ADMIN = "admin"


class ActorContext(BaseModel):
    """发起请求的角色上下文。"""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    role: ActorRole
    class_ids: tuple[uuid.UUID, ...] = ()
    locale: str = "zh-CN"


class ButlerRequest(BaseModel):
    """一次 Butler 运行的入口请求（Compatibility Facade 解析后传入）。"""

    model_config = ConfigDict(extra="forbid")

    actor: ActorContext
    message: str
    scene: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None
    client_request_id: str = Field(min_length=1)
    # 请求级联网授权：用户是否明确同意本次联网（默认关闭；Planner 的
    # needs_web_search 声明绝不能替代该字段；全局 web_search_enabled 只是
    # 服务端能力开关，不是用户授权）。
    web_search_opt_in: bool = False


class ButlerContextSnapshot(BaseModel):
    """运行开始时一次并行组装、运行期内不可变的上下文快照（类型化模型，非裸 dict）。

    profile/conversation/assignments/effective_config/feature_flags 由
    ContextAssembler 填充，字段均有明确类型；后续阶段不得在快照上追加字段。
    """

    model_config = ConfigDict(extra="forbid")

    actor: ActorContext
    scene: str = Field(min_length=1)
    profile: dict[str, Any] = Field(default_factory=dict)
    conversation: dict[str, Any] = Field(default_factory=dict)
    assignments: tuple[dict[str, Any], ...] = ()
    effective_config: dict[str, Any] = Field(default_factory=dict)
    feature_flags: frozenset[str] = frozenset()


class PlannedAction(BaseModel):
    """Planner 产出的一条工具调用计划（PolicyGate 逐条校验）。"""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str


ResponseMode = Literal["direct", "cards", "socratic", "degraded"]


class ActionPlan(BaseModel):
    """Planner 输出：单次运行的结构化计划，最多 5 个动作。"""

    model_config = ConfigDict(extra="forbid")

    intent: str
    goal: str
    actions: list[PlannedAction] = Field(max_length=5)
    response_mode: ResponseMode
    needs_web_search: bool = False


class ToolRisk(StrEnum):
    """工具风险等级：决定 Policy 前置条件与执行隔离。"""

    READ = "read"
    LEARNING_ACTION = "learning_action"
    WRITE = "write"
    EXTERNAL = "external"
    ROLE_RESTRICTED = "role_restricted"


ExecutionStatus = Literal["executed", "replayed", "shadow_skipped", "not_executed"]


class ToolResult(BaseModel):
    """工具统一返回包装（Executor 组装，异常转换为稳定错误码）。

    execution_status 是账本计数的唯一事实源：只有 executed 才计入
    tool_call_count；Runtime 直接读取，禁止通过 error_code 或结果长度猜测。
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: Any = None
    error_code: str | None = None
    user_message: str | None = None
    retryable: bool = False
    degraded: bool = False
    # 执行元数据（账本真实性：Runtime 直接读取，不重新猜测）
    latency_ms: int = 0
    idempotency_key: str | None = None
    execution_status: ExecutionStatus = "not_executed"


class ButlerEnvelope(BaseModel):
    """运行时最终输出信封（ResultComposer 组装，Facade 转既有 {code,message,data}）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    intent: str
    text: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False
    trace: dict[str, Any] = Field(default_factory=dict)


class ButlerBudget(BaseModel):
    """单次运行预算：模型请求 / 工具调用 / 交互超时上限。

    允许调用方降低预算，不允许超过系统上限，也不允许 0 或负数。
    """

    model_config = ConfigDict(extra="forbid")

    max_model_requests: int = Field(default=3, ge=1, le=3)
    max_tool_calls: int = Field(default=5, ge=1, le=5)
    timeout_s: float = Field(default=20.0, gt=0, le=20.0)
