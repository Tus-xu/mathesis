"""RV-XF · 讯飞星辰「线上工作流」代理包（论文工作台功能内核）。

本包把讯飞星辰（Xingchen）的四个**线上工作流**封装成本服务（research-vnext，
端口 8300）内部可复用的能力，并由 `core/api/xf_routes.py` 暴露为 REST 端点：

- `models`   工作流注册表 / 异常层级 / 稳定错误码 / 配置模型 / 掩码工具
- `client`   非流式工作流调用引擎（脱敏铁律在此落地）
- `service`  面向 API 的元信息、健康检查与带审计的运行入口
- `latex`    LaTeX → PDF 编译（**复用** `core/export/compiler.py` 的 xelatex 内核）

设计边界
--------
- **不复用** `services/api/app/providers/xingchen.py`（跨服务边界，禁止 import），
  只照抄其协议形状（URL / 鉴权头 / 请求体 / 错误码语义）。
- 配置一律从环境变量 `RESEARCH_XF_*` 读取（见 `.env.example`），
  **绝不**在本包任何 tracked 文件中写入真实密钥。
- 本包不导出任何敏感物（密钥 / 密文）；`__all__` 只列公开入口。
"""

from __future__ import annotations

from .models import (
    WORKFLOW_BY_KEY,
    WORKFLOWS,
    XfError,
    XfResult,
    WorkflowSpec,
)

__all__ = [
    "WORKFLOWS",
    "WORKFLOW_BY_KEY",
    "WorkflowSpec",
    "XfError",
    "XfResult",
]
