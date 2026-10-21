"""博查（Bocha）Web Search —— 管家联网问答的检索兜底（架构 §2 定案）。

- citation 的 URL 必须来自博查真实返回，禁编造（B5 抽查可达性，B1 §3.2）。
- 结果写 web_search_cache（query_hash=sha256(query|freshness|count)），幂等免重复搜索。
- key 缺失抛 WebSearchError（BLOCKED_ENV 语义），不返回假结果。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"


class WebSearchError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str
    site: str = ""
    source: str = "bocha"


def _freshness(days: int) -> str:
    if days <= 1:
        return "oneDay"
    if days <= 7:
        return "oneWeek"
    if days <= 31:
        return "oneMonth"
    if days <= 366:
        return "oneYear"
    return "noLimit"


def query_hash(query: str, *, freshness_days: int, count: int) -> str:
    raw = f"{query}|{freshness_days}|{count}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_response(data: dict) -> list[WebResult]:
    pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    out: list[WebResult] = []
    for p in pages:
        url = str(p.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue  # 非 http(s) 链接不作为证据
        out.append(
            WebResult(
                title=str(p.get("name") or "").strip(),
                url=url,
                snippet=str(p.get("summary") or p.get("snippet") or "").strip(),
                site=str(p.get("siteName") or "").strip(),
            )
        )
    return out


def _parse_cached(payload: dict) -> list[WebResult]:
    return [WebResult(**r) for r in payload.get("results", [])]


@dataclass
class WebSearchCacheStore:
    """web_search_cache 读写（可选；无 DB 会话时退化为纯调用）。"""

    session_factory: object = None

    async def get(self, qhash: str) -> Optional[list[WebResult]]:
        if self.session_factory is None:
            return None
        from sqlalchemy import select

        from app.db.models.knowledge import WebSearchCache

        async with self.session_factory() as session:
            row = (await session.execute(
                select(WebSearchCache).where(WebSearchCache.query_hash == qhash)
            )).scalar_one_or_none()
            return _parse_cached(row.results) if row else None

    async def put(self, qhash: str, results: list[WebResult]) -> None:
        if self.session_factory is None or not results:
            return
        from app.db.models.knowledge import WebSearchCache

        payload = {"results": [
            {"title": r.title, "url": r.url, "snippet": r.snippet, "site": r.site, "source": r.source}
            for r in results
        ]}
        async with self.session_factory() as session:
            merged = await session.get(WebSearchCache, qhash)
            if merged is None:
                session.add(WebSearchCache(query_hash=qhash, results=payload))
            else:
                merged.results = payload
            await session.commit()


async def search_web(
    query: str,
    *,
    freshness_days: int = 365,
    count: int = 10,
    api_key: Optional[str] = None,
    cache: Optional[WebSearchCacheStore] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> list[WebResult]:
    """博查联网搜索。幂等：同 query+参数走缓存。签名对齐架构 §8。"""
    qhash = query_hash(query, freshness_days=freshness_days, count=count)
    if cache is not None:
        cached = await cache.get(qhash)
        if cached:
            return cached
    key = api_key or os.getenv("BOCHA_API_KEY")
    if not key:
        raise WebSearchError("BOCHA_API_KEY 未配置（key 只经环境变量/注入配置分发）")
    body = {
        "query": query,
        "freshness": _freshness(freshness_days),
        "summary": True,
        "count": count,
        "page": 1,
    }
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.post(
            BOCHA_ENDPOINT, json=body, headers={"Authorization": f"Bearer {key}"}
        )
    finally:
        if own_client:
            await client.aclose()
    if resp.status_code != 200:
        raise WebSearchError(
            f"博查搜索失败: HTTP {resp.status_code} {resp.text[:200]}",
            status_code=resp.status_code,
        )
    results = _parse_response(resp.json())
    if cache is not None:
        await cache.put(qhash, results)
    return results
