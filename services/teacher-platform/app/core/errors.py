"""统一错误码表（契约：code===0 成功，非 0 抛 ApiError；40301 role_denied 必须真实产生）。

对齐 `docs/handoff/frontend-mock-ref/client.js`：`{ code, message, data, request_id }`，
非零 code 由前端抛 `ApiError(code, message)`，`error_key` 供前端 i18n（可选字段）。
"""
from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    OK = 0
    BAD_REQUEST = 40000
    UNAUTHORIZED = 40100
    TOKEN_EXPIRED = 40101
    FORBIDDEN = 40300
    ROLE_DENIED = 40301          # 非 teacher 角色 / 越权——契约测试断言的关键错误码
    NOT_FOUND = 40400
    NOT_FOUND_RESOURCE = 40401   # 知识点等资源不存在（mock 契约）
    NOT_FOUND_ITEM = 40402       # 题目/作业等条目不存在（mock 契约，B5 断言）
    CONFLICT = 40900
    VALIDATION = 42200
    RATE_LIMITED = 42900
    INTERNAL = 50000
    DEPENDENCY_UNAVAILABLE = 50300


class ApiError(Exception):
    """业务异常：由全局异常处理器统一转为 fail 信封。

    `data` 用于必须带上下文的失败（平台契约 command.py：expected_revision 不匹配 →
    409 且返回 server snapshot/diff，仅给冲突码无法让客户端决定 reload/reapply/discard）。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int | None = None,
        data: Any = None,
    ):
        self.code = code
        self.message = message
        self.data = data
        # 无显式 status_code 时按 code 段推断 HTTP 状态
        self.status_code = status_code or _default_status(code)

    def __str__(self) -> str:
        return f"[{self.code.value} {self.code.name}] {self.message}"


def _default_status(code: ErrorCode) -> int:
    mapping = {
        ErrorCode.OK: 200,
        ErrorCode.BAD_REQUEST: 400,
        ErrorCode.UNAUTHORIZED: 401,
        ErrorCode.TOKEN_EXPIRED: 401,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.ROLE_DENIED: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.NOT_FOUND_RESOURCE: 404,
        ErrorCode.NOT_FOUND_ITEM: 404,
        ErrorCode.CONFLICT: 409,
        ErrorCode.VALIDATION: 422,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.INTERNAL: 500,
        ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    }
    return mapping.get(code, 500)
