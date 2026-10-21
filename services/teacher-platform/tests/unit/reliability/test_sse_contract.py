"""SSE 传输契约冻结单测：冻结头 / Last-Event-ID / replay 帧 id / wrapped live 帧 id。"""
from __future__ import annotations

import json

from starlette.requests import Request
from starlette.types import Scope

from app.core.sse import (
    FROZEN_SSE_HEADERS,
    _parse_last_event_id,
    _wrapped_live_seq_id,
    sse_response,
)


def _req(headers: dict[str, str] | None = None) -> Request:
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
    }
    return Request(scope)


class TestFrozenHeaders:
    def test_frozen_headers_always_applied(self) -> None:
        resp = sse_response(iter([]))
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"
        assert resp.headers["connection"] == "keep-alive"

    def test_caller_cannot_drift_from_frozen(self) -> None:
        # 调用方想传冲突缓存头 → 冻结头优先（防 SSE header 漂移）
        resp = sse_response(iter([]), headers={"Cache-Control": "max-age=3600"})
        assert resp.headers["cache-control"] == "no-cache"

    def test_frozen_constant_complete(self) -> None:
        assert set(FROZEN_SSE_HEADERS) == {"Cache-Control", "X-Accel-Buffering", "Connection"}


class TestWrappedLiveSeqId:
    """断点续传锚点：wrapped live 帧必须派生 id=seq（浏览器 Last-Event-ID 随帧推进）。"""

    def test_classroom_wrapped_shape(self) -> None:
        msg = {"payload": {"seq": 42, "event_id": "ev-42", "event_type": "slide.state", "payload": {}}}
        assert _wrapped_live_seq_id(msg) == "42"

    def test_direct_seq_fallback(self) -> None:
        assert _wrapped_live_seq_id({"seq": 7}) == "7"

    def test_no_seq_returns_empty(self) -> None:
        assert _wrapped_live_seq_id({"payload": {"event_type": "x"}}) == ""
        assert _wrapped_live_seq_id({}) == ""
        # 旧形状 None seq 不产生 "None" 字符串（防浏览器 Last-Event-ID 被污染）
        assert _wrapped_live_seq_id({"seq": None}) == ""


class TestLastEventId:
    def test_header_and_query_contract(self) -> None:
        assert _parse_last_event_id(_req({"Last-Event-ID": "42"})) == 42
        scope = dict(_req().scope)
        scope["query_string"] = b"lastEventId=7"
        assert _parse_last_event_id(Request(scope)) == 7
        assert _parse_last_event_id(_req({"Last-Event-ID": "garbage"})) == 0
        assert _parse_last_event_id(_req({"Last-Event-ID": "-3"})) == 0


class TestReconnectContract:
    def test_reconnect_contract_frame_order(self) -> None:
        """契约：连接首帧 retry，其次 snapshot，再 replay(带 id)，后接 live(带 id)。"""
        frames = [
            {"retry": 3000},
            {"event": "snapshot", "data": json.dumps({"status": "idle"})},
            {"event": "event", "data": json.dumps(
                {"seq": 3, "event_id": "e3", "event_type": "t", "payload": {}, "created_at": "t"}
            ), "id": "3"},
        ]
        assert frames[0] == {"retry": 3000}
        assert frames[1]["event"] == "snapshot"
        assert frames[2]["id"] == "3"  # 重连后 from=Last-Event-ID=3 续接 after(3)
