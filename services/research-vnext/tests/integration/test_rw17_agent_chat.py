"""RV17 · 科研工作区「自由对话」端点集成测试（宪法 §31）。

验证的核心行为（D1）：

1. **自由文本 → 真实对话**：`POST /projects/{id}/agent/chat` 走**会话子系统**
   （`message_service.send_message` → LLM 网关），把用户的自然语言问题变成
   一条**真实 assistant 消息**（`reply.role == 'assistant'`），而不是关键词路由到
   preset / 结果卡。
2. **与「点能力」严格分工**：自由对话**不**创建 `research_agent_task`（不抢走
   preset 执行路径），也不会产出 `ResearchResult` 结果卡。
3. 鉴权、幂等、空消息、历史恢复等边界。

LLM 用**桩后端**驱动（不联网、不伪造真实模型），因此测的是「收到模型输出之后
平台做了什么」，而不是模型质量（与 `test_messages_cr6.py` 同一纪律）。
"""

from __future__ import annotations

import json
import uuid

import pytest

from research_vnext.core.llm import gateway as gw
from tests.conftest import auth_headers


class StubBackend:
    """固定返回一段 JSON 的假后端。"""

    name = "stub"
    available = True
    model = "stub-model-1"

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls = 0
        self.raw_finish_reason = ""

    async def acompletion(self, *, model=None, messages=None, stream=False, **kw):  # noqa: ANN001,ANN003,ANN201
        self.calls += 1
        if self._error is not None:
            raise self._error
        return gw.Completion(
            text=self._text,
            model=self.model,
            provider="stub",
            input_tokens=11,
            output_tokens=7,
            latency_ms=3,
            raw={"finish_reason": self.raw_finish_reason},
        )


def proposal_json(kind: str = "answer", text: str = "分析完成", payload: dict | None = None) -> str:
    return json.dumps({"kind": kind, "text": text, "payload": payload or {}}, ensure_ascii=False)


@pytest.fixture()
def stub_llm():
    """安装桩后端并在用例结束后复位（保证不污染其它用例）。"""
    installed: list[StubBackend] = []

    def _install(text: str = "", error: Exception | None = None) -> StubBackend:
        b = StubBackend(text, error)
        gw.set_backend(b)
        installed.append(b)
        return b

    yield _install
    gw.set_backend(None)


def _create_project(client, ctx) -> str:
    r = client.post(
        "/api/research-vnext/projects",
        json={"name": "对话测试项目", "client_request_id": f"rw17-{uuid.uuid4().hex[:12]}"},
        headers=auth_headers(ctx),
    )
    assert r.status_code == 201, r.text
    return r.json()["project"]["id"]


def _post_chat(client, ctx, pid, *, message="这篇论文用了什么方法？", crid=None, headers=None):
    body: dict = {"message": message}
    if crid is not None:
        body["client_message_id"] = crid
    return client.post(
        f"/api/research-vnext/projects/{pid}/agent/chat",
        json=body,
        headers=headers if headers is not None else auth_headers(ctx),
    )


# --------------------------------------------------------------------------
# 核心：自由文本 → 真实对话
# --------------------------------------------------------------------------


def test_free_text_returns_real_assistant_reply(client, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json("answer", "该方法基于有限元误差估计，采用两句话概括如下。"))
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)

    resp = _post_chat(client, ctx, pid, message="这篇论文用了什么方法？用两句话概括")
    assert resp.status_code == 201, resp.text
    assert resp.headers.get("X-Idempotency-Status") == "EXECUTED"

    body = resp.json()
    assert body["session_id"], "必须落到一个真实会话"
    reply = body["reply"]
    assert reply is not None, "必须返回一条 assistant 回答"
    assert reply["role"] == "assistant"
    assert "有限元" in reply["text"], reply["text"]

    # timeline 里同时有 user 与 assistant 两条持久消息
    roles = [it["role"] for it in body["timeline"]]
    assert roles == ["user", "assistant"], roles

    # 能力表：对话输入应为 available
    caps = {c["key"]: c["state"] for c in body["capabilities"]}
    assert caps["research-message"] == "available"

    # 关键分工（D1）：自由对话**不**创建 agent task（不抢走 preset 执行路径）
    tasks = client.get(
        f"/api/research-vnext/projects/{pid}/agent/tasks", headers=auth_headers(ctx)
    ).json()
    assert tasks["items"] == [], "自由对话不得创建 preset 任务"


def test_chat_session_is_reused_across_turns(client, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json("answer", "第二句话。"))
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)

    first = _post_chat(client, ctx, pid, message="第一个问题")
    sid = first.json()["session_id"]
    second = _post_chat(client, ctx, pid, message="第二个问题")
    assert second.json()["session_id"] == sid, "同一项目同一用户必须复用同一段对话"
    assert len(second.json()["timeline"]) == 4  # 2 user + 2 assistant


def test_chat_replay_is_idempotent(client, ctx_factory, stub_llm) -> None:
    b = stub_llm(proposal_json("answer", "唯一回答"))
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)
    crid = f"rw17c-{uuid.uuid4().hex[:12]}"

    first = _post_chat(client, ctx, pid, message="同样的话", crid=crid)
    assert first.status_code == 201
    assert first.headers["X-Idempotency-Status"] == "EXECUTED"

    second = _post_chat(client, ctx, pid, message="同样的话", crid=crid)
    assert second.status_code == 200
    assert second.headers["X-Idempotency-Status"] == "REPLAYED"
    # 重放**不重复调用模型**，返回原回答
    assert b.calls == 1
    assert second.json()["reply"]["text"] == first.json()["reply"]["text"]


def test_chat_llm_failure_degrades_without_crash(client, ctx_factory, stub_llm) -> None:
    """模型不可用 → 仍 201，用户消息持久化，assistant 给**人话降级**（不伪造结论）。"""
    stub_llm(error=gw.LlmGateError("LLM_CALL_FAILED", "模拟上游 500"))
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)

    resp = _post_chat(client, ctx, pid, message="会失败的问题")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["timeline"][0]["text"] == "会失败的问题", "用户消息必须仍然持久化"
    assert "模型调用失败" in body["timeline"][1]["text"]


# --------------------------------------------------------------------------
# 鉴权 / 输入边界
# --------------------------------------------------------------------------


def test_chat_requires_access_context(client, ctx_factory) -> None:
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)
    resp = client.post(f"/api/research-vnext/projects/{pid}/agent/chat", json={"message": "hi"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] in {
        "ACCESS_CONTEXT_MISSING",
        "ACCESS_CONTEXT_INVALID_SIGNATURE",
    }


def test_chat_foreign_actor_forbidden(client, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json("answer", "x"))
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    owner = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, owner)

    stranger = ctx_factory(tenant_id=tenant_id)  # 同 tenant、不同 actor
    resp = _post_chat(client, stranger, pid, message="越权")
    assert resp.status_code in (403, 404), resp.text


def test_chat_empty_message_422(client, ctx_factory) -> None:
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)
    resp = _post_chat(client, ctx, pid, message="   ")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


# --------------------------------------------------------------------------
# 历史恢复
# --------------------------------------------------------------------------


def test_get_chat_thread_before_any_chat_is_honest_empty(client, ctx_factory) -> None:
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)
    resp = client.get(
        f"/api/research-vnext/projects/{pid}/agent/chat", headers=auth_headers(ctx)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"session_id": None, "timeline": []}


def test_get_chat_thread_returns_history(client, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json("answer", "历史的回答"))
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = ctx_factory(actor_id=actor_id, tenant_id=tenant_id)
    pid = _create_project(client, ctx)
    _post_chat(client, ctx, pid, message="历史的问题")

    resp = client.get(
        f"/api/research-vnext/projects/{pid}/agent/chat", headers=auth_headers(ctx)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"]
    texts = [it["text"] for it in body["timeline"]]
    assert "历史的问题" in texts
    assert "历史的回答" in texts
