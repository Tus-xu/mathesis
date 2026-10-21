"""可配置 Embedding 提供商测试（tests/test_embedding_provider.py）

respx mock httpx：
- local：向后兼容回归（无参构造 = env 本地 BGE-M3，请求体不含 dimensions/text_type）
- aliyun：Bearer 鉴权 + dimensions/text_type 透传（入库 document / 检索 query）
- tencent：TC3 签名 GetEmbedding（复用 cloud_kb._tc3_signed_post）
- resolve_embedding_config：system_configs 优先 → env 兜底；维度错配告警不抛错
"""

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from app.config import settings
from app.providers.embedding import (
    ALIYUN_DEFAULT_BASE_URL,
    EmbeddingConfig,
    EmbeddingProvider,
    resolve_embedding_config,
)


def _fake_system_config_module(*, return_value=None, side_effect=None):
    """伪造同事的 app.models.system_config 模块（延迟 import 时命中 sys.modules）"""
    module = types.ModuleType("app.models.system_config")
    module.get_system_config = AsyncMock(return_value=return_value, side_effect=side_effect)
    return module


_ALIYUN_CFG = EmbeddingConfig(
    provider="aliyun",
    base_url="",
    api_key="sk-test",
    model="text-embedding-v4",
    dimension=1024,
    source="system",
)

_TENCENT_CFG = EmbeddingConfig(
    provider="tencent",
    base_url="",
    api_key="test-secret-id:test-secret-key",
    model="bge-m3",
    dimension=1024,
    source="system",
)


class TestLocalProvider:
    """local 分支：向后兼容回归"""

    async def test_shared_http_client_does_not_proxy_local_embedding(self):
        """localhost:8080 必须直连，不能被环境代理改写为 502。"""
        from app.providers.http import close_http, get_http

        await close_http()
        client = get_http()
        assert client._trust_env is False
        await close_http()

    async def test_local_embedding_allows_model_inference_time_for_batches(self, monkeypatch):
        """教材批量向量化不能沿用 10 秒短超时。"""
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"data": [{"embedding": [0.1, 0.2]}]}

        class Client:
            timeout = None

            async def post(self, _url, **kwargs):
                self.timeout = kwargs["timeout"]
                return Response()

        client = Client()
        monkeypatch.setattr("app.providers.embedding.get_http", lambda: client)

        await EmbeddingProvider().embed(["一段教材正文"])

        assert client.timeout == 180.0

    def test_default_ctor_is_env_local(self):
        """无参构造 = env 本地 BGE-M3（向后兼容红线）"""
        provider = EmbeddingProvider()
        assert provider._config.provider == "local"
        assert provider._config.source == "env"
        assert provider._config.model == "bge-m3"
        assert provider._config.dimension == 1024
        assert provider.available is True

    @respx.mock
    async def test_local_regression(self, monkeypatch):
        """local 请求路径/载荷与改造前一致：POST {base_url}/v1/embeddings，无 dimensions/text_type"""
        monkeypatch.setattr(settings, "embedding_base_url", "http://local-bge:8080")
        route = respx.post("http://local-bge:8080/v1/embeddings").mock(
            return_value=httpx.Response(
                200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
            )
        )

        vectors = await EmbeddingProvider().embed(["文本一", "文本二"])

        assert vectors == [[0.1, 0.2], [0.3, 0.4]]
        payload = json.loads(route.calls.last.request.content)
        assert payload == {"model": "bge-m3", "input": ["文本一", "文本二"]}
        assert "Authorization" not in route.calls.last.request.headers

    @respx.mock
    async def test_local_http_error_raises(self, monkeypatch):
        """local 服务 500 → 抛 HTTPStatusError（保持现状语义，由调用方兜底）"""
        monkeypatch.setattr(settings, "embedding_base_url", "http://local-bge:8080")
        respx.post("http://local-bge:8080/v1/embeddings").mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            await EmbeddingProvider().embed(["x"])

    def test_local_unavailable_when_no_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_base_url", "")
        assert EmbeddingProvider().available is False


class TestAliyunProvider:
    """aliyun 分支：OpenAI 兼容端点 + dimensions/text_type 透传"""

    @respx.mock
    async def test_aliyun_document(self):
        """入库：text_type=document，Bearer 鉴权，dimensions=1024，默认 dashscope 端点"""
        route = respx.post(f"{ALIYUN_DEFAULT_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.5] * 4}]})
        )

        vectors = await EmbeddingProvider(_ALIYUN_CFG).embed(["函数的概念"], text_type="document")

        assert vectors == [[0.5] * 4]
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer sk-test"
        payload = json.loads(request.content)
        assert payload == {
            "model": "text-embedding-v4",
            "input": ["函数的概念"],
            "dimensions": 1024,
            "text_type": "document",
        }

    @respx.mock
    async def test_aliyun_query(self):
        """检索：text_type=query"""
        route = respx.post(f"{ALIYUN_DEFAULT_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.6] * 4}]})
        )

        await EmbeddingProvider(_ALIYUN_CFG).embed(["函数"], text_type="query")

        payload = json.loads(route.calls.last.request.content)
        assert payload["text_type"] == "query"

    def test_aliyun_unavailable_without_key(self):
        cfg = EmbeddingConfig(
            provider="aliyun", base_url="", api_key="", model="m", dimension=1024, source="env"
        )
        assert EmbeddingProvider(cfg).available is False


class TestTencentProvider:
    """tencent 分支：TC3 签名 GetEmbedding"""

    @respx.mock
    async def test_tencent_get_embedding(self):
        """断言 X-TC-Action=GetEmbedding + TC3 签名头；Data[0].Embedding 解析正确（逐条调用）"""
        route = respx.post("https://lkeap.tencentcloudapi.com").mock(
            return_value=httpx.Response(
                200, json={"Response": {"Data": [{"Embedding": [0.7, 0.8]}], "RequestId": "r"}}
            )
        )

        vectors = await EmbeddingProvider(_TENCENT_CFG).embed(["文本一", "文本二"])

        assert vectors == [[0.7, 0.8], [0.7, 0.8]]
        assert len(route.calls) == 2  # GetEmbedding 单条输入接口，逐条调用
        request = route.calls.last.request
        assert request.headers["X-TC-Action"] == "GetEmbedding"
        assert request.headers["Authorization"].startswith(
            "TC3-HMAC-SHA256 Credential=test-secret-id/"
        )
        payload = json.loads(request.content)
        assert payload["Model"] == "bge-m3"
        assert payload["Input"] == "文本二"

    @respx.mock
    async def test_tencent_business_error_raises(self):
        """腾讯业务错误 → 抛 RuntimeError（与 local 语义一致，由调用方兜底）"""
        respx.post("https://lkeap.tencentcloudapi.com").mock(
            return_value=httpx.Response(
                200,
                json={"Response": {"Error": {"Code": "AuthFailure", "Message": "bad key"}}},
            )
        )
        with pytest.raises(RuntimeError, match="GetEmbedding"):
            await EmbeddingProvider(_TENCENT_CFG).embed(["x"])

    def test_tencent_unavailable_with_malformed_key(self):
        cfg = EmbeddingConfig(
            provider="tencent",
            base_url="",
            api_key="no-colon",
            model="m",
            dimension=1024,
            source="env",
        )
        assert EmbeddingProvider(cfg).available is False


class TestResolveEmbeddingConfig:
    """配置解析：system_configs 优先 → env 兜底；永不抛异常"""

    async def test_db_none_env_fallback(self):
        """db=None（离线脚本场景）→ env local，不抛异常"""
        cfg = await resolve_embedding_config(None)
        assert cfg.provider == "local"
        assert cfg.model == "bge-m3"
        assert cfg.dimension == 1024
        assert cfg.source == "env"

    async def test_system_config_used(self, monkeypatch):
        """库内配置存在时优先于 env，source=system"""
        monkeypatch.setitem(
            sys.modules,
            "app.models.system_config",
            _fake_system_config_module(
                return_value={
                    "provider": "aliyun",
                    "base_url": "",
                    "api_key": "sk-from-db",
                    "model": "text-embedding-v4",
                    "dimension": 1024,
                }
            ),
        )
        cfg = await resolve_embedding_config(MagicMock())
        assert cfg.provider == "aliyun"
        assert cfg.api_key == "sk-from-db"
        assert cfg.model == "text-embedding-v4"
        assert cfg.source == "system"

    async def test_system_config_error_falls_back_env(self, monkeypatch):
        """库内读取异常 → 兜底 env，不抛异常"""
        monkeypatch.setitem(
            sys.modules,
            "app.models.system_config",
            _fake_system_config_module(side_effect=RuntimeError("table missing")),
        )
        cfg = await resolve_embedding_config(MagicMock())
        assert cfg.provider == "local"
        assert cfg.source == "env"

    async def test_dimension_mismatch_warns_not_raises(self, monkeypatch):
        """dimension ≠ 1024：resolve 层告警（warning 日志）但正常返回，不抛异常"""
        monkeypatch.setitem(
            sys.modules,
            "app.models.system_config",
            _fake_system_config_module(
                return_value={"provider": "aliyun", "api_key": "sk", "dimension": 2048}
            ),
        )
        cfg = await resolve_embedding_config(MagicMock())
        assert cfg.dimension == 2048  # 原样返回，告警走日志（库表 vector(1024) 红线提示）

    async def test_unknown_provider_falls_back_local(self, monkeypatch):
        """非法 provider → 回退 local 并告警"""
        monkeypatch.setitem(
            sys.modules,
            "app.models.system_config",
            _fake_system_config_module(return_value={"provider": "huawei"}),
        )
        cfg = await resolve_embedding_config(MagicMock())
        assert cfg.provider == "local"


class TestHealthCheck:
    """健康检查（保持不抛异常语义）"""

    @respx.mock
    async def test_health_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_base_url", "http://local-bge:8080")
        respx.post("http://local-bge:8080/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.0] * 1024}]})
        )
        result = await EmbeddingProvider().health_check()
        assert result["ok"] is True
        assert "warning" not in result

    @respx.mock
    async def test_health_dimension_warning(self, monkeypatch):
        """健康检查发现维度 ≠ 1024 → ok=True 但带 warning（不抛异常）"""
        monkeypatch.setattr(settings, "embedding_base_url", "http://local-bge:8080")
        respx.post("http://local-bge:8080/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.0] * 512}]})
        )
        result = await EmbeddingProvider().health_check()
        assert result["ok"] is True
        assert "warning" in result

    @respx.mock
    async def test_health_failure(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_base_url", "http://local-bge:8080")
        respx.post("http://local-bge:8080/v1/embeddings").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result = await EmbeddingProvider().health_check()
        assert result["ok"] is False
        assert "error" in result
