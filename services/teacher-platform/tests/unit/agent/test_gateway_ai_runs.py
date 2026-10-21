"""LlmGateway 单测：ai_runs 台账、thinking/token 分通道、无 embed 契约。

全部走 FakeBackend（无网络；环境无 key 也能跑——real_router_enabled 默认开但
无 key 时 backend=None，注入 backend 后可跑）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.ai_runs import AiRunRecord, AiRunsSink
from app.llm.config import LlmSettings, RETRY_POLICY, USE_CASE_ROUTES
from app.llm.gateway import AiRunMeta, ChatChunk, ChatResult, GatewayNotConfigured, LlmGateway


class MemorySink(AiRunsSink):
    def __init__(self) -> None:
        self.records: list[AiRunRecord] = []

    async def write(self, record: AiRunRecord) -> None:
        self.records.append(record)


class FakeBackend:
    """非流式：返回类 litellm ModelResponse；流式：reasoning+token 尾带 usage。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def acompletion(self, *, model, messages, stream=False, **kw):
        self.calls.append({"model": model, "stream": stream, **kw})
        if stream:
            return self._fake_stream()
        message = SimpleNamespace(content="答案", reasoning_content=None, tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7), model=model)

    async def _fake_stream(self):
        delta1 = SimpleNamespace(reasoning_content="先想一下", content=None)
        delta2 = SimpleNamespace(reasoning_content=None, content="你")
        delta3 = SimpleNamespace(reasoning_content=None, content="好")
        yield SimpleNamespace(choices=[SimpleNamespace(delta=delta1)], usage=None)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=delta2)], usage=None)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=delta3)], usage=None)
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7))


def _gateway_with_fake() -> tuple[LlmGateway, MemorySink, FakeBackend]:
    sink = MemorySink()
    backend = FakeBackend()
    # active_profile 显式钉 matrix：pydantic-settings 会读本机 .env（可能配了 spark profile），
    # 单测必须与机器环境隔离
    gateway = LlmGateway(sink, settings=LlmSettings(real_router_enabled=False, active_profile="matrix"), backend=backend)
    return gateway, sink, backend


async def test_chat_records_ai_run() -> None:
    gateway, sink, backend = _gateway_with_fake()
    result = await gateway.chat(
        [{"role": "user", "content": "什么是椭圆"}],
        use_case="chat",
        metadata=AiRunMeta(feature="butler", user_id="u1", prompt_version="v1"),
    )
    assert isinstance(result, ChatResult)
    assert result.content == "答案"
    assert backend.calls[0]["model"] == USE_CASE_ROUTES["chat"].primary
    assert len(sink.records) == 1
    row = sink.records[0]
    assert row.feature == "butler"
    assert row.model == USE_CASE_ROUTES["chat"].primary
    assert row.input_tokens == 11 and row.output_tokens == 7
    assert row.prompt_version == "v1"
    assert row.latency_ms is not None and row.cost is None  # 假响应无价格表 → cost 缺省不炸


async def test_stream_yields_thinking_then_tokens_and_records_usage() -> None:
    gateway, sink, _ = _gateway_with_fake()
    stream = await gateway.chat(
        [{"role": "user", "content": "hi"}], stream=True, metadata=AiRunMeta(feature="butler")
    )
    chunks: list[ChatChunk] = []
    async for chunk in stream:
        chunks.append(chunk)
    assert [c.kind for c in chunks] == ["thinking", "token", "token"]
    assert chunks[0].text == "先想一下"
    assert "".join(c.text for c in chunks if c.kind == "token") == "你好"
    assert len(sink.records) == 1
    assert (sink.records[0].input_tokens, sink.records[0].output_tokens) == (11, 7)


async def test_failure_still_records_error_row() -> None:
    class BoomBackend(FakeBackend):
        async def acompletion(self, **kw):
            raise TimeoutError("upstream timeout")

    sink = MemorySink()
    gateway = LlmGateway(sink, settings=LlmSettings(real_router_enabled=False, active_profile="matrix"), backend=BoomBackend())
    with pytest.raises(TimeoutError):
        await gateway.chat([{"role": "user", "content": "x"}], metadata=AiRunMeta(feature="deck"))
    assert len(sink.records) == 1
    assert "TimeoutError" in sink.records[0].error


def test_spark_profile_routes_text_use_cases():
    """spark profile：文本用例主备切 Spark；vision 恒走矩阵。

    显式置空 model_longform / model_vision——隔离 .env（LLM_MODEL_LONGFORM 等）对路由的覆盖。
    """
    settings = LlmSettings(api_key_spark="ak-x", active_profile="spark", model_spark="spark-x2.5-4b",
                           model_longform="", model_vision="")
    assert settings.resolve_route("chat").primary == "spark-x2.5-4b"
    assert settings.resolve_route("long_form").primary == "spark-x2.5-4b"
    assert settings.resolve_route("voice_formula").primary == "spark-x2.5-4b"
    assert settings.resolve_route("vision").primary == USE_CASE_ROUTES["vision"].primary  # Spark 无视觉
    matrix = LlmSettings(api_key_spark="ak-x", active_profile="matrix", model_spark="spark-x2.5-4b",
                         model_longform="", model_vision="")
    assert matrix.resolve_route("chat").primary == USE_CASE_ROUTES["chat"].primary  # 冻结矩阵不受影响
    # 配置大模型后：chat 与 long_form 切 80B（4B thinking 过长拖慢首 token 的修复）
    big = LlmSettings(api_key_spark="ak-x", active_profile="spark", model_longform="xop3qwen80bnext", model_vision="")
    assert big.resolve_route("chat").primary == "xop3qwen80bnext"
    assert big.resolve_route("long_form").primary == "xop3qwen80bnext"
    # Teacher Prep 的结构化意图/蓝图/诊断也必须复用已配置的长文模型；
    # 4B 在真实教材选项提示词下会拖到多轮 60s 超时，无法完成 Golden Path。
    assert big.resolve_route("teacher_prep_intent").primary == "xop3qwen80bnext"
    assert big.resolve_route("teacher_prep_blueprint").primary == "xop3qwen80bnext"
    assert big.resolve_route("teacher_prep_diagnostic").primary == "xop3qwen80bnext"


def test_spark_deployment_registered_only_with_key():
    """spark 部署仅在有 key 时注册；无 key 矩阵供应商不进 model_list（LiteLLM 拒绝裸 provider）。"""
    from app.llm.gateway import build_router

    settings = LlmSettings(_env_file=None, api_key_spark="ak-x", active_profile="spark", model_spark="spark-x2.5-4b",
                           model_longform="", model_vision="",
                           # litellm import 时 load_dotenv() 会把 CWD .env 灌进环境变量（压过 _env_file=None），
                           # rescue 必须以 init kwargs 显式置空（init 优先级最高）。
                           api_key_rescue="", base_url_rescue="", model_rescue="")
    router = build_router(settings)
    names = {d["model_name"] for d in router.model_list}
    assert names == {"spark-x2.5-4b"}
    empty = build_router(LlmSettings(_env_file=None, api_key_spark="", real_router_enabled=False, active_profile="matrix",
                                     api_key_rescue="", base_url_rescue="", model_rescue=""))
    assert empty.model_list == []  # 零 key → 零部署（FakeBackend 注入路线）


async def test_gateway_has_no_embed_method() -> None:
    """G2 定案：embedding 唯一入口是 B2 Embedder，网关不得提供 embed。"""
    gateway, _, _ = _gateway_with_fake()
    assert not hasattr(gateway, "embed")
    with pytest.raises(AttributeError, match="Embedder"):
        gateway.embed  # noqa: B018


async def test_unconfigured_gateway_raises_clear_error() -> None:
    gateway = LlmGateway(MemorySink(), settings=LlmSettings(api_key_spark="", real_router_enabled=False, active_profile="matrix"))
    with pytest.raises(GatewayNotConfigured):
        await gateway.chat([{"role": "user", "content": "x"}])


def test_retry_policy_matches_red_line() -> None:
    """重试 ≤2 且只对 5xx/超时类（notes/B1-notes.md §2.2 的 LiteLLM RetryPolicy）。"""
    assert RETRY_POLICY["TimeoutErrorRetries"] == 2
    assert RETRY_POLICY["InternalServerErrorRetries"] == 2
    assert RETRY_POLICY["RateLimitErrorRetries"] == 0
    assert RETRY_POLICY["BadRequestErrorRetries"] == 0
    assert RETRY_POLICY["AuthenticationErrorRetries"] == 0
    assert RETRY_POLICY["DefaultRetries"] == 0
    for route in USE_CASE_ROUTES.values():
        # 300：M2-B 实测 Spark 逐页课件 3-5min/页（long_form 扩档，config.py 注释留痕）
        assert route.timeout_s in (30, 60, 180, 300, 600)
