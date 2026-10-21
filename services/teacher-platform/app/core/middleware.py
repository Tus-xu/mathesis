"""请求上下文中间件：request_id + W3C trace 贯穿 + 响应头注入 + 请求日志（PII 白名单）。

`request.state.request_id` 与 `x-request-id` 响应头、`response.py` 信封里的 request_id 三者一致；
structlog contextvars 绑定同一 request_id。
R11.5-C：同一中间件内解析 traceparent/tracestate/x-correlation-id → 写入
`request.state.trace`（reliability.trace.TraceContext）与 contextvar，并注入
`traceparent`/`x-correlation-id`/`x-trace-id` 响应头——API→DB(outbox/job/event)→worker→provider
的 trace 贯穿起点（无外来 header 时现场生成新链路）。
"""
from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .logging import new_request_id, request_id_var


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        rid = new_request_id()
        token = request_id_var.set(rid)
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid)

        # R11.5-C：W3C trace 贯穿（续链或新建）
        from ..reliability.trace import request_trace, reset_current, set_current

        trace = request_trace(
            traceparent=request.headers.get("traceparent"),
            tracestate=request.headers.get("tracestate"),
            correlation_id=request.headers.get("x-correlation-id"),
        )
        ctx_token = set_current(trace)
        request.state.trace = trace

        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            # 请求日志：method/path/status/latency/user（PII 字段在白名单外，自动丢弃）
            logger = structlog.get_logger("teacher.http")
            logger.info(
                "http",
                method=request.method,
                path=request.url.path,
                status=status,
                latency_ms=latency_ms,
                user_id=getattr(request.state, "user_id", ""),
                trace_id=trace.trace_id,
                correlation_id=trace.correlation_id,
            )
            request_id_var.reset(token)
            structlog.contextvars.unbind_contextvars("request_id")
            reset_current(ctx_token)
        response.headers["x-request-id"] = rid
        response.headers["traceparent"] = trace.as_traceparent()
        response.headers["x-trace-id"] = trace.trace_id
        if trace.correlation_id:
            response.headers["x-correlation-id"] = trace.correlation_id
        return response
