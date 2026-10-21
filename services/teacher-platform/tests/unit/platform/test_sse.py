"""SSE 基建纯函数单测：Last-Event-ID 解析、data JSON 编码（契约：data 必须合法 JSON）。"""
from __future__ import annotations

import json

import pytest
from starlette.requests import Request
from starlette.types import Scope

from app.core.sse import _dumps, _parse_last_event_id


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


class TestParseLastEventId:
    def test_header(self):
        assert _parse_last_event_id(_req({"Last-Event-ID": "42"})) == 42

    def test_query_fallback(self):
        req = _req()
        # 构造带 query 的 scope
        scope = dict(req.scope)
        scope["query_string"] = b"lastEventId=7"
        assert _parse_last_event_id(Request(scope)) == 7

    def test_invalid_defaults_zero(self):
        assert _parse_last_event_id(_req({"Last-Event-ID": "abc"})) == 0
        assert _parse_last_event_id(_req()) == 0

    def test_negative_clamped_zero(self):
        assert _parse_last_event_id(_req({"Last-Event-ID": "-5"})) == 0


class TestDumps:
    def test_valid_json(self):
        raw = _dumps({"progress": 25, "message": "demo 步骤 1/4", "中文": "值"})
        assert json.loads(raw) == {"progress": 25, "message": "demo 步骤 1/4", "中文": "值"}

    def test_no_python_repr(self):
        # 回归：sse-starlette 3.4.11 对 data 直接 str()，必须由 _dumps 显式 JSON 化
        assert "'" not in _dumps({"a": 1})

    def test_default_str_for_non_jsonable(self):
        import uuid

        raw = _dumps({"id": uuid.UUID("12345678-1234-5678-1234-567812345678")})
        assert json.loads(raw)  # 不抛错
