"""Provider 单元测试

测试 DeepSeekProvider、SparkProvider 和 ModelRouter。
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.providers.base import ChatResult
from app.providers.deepseek import DeepSeekProvider
from app.providers.router import ModelRouter
from app.providers.spark import SparkProvider

# ========== DeepSeekProvider 测试 ==========


class TestDeepSeekProvider:
    """DeepSeekProvider 单元测试"""

    def test_available_with_key(self):
        """有 API Key 时 available=True"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False
            provider = DeepSeekProvider()
            assert provider.available is True

    def test_available_without_key(self):
        """无 API Key 时 available=False"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = ""
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False
            provider = DeepSeekProvider()
            assert provider.available is False

    async def test_health_check_no_key(self):
        """无 Key 时 health_check 返回 ok=False"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = ""
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False
            provider = DeepSeekProvider()
            result = await provider.health_check()
            assert result["ok"] is False
            assert "not configured" in result["error"]

    async def test_health_check_success(self):
        """health_check 成功时返回 ok=True"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False

            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch("app.providers.deepseek.get_http", return_value=mock_client):
                provider = DeepSeekProvider()
                result = await provider.health_check()
                assert result["ok"] is True
                assert result["model"] == "deepseek-v4-flash"
                assert "latency_ms" in result

    async def test_health_check_failure(self):
        """health_check 失败时返回 ok=False"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

            with patch("app.providers.deepseek.get_http", return_value=mock_client):
                provider = DeepSeekProvider()
                result = await provider.health_check()
                assert result["ok"] is False
                assert "error" in result

    async def test_chat_no_key_raises(self):
        """无 Key 时 chat 抛出 RuntimeError"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = ""
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False
            provider = DeepSeekProvider()
            with pytest.raises(RuntimeError, match="API key not configured"):
                await provider.chat(
                    [{"role": "user", "content": "hi"}],
                    request_id="test",
                    scene="chat",
                )

    async def test_chat_success(self):
        """chat 成功返回 ChatResult"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False

            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "Hello!"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)

            with patch("app.providers.deepseek.get_http", return_value=mock_client):
                provider = DeepSeekProvider()
                result = await provider.chat(
                    [{"role": "user", "content": "hi"}],
                    request_id="test-1",
                    scene="chat",
                )
                assert result["content"] == "Hello!"
                assert result["provider"] == "deepseek"
                assert result["input_tokens"] == 10
                assert result["output_tokens"] == 5

    def test_build_payload_thinking_disabled(self):
        """thinking=False 时 payload 顶层包含 thinking 字段"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False
            # 与真实 config 默认一致（app/config.py llm_omit_thinking_field=False）：
            # MagicMock 的自动属性恒真，不显式钉 False 会被误当作"全局省略 thinking"
            mock_settings.llm_omit_thinking_field = False
            provider = DeepSeekProvider()
            payload = provider._build_payload([{"role": "user", "content": "hi"}])
            # thinking 是 DeepSeek HTTP body 顶层字段（extra_body 是 SDK 客户端概念）
            assert "thinking" in payload
            assert payload["thinking"]["type"] == "disabled"
            assert "extra_body" not in payload

    def test_build_payload_with_functions(self):
        """传入 functions 时 payload 包含 tools"""
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False
            provider = DeepSeekProvider()
            funcs = [{"name": "test_func", "description": "A test function"}]
            payload = provider._build_payload([{"role": "user", "content": "hi"}], functions=funcs)
            assert "tools" in payload
            assert payload["tools"][0]["type"] == "function"


# ========== SparkProvider 测试 ==========


class TestSparkProvider:
    """SparkProvider 单元测试"""

    def test_available_with_password(self):
        """有 API password 时 available=True"""
        with patch("app.providers.spark.settings") as mock_settings:
            mock_settings.spark_api_password = "test-password"
            mock_settings.spark_model = "spark-ultra"
            provider = SparkProvider()
            assert provider.available is True

    def test_available_without_password(self):
        """无 API password 时 available=False"""
        with patch("app.providers.spark.settings") as mock_settings:
            mock_settings.spark_api_password = ""
            mock_settings.spark_model = "spark-ultra"
            provider = SparkProvider()
            assert provider.available is False

    async def test_health_check_no_password(self):
        """无 password 时 health_check 返回 ok=False"""
        with patch("app.providers.spark.settings") as mock_settings:
            mock_settings.spark_api_password = ""
            mock_settings.spark_model = "spark-ultra"
            provider = SparkProvider()
            result = await provider.health_check()
            assert result["ok"] is False
            assert "not configured" in result["error"]


# ========== ModelRouter 测试 ==========


class TestModelRouter:
    """ModelRouter 降级逻辑测试"""

    async def test_spark_success_no_fallback(self):
        """星火成功时不降级"""
        spark = MagicMock(spec=SparkProvider)
        spark.available = True
        spark.chat = AsyncMock(
            return_value=ChatResult(
                content="spark response",
                provider="spark",
                model="spark-ultra",
                input_tokens=10,
                output_tokens=20,
                latency_ms=100,
            )
        )

        deepseek = MagicMock(spec=DeepSeekProvider)
        deepseek.chat = AsyncMock()

        router = ModelRouter(spark=spark, deepseek=deepseek)
        result = await router.chat(
            [{"role": "user", "content": "hi"}],
            request_id="test",
            scene="chat",
        )
        assert result["content"] == "spark response"
        assert result["provider"] == "spark"
        deepseek.chat.assert_not_called()

    async def test_spark_fail_fallback_to_deepseek(self):
        """星火失败时降级到 DeepSeek"""
        spark = MagicMock(spec=SparkProvider)
        spark.available = True
        spark.chat = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        deepseek = MagicMock(spec=DeepSeekProvider)
        deepseek.chat = AsyncMock(
            return_value=ChatResult(
                content="deepseek response",
                provider="deepseek",
                model="deepseek-v4-flash",
                input_tokens=10,
                output_tokens=15,
                latency_ms=200,
            )
        )

        router = ModelRouter(spark=spark, deepseek=deepseek)
        result = await router.chat(
            [{"role": "user", "content": "hi"}],
            request_id="test",
            scene="chat",
        )
        assert result["content"] == "deepseek response"
        assert result["provider"] == "deepseek"

    async def test_spark_unavailable_skip_to_deepseek(self):
        """星火不可用时直接走 DeepSeek"""
        spark = MagicMock(spec=SparkProvider)
        spark.available = False

        deepseek = MagicMock(spec=DeepSeekProvider)
        deepseek.chat = AsyncMock(
            return_value=ChatResult(
                content="deepseek only",
                provider="deepseek",
                model="deepseek-v4-flash",
                input_tokens=5,
                output_tokens=10,
                latency_ms=150,
            )
        )

        router = ModelRouter(spark=spark, deepseek=deepseek)
        result = await router.chat(
            [{"role": "user", "content": "hi"}],
            request_id="test",
            scene="chat",
        )
        assert result["content"] == "deepseek only"
        spark.chat.assert_not_called()

    async def test_all_providers_fail(self):
        """所有 Provider 失败时抛出 RuntimeError"""
        spark = MagicMock(spec=SparkProvider)
        spark.available = True
        spark.chat = AsyncMock(side_effect=httpx.ConnectError("fail"))

        deepseek = MagicMock(spec=DeepSeekProvider)
        deepseek.chat = AsyncMock(side_effect=RuntimeError("also fail"))

        router = ModelRouter(spark=spark, deepseek=deepseek)
        with pytest.raises(RuntimeError, match="All model providers failed"):
            await router.chat(
                [{"role": "user", "content": "hi"}],
                request_id="test",
                scene="chat",
            )

    async def test_spark_5xx_fallback_to_deepseek(self):
        """星火 5xx 错误时降级到 DeepSeek"""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        spark = MagicMock(spec=SparkProvider)
        spark.available = True
        spark.chat = AsyncMock(
            side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)
        )

        deepseek = MagicMock(spec=DeepSeekProvider)
        deepseek.chat = AsyncMock(
            return_value=ChatResult(
                content="fallback ok",
                provider="deepseek",
                model="deepseek-v4-flash",
                input_tokens=5,
                output_tokens=10,
                latency_ms=100,
            )
        )

        router = ModelRouter(spark=spark, deepseek=deepseek)
        result = await router.chat(
            [{"role": "user", "content": "hi"}],
            request_id="test",
            scene="chat",
        )
        assert result["content"] == "fallback ok"
        assert result["provider"] == "deepseek"


# ========== 流式 usage 解析回归测试 ==========


class _FakeStreamResponse:
    """模拟 httpx 流式响应：raise_for_status + aiter_lines"""

    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc) -> bool:
        return False


def _sse(*chunks: dict) -> list[str]:
    return [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]


def _mock_stream_client(*streams: _FakeStreamResponse):
    """按调用顺序依次返回多个流式响应"""
    client = MagicMock()
    client.stream = MagicMock(side_effect=[_FakeStreamCtx(s) for s in streams])
    return client


# 流末 usage chunk 的两种真实实现形态：
#  ① choices 为空数组（OpenAI 官方示例）
#  ② choices 为 [{"index":0,"delta":{},"finish_reason":null}]（讯飞 MaaS / scnet 实测）
_USAGE_CHUNK_EMPTY_CHOICES = {
    "choices": [],
    "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
}
_USAGE_CHUNK_WITH_CHOICE = {
    "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
}
_CONTENT_CHUNK = {
    "choices": [{"index": 0, "delta": {"content": "9"}, "finish_reason": None}]
}
_STOP_CHUNK = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}


@pytest.mark.parametrize(
    "usage_chunk",
    [_USAGE_CHUNK_EMPTY_CHOICES, _USAGE_CHUNK_WITH_CHOICE],
    ids=["empty-choices", "non-empty-choices"],
)
async def test_deepseek_stream_reports_usage(usage_chunk):
    """DeepSeekProvider 流式必须透出流末真实 usage（两种 chunk 形态都要兼容）"""
    with patch("app.providers.deepseek.settings") as mock_settings:
        mock_settings.deepseek_api_key = "test-key"
        mock_settings.deepseek_model = "deepseek-v4-flash"
        mock_settings.deepseek_thinking = False

        response = _FakeStreamResponse(
            _sse(_CONTENT_CHUNK, usage_chunk, _STOP_CHUNK)
        )
        with patch(
            "app.providers.deepseek.get_http",
            return_value=_mock_stream_client(response),
        ):
            provider = DeepSeekProvider()
            events = [
                ev
                async for ev in provider.chat_stream(
                    [{"role": "user", "content": "3的平方"}],
                    request_id="test",
                    scene="chat",
                )
            ]

    usages = [ev["_usage"] for ev in events if "_usage" in ev]
    assert usages, f"未透出 _usage（usage chunk choices={usage_chunk['choices']}）"
    assert usages[0]["prompt_tokens"] == 11
    assert usages[0]["completion_tokens"] == 1
    # usage 必须排在 _finish 之前（事件顺序契约）
    assert events.index(next(e for e in events if "_usage" in e)) < events.index(
        next(e for e in events if "_finish" in e)
    )
    assert "".join(ev["token"] for ev in events if "token" in ev) == "9"


@pytest.mark.parametrize(
    "usage_chunk",
    [_USAGE_CHUNK_EMPTY_CHOICES, _USAGE_CHUNK_WITH_CHOICE],
    ids=["empty-choices", "non-empty-choices"],
)
async def test_spark_stream_reports_usage(usage_chunk):
    """SparkProvider 流式必须透出流末真实 usage（两种 chunk 形态都要兼容）"""
    with patch("app.providers.spark.settings") as mock_settings:
        mock_settings.spark_api_password = "test-password"
        mock_settings.spark_model = "spark-ultra"
        mock_settings.spark_base_url = ""
        mock_settings.spark_thinking = False

        response = _FakeStreamResponse(
            _sse(_CONTENT_CHUNK, usage_chunk, _STOP_CHUNK)
        )
        with patch(
            "app.providers.spark.get_http",
            return_value=_mock_stream_client(response),
        ):
            provider = SparkProvider()
            events = [
                ev
                async for ev in provider.chat_stream(
                    [{"role": "user", "content": "3的平方"}],
                    request_id="test",
                    scene="chat",
                )
            ]

    usages = [ev["_usage"] for ev in events if "_usage" in ev]
    assert usages, f"未透出 _usage（usage chunk choices={usage_chunk['choices']}）"
    assert usages[0]["prompt_tokens"] == 11
    assert usages[0]["completion_tokens"] == 1
    assert events.index(next(e for e in events if "_usage" in e)) < events.index(
        next(e for e in events if "_finish" in e)
    )
    assert "".join(ev["token"] for ev in events if "token" in ev) == "9"


async def test_deepseek_stream_without_usage_chunk_still_works():
    """未返回 usage chunk 时不得报错，也不得凭空造出 _usage"""
    with patch("app.providers.deepseek.settings") as mock_settings:
        mock_settings.deepseek_api_key = "test-key"
        mock_settings.deepseek_model = "deepseek-v4-flash"
        mock_settings.deepseek_thinking = False

        response = _FakeStreamResponse(_sse(_CONTENT_CHUNK, _STOP_CHUNK))
        with patch(
            "app.providers.deepseek.get_http",
            return_value=_mock_stream_client(response),
        ):
            provider = DeepSeekProvider()
            events = [
                ev
                async for ev in provider.chat_stream(
                    [{"role": "user", "content": "3的平方"}],
                    request_id="test",
                    scene="chat",
                )
            ]

    assert not [ev for ev in events if "_usage" in ev]
    assert "".join(ev["token"] for ev in events if "token" in ev) == "9"
    assert [ev for ev in events if "_finish" in ev][0]["_finish"] == "stop"
