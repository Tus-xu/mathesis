"""PPT Studio V2 平台能力复用契约测试（R04）。

目的：以不触网的确定性断言，把 R04「REUSE/WRAP/AVOID/REBUILD」决策固化为可回归守卫，
并显式暴露 768/1024 embedding 维度冲突。真实供应商 API 全部推迟到 R11（密钥轮换后
手工 opt-in），本文件不携带任何真实 key / 供应商响应。

审计依据（证据路径见 R04 报告 D:\\ppt-rescue-control\\20260909-R04\\r04-adapter-contract-audit.md）：
- teacher-platform Embedder：EMBEDDING_DIMS=768（app/rag/embedder.py:26）
- services/api embedding provider：EMBEDDING_DIMENSION=1024（services/api/app/providers/embedding.py:32）
  两服务不得交叉写同一索引（V2 铁律，dimension 冲突由本文件显式暴露）。
- Bocha：BOCHA_ENDPOINT + Bearer + query/freshness/summary/count/page + query_hash 缓存（app/rag/websearch.py）
- Retriever：六类混合 collections 语义（app/rag/collections.py）→ V2 AVOID，教材走 REBUILD
- LLM Gateway：无 embed（G2 定案）、thinking 默认关（app/llm/gateway.py / config.py）
- AuthZ：ResourceOwner 同校 + 教师白名单（app/core/authz.py）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from app.core.authz import ResourceOwner, assert_resource_owner
from app.core.errors import ApiError
from app.core.security import UserToken
from app.llm.ai_runs import MemoryAiRunsSink
from app.llm.config import USE_CASE_ROUTES, LlmSettings
from app.llm.gateway import LlmGateway
from app.rag.collections import ALL_COLLECTIONS, NINE_PAGE_TYPES
from app.rag.embedder import DEFAULT_MODEL, EMBEDDING_DIMS, Embedder, EmbedderError
from app.rag.websearch import WebSearchCacheStore, _freshness, query_hash, search_web

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
API_EMBEDDING_FILE = PLATFORM_ROOT.parent / "api" / "app" / "providers" / "embedding.py"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Embedding 维度契约（768 vs 1024 冲突显式暴露；无网络）
# ─────────────────────────────────────────────────────────────────────────────


def test_teacher_platform_embedding_dimension_is_768() -> None:
    """教师平台 embedding 契约维度（pgvector Vector(768)，knowledge.py:32 同源）。"""
    assert EMBEDDING_DIMS == 768


def test_generic_api_embedding_dimension_is_1024() -> None:
    """通用 API 8000 契约维度来自其源码常量（不跨包 import，避免 teacher app 遮蔽）。"""
    assert API_EMBEDDING_FILE.is_file(), f"services/api embedding provider 缺失: {API_EMBEDDING_FILE}"
    text = API_EMBEDDING_FILE.read_text(encoding="utf-8")
    m = re.search(r"^EMBEDDING_DIMENSION\s*=\s*(\d+)", text, re.MULTILINE)
    assert m, "services/api 缺少 EMBEDDING_DIMENSION 常量"
    assert int(m.group(1)) == 1024


def test_embedding_dimension_conflict_is_explicit() -> None:
    """铁律：768 ≠ 1024，两服务禁止交叉写同一索引（R04 决策 AVOID 的机器门）。"""
    api_text = API_EMBEDDING_FILE.read_text(encoding="utf-8")
    api_dim = int(re.search(r"^EMBEDDING_DIMENSION\s*=\s*(\d+)", api_text, re.MULTILINE).group(1))  # type: ignore[union-attr]
    assert EMBEDDING_DIMS != api_dim, "teacher(768) 与 api(1024) 维度必须不同——交叉写索引会静默损坏向量语义"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Embedder 线上契约（MockTransport 录制请求/响应形状，不触网）
# ─────────────────────────────────────────────────────────────────────────────


def _mock_embedder(handler) -> Embedder:
    return Embedder(
        api_key="test-key",
        model=DEFAULT_MODEL,
        dims=768,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_embedder_contract_request_shape() -> None:
    """线上契约：POST {base}/embeddings，Bearer，model=默认模型，encoding_format=float；维度 768 强制校验；密度弃乱序。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://maas-api.cn-huabei-1.xf-yun.com/v2/embeddings"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == DEFAULT_MODEL
        assert body["encoding_format"] == "float"
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [3.0] + [0.0] * 767},
            {"index": 0, "embedding": [2.0, 1.0] + [0.0] * 766},
        ]})

    emb = _mock_embedder(handler)
    try:
        vecs = await emb.embed(["甲", "乙"])
        assert len(vecs) == 2
        # 乱序响应必须按 index 还原：vecs[0]=index0([2,1,0…]→[2/√5,1/√5,…])，vecs[1]=index1([3,0…]→[1,0,…])
        assert abs(vecs[0][0] / vecs[1][0] - 2 / (5**0.5)) < 1e-9
        for v in (vecs[0], vecs[1]):
            assert abs(sum(x * x for x in v) - 1.0) < 1e-9  # L2 归一化
    finally:
        await emb._client.aclose()


@pytest.mark.asyncio
async def test_embedder_dim_mismatch_fails_closed() -> None:
    """服务端返回非 768 维 → fail closed（不写索引、不产出向量）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    emb = _mock_embedder(handler)
    try:
        with pytest.raises(EmbedderError, match="维度"):
            await emb.embed(["x"])
    finally:
        await emb._client.aclose()


@pytest.mark.asyncio
async def test_embedder_zero_vector_passes_unchanged() -> None:
    """R04 观察：全零向量能通过维度校验且 L2 归一化后仍是零向量（_l2_normalize norm==0 原样返回）。

    V2 EmbeddingAdapter 契约必须额外拒绝退化的全零向量（写索引前 fd），现有 Embedder 不负责此语义。
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 768}]})

    emb = _mock_embedder(handler)
    try:
        vecs = await emb.embed(["x"])
        assert vecs[0] == [0.0] * 768  # 现状：不抛错、不归一、原样通过
    finally:
        await emb._client.aclose()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Bocha 搜索契约（录制请求/响应；query_hash 幂等，无网络）
# ─────────────────────────────────────────────────────────────────────────────


def test_bocha_param_contract_and_url_filter() -> None:
    """freshness 映射边界（366→oneYear，1000→noLimit）。"""
    assert _freshness(1) == "oneDay"
    assert _freshness(366) == "oneYear"
    assert _freshness(1000) == "noLimit"
    assert query_hash("椭圆", freshness_days=365, count=10) == query_hash("椭圆", freshness_days=365, count=10)


@pytest.mark.asyncio
async def test_websearch_request_shape() -> None:
    """线上契约：固定 endpoint + Bearer + body 形状；仅 http(s) URL 进入证据。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.bochaai.com/v1/web-search"
        assert request.headers["Authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert body == {"query": "椭圆", "freshness": "oneYear", "summary": True, "count": 5, "page": 1}
        return httpx.Response(200, json={"data": {"webPages": {"value": [
            {"name": "好站", "url": "https://good.example/a", "summary": "摘要", "siteName": "例"},
            {"name": "坏链接", "url": "javascript:void(0)"},
        ]}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        res = await search_web("椭圆", freshness_days=365, count=5, api_key="k", client=client)
        assert len(res) == 1
        assert res[0].url == "https://good.example/a"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_websearch_cache_idempotency() -> None:
    """query_hash 幂等：同 query+参数第二次不触网（V2 SearchProvider 可包装复用）。"""

    class FakeCache(WebSearchCacheStore):
        def __init__(self) -> None:
            self.store: dict[str, object] = {}

        async def get(self, qhash: str):
            return self.store.get(qhash)

        async def put(self, qhash: str, results) -> None:
            self.store[qhash] = results

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"data": {"webPages": {"value": [
            {"name": "a", "url": "https://a.example", "summary": "s"},
        ]}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = FakeCache()
    try:
        await search_web("椭圆", api_key="k", cache=cache, client=client)
        await search_web("椭圆", api_key="k", cache=cache, client=client)
        assert len(calls) == 1, "同 query+参数必须命中缓存，禁止重复触网"
        assert query_hash("椭圆", freshness_days=365, count=10) in cache.store
    finally:
        await client.aclose()


# ─────────────────────────────────────────────────────────────────────────────
# 4. 混合 collections 词汇快照（V2 教材域 AVOID，REBUILD 的回归守卫）
# ─────────────────────────────────────────────────────────────────────────────


def test_legacy_collections_vocabulary_frozen() -> None:
    """六类混合集合必须保持原样：V2 教材内容单元不得混入其中（C 表 AVOID）。"""
    assert sorted(ALL_COLLECTIONS) == sorted(
        {"kp_graph", "curriculum", "quiz_bank", "lesson_bench", "ppt_slots", "web_cache"}
    )
    assert "textbook" not in ALL_COLLECTIONS
    # NINE_PAGE_TYPES 是旧版式词汇；V2 slide semantics 由 TeachingDeckSpec（R10）接管
    assert len(NINE_PAGE_TYPES) == 9


# ─────────────────────────────────────────────────────────────────────────────
# 5. AuthZ 归属契约（纯逻辑；V2 asset/example 的 owner 校验必须复用）
# ─────────────────────────────────────────────────────────────────────────────


def test_resource_owner_contract() -> None:
    teacher = UserToken("t1", "张老师", "teacher", "s1")
    other = UserToken("t2", "李老师", "teacher", "s1")
    cross_school = UserToken("t3", "王老师", "teacher", "s2")
    admin = UserToken("a1", "管理员", "admin", "s9")

    # 同校 + 白名单内放行（含 dict 形态）
    assert_resource_owner(teacher, ResourceOwner(school_id="s1", allowed_teacher_ids={"t1"}))
    assert_resource_owner(teacher, {"school_id": "s1", "owner_id": "t1"})
    # 白名单外同校教师拒绝（IDOR 防线）
    with pytest.raises(ApiError):
        assert_resource_owner(other, ResourceOwner(school_id="s1", allowed_teacher_ids={"t1"}))
    # 跨校拒绝（BOLA 防线）
    with pytest.raises(ApiError):
        assert_resource_owner(cross_school, ResourceOwner(school_id="s1", allowed_teacher_ids={"t1"}))
    # 资源不限教师但跨校仍拒绝
    with pytest.raises(ApiError):
        assert_resource_owner(cross_school, ResourceOwner(school_id="s1"))
    # admin 豁免
    assert_resource_owner(admin, ResourceOwner(school_id="s2", allowed_teacher_ids={"t1"}))
    # 空资源（新建）放行
    assert_resource_owner(teacher, None)


# ─────────────────────────────────────────────────────────────────────────────
# 6. LLM Gateway 契约（无 embed / thinking 默认关 / spark profile 路由；不触网）
# ─────────────────────────────────────────────────────────────────────────────


def _gateway(*, profile: str) -> LlmGateway:
    return LlmGateway(
        MemoryAiRunsSink(),
        settings=LlmSettings(
            active_profile=profile,
            api_key_spark="k",
            model_spark="spark-x2.5-4b",
            model_vision="xophunyuanocr",
            model_longform="spark-80b",
            real_router_enabled=False,
        ),
    )


def test_gateway_has_no_embed() -> None:
    """G2 定案：embedding 唯一入口是 Embedder；网关暴露 embed 即契约破坏。"""
    gw = _gateway(profile="matrix")
    assert not hasattr(gw, "embed"), "LlmGateway 必须不存在 embed 属性（G2 契约，读题/索引入口唯一）"
    with pytest.raises(AttributeError, match="embedding 唯一入口"):
        gw.embed  # noqa: B018  # 契约断言：属性访问必抛（__getattr__ 拦截）


def test_gateway_thinking_default_off_for_spark() -> None:
    """R11 前提：spark 文本用例 default 不开启 thinking（结构化解析干扰）；V2 需按用例显式开启。"""
    gw = _gateway(profile="spark")
    kwargs = gw._call_kwargs("spark-x2.5-4b", use_case="chat", temperature=0.3, tools=None)
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert kwargs["timeout"] == USE_CASE_ROUTES["chat"].timeout_s


def test_spark_profile_route_resolution() -> None:
    """spark profile：vision→讯飞图像理解模型；配置 model_longform 后长文与首页对话均走 80B
    （cost 敏感点：V2 adapter 需按用例决定是否 pin 4B，见 R11 模型意图）。"""
    gw = _gateway(profile="spark")
    assert gw._settings.resolve_route("vision").primary == "xophunyuanocr"
    assert gw._settings.resolve_route("long_form").primary == "spark-80b"
    # config.py 注释事实：model_longform 存在时 "首页/管家对话切 80B（4B thinking 过长拖慢首 token）"
    assert gw._settings.resolve_route("chat").primary == "spark-80b"


def test_matrix_profile_route_resolution() -> None:
    """matrix profile：vision 走冻结矩阵（qwen-vl-max 主备）。"""
    gw = _gateway(profile="matrix")
    route = gw._settings.resolve_route("vision")
    assert route.primary == USE_CASE_ROUTES["vision"].primary
    assert route.fallback == USE_CASE_ROUTES["vision"].fallback


# ─────────────────────────────────────────────────────────────────────────────
# 7. V2 domain 不引用 vendor / legacy 类型（目录不存在时 vacuous 通过，防回归）
# ─────────────────────────────────────────────────────────────────────────────

_VENDOR_IMPORT = re.compile(r"^\s*(?:from\s+(litellm|minio|presenton)\b|import\s+(litellm|minio|presenton)\b)", re.MULTILINE)
_V3_REFERENCE = re.compile(r"\b(V3Element|V3Slide|V3Deck|teacherV3)\b")


@pytest.mark.parametrize("pattern_kind", ["vendor", "v3"])
def test_future_v2_domain_free_of_vendor_and_v3(pattern_kind: str) -> None:
    """V2 adapter 契约不得引用 LiteLLM/MinIO/Presenton 类型或旧 V3 schema（R04 验收）。

    app/ppt_v2 尚不存在时 vacuous 通过（与 R03 边界测试同哲学），已存在则即时拦截。
    """
    v2_dir = PLATFORM_ROOT / "app" / "ppt_v2"
    if not v2_dir.is_dir():
        return
    violations: list[str] = []
    for f in sorted(v2_dir.rglob("*.py")):
        text = f.read_text(encoding="utf-8")
        if pattern_kind == "vendor" and _VENDOR_IMPORT.search(text):
            violations.append(f"{f.relative_to(PLATFORM_ROOT)}: vendor import")
        if pattern_kind == "v3" and _V3_REFERENCE.search(text):
            violations.append(f"{f.relative_to(PLATFORM_ROOT)}: V3 schema 引用")
    assert not violations, "V2 domain 契约引用被禁对象：\n" + "\n".join(violations)
