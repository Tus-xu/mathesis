"""RV-XF · 工作流注册表 / 异常层级 / 稳定错误码 / 配置模型。

本模块**只依赖标准库**：不 import 本仓库其它模块，避免任何循环导入与耦合。
真实密钥只从环境变量读取（由 `client.load_settings` 负责），本模块只提供
`XfSettings` 容器与纯函数式掩码工具。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# ==========================================================================
# 稳定错误码（前端 switch 的唯一契约；一旦发布不得改名）
# ==========================================================================

XF_NOT_CONFIGURED = "XF_NOT_CONFIGURED"
XF_DISABLED = "XF_DISABLED"
XF_TIMEOUT = "XF_TIMEOUT"
XF_RATE_LIMITED = "XF_RATE_LIMITED"
XF_CONCURRENCY = "XF_CONCURRENCY"
XF_UNAUTHORIZED = "XF_UNAUTHORIZED"
XF_QUOTA = "XF_QUOTA"
XF_UPSTREAM_ERROR = "XF_UPSTREAM_ERROR"
XF_TRANSPORT = "XF_TRANSPORT"
# 请求侧（非上游）错误
XF_UNKNOWN_WORKFLOW = "XF_UNKNOWN_WORKFLOW"
XF_BAD_REQUEST = "XF_BAD_REQUEST"
# LaTeX 编译分支
XF_TEX_SOURCE_INVALID = "XF_TEX_SOURCE_INVALID"
XF_TEX_KERNEL_UNAVAILABLE = "XF_TEX_KERNEL_UNAVAILABLE"
XF_TEX_COMPILE_FAILED = "XF_TEX_COMPILE_FAILED"
XF_TEX_TIMEOUT = "XF_TEX_TIMEOUT"


# ==========================================================================
# 工作流注册表
# ==========================================================================


@dataclass(frozen=True)
class WorkflowSpec:
    """单个线上工作流的元信息（key → env → 中文标题/说明/必填参数）。"""

    key: str
    env_key: str
    title: str
    description: str
    required_parameters: tuple[str, ...]


#: 论文工作台右侧功能栏一一对应的四个线上工作流。
#:
#: `required_parameters` 为**真机探针实测**的上游必填字段（对空 parameters 的
#: `22500 Start node protocol error` 响应解析而得），供前端表单预校验。
WORKFLOWS: tuple[WorkflowSpec, ...] = (
    WorkflowSpec(
        key="latex_editing",
        env_key="RESEARCH_XF_FLOW_LATEX_EDITING",
        title="LaTeX 编辑",
        description="对论文正文/公式的 LaTeX 片段做规范化与结构编辑。",
        required_parameters=(
            "AGENT_USER_INPUT",
            "project_id",
            "instruction",
            "target_file",
            "file_content",
        ),
    ),
    WorkflowSpec(
        key="method_rag",
        env_key="RESEARCH_XF_FLOW_METHOD_RAG",
        title="方法检索（RAG）",
        description=(
            "基于方法库检索与综合，为「方法」章节提供带来源的素材。"
            "（注意：当前配置的 flow_id 真机返回 20201 Flow ID not found，"
            "需人工核对后替换。）"
        ),
        required_parameters=("AGENT_USER_INPUT",),
    ),
    WorkflowSpec(
        key="writing_polish",
        env_key="RESEARCH_XF_FLOW_WRITING_POLISH",
        title="写作润色",
        description="学术段落润色：在保持语义与术语一致的前提下提升表达。",
        required_parameters=("AGENT_USER_INPUT",),
    ),
    WorkflowSpec(
        key="academic_translate",
        env_key="RESEARCH_XF_FLOW_ACADEMIC_TRANSLATE",
        title="学术翻译",
        description="中英文学术互译（保留公式、引用与专有名词）。",
        required_parameters=("AGENT_USER_INPUT", "text", "source_lang", "target_lang"),
    ),
)

#: key → spec 索引。
WORKFLOW_BY_KEY: dict[str, WorkflowSpec] = {w.key: w for w in WORKFLOWS}


# ==========================================================================
# 掩码工具（脱敏铁律的实现细节）
# ==========================================================================


def mask_secret(value: str | None) -> str:
    """把任意敏感串掩码成「前 4 位 + ***」。

    空值返回空串；不足 4 位时只保留首字符。任何日志/响应**只允许**出现本函数
    的返回值，绝不允许出现原值。
    """
    if not value:
        return ""
    v = str(value)
    if len(v) <= 1:
        return "***"
    return f"{v[:4]}***" if len(v) > 4 else f"{v[:1]}***"


def mask_flow_id(value: str | None) -> str:
    """flow_id 掩码展示（如 `7495…7648`）；未配置返回空串。"""
    if not value:
        return ""
    v = str(value)
    if len(v) <= 8:
        return v
    return f"{v[:4]}…{v[-4:]}"


# ==========================================================================
# 配置模型（纯容器；真正的 env 读取在 client.load_settings）
# ==========================================================================


@dataclass(frozen=True)
class XfSettings:
    """RV-XF 有效配置（从 `RESEARCH_XF_*` 读出后的不可变快照）。"""

    enabled: bool = False
    base_url: str = ""
    app_id: str = ""
    api_key: str = ""
    api_secret: str = ""
    timeout_seconds: float = 120.0
    max_concurrency: int = 3
    #: env_key → flow_id
    flows: dict[str, str] = field(default_factory=dict)

    @property
    def has_credentials(self) -> bool:
        """是否具备调用所需的最小凭证（key/secret/base_url/app_id 全部非空）。"""
        return bool(
            self.base_url.strip()
            and self.app_id.strip()
            and self.api_key.strip()
            and self.api_secret.strip()
        )

    def flow_id(self, env_key: str) -> str:
        return (self.flows.get(env_key) or "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw.strip())
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def load_settings() -> XfSettings:
    """从环境变量构造 `XfSettings`（纯读取，无副作用）。

    只认 `RESEARCH_XF_*`（见 `.env.example`）；缺失即用安全默认值，
    由调用方决定是抛 `XfNotConfigured` 还是 `XfDisabled`。
    """
    flows: dict[str, str] = {}
    for spec in WORKFLOWS:
        value = (os.getenv(spec.env_key) or "").strip()
        if value:
            flows[spec.env_key] = value
    return XfSettings(
        enabled=_env_bool("RESEARCH_XF_ENABLED", False),
        base_url=(os.getenv("RESEARCH_XF_BASE_URL") or "").strip(),
        app_id=(os.getenv("RESEARCH_XF_APP_ID") or "").strip(),
        api_key=(os.getenv("RESEARCH_XF_API_KEY") or "").strip(),
        api_secret=(os.getenv("RESEARCH_XF_API_SECRET") or "").strip(),
        timeout_seconds=_env_float("RESEARCH_XF_TIMEOUT_SECONDS", 120.0),
        max_concurrency=_env_int("RESEARCH_XF_MAX_CONCURRENCY", 3),
        flows=flows,
    )


# ==========================================================================
# 异常层级
# ==========================================================================


class XfError(Exception):
    """RV-XF 基类异常。

    契约：
    - `code`：稳定错误码（前端 switch 用，见本模块常量）；
    - `http_status`：由 API 层直接采用的 HTTP 状态码；
    - `retryable`：是否建议重试（前端可据此决定要不要自动退避重试）；
    - `retry_after`：429/503 时建议的 `Retry-After` 秒数（可空）。

    **脱敏铁律**：`message` 在构造时已由调用方脱敏；`__str__` 只回 `message`，
    绝不拼接原 key/secret。
    """

    code: str = XF_UPSTREAM_ERROR
    http_status: int = 502
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        retry_after: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message or self.code
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = int(http_status)
        if retryable is not None:
            self.retryable = bool(retryable)
        self.retry_after = retry_after
        #: 附加结构化字段（如 latex 编译的 available/reason/hint），合并进 to_dict。
        self.detail: dict[str, Any] = detail or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            **self.detail,
        }
        if self.retry_after:
            out["retry_after"] = self.retry_after
        return out


class XfNotConfigured(XfError):
    """`RESEARCH_XF_*` 关键项缺失（base_url/app_id/key/secret/flow_id）。"""

    code = XF_NOT_CONFIGURED
    http_status = 503
    retryable = False


class XfDisabled(XfError):
    """总开关关闭（`RESEARCH_XF_ENABLED=false`）。"""

    code = XF_DISABLED
    http_status = 503
    retryable = False


class XfTimeout(XfError):
    """上游读取超时（20804 / 本地 read_timeout）。"""

    code = XF_TIMEOUT
    http_status = 504
    retryable = True


class XfRateLimited(XfError):
    """上游限流（20375/20376/20902/20903）。"""

    code = XF_RATE_LIMITED
    http_status = 429
    retryable = True


class XfConcurrency(XfError):
    """同账号并发处理中（20357）→ 本地 429。"""

    code = XF_CONCURRENCY
    http_status = 429
    retryable = True


class XfUnauthorized(XfError):
    """上游鉴权失败（HTTP 401/403；上游返回 `{"message": "..."}` 无 code）。"""

    code = XF_UNAUTHORIZED
    http_status = 502
    retryable = False


class XfQuotaExceeded(XfError):
    """模型授权/额度问题（20373）。"""

    code = XF_QUOTA
    http_status = 429
    retryable = False


class XfTransportError(XfError):
    """网络/连接层失败（DNS/连接被拒/TLS 等），未到达上游业务层。"""

    code = XF_TRANSPORT
    http_status = 502
    retryable = True


class XfUpstreamError(XfError):
    """上游其它错误（未知业务码 / 非 200 非鉴权类）。"""

    code = XF_UPSTREAM_ERROR
    http_status = 502
    retryable = False


@dataclass(frozen=True)
class XfResult:
    """一次成功工作流调用的结果。"""

    workflow: str
    flow_id: str
    latency_ms: int
    #: 解析后的结果 dict；非结构化输出时为 `{"text": <原始文本>}`。
    result: dict[str, Any]
    #: 上游返回的原始文本（所有 content 片段拼接）。
    raw_text: str
    #: 上游业务码（成功通常为 0 或缺失 → 归一为 0）。
    upstream_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            # 运行结果返回真实 flowId（供前端与审计关联）；掩码仅用于元信息列表。
            "flowId": self.flow_id,
            "latencyMs": self.latency_ms,
            "result": self.result,
            "rawText": self.raw_text,
            "upstreamCode": self.upstream_code,
        }
