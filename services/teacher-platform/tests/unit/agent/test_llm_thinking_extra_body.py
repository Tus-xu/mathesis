"""LlmGateway thinking extra_body 语法适配单测（2026-10-10 方舟迁移回归）。

背景：SCNet 额度耗尽全量迁方舟后，教师端只认 Spark MaaS 顶层 enable_thinking
语法；方舟只认 {"thinking":{"type":"disabled"}}。缺通用开关时方舟模型默认开思考，
blueprint 输出 3933→17136 tokens、21s→281s（ai_runs 实证）。

全部走 FakeBackend（无网络），与机器 .env 环境隔离。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.ai_runs import AiRunRecord, AiRunsSink
from app.llm.config import LlmSettings
from app.llm.gateway import AiRunMeta, LlmGateway


class NullSink(AiRunsSink):
    async def write(self, record: AiRunRecord) -> None:
        return None


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def acompletion(self, *, model, messages, stream=False, **kw):
        self.calls.append({"model": model, "stream": stream, **kw})
        message = SimpleNamespace(content="答案", reasoning_content=None, tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(
            choices=[choice],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            model=model,
        )


def _gateway(settings: LlmSettings) -> tuple[LlmGateway, FakeBackend]:
    backend = FakeBackend()
    return LlmGateway(NullSink(), settings=settings, backend=backend), backend


ARK_BODY = {"thinking": {"type": "disabled"}}


async def test_thinking_extra_body_sent_verbatim_on_all_primary_models() -> None:
    """置定 LLM_THINKING_EXTRA_BODY → 主通道全部用例外发方舟语法（含 longform 解析）。"""
    gateway, backend = _gateway(
        LlmSettings(
            _env_file=None,  # 隔离机器 .env（LLM_OMIT_THINKING_EXTRA_BODY 等会改变断言对象）
            real_router_enabled=False,
            active_profile="spark",
            api_key_spark="k",
            base_url_spark="https://ark.example/api/plan/v3",
            model_spark="doubao-seed-2.0-mini",
            model_longform="doubao-seed-2.0-lite",
            omit_thinking_extra_body=1,  # 即使沿用旧 omit 开关，通用体也应生效
            thinking_extra_body=ARK_BODY,
        )
    )
    await gateway.chat(
        [{"role": "user", "content": "hi"}],
        use_case="teacher_prep_blueprint",
        metadata=AiRunMeta(feature="teacher_prep_blueprint"),
    )
    assert backend.calls[0]["extra_body"] == ARK_BODY

    await gateway.chat(
        [{"role": "user", "content": "hi"}],
        use_case="chat",
        metadata=AiRunMeta(feature="butler"),
    )
    assert backend.calls[1]["extra_body"] == ARK_BODY


async def test_spark_legacy_body_when_generic_unset() -> None:
    """未置通用体时保持既有契约：spark 模型发顶层 enable_thinking=False。"""
    gateway, backend = _gateway(
        LlmSettings(
            _env_file=None,
            real_router_enabled=False,
            active_profile="spark",
            api_key_spark="k",
            base_url_spark="https://maas-api.cn-huabei-1.xf-yun.com/v2",
            model_spark="xop3qwen235b",
            model_longform="",  # 置空 → chat 解析回 model_spark（否则默认 80B 路由不附带 spark 体）
            thinking_extra_body=None,
            # litellm import 时会 load_dotenv() 把 CWD .env 灌进 os.environ；
            # init kwargs 是唯一压得过环境变量的源，rescue/omit 必须显式钉死。
            api_key_rescue="", base_url_rescue="", model_rescue="",
            omit_thinking_extra_body=0,
        )
    )
    await gateway.chat(
        [{"role": "user", "content": "hi"}],
        use_case="chat",
        metadata=AiRunMeta(feature="butler"),
    )
    assert backend.calls[0]["extra_body"] == {"enable_thinking": False}


def test_env_json_parses_into_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """部署走环境变量：LLM_THINKING_EXTRA_BODY 的 JSON 必须能解析为 dict。"""
    monkeypatch.setenv("LLM_THINKING_EXTRA_BODY", '{"thinking":{"type":"disabled"}}')
    settings = LlmSettings(real_router_enabled=False, active_profile="spark", _env_file=None)
    assert settings.thinking_extra_body == ARK_BODY


def test_env_unset_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_THINKING_EXTRA_BODY", raising=False)
    settings = LlmSettings(real_router_enabled=False, active_profile="spark", _env_file=None)
    assert settings.thinking_extra_body is None
