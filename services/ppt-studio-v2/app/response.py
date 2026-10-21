"""响应信封 ok/fail（8200 自持拷贝，确保不 import 8100 app.core.response）。"""
from __future__ import annotations

from typing import Any

from app.errors import ErrorCode


def ok(data: Any = None, *, message: str = "ok", request_id: str = "") -> dict[str, Any]:
    return {"code": 0, "message": message, "data": data, "request_id": request_id}


def fail(
    code: ErrorCode,
    message: str,
    *,
    request_id: str = "",
    error_key: str = "",
) -> dict[str, Any]:
    return {
        "code": int(code.value),  # str,Enum 会泄漏字符串值，统一转 int 契约
        "message": message,
        "error_key": error_key or code.name.lower(),
        "data": None,
        "request_id": request_id,
    }
