"""RV16 · LLM 流式化单元测试（重试 / 超时分层 / 降级链）。

对齐 KERNEL §2.2 与 X5/X6：重试 ≥2（指数退避+抖动）；降级链 = 同账号第二模型 →
确定性兜底；**禁止跨账号换 key**；降级必须标注 `degraded` + `degraded_reason`。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from research_vnext.core.llm import gateway as gw
from research_vnext.core.llm import streaming


class StreamStub:
    """可编程的流式后端桩（不联网、不伪造真实模型）。"""

    name = "stub"
    available = True

    def __init__(self, *, model: str = "stub-primary", fail_times: int = 0, error: Exception | None = None):
        self.model = model
        self.calls = 0
        self._fail_times = fail_times
        self._error = error

    async def acompletion(self, **kw: Any) -> gw.Completion:  # pragma: no cover - 未用
        raise NotImplementedError

    async def astream(self, *, model: str | None = None, messages: Any = None, **kw: Any) -> AsyncIterator[dict]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise (self._error or gw.LlmGateError("LLM_TIMEOUT", "stub timeout"))
        for piece in ("你", "好"):
            yield {"type": "token", "delta": piece, "model": model or self.model, "finish_reason": None}


@pytest.fixture(autouse=True)
def _reset_backend():
    yield
    gw.set_backend(None)


def _collect(agen) -> list[dict]:
    import asyncio

    async def _run():
        out = []
        async for ev in agen:
            out.append(ev)
        return out

    return asyncio.run(_run())


def test_primary_success_not_degraded(monkeypatch):
    monkeypatch.setenv("RESEARCH_LLM_MODEL_FALLBACK", "")
    stub = StreamStub()
    gw.set_backend(stub)
    events = _collect(streaming.astream_completion([{"role": "user", "content": "hi"}]))
    tokens = [e for e in events if e["type"] == "token"]
    done = [e for e in events if e["type"] == "done"][0]
    assert "".join(t["delta"] for t in tokens) == "你好"
    assert done["degraded"] is False
    assert done["degraded_reason"] is None


def test_retry_then_success(monkeypatch):
    """首次失败 → 显式重试（指数退避+抖动）→ 成功。"""
    monkeypatch.setenv("RESEARCH_LLM_MODEL_FALLBACK", "")
    monkeypatch.setenv("RESEARCH_LLM_STREAM_RETRIES", "2")
    monkeypatch.setenv("RESEARCH_LLM_RETRY_BACKOFF_BASE_S", "0.0")
    stub = StreamStub(fail_times=1)
    gw.set_backend(stub)
    events = _collect(streaming.astream_completion([{"role": "user", "content": "hi"}]))
    assert any(e["type"] == "token" for e in events)
    assert stub.calls == 2  # 第一次失败 + 一次重试成功


def test_deterministic_fallback_when_no_second_model(monkeypatch):
    """X6：同账号无第二模型 → 降级止于确定性兜底，并**标注** degraded。"""
    monkeypatch.setenv("RESEARCH_LLM_MODEL_FALLBACK", "")
    monkeypatch.setenv("RESEARCH_LLM_STREAM_RETRIES", "0")
    monkeypatch.setenv("RESEARCH_LLM_DETERMINISTIC_FALLBACK", "1")
    stub = StreamStub(fail_times=99)
    gw.set_backend(stub)
    events = _collect(streaming.astream_completion([{"role": "user", "content": "hi"}]))
    done = [e for e in events if e["type"] == "done"][0]
    assert done["degraded"] is True
    assert done["degraded_reason"] == "deterministic_fallback"
    assert done["model"] == "deterministic"
    assert any("降级" in (e.get("delta") or "") for e in events if e["type"] == "token")


def test_honest_unavailable_when_no_fallback(monkeypatch):
    """确定性兜底关闭 + 无第二模型 ⇒ 如实 LLM_UNAVAILABLE（**不伪装成功**）。"""
    monkeypatch.setenv("RESEARCH_LLM_MODEL_FALLBACK", "")
    monkeypatch.setenv("RESEARCH_LLM_STREAM_RETRIES", "0")
    monkeypatch.setenv("RESEARCH_LLM_DETERMINISTIC_FALLBACK", "0")
    stub = StreamStub(fail_times=99)
    gw.set_backend(stub)
    events = _collect(streaming.astream_completion([{"role": "user", "content": "hi"}]))
    assert not any(e["type"] == "done" for e in events)
    err = [e for e in events if e["type"] == "error"][0]
    assert err["code"] in ("LLM_UNAVAILABLE", "LLM_NOT_CONFIGURED")
    assert err["degraded"] is True


def test_second_model_degraded_reason(monkeypatch):
    """配了同账号第二模型：主模型失败 → 备用模型成功，标注 degraded。"""
    monkeypatch.setenv("RESEARCH_LLM_MODEL_FALLBACK", "stub-secondary")
    monkeypatch.setenv("RESEARCH_LLM_STREAM_RETRIES", "0")
    stub = StreamStub(model="stub-primary", fail_times=1)
    gw.set_backend(stub)
    events = _collect(streaming.astream_completion([{"role": "user", "content": "hi"}]))
    done = [e for e in events if e["type"] == "done"][0]
    assert done["degraded"] is True
    assert done["degraded_reason"] in ("primary_timeout", "primary_error")
    assert done["model"] == "stub-secondary"


def test_astream_collect_returns_text(monkeypatch):
    monkeypatch.setenv("RESEARCH_LLM_MODEL_FALLBACK", "")
    stub = StreamStub()
    gw.set_backend(stub)
    import asyncio

    text, terminal = asyncio.run(
        streaming.astream_collect([{"role": "user", "content": "hi"}])
    )
    assert text == "你好"
    assert terminal["type"] == "done"
