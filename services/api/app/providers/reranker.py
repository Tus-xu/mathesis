"""Rerank Provider（providers/reranker.py）

bge-reranker 本地服务化调用（CPU 即可）。
服务地址从配置读取（默认 localhost:8081）。
协议假设：POST {base}/rerank  {query, documents} → {"scores": [float]}
（兼容 text-embeddings-inference / 常见 rerank 包装服务；
 未部署时健康检查失败，RAG 自动降级为 RRF 直取，接口保持不变。）
"""

import time

import structlog

from app.config import settings
from app.providers.http import get_http

logger = structlog.get_logger()


class RerankProvider:
    """bge-reranker 本地精排服务"""

    name: str = "reranker"

    def __init__(self) -> None:
        self._base_url = settings.reranker_base_url.rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self._base_url)

    async def rerank(
        self,
        query: str,
        docs: list[str],
        *,
        request_id: str = "",
    ) -> list[tuple[int, float]]:
        """精排：返回 [(原下标, 分数)]，按分数降序。"""
        if not self.available:
            raise RuntimeError("Reranker service URL not configured")

        log = logger.bind(request_id=request_id, provider="reranker")
        t0 = time.monotonic()
        client = get_http()
        resp = await client.post(
            f"{self._base_url}/rerank",
            json={"query": query, "documents": docs},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

        # 兼容两种返回：{"scores": [...]} 或 [{"index": i, "score": s}]
        if isinstance(data, dict) and "scores" in data:
            pairs = [(i, float(s)) for i, s in enumerate(data["scores"])]
        elif isinstance(data, list):
            pairs = [
                (int(item.get("index", i)), float(item.get("score", 0.0)))
                for i, item in enumerate(data)
            ]
        else:
            raise ValueError("unrecognized reranker response format")

        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info("rerank.ok", latency_ms=int((time.monotonic() - t0) * 1000), docs=len(docs))
        return pairs

    async def health_check(self) -> dict:
        """探测 Reranker 服务可用性"""
        if not self.available:
            return {"ok": False, "latency_ms": 0, "error": "Service not configured"}

        t0 = time.monotonic()
        client = get_http()
        try:
            resp = await client.post(
                f"{self._base_url}/rerank",
                json={"query": "health", "documents": ["health"]},
                timeout=5.0,
            )
            resp.raise_for_status()
            latency = int((time.monotonic() - t0) * 1000)
            return {"ok": True, "latency_ms": latency}
        except Exception as e:
            latency = int((time.monotonic() - t0) * 1000)
            return {"ok": False, "latency_ms": latency, "error": str(e)[:200]}
