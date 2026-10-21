"""R11.5-C fail-closed 修复验收：显式空 key / 显式空模型 / 搜索 key 语义（回归钉死）。"""
from __future__ import annotations

import pytest

from app.llm.ai_runs import MemoryAiRunsSink
from app.llm.config import LlmSettings
from app.llm.gateway import GatewayNotConfigured, LlmGateway
from app.ppt_v2.adapters.ai import AIPlatformAdapter
from app.ppt_v2.adapters.errors import AdapterFatalError
from app.ppt_v2.adapters.search import SearchAdapter

# ── AI 适配器：显式空 key 必须 fail-closed（即使注入了 backend）──

class _FakeBackend:
    """最小 fake：仅记录调用（不应被调用到）。"""

    def __init__(self):
        self.calls = []

    async def acompletion(self, *, model, messages, stream=False, **kw):
        self.calls.append(model)
        from types import SimpleNamespace

        choice = SimpleNamespace(
            message=SimpleNamespace(content="x", reasoning_content=None, tool_calls=None),
            finish_reason="stop",
        )
        return SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1), model=model)


async def test_ai_explicit_empty_text_key_fails_closed_even_with_backend() -> None:
    ai = AIPlatformAdapter(backend=_FakeBackend(), text_key="")
    with pytest.raises(AdapterFatalError, match="MODEL_API_KEY"):
        await ai.chat([{"role": "user", "content": "x"}])


async def test_ai_explicit_empty_vision_key_fails_closed() -> None:
    ai = AIPlatformAdapter(backend=_FakeBackend(), text_key="k", vision_key="")
    with pytest.raises(AdapterFatalError, match="VISION_API_KEY"):
        await ai.vision("data:image/png;base64,AAAA", "分析")


async def test_ai_none_key_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "env-key")
    bookmark = {"calls": 0}

    class _B(_FakeBackend):
        async def acompletion(self, *, model, messages, stream=False, **kw):
            bookmark["calls"] += 1
            return await super().acompletion(model=model, messages=messages, stream=stream, **kw)

    ai = AIPlatformAdapter(backend=_B(), text_key=None, vision_key="k")
    await ai.chat([{"role": "user", "content": "hi"}])
    assert bookmark["calls"] == 1  # None → 环境兜底可用（显式空 ≠ None）


# ── 搜索适配器：显式空 api_key 必须 fail-closed ──

async def test_search_explicit_empty_key_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_API_KEY", "env-key")  # 即便环境有 key，显式空仍拒绝
    sa = SearchAdapter(api_key="")
    with pytest.raises(AdapterFatalError, match="SEARCH_API_KEY"):
        await sa.search("椭圆")


async def test_search_none_key_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_API_KEY", "env-secret")
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer env-secret"
        import httpx

        return httpx.Response(200, json={"data": {"webPages": {"value": []}}})

    import httpx

    sa = SearchAdapter(api_key=None, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await sa.search("q")
    assert out.hits == []


async def test_search_no_key_anywhere_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    sa = SearchAdapter(api_key=None)
    with pytest.raises(AdapterFatalError, match="SEARCH_API_KEY"):
        await sa.search("q")


# ── LlmGateway：显式空模型 → fail-closed（不向供应商发空模型名）──

def _gateway(settings: LlmSettings) -> LlmGateway:

    return LlmGateway(MemoryAiRunsSink(), settings=settings, backend=_FakeBackend())


def _spark_settings(**overrides) -> LlmSettings:
    """Spark profile 显式钉空 model_longform：防 .env 的 LLM_MODEL_LONGFORM 污染路由解析。"""
    base = {
        "active_profile": "spark",
        "api_key_spark": "k",
        "model_spark": "spark-x2.5-4b",
        "model_longform": "",
    }
    base.update(overrides)
    return LlmSettings(**base)


def test_empty_resolved_model_fails_closed() -> None:
    gw = _gateway(_spark_settings(model_spark="", model_longform=""))
    with pytest.raises(GatewayNotConfigured, match="空模型"):
        gw._resolve_model("long_form", None)


def test_explicit_blank_model_fails_closed() -> None:
    gw = _gateway(_spark_settings())
    with pytest.raises(GatewayNotConfigured, match="显式模型名为空"):
        gw._resolve_model("chat", "   ")


def test_valid_model_passes() -> None:
    gw = _gateway(_spark_settings())
    assert gw._resolve_model("chat", "qwen/qwen-plus") == "qwen/qwen-plus"
    assert gw._resolve_model("chat", None) == "spark-x2.5-4b"


def test_whitespace_padded_model_stripped() -> None:
    gw = _gateway(_spark_settings())
    assert gw._resolve_model("chat", "  deepseek/deepseek-chat ") == "deepseek/deepseek-chat"
