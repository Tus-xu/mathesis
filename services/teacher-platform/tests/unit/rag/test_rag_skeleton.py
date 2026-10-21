"""app.rag 骨架单测（纯逻辑部分，无 DB 依赖）。

DB 依赖路径（Retriever SQL/Workspace 会话）由 B5 集成测试覆盖；
此处验证：collections 词汇、Embedder 协议/归一化/错误语义、
websearch 参数映射、CRAG 评估器与 citation 组装。
"""

import json

import httpx
import pytest

from app.rag.collections import ALL_COLLECTIONS, NINE_PAGE_TYPES, validate_collection
from app.rag.embedder import EMBEDDING_DIMS, Embedder, EmbedderError
from app.rag.crag import CragPipeline, heuristic_assessor
from app.rag.retriever import RetrievedChunk
from app.rag.websearch import WebResult, _freshness, _parse_response, query_hash


def test_collections_vocabulary():
    assert set(ALL_COLLECTIONS) == {
        "kp_graph", "curriculum", "quiz_bank", "lesson_bench", "ppt_slots", "web_cache"
    }
    assert len(NINE_PAGE_TYPES) == 9 and "worked_example" in NINE_PAGE_TYPES
    with pytest.raises(ValueError):
        validate_collection("nope")


def _mock_embedder(handler) -> Embedder:
    return Embedder(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_embedder_normalizes_and_orders(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")  # 隔离 .env（EMBEDDING_MODEL）对默认模型的覆盖

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "BAAI/bge-m3"
        # 故意乱序返回，验证 index 排序还原
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0] * EMBEDDING_DIMS},
            {"index": 0, "embedding": [3.0] + [0.0] * (EMBEDDING_DIMS - 1)},
        ]})

    emb = _mock_embedder(handler)
    vecs = await emb.embed(["甲", "乙"])
    assert len(vecs) == 2
    assert abs(vecs[0][0] - 1.0) < 1e-9  # L2 归一化后首维为 1
    assert abs(sum(v * v for v in vecs[0]) - 1.0) < 1e-9
    await emb._client.aclose()


@pytest.mark.asyncio
async def test_embedder_dim_mismatch_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    emb = _mock_embedder(handler)
    with pytest.raises(EmbedderError, match="维度"):
        await emb.embed(["x"])
    await emb._client.aclose()


@pytest.mark.asyncio
async def test_embedder_no_key_raises(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)  # 隔离 .env：EMBEDDING_API_KEY 同样是有效 key 源
    emb = Embedder()
    with pytest.raises(EmbedderError, match="EMBEDDING_API_KEY"):
        await emb.embed(["x"])


def test_freshness_mapping():
    assert _freshness(1) == "oneDay"
    assert _freshness(7) == "oneWeek"
    assert _freshness(30) == "oneMonth"
    assert _freshness(365) == "oneYear"
    assert _freshness(1000) == "noLimit"


def test_bocha_parse_skips_bad_urls():
    data = {"data": {"webPages": {"value": [
        {"name": "椭圆定义", "url": "https://example.com/a", "summary": "第一定义…", "siteName": "例站"},
        {"name": "坏链接", "url": "javascript:void(0)"},
    ]}}}
    results = _parse_response(data)
    assert len(results) == 1 and results[0].url.startswith("https://")


def test_query_hash_stable():
    assert query_hash("椭圆", freshness_days=30, count=10) == query_hash("椭圆", freshness_days=30, count=10)
    assert query_hash("椭圆", freshness_days=30, count=10) != query_hash("双曲线", freshness_days=30, count=10)


def _chunk(content: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(collection="quiz_bank", doc_ref="quiz:1", chunk_index=0,
                          content=content, score=score, metadata={"locator": "P1"})


@pytest.mark.asyncio
async def test_heuristic_assessor_thresholds():
    hi = await heuristic_assessor("q", [_chunk("a", 0.9), _chunk("b", 0.8)])
    assert hi.quality == "correct"
    mid = await heuristic_assessor("q", [_chunk("a", 0.5)])
    assert mid.quality == "ambiguous"
    low = await heuristic_assessor("q", [])
    assert low.quality == "incorrect" and low.score == 0.0


@pytest.mark.asyncio
async def test_crag_answer_uses_chunks_and_citations():
    class FakeRetriever:
        async def retrieve(self, query, *, collections, top_k=6, rerank=True, tenant=None):
            return [_chunk("椭圆第一定义内容", 0.9), _chunk("第二定义内容", 0.8)]

    async def fake_llm(query, chunks):
        return "基于证据的回答"

    pipe = CragPipeline(FakeRetriever(), llm_chat=fake_llm)  # type: ignore[arg-type]
    ans = await pipe.answer("椭圆的第一定义", collections=["quiz_bank"])
    assert ans.used_web is False and ans.text == "基于证据的回答"
    assert ans.verdict == "correct"
    assert ans.citations[0]["source_type"] == "quiz_bank"
    assert ans.citations[0]["content_hash"]  # 证据必须带内容指纹


class _FixedRetriever:
    async def retrieve(self, query, *, collections, top_k=6, rerank=True, tenant=None):
        return [_chunk("内部证据", 0.9)]


async def _fixed_assessor(query, chunks):
    from app.rag.crag import CragAssessment

    return _FIXED_VERDICT


_FIXED_VERDICT = None


def _verdict_pipe(quality: str, web_results: list[WebResult]) -> CragPipeline:
    async def web(query, **kwargs):
        return web_results

    async def assess(query, chunks):
        from app.rag.crag import CragAssessment

        return CragAssessment(quality=quality, score=0.9 if quality == "correct" else 0.2,
                              reason="fixed")

    return CragPipeline(_FixedRetriever(), assessor=assess, web_search=web)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_crag_incorrect_discards_internal(monkeypatch):
    global _FIXED_VERDICT
    _FIXED_VERDICT = "incorrect"
    from app.rag.crag import WebResult as _WR
    pipe = _verdict_pipe("incorrect", [_WR(title="网", url="https://a.b", snippet="…")])
    ans = await pipe.answer("q", collections=["quiz_bank"])
    assert ans.used_web is True and ans.verdict == "incorrect"
    assert all(c["doc_ref"] is None for c in ans.citations)  # 内部证据被弃用


@pytest.mark.asyncio
async def test_crag_ambiguous_fuses_internal_and_web(monkeypatch):
    global _FIXED_VERDICT
    _FIXED_VERDICT = "ambiguous"
    from app.rag.crag import WebResult as _WR
    pipe = _verdict_pipe("ambiguous", [_WR(title="网", url="https://a.b", snippet="…")])
    ans = await pipe.answer("q", collections=["quiz_bank"])
    assert ans.used_web is True and ans.verdict == "ambiguous"
    kinds = {c["source_type"] for c in ans.citations}
    assert "quiz_bank" in kinds and "web" in kinds  # 内部 + web 并用
