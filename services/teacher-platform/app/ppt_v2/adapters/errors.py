"""V2 Adapter 错误分类学（R11）。

分类：可重试 / 可降级 / 不可恢复。异常不携带 key、原文、presigned URL 等敏感内容；
只带分类、correlation id 与可选上游 status code，便于 redacted log 与降级决策。
"""
from __future__ import annotations

import uuid


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


class AdapterError(RuntimeError):
    """V2 adapter 统一异常基类（分类 + correlation id）。"""

    def __init__(
        self,
        message: str,
        *,
        correlation_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.correlation_id = correlation_id or new_correlation_id()
        self.status_code = status_code


class AdapterRetryableError(AdapterError):
    """可重试：上游 5xx / 超时 / 连接错误，调用方按 bounded retry 处理。"""


class AdapterDegradedError(AdapterError):
    """可降级：明确报错但存在降级路径（如搜索空结果、vision 无权访问）。"""


class AdapterFatalError(AdapterError):
    """不可恢复：维度不匹配 / 供应商拒绝参数 / 结构化输出非法（禁止伪装通过）。"""
