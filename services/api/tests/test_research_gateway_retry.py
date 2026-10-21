"""A 方案 · 网关对瞬时下游故障的短重试 —— 边界测试。

**这个文件存在的意义是守住两条硬边界**（比证明"重试能工作"更重要）：
  1. **非幂等写请求绝不重试**：没有 §46 幂等键的 POST/PUT/PATCH/DELETE 一律不重试，
     否则会凭空多建一份数据 —— 这是比"页面 500"严重得多的故障。
  2. **确定性 500 不重试**：只重试命中**具体**瞬时特征串的 500（连接被掐/recovery/
     死锁/权限抖动）；代码 bug 造成的 500 重试三遍只是把同一个错误犯三遍。

做法：把上游调用 `_send_upstream` 换成一个**可编排的桩**（第 N 次返回什么），
然后对 `_forward` 发真实 Starlette Request，断言"上游被调用了几次"。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from app.gateway import research_gateway as gw


class _FakeUpstream:
    """`_send_upstream` 的桩：按预设脚本依次返回，并记录调用次数/收到的 body。"""

    def __init__(self, script: list[httpx.Response]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, method, url, *, headers, body, timeout):  # noqa: ANN001, ANN204
        self.calls.append({"method": method, "url": url, "body": body})
        resp = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        return resp, _FakeClient()


class _FakeClient:
    async def aclose(self) -> None:
        return None


def _resp(
    status: int, body: bytes = b"{}", content_type: str = "application/json"
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"content-type": content_type},
        content=body,
    )


def _request(method: str, *, headers: dict[str, str] | None = None, body: bytes = b"") -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": "/api/research-vnext/projects/p1/papers",
        "query_string": b"",
        "headers": raw_headers + [(b"host", b"127.0.0.1:8000")],
        "scheme": "http",
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope, receive=_receive(body))
    req.state.research_identity = _Identity()
    return req


def _receive(body: bytes):  # noqa: ANN202
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class _Identity:
    """`_build_signed_headers` / `_derive_access_context` 只需要极少字段 —— 给一个够用的替身。

    `security_version` 是**必填**：撤权后旧令牌必须立即失效，网关会把它写进 AccessContext
    （见 `_derive_access_context` 的说明），所以替身也必须提供。
    """

    user_id = "00000000-0000-0000-0000-000000000001"
    tenant_id = "00000000-0000-0000-0000-000000000002"
    session_id = "00000000-0000-0000-0000-000000000003"
    role = "researcher"
    roles = ("researcher",)
    capabilities: tuple[str, ...] = ()
    resource_scopes: tuple[str, ...] = ()
    security_version = 1


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch):
    """把退避调到 0（测试不必真等），并给一个测试密钥。

    ⚠️ 密钥**必须**给：`_build_signed_headers` 在没有 `RESEARCH_GATEWAY_HMAC_SECRET` 时会
    **fail closed**（503 `ACCESS_CONTEXT_MISSING`）—— 这是正确的生产行为，
    不允许"没有密钥就伪造一个无签名的 AccessContext"。所以这里显式提供测试密钥，
    而不是把那段保护去掉。
    """
    monkeypatch.setattr(gw.settings, "research_gateway_retry_backoff", 0.0, raising=False)
    monkeypatch.setattr(gw.settings, "research_gateway_retry_attempts", 2, raising=False)
    monkeypatch.setattr(
        gw.settings,
        "research_gateway_hmac_secret",
        "test-hmac-secret-for-retry-tests",
        raising=False,
    )


def test_signed_headers_fail_closed_without_secret(monkeypatch) -> None:
    """反向保护：没有密钥时**必须**拒绝伪造 AccessContext（不得静默降级）。"""
    monkeypatch.setattr(gw.settings, "research_gateway_hmac_secret", "", raising=False)
    with pytest.raises(gw._GatewayError) as ei:
        gw._build_signed_headers(_Identity(), trace_id="t-1")
    assert ei.value.http_status == 503
    assert ei.value.code == "ACCESS_CONTEXT_MISSING"


ASGI_TRANSIENT_500 = b'{"detail":"InsufficientPrivilegeError: could not connect to server"}'
BORING_500 = b'{"error":{"code":"INTERNAL","message":"NoneType has no attribute foo"}}'


# ── 边界 1：非幂等写请求绝不重试 ─────────────────────────────────────────


def test_post_without_idempotency_key_is_not_retryable() -> None:
    req = _request("POST", body=b'{"preset":"citation"}')
    assert (
        gw._is_retryable_request(req, b'{"preset":"citation"}') is False
    ), "没有幂等键的 POST 绝不能重试 —— 重试会凭空多建一份数据"


def test_post_with_body_idempotency_key_is_retryable() -> None:
    body = json.dumps({"preset": "citation", "client_request_id": "cid-1"}).encode()
    assert gw._is_retryable_request(_request("POST", body=body), body) is True


def test_post_with_header_idempotency_key_is_retryable() -> None:
    body = b'{"preset":"citation"}'
    req = _request("POST", headers={"X-Client-Request-Id": "cid-2"}, body=body)
    assert gw._is_retryable_request(req, body) is True


def test_non_json_body_without_key_is_not_retryable() -> None:
    assert gw._is_retryable_request(_request("PUT", body=b"not-json"), b"not-json") is False


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_are_retryable(method: str) -> None:
    assert gw._is_retryable_request(_request(method), b"") is True


# ── 边界 2：只有"瞬时"500 才重试 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_retries_on_transient_500_then_succeeds(monkeypatch) -> None:
    fake = _FakeUpstream([_resp(500, ASGI_TRANSIENT_500), _resp(200, b'{"items":[]}')])
    monkeypatch.setattr(gw, "_send_upstream", fake)
    out = await gw._forward(_request("GET"), "/projects/p1/papers")
    assert len(fake.calls) == 2, "瞬时 500 应触发一次重试"
    assert out.status_code == 200


@pytest.mark.asyncio
async def test_get_does_not_retry_on_deterministic_500(monkeypatch) -> None:
    fake = _FakeUpstream([_resp(500, BORING_500), _resp(200, b'{"items":[]}')])
    monkeypatch.setattr(gw, "_send_upstream", fake)
    out = await gw._forward(_request("GET"), "/projects/p1/papers")
    assert len(fake.calls) == 1, "确定性 500（代码 bug）不得重试 —— 重试只是把同一错误犯三遍"
    assert out.status_code == 500


@pytest.mark.asyncio
async def test_non_retryable_post_surfaces_first_failure(monkeypatch) -> None:
    fake = _FakeUpstream([_resp(500, ASGI_TRANSIENT_500), _resp(200, b"{}")])
    monkeypatch.setattr(gw, "_send_upstream", fake)
    body = b'{"preset":"citation"}'
    out = await gw._forward(_request("POST", body=body), "/projects/p1/agent/tasks")
    assert len(fake.calls) == 1, "无幂等键的 POST 即使遇到瞬时 500 也不能重试"
    assert out.status_code == 500


@pytest.mark.asyncio
async def test_retryable_post_with_key_retries(monkeypatch) -> None:
    fake = _FakeUpstream([_resp(503, b"server closed the connection"), _resp(200, b"{}")])
    monkeypatch.setattr(gw, "_send_upstream", fake)
    body = json.dumps({"preset": "citation", "client_request_id": "cid-9"}).encode()
    out = await gw._forward(_request("POST", body=body), "/projects/p1/agent/tasks")
    assert len(fake.calls) == 2, "带幂等键的 POST 应重试"
    assert out.status_code == 200


@pytest.mark.asyncio
async def test_attempts_exhausted_returns_last_failure(monkeypatch) -> None:
    fake = _FakeUpstream([_resp(500, ASGI_TRANSIENT_500)])  # 一直瞬时失败
    monkeypatch.setattr(gw, "_send_upstream", fake)
    out = await gw._forward(_request("GET"), "/projects/p1/papers")
    n = int(gw.settings.research_gateway_retry_attempts)
    assert len(fake.calls) == n, f"应在 {n} 次尝试后停止（不得无限重试）"
    assert out.status_code == 500


@pytest.mark.asyncio
async def test_4xx_never_retries(monkeypatch) -> None:
    fake = _FakeUpstream([_resp(404, b'{"error":{"code":"NOT_FOUND"}}')])
    monkeypatch.setattr(gw, "_send_upstream", fake)
    out = await gw._forward(_request("GET"), "/projects/gone/papers")
    assert len(fake.calls) == 1, "4xx 是确定性结果，重试没有意义"
    assert out.status_code == 404


@pytest.mark.asyncio
async def test_connection_error_is_retried_for_safe_request(monkeypatch) -> None:
    calls = {"n": 0}

    async def flaky(method, url, *, headers, body, timeout):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise gw._GatewayError(502, "UPSTREAM_UNAVAILABLE", "科研端不可达")
        return _resp(200, b"{}"), _FakeClient()

    monkeypatch.setattr(gw, "_send_upstream", flaky)
    out = await gw._forward(_request("GET"), "/projects/p1/papers")
    assert calls["n"] == 2, "连接级失败对安全请求应重试"
    assert out.status_code == 200


@pytest.mark.asyncio
async def test_connection_error_not_retried_for_plain_post(monkeypatch) -> None:
    calls = {"n": 0}

    async def always_fail(method, url, *, headers, body, timeout):  # noqa: ANN001, ANN202
        calls["n"] += 1
        raise gw._GatewayError(502, "UPSTREAM_UNAVAILABLE", "科研端不可达")

    monkeypatch.setattr(gw, "_send_upstream", always_fail)
    with pytest.raises(gw._GatewayError):
        await gw._forward(
            _request("POST", body=b'{"preset":"citation"}'), "/projects/p1/agent/tasks"
        )
    assert calls["n"] == 1, "连接级失败对**非幂等** POST 也不得重试"


def test_transient_markers_are_specific_not_generic() -> None:
    """特征串必须**具体**：不能用 error/database 这类宽泛词，否则真 bug 也会被重试。"""
    joined = b" ".join(gw._TRANSIENT_MARKERS)
    assert b"error" not in joined.split(), "不得使用裸 error 作为特征"
    assert b"database" not in gw._TRANSIENT_MARKERS, "不得使用裸 database"
    # 但必须覆盖已知的真实瞬时措辞
    for needed in (b"operationalerror", b"connection refused", b"deadlock detected"):
        assert needed in gw._TRANSIENT_MARKERS


async def test_send_upstream_returns_response_client_tuple(monkeypatch) -> None:
    """回归（2026-10-10 生产事故）：`_send_upstream` 必须按契约返回 `(response, client)` 元组。

    事故：实现只 `return await client.send(...)` 了单个 `Response`，`_forward` 的
    `upstream, client = ...` 解包直接 `TypeError` → 科研端所有 `/api/research-vnext/*`
    全量 500（前端呈现为"AI 服务暂时不可用"）。本文件其余用例全部桩掉
    `_send_upstream`，解包契约恰好无守卫——此用例走真实 httpx 栈补上该缺口。
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b'{"ok": true}'
        )

    real_factory = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any):  # noqa: ANN202
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(gw.httpx, "AsyncClient", _factory)

    got = await gw._send_upstream(
        "GET",
        "http://research-vnext.test:8300/api/research-vnext/projects",
        headers={"x-trace-id": "t1"},
        body=b"",
        timeout=httpx.Timeout(5.0),
    )
    assert isinstance(got, tuple) and len(got) == 2, "契约：必须 (response, client) 成对返回"
    upstream, client = got
    try:
        assert upstream.status_code == 200
        assert await upstream.aread() == b'{"ok": true}'
    finally:
        await upstream.aclose()
        await client.aclose()
