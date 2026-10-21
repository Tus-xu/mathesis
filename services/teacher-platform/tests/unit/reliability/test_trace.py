"""W3C trace 贯穿单测：解析/续链/生成/contextvar/outbound header 投影。"""
from __future__ import annotations

import asyncio

from app.reliability.trace import (
    TraceContext,
    get_current,
    new_trace,
    parse_traceparent,
    provider_headers,
    request_trace,
    reset_current,
    set_current,
)


def test_parse_valid_traceparent() -> None:
    parsed = parse_traceparent("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")
    assert parsed == ("0123456789abcdef0123456789abcdef", "0123456789abcdef", "01")


def test_parse_rejects_invalid() -> None:
    assert parse_traceparent(None) is None
    assert parse_traceparent("") is None
    assert parse_traceparent("00-zz-zz-01") is None
    assert parse_traceparent("01-0123456789abcdef0123456789abcdef-0123456789abcdef-01") is None  # 版本不支持
    assert parse_traceparent("garbage") is None


def test_request_trace_continues_existing_link() -> None:
    tp = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
    ctx = request_trace(traceparent=tp, tracestate="vendor=1", correlation_id="corr-9")
    assert ctx.trace_id == "a" * 32
    assert ctx.parent_span_id == "b" * 16
    assert ctx.tracestate == "vendor=1"
    assert ctx.correlation_id == "corr-9"
    assert ctx.sampled is True


def test_request_trace_generates_when_absent() -> None:
    ctx = request_trace(correlation_id="new-corr")
    assert len(ctx.trace_id) == 32
    assert ctx.parent_span_id is None
    assert ctx.as_traceparent().startswith("00-")


def test_unsampled_flags() -> None:
    ctx = request_trace(traceparent="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-00")
    assert ctx.sampled is False


def test_child_spawns_new_span_same_trace() -> None:
    parent = new_trace(correlation_id="c1")
    child = parent.child()
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == parent.span_id
    assert child.span_id != parent.span_id


async def test_contextvar_propagation_and_restore() -> None:
    assert get_current() is None
    ctx = new_trace(correlation_id="ctx-1")
    tok = set_current(ctx)
    try:
        assert get_current() is ctx
        spawned: list[TraceContext | None] = []

        async def _worker() -> None:
            await asyncio.sleep(0)
            spawned.append(get_current())  # 子任务继承 contextvar

        await asyncio.gather(_worker())
        assert spawned == [ctx]
    finally:
        reset_current(tok)
    assert get_current() is None


def test_provider_headers_emit_w3c() -> None:
    tp = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
    ctx = request_trace(traceparent=tp, tracestate="vendor=2", correlation_id="corr-x")
    tok = set_current(ctx)
    try:
        headers = provider_headers()
        # 出站头 = 当前服务自身 span（trace 续链、span 归己），trace_id 与 parent 一致
        assert headers["traceparent"] == ctx.as_traceparent()
        assert headers["traceparent"].startswith(f"00-{ctx.trace_id}-")
        assert headers["tracestate"] == "vendor=2"
        assert headers["x-correlation-id"] == "corr-x"
    finally:
        reset_current(tok)
    assert provider_headers() == {}  # 无上下文 → 空（不伪造 header）


def test_row_projection_keeps_trace_and_new_span() -> None:
    ctx = new_trace(correlation_id="row-1")
    row = ctx.to_row()
    assert row["trace_id"] == ctx.trace_id
    assert row["correlation_id"] == "row-1"
    assert row["parent_span_id"] == ctx.span_id
    assert row["span_id"] != ctx.span_id
