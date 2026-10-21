"""8201 服务级鉴权：X-Presenton-Service-Token 常量时间比较（fail-closed）。"""
from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from presenton_adapter.config import get_settings

TOKEN_HEADER = "X-Presenton-Service-Token"


class ServiceTokenMiddleware(BaseHTTPMiddleware):
    """除 /healthz 外全部路由要求服务令牌。"""

    PUBLIC_PREFIXES = ("/healthz",)

    async def dispatch(self, request: Request, call_next):  # noqa: ANN201
        path = request.url.path
        if any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)
        token = request.headers.get(TOKEN_HEADER, "")
        expected = get_settings().service_token
        if not token or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            return JSONResponse(
                status_code=401,
                content={
                    "code": 40100,
                    "message": "service token required or invalid",
                    "error_key": "unauthorized",
                    "data": None,
                    "request_id": "",
                },
                headers={"X-Service-Realm": "presenton-adapter"},
            )
        request.state.service_caller = "ppt-studio-v2" if token == expected else "unknown"
        return await call_next(request)
