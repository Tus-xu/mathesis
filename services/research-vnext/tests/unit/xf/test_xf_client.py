"""RV-XF · 工作流客户端单测（httpx.MockTransport 注入）。

覆盖：鉴权头/请求体形状、上游错误码映射、HTTP 401 message 透传、未配置、
content 三种形态解析，以及**脱敏铁律**（secret 永不外泄）。
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from research_vnext.core.xf import client as xf_client
from research_vnext.core.xf.models import (
    XfConcurrency,
    XfDisabled,
    XfNotConfigured,
    XfQuotaExceeded,
    XfRateLimited,
    XfTimeout,
    XfUnauthorized,
)

SECRET = "SUPER_SECRET_SENTINEL"
API_KEY = "KEY123"
FLOW_LATEX = "7495997193697947648"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例独立的信号量缓存 + 完整 XF 环境变量基线。"""
    xf_client._semaphores.clear()
    monkeypatch.setenv("RESEARCH_XF_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_XF_BASE_URL", "https://xingchen-api.xf-yun.com")
    monkeypatch.setenv("RESEARCH_XF_APP_ID", "app123")
    monkeypatch.setenv("RESEARCH_XF_API_KEY", API_KEY)
    monkeypatch.setenv("RESEARCH_XF_API_SECRET", SECRET)
    monkeypatch.setenv("RESEARCH_XF_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("RESEARCH_XF_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("RESEARCH_XF_FLOW_LATEX_EDITING", FLOW_LATEX)


def _run(handler, params=None, workflow="latex_editing"):
    transport = httpx.MockTransport(handler)
    return asyncio.run(
        xf_client.run_workflow(
            workflow, params or {"source": "x"}, uid="user-1", transport=transport
        )
    )


def _capture(record: dict, response: httpx.Response):
    def handler(request: httpx.Request) -> httpx.Response:
        record["url"] = str(request.url)
        record["headers"] = dict(request.headers)
        record["body"] = json.loads(request.content.decode("utf-8"))
        return response

    return handler


def _ok(content) -> httpx.Response:
    """构造一个成功响应，content 为字符串或 dict。"""
    return httpx.Response(
        200,
        json={"code": 0, "choices": [{"messages": [{"content": content}]}]},
    )


# --------------------------------------------------------------------------
# 形状
# --------------------------------------------------------------------------


def test_auth_header_and_payload_shape() -> None:
    rec: dict = {}
    _run(_capture(rec, _ok('{"latex":"x"}')), params={"source": "hello"})
    assert rec["headers"]["authorization"] == f"Bearer {API_KEY}:{SECRET}"
    assert rec["headers"]["content-type"] == "application/json"
    assert rec["url"].endswith("/workflow/v1/chat/completions")
    body = rec["body"]
    assert body["flow_id"] == FLOW_LATEX
    assert body["uid"] == "user-1"
    assert body["stream"] is False
    assert body["parameters"] == {"source": "hello"}


# --------------------------------------------------------------------------
# content 三种形态
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,expected",
    [
        ('{"latex":"x^2"}', {"latex": "x^2"}),          # JSON 字符串
        ({"latex": "y", "ok": True}, {"latex": "y", "ok": True}),  # dict
        ("plain text", {"text": "plain text"}),          # 纯文本
    ],
)
def test_content_forms(content, expected) -> None:
    result = _run(_capture({}, _ok(content)))
    assert result.result == expected
    assert result.upstream_code == 0
    assert result.latency_ms >= 0


# --------------------------------------------------------------------------
# 上游业务码映射
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,exc",
    [
        (20357, XfConcurrency),
        (20375, XfRateLimited),
        (20376, XfRateLimited),
        (20902, XfRateLimited),
        (20903, XfRateLimited),
        (20804, XfTimeout),
        (20373, XfQuotaExceeded),
    ],
)
def test_upstream_code_mapping(code, exc) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": code, "message": "upstream boom"})

    with pytest.raises(exc):
        _run(handler)


# --------------------------------------------------------------------------
# HTTP 401 message 原样透传
# --------------------------------------------------------------------------


def test_http_401_message_passthrough() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "HMAC secret key does not match"})

    with pytest.raises(XfUnauthorized) as ei:
        _run(handler)
    assert "HMAC secret key does not match" in str(ei.value)


# --------------------------------------------------------------------------
# 未配置 / 禁用
# --------------------------------------------------------------------------


def test_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RESEARCH_XF_BASE_URL",
        "RESEARCH_XF_APP_ID",
        "RESEARCH_XF_API_KEY",
        "RESEARCH_XF_API_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("未配置时不应发起网络请求")

    with pytest.raises(XfNotConfigured):
        _run(handler)


def test_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_XF_ENABLED", "false")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("禁用时不应发起网络请求")

    with pytest.raises(XfDisabled):
        _run(handler)


# --------------------------------------------------------------------------
# 脱敏铁律
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"message": "HMAC secret key does not match"}),
        httpx.Response(403, json={"message": "forbidden"}),
        httpx.Response(500, text=f"inner leaked {SECRET}"),
        httpx.Response(200, json={"code": 20357, "message": f"conc {SECRET}"}),
        httpx.Response(200, json={"code": 20373, "message": "quota"}),
    ],
)
def test_secret_never_leaks(response, caplog: pytest.LogCaptureFixture) -> None:
    """任何错误的 str() 与任何日志都不得出现 secret 明文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return response

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception) as ei:
            _run(handler)
    assert SECRET not in str(ei.value)
    assert SECRET not in caplog.text


def test_upstream_echoing_secret_is_scrubbed() -> None:
    """上游若回显 secret，也必须被覆写成掩码。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": f"key={SECRET} bad"})

    with pytest.raises(XfUnauthorized) as ei:
        _run(handler)
    assert SECRET not in str(ei.value)
    assert "***" in str(ei.value)


def test_unknown_workflow_is_400() -> None:
    from research_vnext.core.xf.models import XfError

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("未知工作流不应发起网络请求")

    with pytest.raises(XfError) as ei:
        _run(handler, workflow="does_not_exist")
    assert ei.value.http_status == 400
    assert ei.value.code == "XF_UNKNOWN_WORKFLOW"
