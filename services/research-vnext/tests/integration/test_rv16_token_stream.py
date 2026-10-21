"""RV16 · token 流 SSE 集成测试（真实 PostgreSQL + Alembic schema）。

覆盖 X7（第二 SSE 通道 / 不含 mutation / 落库续传 / 双前缀）与 K2（帧序）。
LLM 用桩后端驱动（不联网、不伪造真实模型）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from research_vnext.core.llm import gateway as gw
from tests.conftest import auth_headers
from tests.integration.conftest import create_session


class StreamStub:
    name = "stub"
    available = True
    model = "stub-model"

    def __init__(self) -> None:
        self.acalls = 0
        self.scalls = 0

    async def acompletion(self, *, model=None, messages=None, stream=False, **kw):  # noqa: ANN001,ANN003,ANN201
        self.acalls += 1
        return gw.Completion(
            text="", model=self.model, provider="stub", raw={"finish_reason": "stop"}
        )

    async def astream(self, *, model=None, messages=None, **kw) -> AsyncIterator[dict]:  # noqa: ANN001,ANN003
        self.scalls += 1
        for piece in ("甲", "乙", "丙"):
            yield {"type": "token", "delta": piece, "model": self.model, "finish_reason": None}


@pytest.fixture()
def stub_llm():
    b = StreamStub()
    gw.set_backend(b)
    yield b
    gw.set_backend(None)


def _parse_sse(text: str) -> list[tuple[str | None, str | None, str]]:
    """解析 SSE 文本 → [(event, id, data)]。"""
    frames: list[tuple[str | None, str | None, str]] = []
    event = id_ = data = None
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line == "":
            if event is not None or data is not None or id_ is not None:
                frames.append((event, id_, data or ""))
            event = id_ = data = None
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("id:"):
            id_ = line[len("id:"):].strip()
        elif line.startswith("data:"):
            data = line[len("data:"):].strip()
    return frames


def _make_session(client, ctx, new_ids):
    r, _ = create_session(client, ctx, new_ids)
    assert r.status_code == 201, r.text
    return r.json()["session"]["id"]


def test_token_stream_frames_and_no_mutation(client, new_ids, ctx_factory, stub_llm):
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _make_session(client, ctx, new_ids)
    headers = auth_headers(ctx)

    # 发一条消息（user message 落库；assistant 侧由桩后端驱动）
    client.post(f"/api/research-v1/sessions/{sid}/messages", json={"message": "测试流式"}, headers=headers)

    before = client.get(f"/api/research-v1/sessions/{sid}", headers=headers).json()

    resp = client.get(f"/api/research-v1/sessions/{sid}/stream", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(resp.text)
    events = [e for e, _, _ in frames]
    assert "snapshot" in events
    token_frames = [d for e, _, d in frames if e == "token"]
    assert token_frames, "应产出 token 帧"
    joined = "".join(
        __import__("json").loads(d)["delta"] for d in token_frames
    )
    assert "甲乙丙" in joined
    done = [d for e, _, d in frames if e == "done"]
    assert done and __import__("json").loads(done[0])["degraded"] is False
    # token 帧带 turn 内单调 seq（id）
    ids = [int(i) for e, i, _ in frames if e == "token" and i]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)

    after = client.get(f"/api/research-v1/sessions/{sid}", headers=headers).json()
    # X7：token 流**不含 mutation** → 会话 revision 不变
    assert before["revision"] == after["revision"]


def test_token_stream_resume_is_terminal(client, new_ids, ctx_factory, stub_llm):
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _make_session(client, ctx, new_ids)
    headers = auth_headers(ctx)
    client.post(f"/api/research-v1/sessions/{sid}/messages", json={"message": "续传"}, headers=headers)

    r1 = client.get(f"/api/research-v1/sessions/{sid}/stream", headers=headers)
    frames1 = _parse_sse(r1.text)
    last_id = max(int(i) for _, i, _ in frames1 if i)
    calls_after_first = stub_llm.scalls

    # 带 Last-Event-ID 续传：已终态 → 只回 snapshot，不再驱动 LLM
    r2 = client.get(
        f"/api/research-v1/sessions/{sid}/stream",
        headers={**headers, "Last-Event-ID": str(last_id)},
    )
    frames2 = _parse_sse(r2.text)
    events2 = [e for e, _, _ in frames2]
    assert "snapshot" in events2
    assert "token" not in events2  # 终态后不再产出
    assert stub_llm.scalls == calls_after_first  # 未再次调用模型


def test_token_stream_dual_prefix(client, new_ids, ctx_factory, stub_llm):
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _make_session(client, ctx, new_ids)
    headers = auth_headers(ctx)
    client.post(f"/api/research-v1/sessions/{sid}/messages", json={"message": "双前缀"}, headers=headers)
    for prefix in ("/api/research-v1", "/api/research-vnext"):
        r = client.get(f"{prefix}/sessions/{sid}/stream", headers=headers)
        assert r.status_code == 200, f"{prefix}: {r.text}"
        assert "event: snapshot" in r.text


def test_stream_requires_access_context(client, new_ids, ctx_factory):
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _make_session(client, ctx, new_ids)
    r = client.get(f"/api/research-v1/sessions/{sid}/stream")
    assert r.status_code == 401
