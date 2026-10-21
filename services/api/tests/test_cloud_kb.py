"""云知识库提供商测试（tests/test_cloud_kb.py）

respx mock httpx：
- 腾讯 LKEAP：TC3-HMAC-SHA256 签名头断言（可按时间戳复算）+ Records 解析（0~100 归一化）
- 阿里百炼：POP 签名存在且可复算 + Nodes 解析
- 异常（500/业务错误/超时）→ 返回 [] 不抛错
- 配置解析：system_configs 优先 → env 兜底（system_config 未合入时静默回退）
- RAG 第 4 通道：enabled 进 RRF 融合；disabled 与现状零差异；失败/超时静默跳过
"""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import types
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.kernel.rag import RAGPipeline, ScoredChunk
from app.providers.cloud_kb import (
    ALIYUN_BAILIAN_URL,
    TENCENT_LKEAP_URL,
    CloudKBConfig,
    _pop_percent_encode,
    _tc3_authorization,
    resolve_cloud_kb_config,
    retrieve_cloud_kb,
)
from app.providers.cloud_kb import test_cloud_kb as _test_cloud_kb  # 避免被 pytest 误收集

_TENCENT_CFG = CloudKBConfig(
    enabled=True,
    provider="tencent_lkeap",
    credentials={"secret_id": "test-secret-id", "secret_key": "test-secret-key"},
    knowledge_base_id="kb-123",
    workspace_id="",
    top_k=5,
    score_threshold=0.5,
    source="env",
)

_ALIYUN_CFG = CloudKBConfig(
    enabled=True,
    provider="aliyun_bailian",
    credentials={"access_key_id": "test-ak-id", "access_key_secret": "test-ak-secret"},
    knowledge_base_id="index-456",
    workspace_id="ws-1",
    top_k=5,
    score_threshold=0.5,
    source="env",
)


def _fake_system_config_module(*, return_value=None, side_effect=None):
    """伪造同事的 app.models.system_config 模块（延迟 import 时命中 sys.modules）"""
    module = types.ModuleType("app.models.system_config")
    module.get_system_config = AsyncMock(return_value=return_value, side_effect=side_effect)
    return module


class TestTencentLKEAP:
    """腾讯云 LKEAP RetrieveKnowledge（TC3 签名）"""

    @respx.mock
    async def test_tc3_signature_headers_and_records(self):
        """请求头含 TC3 签名且可按 X-TC-Timestamp 复算；Records 解析正确（85 分归一化 0.85）"""
        route = respx.post(TENCENT_LKEAP_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Response": {
                        "Records": [
                            {
                                "Title": "必修一",
                                "Content": "函数的定义：设 A、B 是非空数集…",
                                "Score": 85.0,
                            },
                            {"Title": "必修一", "Content": "", "Score": 10.0},  # 空内容应被过滤
                            {"Title": "选修二", "Content": "单调性与导数的关系", "Score": 0.62},
                        ],
                        "RequestId": "req-1",
                    }
                },
            )
        )

        records = await retrieve_cloud_kb(_TENCENT_CFG, "函数")

        # 签名头断言
        request = route.calls.last.request
        authorization = request.headers["Authorization"]
        assert authorization.startswith("TC3-HMAC-SHA256 Credential=test-secret-id/")
        assert "SignedHeaders=content-type;host" in authorization
        assert request.headers["X-TC-Action"] == "RetrieveKnowledge"
        assert request.headers["X-TC-Version"] == "2024-05-22"
        # 用请求实际发送的时间戳与载荷复算 Authorization，必须完全一致
        expected = _tc3_authorization(
            secret_id="test-secret-id",
            secret_key="test-secret-key",
            service="lkeap",
            host="lkeap.tencentcloudapi.com",
            payload_bytes=request.content,
            timestamp=int(request.headers["X-TC-Timestamp"]),
        )
        assert authorization == expected
        # 业务参数断言
        payload = json.loads(request.content)
        assert payload["KnowledgeBaseId"] == "kb-123"
        assert payload["Query"] == "函数"
        assert payload["RetrievalMethod"] == "SEMANTIC"
        assert payload["RetrievalSetting"] == {"TopK": 5, "ScoreThreshold": 0.5}

        # 解析断言：空内容被过滤、0~100 分度归一化、source 标记
        assert len(records) == 2
        assert records[0]["title"] == "必修一"
        assert records[0]["score"] == pytest.approx(0.85)
        assert records[0]["source"] == "cloud_kb:tencent_lkeap"
        assert records[1]["score"] == pytest.approx(0.62)

    @respx.mock
    async def test_tencent_business_error_returns_empty(self):
        """Response.Error 业务错误 → 返回 [] 不抛错"""
        respx.post(TENCENT_LKEAP_URL).mock(
            return_value=httpx.Response(
                200,
                json={"Response": {"Error": {"Code": "InvalidParameter", "Message": "bad kb id"}}},
            )
        )
        assert await retrieve_cloud_kb(_TENCENT_CFG, "函数") == []

    async def test_tencent_config_incomplete_returns_empty(self):
        """凭证/库标识缺失 → 返回 [] 不发起请求（无需 mock，命中即说明发了请求）"""
        cfg = CloudKBConfig(
            enabled=True,
            provider="tencent_lkeap",
            credentials={},
            knowledge_base_id="",
            workspace_id="",
            top_k=5,
            score_threshold=0.5,
            source="env",
        )
        assert await retrieve_cloud_kb(cfg, "函数") == []


class TestAliyunBailian:
    """阿里云百炼 Retrieve（POP 签名）"""

    @respx.mock
    async def test_pop_signature_recomputable_and_nodes(self):
        """Signature 存在且可按 POP 算法复算；Nodes 解析正确"""
        route = respx.get(ALIYUN_BAILIAN_URL + "/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "RequestId": "req-2",
                    "Success": True,
                    "Data": {
                        "Nodes": [
                            {
                                "Text": "函数的三要素：定义域、值域、对应法则",
                                "Score": 0.88,
                                "Metadata": {"doc_name": "高中数学必修一.pdf"},
                            },
                            {"Text": "", "Score": 0.1, "Metadata": {}},  # 空文本应被过滤
                        ]
                    },
                },
            )
        )

        records = await retrieve_cloud_kb(_ALIYUN_CFG, "函数")

        # POP 签名复算
        request = route.calls.last.request
        # httpx URL.query 返回 bytes，先解码再 parse_qsl
        params = dict(
            urllib.parse.parse_qsl(request.url.query.decode("utf-8"), keep_blank_values=True)
        )
        signature = params.pop("Signature")
        assert params["Action"] == "Retrieve"
        assert params["Version"] == "2023-12-29"
        assert params["AccessKeyId"] == "test-ak-id"
        assert params["SignatureMethod"] == "HMAC-SHA1"
        assert params["SignatureVersion"] == "1.0"
        assert params["IndexId"] == "index-456"
        assert params["WorkspaceId"] == "ws-1"
        assert params["DenseSimilarityTopK"] == "5"
        assert params["EnableReranking"] == "true"
        assert params["Rerank.1.ModelName"] == "qwen3-rerank-hybrid"
        assert params["Rerank.1.RerankMinScore"] == "0.5"
        assert params["Rerank.1.RerankTopN"] == "5"
        canonicalized = "&".join(
            f"{_pop_percent_encode(k)}={_pop_percent_encode(params[k])}" for k in sorted(params)
        )
        string_to_sign = "GET&%2F&" + _pop_percent_encode(canonicalized)
        expected = base64.b64encode(
            hmac.new(b"test-ak-secret&", string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        ).decode("utf-8")
        assert signature == expected

        # 解析断言：空文本过滤、Score 0~1 不动、source 标记
        assert len(records) == 1
        assert records[0]["title"] == "高中数学必修一.pdf"
        assert records[0]["score"] == pytest.approx(0.88)
        assert records[0]["source"] == "cloud_kb:aliyun_bailian"

    @respx.mock
    async def test_aliyun_http_500_returns_empty(self):
        """HTTP 500 → 返回 [] 不抛错"""
        respx.get(ALIYUN_BAILIAN_URL + "/").mock(return_value=httpx.Response(500))
        assert await retrieve_cloud_kb(_ALIYUN_CFG, "函数") == []


class TestRetrieveGuards:
    """云通道兜底纪律：任何失败返回 [] 不抛错"""

    async def test_disabled_returns_empty(self):
        cfg = CloudKBConfig(
            enabled=False,
            provider="tencent_lkeap",
            credentials={},
            knowledge_base_id="",
            workspace_id="",
            top_k=5,
            score_threshold=0.5,
            source="disabled",
        )
        assert await retrieve_cloud_kb(cfg, "函数") == []

    async def test_unknown_provider_returns_empty(self):
        cfg = CloudKBConfig(
            enabled=True,
            provider="huawei",
            credentials={},
            knowledge_base_id="kb",
            workspace_id="",
            top_k=5,
            score_threshold=0.5,
            source="env",
        )
        assert await retrieve_cloud_kb(cfg, "函数") == []

    @respx.mock
    async def test_timeout_returns_empty(self):
        """网络超时 → 返回 [] 不抛错"""
        respx.post(TENCENT_LKEAP_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
        assert await retrieve_cloud_kb(_TENCENT_CFG, "函数") == []

    @respx.mock
    async def test_http_500_returns_empty(self):
        respx.post(TENCENT_LKEAP_URL).mock(return_value=httpx.Response(500))
        assert await retrieve_cloud_kb(_TENCENT_CFG, "函数") == []


class TestResolveConfig:
    """配置解析：system_configs 优先 → env 兜底；永不抛异常"""

    async def test_env_fallback_default_disabled(self):
        """同事 config.py 未合入（getattr 防御）→ 默认 disabled，不抛异常"""
        cfg = await resolve_cloud_kb_config(None)
        assert cfg.enabled is False
        assert cfg.provider == ""
        assert cfg.source == "disabled"

    async def test_system_config_priority(self, monkeypatch):
        """库内配置存在时优先于 env，source=system"""
        monkeypatch.setitem(
            sys.modules,
            "app.models.system_config",
            _fake_system_config_module(
                return_value={
                    "enabled": True,
                    "provider": "aliyun_bailian",
                    "credentials": {"access_key_id": "ak", "access_key_secret": "sk"},
                    "knowledge_base_id": "idx-1",
                    "workspace_id": "ws-9",
                    "top_k": 8,
                    "score_threshold": 0.7,
                }
            ),
        )
        cfg = await resolve_cloud_kb_config(MagicMock())
        assert cfg.enabled is True
        assert cfg.provider == "aliyun_bailian"
        assert cfg.credentials == {"access_key_id": "ak", "access_key_secret": "sk"}
        assert cfg.knowledge_base_id == "idx-1"
        assert cfg.workspace_id == "ws-9"
        assert cfg.top_k == 8
        assert cfg.score_threshold == pytest.approx(0.7)
        assert cfg.source == "system"

    async def test_system_config_error_falls_back_env(self, monkeypatch):
        """库内读取异常（非 ImportError）→ 兜底 env，不抛异常"""
        monkeypatch.setitem(
            sys.modules,
            "app.models.system_config",
            _fake_system_config_module(side_effect=RuntimeError("table missing")),
        )
        cfg = await resolve_cloud_kb_config(MagicMock())
        assert cfg.source in ("env", "disabled")
        assert cfg.enabled is False  # 当前 env 未配置云知识库

    async def test_system_config_none_falls_back_env(self, monkeypatch):
        """库内无该 key（返回 None）→ env 兜底"""
        monkeypatch.setitem(
            sys.modules, "app.models.system_config", _fake_system_config_module(return_value=None)
        )
        cfg = await resolve_cloud_kb_config(MagicMock())
        assert cfg.source in ("env", "disabled")


class TestCloudKbSelfTest:
    """连通性自测接口"""

    async def test_not_enabled(self):
        cfg = CloudKBConfig(
            enabled=False,
            provider="",
            credentials={},
            knowledge_base_id="",
            workspace_id="",
            top_k=5,
            score_threshold=0.5,
            source="disabled",
        )
        result = await _test_cloud_kb(cfg)
        assert result["ok"] is False
        assert result["error"]

    @respx.mock
    async def test_ok_with_records(self):
        """query="函数" 实测命中 → ok=True 且 records/latency 正确"""
        respx.post(TENCENT_LKEAP_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Response": {
                        "Records": [{"Title": "t", "Content": "函数的概念", "Score": 0.9}],
                        "RequestId": "r",
                    }
                },
            )
        )
        result = await _test_cloud_kb(_TENCENT_CFG)
        assert result["ok"] is True
        assert result["provider"] == "tencent_lkeap"
        assert result["records"] == 1
        assert result["latency_ms"] >= 0
        assert result["error"] == ""

    @respx.mock
    async def test_failure_surfaces_error(self):
        """自测不吞错误：HTTP 500 时 error 原样上报（区别于 retrieve 的静默兜底）"""
        respx.post(TENCENT_LKEAP_URL).mock(return_value=httpx.Response(500, text="boom"))
        result = await _test_cloud_kb(_TENCENT_CFG)
        assert result["ok"] is False
        assert result["error"]


class TestRAGCloudChannel:
    """RAG 第 4 召回通道：enabled 进 RRF；disabled 与现状零差异；失败静默跳过"""

    def _local_chunk(self, chunk_id: str, raw: float) -> ScoredChunk:
        return ScoredChunk(
            chunk_id=chunk_id,
            doc_id="doc-1",
            content=f"本地切片 {chunk_id}",
            doc_title="本地教材",
            score=raw,
            raw_score=raw,
        )

    def _patch_pipeline(self, *, cloud_cfg, cloud_records=None, cloud_side_effect=None):
        """统一 patch：改写直通、三路本地召回与 rerank 固定、云配置/云检索可控"""
        retrieve_mock = AsyncMock(return_value=cloud_records or [], side_effect=cloud_side_effect)
        return (
            patch(
                "app.providers.cloud_kb.resolve_cloud_kb_config",
                AsyncMock(return_value=cloud_cfg),
            ),
            patch("app.providers.cloud_kb.retrieve_cloud_kb", retrieve_mock),
            patch.object(RAGPipeline, "_rewrite_query", AsyncMock(return_value="函数")),
            patch.object(RAGPipeline, "_vector_search", AsyncMock(return_value=[])),
            patch.object(RAGPipeline, "_kp_tag_search", AsyncMock(return_value=[])),
            patch.object(
                RAGPipeline,
                "_rerank",
                AsyncMock(side_effect=lambda q, chunks, request_id: (chunks, False)),
            ),
            retrieve_mock,
        )

    async def test_enabled_cloud_records_enter_rrf(self):
        """enabled 时云记录转 ScoredChunk 进入 RRF 融合结果"""
        p1, p2, p3, p4, p5, p6, _ = self._patch_pipeline(
            cloud_cfg=_TENCENT_CFG,
            cloud_records=[{"title": "云端", "content": "云端：函数的定义", "score": 0.9}],
        )
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            patch.object(
                RAGPipeline,
                "_trgm_search",
                AsyncMock(return_value=[self._local_chunk("local-1", 0.5)]),
            ),
        ):
            result = await RAGPipeline().retrieve("函数", db=MagicMock())

        assert result.answerable is True
        cloud_chunks = [c for c in result.chunks if c.chunk_id.startswith("cloudkb-")]
        assert len(cloud_chunks) == 1
        assert cloud_chunks[0].doc_id == "cloud_kb"
        assert cloud_chunks[0].doc_title == "云知识库·tencent_lkeap"
        assert cloud_chunks[0].content == "云端：函数的定义"
        assert cloud_chunks[0].raw_score == pytest.approx(0.9)
        # 本地切片不受影响，同进融合结果
        assert any(c.chunk_id == "local-1" for c in result.chunks)

    async def test_disabled_zero_diff(self):
        """disabled 时云检索完全不被调用，结果与现状零差异"""
        disabled_cfg = CloudKBConfig(
            enabled=False,
            provider="",
            credentials={},
            knowledge_base_id="",
            workspace_id="",
            top_k=5,
            score_threshold=0.5,
            source="disabled",
        )
        p1, p2, p3, p4, p5, p6, retrieve_mock = self._patch_pipeline(cloud_cfg=disabled_cfg)
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            patch.object(
                RAGPipeline,
                "_vector_search",
                AsyncMock(return_value=[self._local_chunk("vec-1", 0.6)]),
            ),
            patch.object(
                RAGPipeline,
                "_trgm_search",
                AsyncMock(return_value=[self._local_chunk("trgm-1", 0.5)]),
            ),
        ):
            result = await RAGPipeline().retrieve("函数", db=MagicMock())

        retrieve_mock.assert_not_called()
        # 两路 rank0 同分，按列表顺序稳定排序：vector 在前
        assert [c.chunk_id for c in result.chunks] == ["vec-1", "trgm-1"]
        assert result.answerable is True

    async def test_cloud_failure_silently_skipped(self):
        """云通道抛异常 → 静默跳过，本地结果正常返回"""
        p1, p2, p3, p4, p5, p6, _ = self._patch_pipeline(
            cloud_cfg=_TENCENT_CFG, cloud_side_effect=RuntimeError("boom")
        )
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            patch.object(
                RAGPipeline,
                "_trgm_search",
                AsyncMock(return_value=[self._local_chunk("local-1", 0.5)]),
            ),
        ):
            result = await RAGPipeline().retrieve("函数", db=MagicMock())

        assert result.answerable is True
        assert [c.chunk_id for c in result.chunks] == ["local-1"]

    async def test_cloud_timeout_silently_skipped(self):
        """云通道超时（≤8s 上限，测试中缩到 0.05s）→ 静默跳过"""

        async def _slow_retrieve(*args, **kwargs):
            # AsyncMock 对 async 函数 side_effect 会真实 await，wait_for 才能触发超时
            await asyncio.sleep(5)

        p1, p2, p3, p4, p5, p6, _ = self._patch_pipeline(
            cloud_cfg=_TENCENT_CFG, cloud_side_effect=_slow_retrieve
        )
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            patch("app.kernel.rag.CLOUD_KB_TIMEOUT_S", 0.05),
            patch.object(
                RAGPipeline,
                "_trgm_search",
                AsyncMock(return_value=[self._local_chunk("local-1", 0.5)]),
            ),
        ):
            result = await RAGPipeline().retrieve("函数", db=MagicMock())

        assert result.answerable is True
        assert [c.chunk_id for c in result.chunks] == ["local-1"]

    async def test_only_cloud_records_answerable(self):
        """本地三路全空、仅云通道命中 → 仍可答（云通道参与空集判定）"""
        p1, p2, p3, p4, p5, p6, _ = self._patch_pipeline(
            cloud_cfg=_TENCENT_CFG,
            cloud_records=[{"title": "云端", "content": "云端：函数的定义", "score": 0.9}],
        )
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            patch.object(RAGPipeline, "_trgm_search", AsyncMock(return_value=[])),
        ):
            result = await RAGPipeline().retrieve("函数", db=MagicMock())

        assert result.answerable is True
        assert len(result.chunks) == 1
        assert result.chunks[0].chunk_id.startswith("cloudkb-")
