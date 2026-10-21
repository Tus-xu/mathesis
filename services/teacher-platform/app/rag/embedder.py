"""Embedder —— 全平台唯一 embedding 入口（G2 定案，LlmGateway 无 embed）。

模型与通道由环境变量驱动（配置外置，禁止硬编码供应商）：
- EMBEDDING_BASE_URL    OpenAI 兼容端点（默认讯飞星火 MaaS）
- EMBEDDING_MODEL       模型 ID（默认 xop3qwen8bembedding，768 维）
- EMBEDDING_API_KEY     API Key（兼容旧名 SILICONFLOW_API_KEY）
- EMBEDDING_DIMS        期望向量维度，-1 表示不校验（随模型自动）
- EMBEDDING_RERANKER_MODEL rerank 模型（无则重排通道不可用，检索侧降级为语义序）

返回向量做 L2 归一化（余弦语义，pgvector 距离不受影响）。
密钥纪律（B2 红线 3）：key 只从环境变量/注入配置读取，不进代码与文档；
key 缺失抛 EmbedderError（调用方按 BLOCKED_ENV 处理），不做任何降级假 embedding。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

import httpx

DEFAULT_BASE_URL = "https://maas-api.cn-huabei-1.xf-yun.com/v2"
DEFAULT_MODEL = "xop3qwen8bembedding"
EMBEDDING_DIMS = 768
# 兼容旧通道（SiliconFlow BGE-M3 曾为默认）；切换后仅供 rerank 兜底参考
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
BGE_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


class EmbedderError(RuntimeError):
    """embedding/rerank 调用失败（配置缺失、网络、上游错误）。"""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _env_int(name: str, default: int) -> int:
    """整数环境变量；缺失/非法回退默认（EMBEDDING_DIMS=-1 表示不校验维度）。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


@dataclass(frozen=True)
class RerankHit:
    index: int
    relevance_score: float


class Embedder:
    """OpenAI 兼容 embedding + rerank 的唯一入口（async，httpx 连接池复用）。

    全参数可用环境变量覆盖（EMBEDDING_*），构造注入优先于环境变量。
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reranker_model: Optional[str] = None,
        base_url: Optional[str] = None,
        dims: Optional[int] = None,
        timeout_s: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_key = api_key
        self._model = model or os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL
        self._reranker_model = reranker_model or os.getenv("EMBEDDING_RERANKER_MODEL") or BGE_RERANKER_MODEL
        self._base_url = (base_url or os.getenv("EMBEDDING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._dims = dims if dims is not None else _env_int("EMBEDDING_DIMS", EMBEDDING_DIMS)
        self._timeout_s = timeout_s
        self._client = client  # 测试注入 MockTransport 用

    def _key(self) -> str:
        key = self._api_key or os.getenv("EMBEDDING_API_KEY") or os.getenv("SILICONFLOW_API_KEY")
        if not key:
            raise EmbedderError(
                "EMBEDDING_API_KEY 未配置（key 只经环境变量/注入配置分发，B2 红线 3）"
            )
        return key

    async def _post(self, path: str, payload: dict) -> dict:
        async def _call() -> dict:
            client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
            try:
                resp = await client.post(
                    f"{self._base_url}{path}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._key()}"},
                )
            finally:
                if self._client is None:
                    await client.aclose()
            if resp.status_code != 200:
                raise EmbedderError(
                    f"SiliconFlow {path} 失败: HTTP {resp.status_code} {resp.text[:200]}",
                    status_code=resp.status_code,
                )
            return resp.json()

        return await _call()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → 1024 维归一化向量。顺序与输入一致；空列表直接返回。"""
        if not texts:
            return []
        data = await self._post(
            "/embeddings",
            {"model": self._model, "input": texts, "encoding_format": "float"},
        )
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        if len(items) != len(texts):
            raise EmbedderError(f"embedding 返回条数不符: 请求 {len(texts)} 得到 {len(items)}")
        vectors: list[list[float]] = []
        for item in items:
            vec = item.get("embedding")
            if not isinstance(vec, list):
                raise EmbedderError(f"embedding 返回结构异常：无 embedding 数组")
            if self._dims > 0 and len(vec) != self._dims:
                raise EmbedderError(f"embedding 维度异常: 期望 {self._dims} 维，实际 {len(vec)} 维")
            vectors.append(_l2_normalize([float(v) for v in vec]))
        return vectors

    async def rerank(self, query: str, documents: list[str], *, top_n: Optional[int] = None) -> list[RerankHit]:
        """BGE-reranker-v2-m3 精排。返回按相关性降序的 (index, score)。"""
        if not documents:
            return []
        payload: dict = {"model": self._reranker_model, "query": query, "documents": documents}
        if top_n is not None:
            payload["top_n"] = top_n
        data = await self._post("/rerank", payload)
        results = data.get("results", [])
        hits = [RerankHit(index=int(r["index"]), relevance_score=float(r["relevance_score"]))
                for r in results]
        hits.sort(key=lambda h: h.relevance_score, reverse=True)
        return hits
