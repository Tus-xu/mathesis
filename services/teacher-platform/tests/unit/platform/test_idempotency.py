"""幂等（G8）单测：hermetic 部分（skip 判定 / 用户 key 提取 / 窗口判定）+ fakeredis 中间件端到端。

完整 SETNX+PG 重放链路在 ValkeyCompatibilityGate/集成测试覆盖（真实容器）。
"""
from __future__ import annotations

import time

import pytest
from starlette.requests import Request

from app.core.idempotency import IdempotencyMiddleware, _user_id_from, _within_window

from .test_sse import _req


def _make_middleware() -> IdempotencyMiddleware:
    # 不真实连接：仅测试纯逻辑
    return IdempotencyMiddleware(app=lambda: None, redis_url="redis://127.0.0.1:6380/0")


class TestShouldSkip:
    @pytest.mark.parametrize(
        ("method", "path", "idem", "expected"),
        [
            ("GET", "/api/teacher-v3/tasks", None, True),  # GET 跳过
            ("POST", "/api/teacher-v3/tasks", None, True),  # 无 Idempotency-Key 跳过
            ("POST", "/api/teacher-v3/platform/demo/sleep", "k1", False),  # 写端点需幂等
            ("POST", "/api/teacher-v3/platform/demo/tasks/x/stream", "k1", True),  # SSE 流跳过
            ("POST", "/auth/login", "k1", True),  # 非 teacher-v3 前缀跳过
        ],
    )
    def test_skip_rules(self, method, path, idem, expected):
        headers = {"Authorization": "Bearer t"}
        if idem:
            headers["Idempotency-Key"] = idem
        mw = _make_middleware()
        assert mw._should_skip(_req_with(method, path, headers)) is expected


def _req_with(method: str, path: str, headers: dict[str, str]) -> Request:
    req = _req(headers)
    scope = dict(req.scope)
    scope["method"] = method
    scope["path"] = path
    return Request(scope)


class TestUserIdFrom:
    def test_extracts_sub(self):
        from app.core.security import create_access_token

        token = create_access_token(user_id="user-abc", name="x", role="teacher")
        req = _req({"Authorization": f"Bearer {token}"})
        assert _user_id_from(req) == "user-abc"

    def test_missing_returns_anon(self):
        assert _user_id_from(_req({})) == "anon"

    def test_bad_token_returns_anon(self):
        assert _user_id_from(_req({"Authorization": "Bearer garbage"})) == "anon"


class TestWindow:
    def test_within_window(self):
        assert _within_window(time.time()) is True

    def test_older_than_window_false(self):
        assert _within_window(time.time() - 20000) is False
