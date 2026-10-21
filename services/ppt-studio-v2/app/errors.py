"""错误码 + 统一异常（8200 契约信封；独立实现，不 import 8100 app.core.errors）。

错误码语义与既有 teacher 契约保持一致（40301 角色拒绝等），
但代码为本服务自持，保证 8100 停机不影响 8200。
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(int, Enum):
    BAD_REQUEST = 40000
    UNAUTHORIZED = 40100
    FORBIDDEN = 40300
    NOT_FOUND = 40400
    CONFLICT = 40900
    VALIDATION = 42200
    QUOTA_EXCEEDED = 42900
    INTERNAL = 50000


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int | None = None,
        error_key: str | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code or _guess_status(code)
        self.error_key = error_key or code.name.lower()
        super().__init__(message)


def _guess_status(code: ErrorCode) -> int:
    return int(str(code.value)) // 100
