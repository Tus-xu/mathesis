"""RV-WB · 论文工作台「自研内核」稳定错误码 / 能力注册表 / 异常层级。

本模块**只依赖标准库**（不 import 本仓库其它模块），因此可被任意层安全引入，
且天然规避循环导入 —— 与 `core/xf/models.py` 的设计取向一致。

需求变更（第 2 阶段）
--------------------
论文工作台原四个功能此前**全部**由讯飞星辰线上工作流承载。第 2 阶段只保留
`writing_polish`（写作润色，即 `wf_writing_polish_V2`）走讯飞；**其余三项改由
平台自研内核承载**：

| 功能 | key | 内核 | 端点 |
|---|---|---|---|
| LaTeX 编辑 | `latex_editing` | LLM 网关 + TeX 前置安全校验 | `POST /wb/latex:edit` |
| 方法检索 | `method_rag` | 文献内核（Crossref，真实检索，不生成） + LLM 生成方法说明 | `POST /wb/method:search` |
| 写作润色 | `writing_polish` | 讯飞星辰线上工作流（**保留**） | `POST /xf/workflows/writing_polish/run` |
| 学术翻译 | `academic_translate` | LLM 网关（同一次调用产出段级对照） | `POST /wb/translate` |

`/xf/*` 端点保持不变（既有消费者不受影响），新增 `wb` 端点。

契约一致性（定案：加法式别名）
------------------------------
`/wb/*` 的响应**逐键对齐**前端渲染契约；`/xf/*` 保留做 A/B 与回退，将来把某功能
切回讯飞时前端零改动。既有键（`model`/`latencyMs`/`engines`/`items`）**全部保留**，
新字段一律**新增**（非破坏性）。「诚实降级」体现在**值**上（拿不到就空 + `warning`），
不体现在缺键上（缺键会让前端读到 `undefined` 而静默出错）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# ==========================================================================
# 稳定错误码（前端 switch 的唯一契约；一旦发布不得改名）
# ==========================================================================

WB_LLM_NOT_CONFIGURED = "WB_LLM_NOT_CONFIGURED"
WB_LLM_TIMEOUT = "WB_LLM_TIMEOUT"
WB_LLM_BAD_OUTPUT = "WB_LLM_BAD_OUTPUT"
WB_LLM_ERROR = "WB_LLM_ERROR"
WB_LITERATURE_UNAVAILABLE = "WB_LITERATURE_UNAVAILABLE"
WB_TEX_SOURCE_INVALID = "WB_TEX_SOURCE_INVALID"
#: 通用**请求侧**错误（缺字段 / 参数非法）。
WB_UNKNOWN = "WB_UNKNOWN"


# ==========================================================================
# 异常层级
# ==========================================================================


class WbError(Exception):
    """RV-WB 基类异常。

    契约（与 `XfError` 同形，便于前端统一处理）：
    - `code`：稳定错误码（前端 switch 用，见本模块常量）；
    - `http_status`：由 API 层直接采用的 HTTP 状态码；
    - `retryable`：是否建议重试；
    - `retry_after`：503/504 时建议的 `Retry-After` 秒数（可空）；
    - `detail`：附加结构化字段（合并进 `to_dict`）。

    **脱敏铁律**：`message` 由调用方保证不含密钥/凭据原文。
    """

    code: str = WB_UNKNOWN
    http_status: int = 400
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


class WbLlmNotConfigured(WbError):
    """LLM 凭据未配置（`RESEARCH_LLM_API_KEY` / `SPARK_API_PASSWORD` 缺失）。"""

    code = WB_LLM_NOT_CONFIGURED
    http_status = 503
    retryable = False


class WbLlmTimeout(WbError):
    """LLM 调用超时（超过 `wb_llm_timeout_s()` 的硬性墙钟上限）。"""

    code = WB_LLM_TIMEOUT
    http_status = 504
    retryable = True


class WbLlmBadOutput(WbError):
    """模型输出不可用（空输出 / 未过安全校验 / 结构无法解析）。

    `retryable=False`：**输出不合格是确定性失败** —— 同样的输入与提示词再打一次，
    大概率产出同样不合格的输出，只会白烧额度。区别于 `WB_LLM_TIMEOUT`（瞬时负载，
    可重试）。
    """

    code = WB_LLM_BAD_OUTPUT
    http_status = 502
    retryable = False


class WbLlmError(WbError):
    """LLM 调用的其它失败（上游 5xx / 网络 / 未分类）。"""

    code = WB_LLM_ERROR
    http_status = 502
    retryable = True


class WbLiteratureUnavailable(WbError):
    """文献内核不可用（provider 超时 / 限流 / 网络 / 上游错误）。

    诚实降级：**绝不**在检索失败时用模型生成"看起来像文献"的内容顶替。
    """

    code = WB_LITERATURE_UNAVAILABLE
    http_status = 503
    retryable = True


class WbTexSourceInvalid(WbError):
    """LaTeX 源码未通过前置安全校验（fail-closed，用户入参）。"""

    code = WB_TEX_SOURCE_INVALID
    http_status = 400


class WbBadRequest(WbError):
    """请求侧参数缺失/非法（400）。"""

    code = WB_UNKNOWN
    http_status = 400


# ==========================================================================
# 能力注册表（`GET /wb/capabilities` 数据源）
# ==========================================================================


@dataclass(frozen=True)
class WbCapability:
    """论文工作台单个功能的元信息（供前端**数据驱动**渲染，不必手抄字段名）。"""

    key: str
    title: str
    #: 承载引擎：`self`（平台自研内核）/ `xingchen`（讯飞星辰线上工作流）。
    engine: str
    #: 内核标识：`llm` / `literature` / `llm+tex-guard` / `xingchen-workflow`。
    kernel: str
    method: str
    path: str
    description: str
    #: 入参 schema（`字段名 -> 类型`；`?` 后缀表示可选）。供前端表单数据驱动。
    request_schema: dict[str, str] = field(default_factory=dict)
    #: 成功响应的键集合（供前端断言/渲染，逐键对齐）。
    response_keys: tuple[str, ...] = ()


#: 论文工作台右侧功能栏的四个功能（引擎归属见模块 docstring）。
CAPABILITIES: tuple[WbCapability, ...] = (
    WbCapability(
        key="latex_editing",
        title="LaTeX 编辑",
        engine="self",
        kernel="llm+tex-guard",
        method="POST",
        path="/wb/latex:edit",
        description="用 LLM 网关编辑/规范化 LaTeX 片段；入参与模型输出均经 TeX 前置安全校验。",
        # 规范字段名（与 `/xf/workflows/latex_editing/run` 对齐）；`?`=可选。
        request_schema={
            "file_content": "string",
            "instruction": "string",
            "target_file?": "string",
            "source?": "string (compat alias of file_content)",
            "filename?": "string (compat alias of target_file)",
        },
        response_keys=(
            "success",
            "changes",
            "final_updated_code",
            "security_msg",
            "target_file",
            "filename",
            "instruction",
            "source",
            "model",
            "latencyMs",
            "engines",
        ),
    ),
    WbCapability(
        key="method_rag",
        title="方法检索",
        engine="self",
        kernel="literature",
        method="POST",
        path="/wb/method:search",
        description="经文献内核（Crossref）真实检索方法素材；方法说明字段由模型生成并如实标注。",
        # 规范字段名：`method_name`（`query` 为兼容别名）。
        request_schema={
            "method_name": "string",
            "query?": "string (compat alias of method_name)",
            "limit?": "integer",
        },
        response_keys=(
            "query",
            "method_name",
            "method_name_en",
            "keywords",
            "core_formula",
            "common_scenario",
            "assumptions",
            "precondition_checklist",
            "limitation",
            "reference_note",
            "sources",
            "evidence_records",
            "ai_generated_fields",
            "warning",
            "count",
            "provider",
            "note",
        ),
    ),
    WbCapability(
        key="writing_polish",
        title="写作润色",
        engine="xingchen",
        kernel="xingchen-workflow",
        method="POST",
        path="/xf/workflows/writing_polish/run",
        description="学术段落润色（保留讯飞星辰线上工作流 wf_writing_polish_V2）。",
        request_schema={"parameters": "object", "uid?": "string"},
        response_keys=("workflow", "flowId", "latencyMs", "result", "rawText", "upstreamCode"),
    ),
    WbCapability(
        key="academic_translate",
        title="学术翻译",
        engine="self",
        kernel="llm",
        method="POST",
        path="/wb/translate",
        description="中英学术互译，保留公式/引用/专有名词；同一次调用产出段级对照。",
        # 规范字段名：`target_lang` / `source_lang`（camelCase 为兼容别名）。
        request_schema={
            "text": "string",
            "target_lang": "string",
            "source_lang?": "string",
            "targetLang?": "string (compat alias of target_lang)",
            "sourceLang?": "string (compat alias of source_lang)",
        },
        response_keys=(
            "source_lang",
            "target_lang",
            "sourceLang",
            "targetLang",
            "translated_text",
            "bilingual_blocks",
            "formulas_preserved",
            "formulas_total",
            "text",
            "model",
            "latencyMs",
            "warning",
        ),
    ),
)

CAPABILITY_BY_KEY: dict[str, WbCapability] = {c.key: c for c in CAPABILITIES}

#: `writing_polish` 对应的讯飞 flow 环境变量（**唯一**保留的线上工作流）。
XF_FLOW_ENV_FOR_WRITING_POLISH = "RESEARCH_XF_FLOW_WRITING_POLISH"

#: `/wb/*` LLM 调用的**默认**硬性墙钟上限（秒）。可用 `RESEARCH_WB_LLM_TIMEOUT_SECONDS` 覆盖。
#:
#: 取 60s 的依据：网关自身 read 超时（默认 120s）会被"缓慢滴流字节"不断重置，
#: 单靠它无法给出有界响应；99.8s 级别的失控调用是不可接受的（生产实测过）。
#: 这里显式钉一个更紧的默认，超时即 `WB_LLM_TIMEOUT`(504)。
WB_LLM_TIMEOUT_DEFAULT_SECONDS = 60.0


def wb_llm_timeout_s() -> float:
    """`/wb/*` LLM 调用的硬性墙钟上限（秒）。

    优先读 `RESEARCH_WB_LLM_TIMEOUT_SECONDS`（须 > 0）；缺省 `60s`。
    """
    raw = (os.getenv("RESEARCH_WB_LLM_TIMEOUT_SECONDS") or "").strip()
    if raw:
        try:
            override = float(raw)
        except ValueError:
            override = None  # 非法数值 → 退回默认
        if override is not None and override > 0:
            return override
    return WB_LLM_TIMEOUT_DEFAULT_SECONDS


__all__ = [
    "WB_LLM_NOT_CONFIGURED",
    "WB_LLM_TIMEOUT",
    "WB_LLM_BAD_OUTPUT",
    "WB_LLM_ERROR",
    "WB_LITERATURE_UNAVAILABLE",
    "WB_TEX_SOURCE_INVALID",
    "WB_UNKNOWN",
    "WbError",
    "WbLlmNotConfigured",
    "WbLlmTimeout",
    "WbLlmBadOutput",
    "WbLlmError",
    "WbLiteratureUnavailable",
    "WbTexSourceInvalid",
    "WbBadRequest",
    "WbCapability",
    "CAPABILITIES",
    "CAPABILITY_BY_KEY",
    "XF_FLOW_ENV_FOR_WRITING_POLISH",
    "WB_LLM_TIMEOUT_DEFAULT_SECONDS",
    "wb_llm_timeout_s",
]
