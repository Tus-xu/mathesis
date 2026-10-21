"""RV-WB · 论文工作台「自研内核」包（第 2 阶段需求变更）。

论文工作台原四个功能此前全部由讯飞星辰线上工作流承载。第 2 阶段只保留
`writing_polish`（写作润色 / `wf_writing_polish_V2`）走讯飞；其余三项改由
**平台自研内核**承载，本包即这三个内核的服务层：

- `models`   稳定错误码 / 能力注册表 / 异常层级（仅标准库）；
- `service`  三个自研能力的服务实现：
  - `edit_latex`      → LLM 网关 + TeX 前置安全校验（复用 `core/xf/latex.py`）；
  - `search_methods`  → 文献内核（`core/literature/crossref.py`，真实检索）；
  - `translate`       → LLM 网关。

端点由 `core/api/wb_routes.py` 暴露，并在 `core/api/routes.py` 末尾以
`router.include_router` 挂载（因此随父路由在**双前缀**下可达）。

设计边界
--------
- 本包**不新造** LLM 调用：一律经 `core/llm/gateway.py`；文献检索一律经
  `core/literature/*`，绝不生成"看起来像文献"的假结果；
- 不导出任何敏感物；`__all__` 只列公开入口。
"""

from __future__ import annotations

from .models import (
    CAPABILITIES,
    WbCapability,
    WbError,
    WbLlmBadOutput,
    WbLlmError,
    WbLlmNotConfigured,
    WbLlmTimeout,
    WbLiteratureUnavailable,
    WbTexSourceInvalid,
)

__all__ = [
    "CAPABILITIES",
    "WbCapability",
    "WbError",
    "WbLlmNotConfigured",
    "WbLlmTimeout",
    "WbLlmBadOutput",
    "WbLlmError",
    "WbLiteratureUnavailable",
    "WbTexSourceInvalid",
]
