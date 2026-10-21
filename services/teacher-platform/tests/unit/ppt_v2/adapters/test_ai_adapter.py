"""AIPlatformAdapter 单元测试（R11；FakeBackend 不触网）。

覆盖：chat 模型 pin（spark-x2.5-4b）+ thinking 默认关、stream 通道、structured
解析/纠错重试/上限、key 缺失 fail closed、vision 通道。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from app.ppt_v2.adapters.ai import MODEL_TEXT, MODEL_VISION, AIPlatformAdapter
from app.ppt_v2.adapters.errors import AdapterFatalError


class _FakeBackend:
    """按序返回预设 content；流式返回 thinking+token。"""

    def __init__(self, contents: list[str] | None = None) -> None:
        self.contents = list(contents or ["答案"])
        self.calls: list[dict] = []
        self._idx = 0

    async def acompletion(self, *, model, messages, stream=False, **kw):
        self.calls.append({"model": model, "stream": stream, "messages": messages, **kw})
        if stream:
            return self._fake_stream()
        content = self.contents[min(self._idx, len(self.contents) - 1)]
        self._idx += 1
        message = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(
            choices=[choice],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model=model,
        )

    async def _fake_stream(self):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content=None, content="你"))], usage=None)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content=None, content="好"))], usage=None)
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2))


def _adapter(backend: _FakeBackend) -> AIPlatformAdapter:
    return AIPlatformAdapter(backend=backend, text_key="k", vision_key="k")


async def test_chat_pins_spark_4b_and_thinking_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # 隔离机器 .env（LLM_THINKING_EXTRA_BODY 等会改变 extra_body 断言对象）：
    # LlmSettings 的 env_file 是相对 CWD 的 .env，切到空临时目录即与机器环境解耦。
    # 全量套件下还须 delenv：任一先跑的测试 import main（main.py load_dotenv）后 .env 值
    # 进入 os.environ，pydantic-settings 的 os.environ 优先级会穿透 chdir 隔离
    # （tel F5；与 agent/test_llm_thinking_extra_body.py:112 同一隔离惯例）。
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_THINKING_EXTRA_BODY", raising=False)
    backend = _FakeBackend(["椭圆是到两定点距离之和等于常数的点的轨迹。"])
    ai = _adapter(backend)
    reply = await ai.chat([{"role": "user", "content": "什么是椭圆"}], use_case="chat", temperature=0.3)
    assert reply.text.startswith("椭圆")
    assert reply.model == MODEL_TEXT
    call = backend.calls[0]
    assert call["model"] == MODEL_TEXT
    # R04 契约：thinking 默认关。通道实测（app/llm/gateway.py:_call_kwargs）该 MaaS
    # 只认顶层 enable_thinking（旧 {"thinking":{"type":"disabled"}} 实测不生效 →
    # reasoning token 翻倍 1726→8687、延迟 25.4s→108.4s）。断言随契约修正为实际外发体。
    assert call["extra_body"] == {"enable_thinking": False}


async def test_stream_yields_tokens() -> None:
    backend = _FakeBackend()
    ai = _adapter(backend)
    chunks = [c async for c in ai.stream([{"role": "user", "content": "hi"}])]
    assert "".join(c.text for c in chunks) == "你好"
    assert backend.calls[0]["stream"] is True


class _Outline(BaseModel):
    title: str = Field(description="标题")
    goals: list[str] = Field(description="目标")


async def test_structured_parses_valid_json() -> None:
    backend = _FakeBackend(['{"title": "椭圆的定义", "goals": ["理解定义", "会求方程"]}'])
    ai = _adapter(backend)
    obj = await ai.structured(_Outline, system="sys", user="outline")
    assert obj.title == "椭圆的定义"
    assert obj.goals == ["理解定义", "会求方程"]


async def test_structured_recovers_from_invalid_json_on_retry() -> None:
    backend = _FakeBackend([
        "抱歉，我输出不了 JSON",
        '```json\n{"title": "t", "goals": ["g1"]}\n```',
    ])
    ai = _adapter(backend)
    obj = await ai.structured(_Outline, system="sys", user="u")
    assert obj.title == "t"
    assert len(backend.calls) == 2


async def test_structured_fails_closed_after_bounded_retries() -> None:
    backend = _FakeBackend(["不是 JSON"] * 5)
    ai = _adapter(backend)
    with pytest.raises(AdapterFatalError, match="结构化"):
        await ai.structured(_Outline, system="sys", user="u")
    assert len(backend.calls) == 3  # 初始 + 2 次纠错重试（bounded）


async def test_missing_text_key_fails_closed() -> None:
    ai = AIPlatformAdapter(backend=_FakeBackend(), text_key="")
    with pytest.raises(AdapterFatalError, match="MODEL_API_KEY"):
        await ai.chat([{"role": "user", "content": "x"}])


async def test_vision_passes_image_through_and_returns_text() -> None:
    backend = _FakeBackend(['{"purpose": "p", "objects": [], "relations": []}'])
    ai = _adapter(backend)
    text = await ai.vision("data:image/png;base64,AAAA", "分析")
    assert text.startswith("{")
    # 视觉通道模型 = xophunyuanocr；image 原样进入 messages
    vision_call = backend.calls[0]
    assert vision_call["model"] == MODEL_VISION
    content = vision_call["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAAA"
