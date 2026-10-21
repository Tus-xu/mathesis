"""SearchAdapter 单元测试（R11；MockTransport 不触网）。

覆盖：请求 body 形状、provenance（含 time）、URL 过滤、幂等缓存、空结果、错误分类。
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.ppt_v2.adapters.errors import AdapterFatalError, AdapterRetryableError
from app.ppt_v2.adapters.search import SearchAdapter

_PAGES = {"data": {"webPages": {"value": [
    {"name": "椭圆定义-好站", "url": "https://good.example/a", "summary": "摘要A", "siteName": "例站", "dateLastCrawled": "2026-09-01T10:00:00Z"},
    {"name": "坏链接", "url": "javascript:void(0)", "summary": "x"},
]}}}


def _adapter(handler, *, cache=None) -> SearchAdapter:
    return SearchAdapter(
        api_key="k",
        cache=cache,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_search_request_shape_and_provenance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.bochaai.com/v1/web-search"
        assert request.headers["Authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert body == {"query": "椭圆", "freshness": "oneYear", "summary": True, "count": 5, "page": 1}
        return httpx.Response(200, json=_PAGES)

    sa = _adapter(handler)
    out = await sa.search("椭圆", freshness_days=365, count=5)
    assert out.cached is False
    assert len(out.hits) == 1  # javascript: 被过滤
    hit = out.hits[0]
    assert hit.url == "https://good.example/a"
    assert hit.title == "椭圆定义-好站"
    assert hit.site == "例站"
    assert hit.published_time == "2026-09-01T10:00:00Z"
    assert hit.source == "bocha"


async def test_search_idempotent_cache_no_second_network() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_PAGES)

    sa = _adapter(handler)
    await sa.search("椭圆", freshness_days=365, count=5)
    out2 = await sa.search("椭圆", freshness_days=365, count=5)
    assert len(calls) == 1
    assert out2.cached is True


async def test_search_empty_result_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"webPages": {"value": []}}})

    sa = _adapter(handler)
    out = await sa.search("不存在的查询", count=3)
    assert out.hits == []
    assert out.cached is False


async def test_search_5xx_retryable() -> None:
    sa = _adapter(lambda request: httpx.Response(503, json={}))
    with pytest.raises(AdapterRetryableError):
        await sa.search("q")


async def test_search_4xx_fatal() -> None:
    sa = _adapter(lambda request: httpx.Response(401, json={}))
    with pytest.raises(AdapterFatalError):
        await sa.search("q")


async def test_search_missing_key_fails_closed() -> None:
    sa = SearchAdapter(api_key="")
    with pytest.raises(AdapterFatalError, match="SEARCH_API_KEY"):
        await sa.search("q")
