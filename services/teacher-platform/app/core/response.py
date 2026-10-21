"""统一响应壳 ok/fail（契约不可破坏项）。

前端语义（`client.js`）：`code === 0` 成功返回 `data`；非 0 抛 `ApiError(code, message)`；
`request_id` 用于链路追踪；`error_key` 可选供 i18n。
"""
from __future__ import annotations

from typing import Any

from .errors import ErrorCode


def ok(data: Any = None, *, request_id: str = "") -> dict[str, Any]:
    return {"code": ErrorCode.OK.value, "message": "ok", "data": data, "request_id": request_id}


def fail(
    code: ErrorCode | int,
    message: str,
    *,
    request_id: str = "",
    error_key: str | None = None,
) -> dict[str, Any]:
    return {
        "code": int(code),
        "message": message,
        "data": None,
        "request_id": request_id,
        **({"error_key": error_key} if error_key else {}),
    }
