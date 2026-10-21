"""单一供应商拓扑下的**诚实失败 → 可恢复**路径（集成，真实 PostgreSQL）。

LEAD ADDENDUM 的核心要求：

> 只有一家供应商、一个账号，没有供应商级容灾。因此失败是**预期会发生的状态**，
> 不是异常；它必须是**一等的、有测试的状态**，而不是事后补的 fallback。

本文件锁定四件事：
1. 失败时用户消息**仍然持久化**（不能因为模型挂了就丢掉研究者写的话）；
2. 失败**被记入** `research_ai_run`（模型 / provider / 错误码 / trace）；
3. 快照里的能力状态与 assistant 说明**诚实**（绝不编造回答）；
4. 这一轮**可以重试**，且重试**不会新增 user 消息**。
"""

from __future__ import annotations

import json
import uuid

import pytest

from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import ResearchAiRunRow, ResearchMessageRow
from research_vnext.core.llm import capacity as cap
from research_vnext.core.llm import gateway as gw
from tests.integration.conftest import auth_headers, create_session
from tests.integration.test_messages_cr6 import StubBackend, proposal_json


@pytest.fixture()
def sess(client, new_ids, ctx_factory):
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    assert r.status_code == 201, r.text
    return {
        "ctx": ctx,
        "session_id": uuid.UUID(r.json()["session"]["id"]),
        "tenant_id": new_ids["tenant_id"],
        "actor_id": uuid.UUID(ctx["actor_id"]),
    }


@pytest.fixture(autouse=True)
def _reset_capacity():
    """容量闸是**进程内**状态；每个用例必须从干净状态开始。"""
    cap.reset_capacity()
    yield
    gw.set_backend(None)
    cap.reset_capacity()


def _post(client, sess, *, message="你好", crid=None):
    body = {"message": message}
    if crid:
        body["client_message_id"] = crid
    return client.post(
        f"/api/research-v1/sessions/{sess['session_id']}/messages",
        json=body,
        headers=auth_headers(sess["ctx"]),
    )


def _user_message_id(client, sess) -> str:
    snap = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    users = [m for m in snap["timeline"] if m["role"] == "user"]
    return users[-1]["id"]


def _retry(client, sess, message_id: str):
    return client.post(
        f"/api/research-v1/sessions/{sess['session_id']}/messages/{message_id}:retry",
        headers=auth_headers(sess["ctx"]),
    )


# ==========================================================================
# 1 · 未配置凭据（生产里会真实发生的状态）
# ==========================================================================


def test_unconfigured_is_a_first_class_state(client, sess) -> None:
    """无凭据 ⇒ 用户消息落库 + 诚实说明 + 能力标记 unavailable + 可重试。"""
    assert gw.llm_configured() is False
    r = _post(client, sess, message="我的消息不能丢")
    assert r.status_code == 201, r.text

    snap = r.json()
    roles = [m["role"] for m in snap["timeline"]]
    assert roles == ["user", "assistant"]
    assert snap["timeline"][0]["text"] == "我的消息不能丢"
    assert "模型不可用" in snap["timeline"][1]["text"]
    assert "已保存" in snap["timeline"][1]["text"]

    caps = {c["key"]: c for c in snap["capabilities"]}
    assert caps["research-message"]["state"] == "unavailable"
    assert caps["research-message"]["reason"] == "llm_not_configured"

    # AI run 必须记录这次失败（而不是"什么都没发生过"）
    with session_scope() as db:
        runs = (
            db.query(ResearchAiRunRow)
            .filter(ResearchAiRunRow.session_id == sess["session_id"])
            .all()
        )
    assert len(runs) == 1
    assert runs[0].status == "FAILED"
    assert runs[0].error_code == "LLM_NOT_CONFIGURED"
    assert len(runs[0].trace_id) == 32

    # 该轮被标记为可重试
    st = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}/messages/{_user_message_id(client, sess)}",
        headers=auth_headers(sess["ctx"]),
    )
    assert st.status_code == 200, st.text
    assert st.json()["retryable"] is True
    assert st.json()["error_code"] == "LLM_NOT_CONFIGURED"
    assert st.json()["has_proposal"] is False


# ==========================================================================
# 2 · 账号↔模型不配对（实测 403）
# ==========================================================================


def test_account_model_mismatch_is_actionable_not_generic(client, sess) -> None:
    """403 必须呈现为"换哪把 key"，而不是通用的调用失败。"""
    err = gw.ModelAccountMismatch(
        model="xophunyuanocr", account="A",
        hint="模型 'xophunyuanocr' 只在账号 B 下可用；当前账号是 'A'",
    )
    gw.set_backend(StubBackend(error=err))

    r = _post(client, sess, message="需要视觉模型")
    assert r.status_code == 201, r.text
    snap = r.json()
    text = snap["timeline"][1]["text"]
    assert "403" in text and "账号" in text
    assert "重试" in text  # 明确说明重试不会恢复
    caps = {c["key"]: c for c in snap["capabilities"]}
    assert caps["research-message"]["state"] == "capability-disabled"
    assert caps["research-message"]["reason"] == "llm_model_account_mismatch"

    with session_scope() as db:
        run = (
            db.query(ResearchAiRunRow)
            .filter(ResearchAiRunRow.session_id == sess["session_id"])
            .one()
        )
    assert run.error_code == "LLM_MODEL_ACCOUNT_MISMATCH"


def test_rate_limit_is_identified_as_capacity_event(client, sess) -> None:
    """429 = 单账号容量事件，必须与普通失败可区分。"""
    gw.set_backend(StubBackend(error=gw.LlmGateError("LLM_RATE_LIMITED", "账号 RPM 上限已触及")))
    r = _post(client, sess, message="突发流量")
    snap = r.json()
    assert "429" in snap["timeline"][1]["text"]
    assert "没有第二供应商" in snap["timeline"][1]["text"]
    caps = {c["key"]: c for c in snap["capabilities"]}
    assert caps["research-message"]["reason"] == "llm_rate_limited"


# ==========================================================================
# 3 · 重试这一轮（不新增 user 消息）
# ==========================================================================


def test_retry_after_failure_succeeds_and_does_not_duplicate_user_message(client, sess) -> None:
    """核心：失败 → 重试 → 成功，且**只有一条** user 消息。"""
    gw.set_backend(gw.UnavailableBackend())
    assert _post(client, sess, message="只写一次").status_code == 201
    before = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    assert [m["role"] for m in before["timeline"]] == ["user", "assistant"]

    mid = _user_message_id(client, sess)
    gw.set_backend(StubBackend(proposal_json("answer", "补上的回答")))
    r = _retry(client, sess, mid)
    assert r.status_code == 200, r.text
    snap = r.json()

    users = [m for m in snap["timeline"] if m["role"] == "user"]
    assert len(users) == 1, "重试**不得**新增 user 消息（否则污染实验记录）"
    assert users[0]["text"] == "只写一次"
    assistants = [m for m in snap["timeline"] if m["role"] == "assistant"]
    assert len(assistants) == 1, "重试前必须先清掉该轮的失败提示行"
    assert assistants[0]["text"] == "补上的回答"

    caps = {c["key"]: c for c in snap["capabilities"]}
    assert caps["research-message"]["state"] == "available"


def test_retry_records_a_second_ai_run(client, sess) -> None:
    """两次尝试必须留下**两条**审计记录（而不是覆盖）。"""
    gw.set_backend(gw.UnavailableBackend())
    _post(client, sess, message="审计")
    mid = _user_message_id(client, sess)

    gw.set_backend(StubBackend(proposal_json("answer", "ok")))
    assert _retry(client, sess, mid).status_code == 200

    with session_scope() as db:
        runs = (
            db.query(ResearchAiRunRow)
            .filter(ResearchAiRunRow.session_id == sess["session_id"])
            .order_by(ResearchAiRunRow.created_at)
            .all()
        )
    assert len(runs) == 2
    assert runs[0].status == "FAILED"
    assert runs[1].status == "SUCCEEDED"


def test_retry_emits_event_marked_as_retry(client, sess) -> None:
    from research_vnext.core.db.models import DomainEventRow

    gw.set_backend(gw.UnavailableBackend())
    _post(client, sess, message="事件")
    mid = _user_message_id(client, sess)
    gw.set_backend(StubBackend(proposal_json("answer", "ok")))
    _retry(client, sess, mid)

    with session_scope() as db:
        rows = (
            db.query(DomainEventRow)
            .filter(
                DomainEventRow.session_id == sess["session_id"],
                DomainEventRow.event_type == "session.assistant_message_created",
            )
            .order_by(DomainEventRow.id)
            .all()
        )
    assert len(rows) == 2
    assert rows[0].payload.get("retry_of") is None
    assert rows[1].payload.get("retry_of") == mid, "重试事件必须标出 retry_of（审计/前端去重）"


def test_retry_of_successful_turn_is_rejected(client, sess) -> None:
    """已产出提案的轮次不得重试 —— 那会产生第二个提案与第二个 PendingAction。"""
    # 注意 `proposal_json(kind, text, payload)` 的第二个参数是**文本**，不是 payload。
    gw.set_backend(StubBackend(proposal_json("intake", "已生成提案", {"statement": "s"})))
    assert _post(client, sess, message="成功的一轮").status_code == 201
    mid = _user_message_id(client, sess)

    st = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}/messages/{mid}",
        headers=auth_headers(sess["ctx"]),
    ).json()
    assert st["retryable"] is False
    assert st["has_proposal"] is True

    r = _retry(client, sess, mid)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "TURN_ALREADY_SUCCEEDED"


def test_retry_unknown_message_is_404(client, sess) -> None:
    r = _retry(client, sess, str(uuid.uuid4()))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "MESSAGE_NOT_FOUND"


def test_retry_requires_object_level_authz(client, sess, ctx_factory) -> None:
    other = ctx_factory()
    r = client.post(
        f"/api/research-v1/sessions/{sess['session_id']}/messages/{uuid.uuid4()}:retry",
        headers=auth_headers(other),
    )
    assert r.status_code == 403, r.text


def test_retry_unauthenticated_is_401(client, sess) -> None:
    r = client.post(
        f"/api/research-v1/sessions/{sess['session_id']}/messages/{uuid.uuid4()}:retry"
    )
    assert r.status_code == 401


def test_retry_can_itself_fail_and_stay_retryable(client, sess) -> None:
    """重试也失败 ⇒ 仍然可重试（单一供应商下这会是常态，不能一次失败就锁死）。"""
    gw.set_backend(gw.UnavailableBackend())
    _post(client, sess, message="反复失败")
    mid = _user_message_id(client, sess)

    assert _retry(client, sess, mid).status_code == 200
    st = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}/messages/{mid}",
        headers=auth_headers(sess["ctx"]),
    ).json()
    assert st["retryable"] is True

    gw.set_backend(StubBackend(proposal_json("answer", "第三次成功")))
    assert _retry(client, sess, mid).status_code == 200
    snap = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    assert len([m for m in snap["timeline"] if m["role"] == "user"]) == 1
    assert snap["timeline"][-1]["text"] == "第三次成功"


# ==========================================================================
# 4 · 容量闸（单账号单点的唯一保护层）
# ==========================================================================


def test_capacity_limits_are_configurable(monkeypatch) -> None:
    from research_vnext.core import config

    monkeypatch.setenv("RESEARCH_MAX_LLM_INFLIGHT", "7")
    assert config.max_llm_inflight() == 7
    monkeypatch.setenv("RESEARCH_MAX_LLM_TURNS_PER_USER", "3")
    assert config.max_llm_turns_per_user() == 3
    monkeypatch.setenv("RESEARCH_MAX_LLM_TURNS_PER_SESSION", "5")
    assert config.max_llm_turns_per_session() == 5
    monkeypatch.setenv("RESEARCH_MAX_LLM_CALLS_PER_WINDOW", "11")
    assert config.max_llm_calls_per_window() == 11
    monkeypatch.setenv("RESEARCH_LLM_RATE_WINDOW_SECONDS", "30")
    assert config.llm_rate_limit_window_seconds() == 30


def test_per_user_inflight_cap_returns_429_with_retry_after(client, sess, monkeypatch) -> None:
    """每用户在飞轮次上限 ⇒ 429 + `Retry-After`（不是排队、不是 500）。"""
    monkeypatch.setenv("RESEARCH_MAX_LLM_TURNS_PER_USER", "1")
    monkeypatch.setenv("RESEARCH_RETRY_AFTER_SECONDS", "6")
    cap.reset_capacity()

    # 手动占住该用户的唯一槽位（模拟一个仍在飞的调用）
    lease = cap.get_capacity().acquire(user_id=sess["actor_id"], session_id=None)
    try:
        r = _post(client, sess, message="并发的一轮")
        assert r.status_code == 429, r.text
        assert r.headers.get("Retry-After") == "6"
        body = r.json()["error"]
        assert body["code"] == "BACKPRESSURE"
        assert body["scope"] == "llm-user"
        assert body["retry_after"] == 6
    finally:
        cap.get_capacity().release(lease)


def test_window_rate_limit_returns_429(client, sess, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MAX_LLM_CALLS_PER_WINDOW", "1")
    monkeypatch.setenv("RESEARCH_LLM_RATE_WINDOW_SECONDS", "60")
    monkeypatch.setenv("RESEARCH_MAX_LLM_TURNS_PER_USER", "99")
    monkeypatch.setenv("RESEARCH_MAX_LLM_TURNS_PER_SESSION", "99")
    cap.reset_capacity()
    gw.set_backend(StubBackend(proposal_json("answer", "ok")))

    assert _post(client, sess, message="第一次").status_code == 201
    r = _post(client, sess, message="第二次")
    assert r.status_code == 429, r.text
    assert r.json()["error"]["scope"] == "llm-user-window"
    assert int(r.headers["Retry-After"]) >= 1


def test_capacity_is_released_after_failure(client, sess) -> None:
    """**关键**：失败路径必须归还槽位，否则一次失败就永久毒化容量。"""
    monkeypatch_ok = True
    gw.set_backend(StubBackend(error=gw.LlmGateError("LLM_CALL_FAILED", "boom")))
    for i in range(6):
        r = _post(client, sess, message=f"失败 {i}")
        assert r.status_code == 201, f"第 {i} 次被容量闸误挡: {r.text}"

    snap = cap.get_capacity().snapshot()
    assert snap["inflight_users"] == {}, f"槽位泄漏: {snap}"
    assert snap["inflight_total"] == 0


def test_capacity_is_released_after_success(client, sess) -> None:
    gw.set_backend(StubBackend(proposal_json("answer", "ok")))
    _post(client, sess, message="成功")
    snap = cap.get_capacity().snapshot()
    assert snap["inflight_users"] == {}
    assert snap["inflight_total"] == 0


def test_no_second_provider_fallback_is_attempted(client, sess) -> None:
    """**单供应商单点**：失败后**不得**偷偷尝试第二家。

    用"只被调用一次"的桩来证明：没有隐藏的第二次调用（那会是一条不存在的兜底路径）。
    """
    b = StubBackend(error=gw.LlmGateError("LLM_CALL_FAILED", "唯一供应商挂了"))
    gw.set_backend(b)
    _post(client, sess, message="只能试一次")
    assert b.calls == 1, f"出现了隐藏的兜底重试：调用 {b.calls} 次"


# ==========================================================================
# 5 · /llmz 可观测端点（不含密钥值）
# ==========================================================================


def test_llmz_reports_degraded_without_credentials(client) -> None:
    r = client.get("/llmz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "degraded"
    assert body["configured"] is False
    assert body["code"] == "LLM_NOT_CONFIGURED"
    assert "sk-" not in json.dumps(body)


def test_llmz_reports_mismatch_as_503_with_hint(client, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_LLM_API_KEY", "dummy-key-value")
    monkeypatch.setenv("RESEARCH_LLM_ACCOUNT", "A")
    monkeypatch.setenv("RESEARCH_LLM_MODEL", "xophunyuanocr")
    r = client.get("/llmz")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "model-account-mismatch"
    assert body["model"] == "xophunyuanocr"
    assert body["account"] == "A"
    assert "B" in body["hint"]
    # 只有指纹，绝无密钥值
    assert body["key_fingerprint"] == gw.key_fingerprint("dummy-key-value")
    assert "dummy-key-value" not in json.dumps(body)


def test_llmz_ok_path_reports_full_endpoint(client, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_LLM_API_KEY", "dummy-key-value")
    monkeypatch.setenv("RESEARCH_LLM_ACCOUNT", "A")
    monkeypatch.setenv("RESEARCH_LLM_MODEL", "spark-x2.5-4b")
    monkeypatch.setenv("RESEARCH_LLM_BASE_URL", gw.DEFAULT_BASE_URL)
    r = client.get("/llmz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["ok"] is True
    assert body["uses_full_endpoint"] is True
    assert body["base_url"].count("/chat/completions") == 1
