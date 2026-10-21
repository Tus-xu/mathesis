"""SearchAdapter —— V2 博查 Web Search 适配（R11）。

现状差距（写入 R11 报告）：
1. legacy app/rag/websearch._parse_response 丢弃 published time 字段，无法满足 V2
   source provenance（time）要求；2. legacy 默认读 BOCHA_API_KEY，而 V2 密钥约定是
   SEARCH_API_KEY。因此 V2 adapter 实现薄客户端：只读复用 BOCHA_ENDPOINT / query_hash /
   _freshness（不改写 dirty websearch.py），同一请求 body 形状（contract 测试钉死），
   幂等缓存默认内存实现（R12 再接 DB 缓存）。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

from app.ppt_v2.adapters.dto import SearchHit, SearchOutcome
from app.ppt_v2.adapters.errors import AdapterFatalError, AdapterRetryableError
from app.rag.websearch import BOCHA_ENDPOINT, _freshness, query_hash


@dataclass
class InMemorySearchCache:
    """ttl 幂等缓存（同 query_hash 命中不触网）。"""

    ttl_s: float = 300.0
    _store: dict[str, tuple[list[SearchHit], float]] = field(default_factory=dict)

    def get(self, qhash: str) -> list[SearchHit] | None:
        row = self._store.get(qhash)
        if row is None:
            return None
        if time.monotonic() - row[1] > self.ttl_s:
            self._store.pop(qhash, None)
            return None
        return row[0]

    def put(self, qhash: str, hits: list[SearchHit]) -> None:
        self._store[qhash] = (hits, time.monotonic())


def _parse_hits(payload: dict) -> list[SearchHit]:
    """博查响应 → V2 SearchHit（仅 http(s) URL 作为证据；保留 published time）。"""
    pages = ((payload.get("data") or {}).get("webPages") or {}).get("value") or []
    out: list[SearchHit] = []
    for p in pages:
        url = str(p.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue  # 非 http(s) 链接不作为证据（与 legacy 同规则）
        out.append(
            SearchHit(
                title=str(p.get("name") or "").strip(),
                url=url,
                snippet=str(p.get("summary") or p.get("snippet") or "").strip(),
                site=str(p.get("siteName") or "").strip(),
                published_time=str(
                    p.get("dateLastCrawled") or p.get("datePublished") or ""
                ).strip()
                or None,
            )
        )
    return out


class SearchAdapter:
    """V2 博查搜索：provenance（含 time）+ 幂等缓存 + URL 过滤。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        cache: InMemorySearchCache | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._cache = cache if cache is not None else InMemorySearchCache()
        self._client = client
        self._timeout_s = timeout_s

    def _key(self) -> str:
        # R11.5-C fail-closed：显式空字符串 = 调用方显式声明未配置，**不回落环境变量**
        # （防止显式空时静默借用易变的环境凭据）；仅 None 允许环境兜底。
        if self._api_key is not None and not self._api_key.strip():
            raise AdapterFatalError("SEARCH_API_KEY 未配置（显式空 key 被拒绝；key 只经进程环境注入）")
        key = self._api_key or os.getenv("SEARCH_API_KEY")
        if not key:
            raise AdapterFatalError("SEARCH_API_KEY 未配置（key 只经进程环境注入）")
        return key

    async def search(
        self,
        query: str,
        *,
        freshness_days: int = 365,
        count: int = 10,
    ) -> SearchOutcome:
        """博查搜索；同 query+参数幂等（缓存命中不触网）。"""
        qhash = query_hash(query, freshness_days=freshness_days, count=count)
        cached = self._cache.get(qhash)
        if cached is not None:
            return SearchOutcome(hits=cached, query_hash=qhash, cached=True)

        body = {
            "query": query,
            "freshness": _freshness(freshness_days),
            "summary": True,
            "count": count,
            "page": 1,
        }
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
        try:
            resp = await client.post(
                BOCHA_ENDPOINT,
                json=body,
                headers={"Authorization": f"Bearer {self._key()}"},
            )
        finally:
            if own_client:
                await client.aclose()
        if resp.status_code != 200:
            exc_cls = AdapterRetryableError if resp.status_code >= 500 else AdapterFatalError
            raise exc_cls(f"博查搜索失败: HTTP {resp.status_code}", status_code=resp.status_code)
        hits = _parse_hits(resp.json())
        self._cache.put(qhash, hits)
        return SearchOutcome(hits=hits, query_hash=qhash, cached=False)
