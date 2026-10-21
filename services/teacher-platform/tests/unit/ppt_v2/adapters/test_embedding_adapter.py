"""EmbeddingAdapter 单元测试（R11；MockTransport 不触网）。"""
from __future__ import annotations

import httpx
import pytest

from app.ppt_v2.adapters.embedding import V2_INDEX_DIMS, EmbeddingAdapter
from app.ppt_v2.adapters.errors import AdapterFatalError
from app.rag.embedder import Embedder


def _adapter(handler) -> EmbeddingAdapter:
    emb = Embedder(
        api_key="test-key",
        dims=-1,  # V2 adapter 权威执行维度门（Embedder 关闭内部检查）
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return EmbeddingAdapter(embedder=emb, model="xop3qwen8bembedding")


async def test_embed_happy_path_metadata_and_order() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/embeddings" in str(request.url)
        return httpx.Response(200, json={"data": [
            {"index": 0, "embedding": [1.0, 0.0] + [0.0] * 766},
            {"index": 1, "embedding": [0.0, 1.0] + [0.0] * 766},
        ]})

    adapter = _adapter(handler)
    vecs = await adapter.embed(["甲", "乙"])
    assert len(vecs) == 2
    assert vecs[0].dim == V2_INDEX_DIMS == 768
    assert vecs[0].model == "xop3qwen8bembedding"
    assert vecs[0].normalized is True
    assert abs(sum(x * x for x in vecs[0].vector) - 1.0) < 1e-9
    assert vecs[0].text_hash != vecs[1].text_hash


async def test_embed_dim_mismatch_fails_closed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    adapter = _adapter(handler)
    with pytest.raises(AdapterFatalError, match="维度"):
        await adapter.embed(["x"])


async def test_embed_zero_vector_rejected() -> None:
    """R04 观察：现有 Embedder 全零向量原样放行；V2 adapter 必须拒绝（写索引前 fd）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 768}]})

    adapter = _adapter(handler)
    with pytest.raises(AdapterFatalError, match="全零"):
        await adapter.embed(["x"])


async def test_embed_empty_list_returns_empty() -> None:
    adapter = _adapter(lambda request: httpx.Response(500, json={}))
    assert await adapter.embed([]) == []
