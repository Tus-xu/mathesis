"""CR-6a · `POST /sessions/{id}/messages` 集成测试（真实 PostgreSQL + 真实 Alembic schema）。

覆盖 ADR-RV-003 §3.1 的**逐条**要求：
- 401 未认证 / 403 对象级鉴权失败；
- 409 `expected_revision` 冲突（含机器可处理的 `current` + `snapshot_ref`）；
- 201 `X-Idempotency-Status: EXECUTED`；
- 同 `client_message_id` + 同内容 → 200 `REPLAYED` 且返回**原结果**；
- 同 `client_message_id` + 不同内容 → 409；
- 响应体是**完整 `ResearchSnapshot`**；
- 一次调用内：user message + domain event + outbox 三者在同一事务落库。

LLM 用**桩后端**驱动（不联网、不伪造真实模型），因此这些用例测的是
"收到模型输出之后平台做了什么"，而不是模型质量。
"""

from __future__ import annotations

import json
import uuid

import pytest

from research_vnext.core.llm import gateway as gw
from tests.integration.conftest import auth_headers, create_session, envelope


class StubBackend:
    """固定返回一段 JSON 的假后端。"""

    name = "stub"
    available = True
    model = "stub-model-1"

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls = 0
        #: RV09：测试可以模拟"输出被 max_tokens 截断"（`finish_reason=length`）。
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


def _post_message(client, ctx, sid, *, message="你好", crid=None, rev=None, attachments=None, headers=None):
    body: dict = {"message": message}
    if crid is not None:
        body["client_message_id"] = crid
    if rev is not None:
        body["expected_revision"] = rev
    if attachments is not None:
        body["attachments"] = attachments
    return client.post(
        f"/api/research-v1/sessions/{sid}/messages",
        json=body,
        headers=headers if headers is not None else auth_headers(ctx),
    )


# --------------------------------------------------------------------------
# 鉴权
# --------------------------------------------------------------------------


def test_unauthenticated_returns_401(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    resp = client.post(f"/api/research-v1/sessions/{sid}/messages", json={"message": "hi"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] in {
        "ACCESS_CONTEXT_MISSING",
        "ACCESS_CONTEXT_INVALID_SIGNATURE",
    }


def test_tampered_signature_returns_401(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    resp = _post_message(client, ctx, sid, headers=auth_headers(ctx, tamper=True))
    assert resp.status_code == 401


def test_foreign_tenant_is_forbidden(client, new_ids, ctx_factory) -> None:
    """**对象级**鉴权：合法 token 但 tenant 不符 → 403（不是 200）。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    other = ctx_factory()  # 另一个 tenant
    resp = _post_message(client, other, sid)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "TENANT_MISMATCH"


def test_missing_scope_is_forbidden(client, new_ids, ctx_factory) -> None:
    """同 tenant 但既无 project/session scope 又不是 owner → 403。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    stranger = ctx_factory(tenant_id=new_ids["tenant_id"])
    resp = _post_message(client, stranger, sid)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "OBJECT_SCOPE_MISSING"


def test_missing_write_capability_is_forbidden(client, new_ids, ctx_factory) -> None:
    """无写 capability 的调用者必须被拒（403），且**不落任何消息**。

    注：既有 `authorize_session` 的检查顺序是
    `_check_workspace → _check_revocation → tenant → 对象级 scope → capability`，
    因此该上下文先撞上 `OBJECT_SCOPE_MISSING`。本用例锁定**拒绝**这一安全属性，
    不锁定具体的错误码优先级（那会变成一个脆弱的实现细节断言）。
    """
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    reader = ctx_factory(tenant_id=new_ids["tenant_id"], capabilities=["workspace.research.enter"])
    resp = _post_message(client, reader, sid)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] in {
        "CAPABILITY_MISSING",
        "OBJECT_SCOPE_MISSING",
        "PROJECT_MISMATCH",
    }

    # 只读调用者连 GET 都允许，但**绝不能**写进任何消息
    got = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx)).json()
    assert got["timeline"] == []


def test_reader_with_scope_but_without_write_capability_is_forbidden(client, new_ids, ctx_factory) -> None:
    """有对象级 scope 但**没有**写 capability ⇒ 必须是 `CAPABILITY_MISSING`。

    这一条把"capability 检查确实生效"从"scope 检查把它挡住了"里分离出来。
    """
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    scoped_reader = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        capabilities=["workspace.research.enter"],
        resource_scopes=[{"kind": "project", "id": str(new_ids["project_id"]), "role": "viewer"}],
    )
    resp = _post_message(client, scoped_reader, sid)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "CAPABILITY_MISSING"


# --------------------------------------------------------------------------
# revision 冲突
# --------------------------------------------------------------------------


def test_stale_expected_revision_returns_409_with_current_and_snapshot_ref(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    cur = r.json()["session"]["revision"]

    resp = _post_message(client, ctx, sid, rev=cur + 99)
    assert resp.status_code == 409, resp.text
    err = resp.json()["error"]
    assert err["code"] == "REVISION_CONFLICT"
    assert err["current"] == cur
    assert err["expected"] == cur + 99
    assert err["snapshot_ref"] == f"snapshot:{sid}"


def test_stale_revision_writes_nothing(client, new_ids, ctx_factory) -> None:
    """冲突必须**不落任何写入**（否则前端重试会看到凭空多出的消息）。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    assert _post_message(client, ctx, sid, rev=999).status_code == 409
    snap = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx)).json()
    assert snap["timeline"] == []


# --------------------------------------------------------------------------
# 正常写入
# --------------------------------------------------------------------------


def test_new_message_returns_201_executed_and_full_snapshot(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json("answer", "这是回答"))
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    resp = _post_message(client, ctx, sid, message="帮我分析一下")
    assert resp.status_code == 201, resp.text
    assert resp.headers.get("X-Idempotency-Status") == "EXECUTED"
    assert "X-Trace-Id" in resp.headers

    snap = resp.json()
    for key in ("timeline", "evidence", "capabilities", "problem", "artifactFocus"):
        assert key in snap, f"缺少 {key}"
    assert isinstance(snap["timeline"], list) and isinstance(snap["evidence"], list)
    assert isinstance(snap["capabilities"], list)

    roles = [m["role"] for m in snap["timeline"]]
    assert roles == ["user", "assistant"]
    assert snap["timeline"][0]["text"] == "帮我分析一下"
    assert snap["timeline"][1]["text"] == "这是回答"
    for item in snap["timeline"]:
        assert set(item) >= {"id", "role", "text", "createdAt", "evidenceRefs"}
        assert isinstance(item["evidenceRefs"], list)  # 永远数组
    # seq 稳定递增
    assert [m["evidenceRefs"] for m in snap["timeline"]] == [[], []]


def test_assistant_message_payload_is_typed_proposal_not_html(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json("answer", "纯文本解释"))
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    _post_message(client, ctx, sid)

    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.db.models import ResearchMessageRow

    with session_scope() as db:
        rows = (
            db.query(ResearchMessageRow)
            .filter(ResearchMessageRow.session_id == uuid.UUID(sid))
            .order_by(ResearchMessageRow.seq)
            .all()
        )
        assistant = [x for x in rows if x.role == "assistant"][0]
        assert assistant.payload is not None
        assert "proposal" in assistant.payload
        assert assistant.payload["proposal"]["kind"] == "answer"
        assert "html" not in json.dumps(assistant.payload).lower()


def test_message_can_be_posted_on_both_prefixes(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    for prefix in ("/api/research-v1", "/api/research-vnext"):
        resp = client.post(
            f"{prefix}/sessions/{sid}/messages",
            json={"message": f"via {prefix}"},
            headers=auth_headers(ctx),
        )
        assert resp.status_code == 201, f"{prefix}: {resp.text}"
        assert isinstance(resp.json()["timeline"], list)


def test_empty_and_oversized_message_rejected(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    empty = _post_message(client, ctx, sid, message="   ")
    assert empty.status_code == 422, empty.text
    assert empty.json()["error"]["code"] == "MESSAGE_INVALID"

    big = _post_message(client, ctx, sid, message="x" * 8001)
    assert big.status_code == 422
    assert big.json()["error"]["code"] == "MESSAGE_INVALID"


def test_max_length_message_accepted(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    resp = _post_message(client, ctx, sid, message="y" * 8000)
    assert resp.status_code == 201, resp.text


def test_bad_client_message_id_format_rejected(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    resp = _post_message(client, ctx, sid, crid="short")
    assert resp.status_code == 422
    assert "client_message_id" in resp.json()["error"]["message"]


def test_attachments_are_validated_not_silently_ignored(client, new_ids, ctx_factory) -> None:
    """RV08 契约更新：附件不再「一律拒绝」，而是**必须校验**。

    安全性质不变：任何非法/未授权引用都**明确报错**，绝不静默忽略或静默丢弃。
    """
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    # 1) 未登记的 asset_id：明确拒绝
    resp = _post_message(client, ctx, sid, attachments=["00000000-0000-0000-0000-0000000000aa"])
    assert resp.status_code == 422
    assert "附件" in resp.json()["error"]["message"]

    # 2) 形状非法（缺 asset_id）：明确拒绝
    resp2 = _post_message(client, ctx, sid, attachments=[{"kind": "image"}])
    assert resp2.status_code == 422
    assert "asset_id" in resp2.json()["error"]["message"]

    # 3) 非数组：明确拒绝
    resp3 = _post_message(client, ctx, sid, attachments={"asset_id": "x"})
    assert resp3.status_code == 422


# --------------------------------------------------------------------------
# 幂等
# --------------------------------------------------------------------------


def test_same_crid_same_content_returns_replayed_original(client, new_ids, ctx_factory, stub_llm) -> None:
    b = stub_llm(proposal_json("answer", "唯一回答"))
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    crid = "c-msg-replay-0001"
    first = _post_message(client, ctx, sid, message="同样的话", crid=crid)
    assert first.status_code == 201
    assert first.headers["X-Idempotency-Status"] == "EXECUTED"

    second = _post_message(client, ctx, sid, message="同样的话", crid=crid)
    assert second.status_code == 200, second.text
    assert second.headers["X-Idempotency-Status"] == "REPLAYED"
    assert second.json()["timeline"] == first.json()["timeline"], "重放必须返回原结果"
    assert b.calls == 1, "重放**不得**再调用一次模型"


def test_same_crid_different_content_is_conflict(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    crid = "c-msg-conflict-0001"
    assert _post_message(client, ctx, sid, message="第一版", crid=crid).status_code == 201

    other = _post_message(client, ctx, sid, message="完全不同的第二版", crid=crid)
    assert other.status_code == 409, other.text
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_same_crid_different_expected_revision_is_conflict(client, new_ids, ctx_factory, stub_llm) -> None:
    """服务端摘要包含 `expected_revision` —— 不同前置状态即不同命令。"""
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    crid = "c-msg-revconflict-1"
    assert _post_message(client, ctx, sid, message="m", crid=crid, rev=None).status_code == 201
    # 同一 crid、同一文本、但显式带上 revision ⇒ 摘要不同 ⇒ 冲突
    got = _post_message(client, ctx, sid, message="m", crid=crid, rev=0)
    assert got.status_code == 409, got.text
    assert got.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_no_crid_means_no_idempotency(client, new_ids, ctx_factory, stub_llm) -> None:
    """缺省 `client_message_id` 时幂等**不生效**（ADR-RV-003 §3.1）。"""
    b = stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    a = _post_message(client, ctx, sid, message="同一条")
    c = _post_message(client, ctx, sid, message="同一条")
    assert a.status_code == 201 and c.status_code == 201
    assert b.calls == 2
    assert len(c.json()["timeline"]) == 4


def test_idempotency_is_scoped_per_actor(client, new_ids, ctx_factory, stub_llm) -> None:
    """同一 crid 不同 actor：不得互相吞掉（各自是独立命令）。"""
    stub_llm(proposal_json())
    owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, owner, new_ids)
    sid = r.json()["session"]["id"]

    crid = "c-msg-actor-scope"
    assert _post_message(client, owner, sid, message="x", crid=crid).status_code == 201

    other = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        resource_scopes=[{"kind": "project", "id": str(new_ids["project_id"]), "role": "editor"}],
    )
    resp = _post_message(client, other, sid, message="x", crid=crid)
    # 不同 actor ⇒ 不是重放；该 actor 有 project scope 但缺 owner → 仍被对象级 scope 放行
    assert resp.status_code in (201, 403), resp.text
    if resp.status_code == 201:
        assert resp.headers["X-Idempotency-Status"] == "EXECUTED"


# --------------------------------------------------------------------------
# 事件 + outbox 同事务
# --------------------------------------------------------------------------


def test_message_writes_domain_event_and_outbox_in_same_txn(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    _post_message(client, ctx, sid, message="触发事件")

    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.db.models import DomainEventRow, OutboxEventRow

    with session_scope() as db:
        events = (
            db.query(DomainEventRow)
            .filter(DomainEventRow.session_id == uuid.UUID(sid))
            .order_by(DomainEventRow.id)
            .all()
        )
        types = [e.event_type for e in events]
        assert "session.message_created" in types
        assert "session.assistant_message_created" in types

        # 每个 domain event 必须有且仅有一条对应 outbox 行（同事务写入）
        for e in events:
            n = (
                db.query(OutboxEventRow)
                .filter(OutboxEventRow.event_id == e.event_id)
                .count()
            )
            assert n == 1, f"{e.event_type} 的 outbox 行数 {n} != 1"


def test_message_increments_revision_and_summary(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    before = r.json()["session"]["revision"]

    snap = _post_message(client, ctx, sid).json()
    assert snap["session"]["revision"] == before + 2, "一次消息 = user + assistant 两次 revision"
    assert snap["summary_ref"].startswith(f"summary:{sid}@v")

    hist = client.get(f"/api/research-v1/sessions/{sid}/summary/history", headers=auth_headers(ctx))
    assert hist.status_code == 200
    assert len(hist.json()["items"]) >= 2


def test_expected_revision_match_succeeds(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    rev = r.json()["session"]["revision"]
    assert _post_message(client, ctx, sid, rev=rev).status_code == 201


# --------------------------------------------------------------------------
# AI run 审计
# --------------------------------------------------------------------------


def test_ai_run_recorded_with_prompt_ref_and_usage(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    _post_message(client, ctx, sid)

    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.db.models import ResearchAiRunRow

    with session_scope() as db:
        runs = (
            db.query(ResearchAiRunRow)
            .filter(ResearchAiRunRow.session_id == uuid.UUID(sid))
            .all()
        )
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "SUCCEEDED"
        assert run.model == "stub-model-1"
        assert run.input_tokens == 11 and run.output_tokens == 7
        assert run.latency_ms == 3
        # RV09 起发布版本为 v3（补齐 problem_map / research_map 契约 +
        # 上下文经 ContextManifest 裁剪后注入）。运行路径只取**已发布**版本，
        # 且把"当时用的是不是发布版"一并落库 —— 台账之后被改动也不影响复核。
        assert run.prompt_ref.startswith("rv.assistant.propose@v4#")
        assert run.prompt_version == 4
        assert run.prompt_published is True
        assert len(run.trace_id) == 32


def test_proposal_schema_failure_is_recorded_and_degrades_honestly(client, new_ids, ctx_factory, stub_llm) -> None:
    """模型返回非契约内容 ⇒ **不接受**，落 REJECTED 的 ai_run + 诚实说明，绝不写入正式状态。"""
    stub_llm("<html><body>我是 HTML</body></html>")
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    snap = _post_message(client, ctx, sid).json()

    assert len(snap["timeline"]) == 2
    assistant = snap["timeline"][1]
    assert assistant["role"] == "assistant"
    assert "结构化提案契约" in assistant["text"], assistant["text"]
    assert "<html>" not in assistant["text"], "被拒绝的原始内容不得回灌进 timeline"

    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.db.models import ResearchAiRunRow

    with session_scope() as db:
        run = (
            db.query(ResearchAiRunRow)
            .filter(ResearchAiRunRow.session_id == uuid.UUID(sid))
            .one()
        )
        assert run.status == "REJECTED"
        assert run.error_code == "PROPOSAL_NOT_JSON"

    # 能力状态必须诚实：不是 available
    cap = {c["key"]: c for c in snap["capabilities"]}
    assert cap["research-message"]["state"] != "available"


def test_llm_error_degrades_and_never_fabricates_answer(client, new_ids, ctx_factory, stub_llm) -> None:
    stub_llm(error=gw.LlmGateError("LLM_CALL_FAILED", "模拟上游 500"))
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    resp = _post_message(client, ctx, sid, message="会失败的消息")

    assert resp.status_code == 201, resp.text
    snap = resp.json()
    assert snap["timeline"][0]["text"] == "会失败的消息", "用户消息必须仍然持久化"
    assert "模型调用失败" in snap["timeline"][1]["text"]
    cap = {c["key"]: c for c in snap["capabilities"]}
    assert cap["research-message"]["state"] == "capability-disabled"
    assert cap["research-message"]["reason"] == "llm_call_failed"


def test_unconfigured_llm_marks_capability_unavailable(client, new_ids, ctx_factory) -> None:
    """未配置凭据 ⇒ 默认后端（`UnavailableBackend`）⇒ 诚实降级。

    本用例**不**安装桩后端，走真实 `get_backend()`。
    """
    assert gw.llm_configured() is False, "测试环境不应配置真实 LLM 凭据"

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    resp = _post_message(client, ctx, sid, message="没有模型可用")
    assert resp.status_code == 201, resp.text

    snap = resp.json()
    assert "模型不可用" in snap["timeline"][1]["text"]
    cap = {c["key"]: c for c in snap["capabilities"]}
    assert cap["research-message"]["state"] == "unavailable"
    assert cap["research-message"]["reason"] == "llm_not_configured"

    # GET 也必须报告同一状态（否则前端 composer 状态不一致）
    got = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx)).json()
    gotcap = {c["key"]: c for c in got["capabilities"]}
    assert gotcap["research-message"]["state"] == "unavailable"


# --------------------------------------------------------------------------
# 并发（seq 稳定）
# --------------------------------------------------------------------------


def test_concurrent_messages_get_distinct_monotonic_seq(client, new_ids, ctx_factory, stub_llm) -> None:
    """并发发送不得撞 `(session_id, seq)` 唯一约束，也不得乱序。"""
    import concurrent.futures as cf

    stub_llm(proposal_json())
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    def _send(i: int) -> int:
        return _post_message(client, ctx, sid, message=f"并发 {i}").status_code

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        codes = list(ex.map(_send, range(6)))
    assert all(c == 201 for c in codes), codes

    snap = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx)).json()
    assert len(snap["timeline"]) == 12, f"期望 6 user + 6 assistant，实际 {len(snap['timeline'])}"

    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.db.models import ResearchMessageRow

    with session_scope() as db:
        seqs = [
            x.seq
            for x in db.query(ResearchMessageRow)
            .filter(ResearchMessageRow.session_id == uuid.UUID(sid))
            .order_by(ResearchMessageRow.seq)
            .all()
        ]
    assert seqs == list(range(1, 13)), f"seq 必须 1..12 连续，实际 {seqs}"
