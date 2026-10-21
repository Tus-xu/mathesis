"""W3C tracecontext 贯穿（R11.5-C P0）：traceparent/tracestate/correlation_id。

- `TraceContext`：不可变单条链路上下文（trace_id=32hex、span_id=16hex、parent_span_id、
  tracestate、correlation_id、sampled）。
- API 边界（RequestContextMiddleware 内嵌）提取/生成；写库行（job/outbox/event/ai_runs）带
  trace_id + correlation_id；worker 侧从库行恢复上下文；provider 出站透传 `traceparent` 头。
- contextvar 隔离并发：`set_current / get_current / restore`。
"""
from __future__ import annotations

import contextvars
import os
import re
import uuid
from dataclasses import dataclass, field

_TRACE_VERSION = "00"
_TRACEPARENT_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str = field(default_factory=lambda: _new_span())
    parent_span_id: str | None = None
    tracestate: str | None = None
    correlation_id: str | None = None
    sampled: bool = True

    def as_traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"{_TRACE_VERSION}-{self.trace_id}-{self.span_id}-{flags}"

    def to_row(self) -> dict[str, str | None]:
        """库行投影：新建 span（下游子链路用本行 span 作 parent）。"""
        return {
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            # 出站 span：每次写库/投递生成新 span，保留 parent 便于串联日志
            "span_id": _new_span(),
            "parent_span_id": self.span_id,
        }

    def child(self) -> TraceContext:
        """派生子链路（span_id 刷新、parent 指向上游 span；trace 不变）。"""
        return TraceContext(
            trace_id=self.trace_id,
            parent_span_id=self.span_id,
            tracestate=self.tracestate,
            correlation_id=self.correlation_id,
            sampled=self.sampled,
        )


def _new_span() -> str:
    return uuid.uuid4().hex[:16]


def _new_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex


def new_trace(correlation_id: str | None = None) -> TraceContext:
    return TraceContext(trace_id=_new_trace_id(), correlation_id=correlation_id)


def parse_traceparent(header: str | None) -> tuple[str, str, str | None] | None:
    """严格解析 traceparent（RFC 7239 语义收敛）：合法返回 (trace_id, span_id, tracestate 无关)。

    返回 (trace_id, parent_span_id, flags)；非法/头缺失 → None（调用方决定生成新链路）。
    """
    if not header:
        return None
    m = _TRACEPARENT_RE.match(header.strip())
    if not m:
        return None
    version, trace_id, span_id, flags = m.groups()
    if version != _TRACE_VERSION:
        return None  # 仅支持 00 版本；更高版本不擅自降级
    return trace_id, span_id, flags


def request_trace(
    *,
    traceparent: str | None = None,
    tracestate: str | None = None,
    correlation_id: str | None = None,
) -> TraceContext:
    """API 边界入口：有合法 traceparent 则续链，否则新建。

    trace_id 全 0（未采样透传）视为无效 → 新建。
    """
    parsed = parse_traceparent(traceparent)
    if parsed:
        trace_id, parent_span_id, flags = parsed
        if trace_id != "0" * 32:
            return TraceContext(
                trace_id=trace_id,
                parent_span_id=parent_span_id,
                tracestate=tracestate,
                correlation_id=correlation_id,
                sampled=flags == "01",
            )
    return new_trace(correlation_id=correlation_id)


# ── contextvar（并发隔离；logging/structlog 可读 core.logging 的 request_id_var 同风格）──
_current: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar("reliability_trace", default=None)


def set_current(trace: TraceContext | None) -> contextvars.Token:
    return _current.set(trace)


def reset_current(token: contextvars.Token) -> None:
    _current.reset(token)


def get_current() -> TraceContext | None:
    return _current.get()


def restore(token: contextvars.Token) -> None:
    _current.reset(token)


def ensure_current() -> TraceContext:
    """取当前链路；无则新建并设置为当前（库写入前兜底，保证 trace 永不缺失）。"""
    ctx = get_current()
    if ctx is None:
        ctx = new_trace()
        set_current(ctx)
    return ctx


def provider_headers() -> dict[str, str]:
    """provider 出站请求头（LiteLLM extra_headers / httpx headers 透传）。"""
    ctx = get_current()
    if ctx is None:
        return {}
    headers = {"traceparent": ctx.as_traceparent()}
    if ctx.tracestate:
        headers["tracestate"] = ctx.tracestate
    if ctx.correlation_id:
        headers["x-correlation-id"] = ctx.correlation_id
    return headers


def env_correlation_id() -> str | None:
    """部署级默认 correlation 前缀（如实例名），供无透传时标识来源。"""
    return os.getenv("SERVICE_INSTANCE_ID") or None
