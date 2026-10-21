"""Trace 贯穿（8200 自持）：traceparent / x-correlation-id → trace_id/span。

- 生成 trace_id（32hex）/ span_id（16hex），写入 request.state 与 structlog context；
- 响应头回传：x-request-id / traceparent / x-trace-id；
- 落库由各 service 显式把 trace_id/correlation_id 写入 outbox/job 行。
"""
from __future__ import annotations

import secrets
import string
from hashlib import sha256

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_TRACE_ALPHABET = string.hexdigits


def _parse_traceparent(header: str) -> tuple[str, str] | None:
    parts = header.strip().split("-")
    if len(parts) == 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
        return parts[1], parts[2]
    return None


def make_trace_id() -> str:
    return sha256(secrets.token_bytes(24)).hexdigest()[:32]


def make_span_id() -> str:
    return sha256(secrets.token_bytes(16)).hexdigest()[:16]


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN201
        traceparent = request.headers.get("traceparent")
        parent = _parse_traceparent(traceparent) if traceparent else None
        trace_id = parent[0] if parent else make_trace_id()
        span_id = make_span_id()
        request.state.trace_id = trace_id
        request.state.correlation_id = request.headers.get("x-correlation-id") or trace_id
        request.state.request_id = request.headers.get("x-request-id") or make_span_id()

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            correlation_id=request.state.correlation_id,
            path=request.url.path,
            request_id=request.state.request_id,
        )

        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        response.headers["x-trace-id"] = trace_id
        response.headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
        return response
