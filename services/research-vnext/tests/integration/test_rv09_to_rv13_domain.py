"""CR-6 / RV09–RV13 · typed proposal 的**确认门**与快照投影（真实 PostgreSQL）。

锁定 ADR-RV-003 不变量 2：**LLM 输出永远不能直接改变正式领域状态**。
流程必须依次经过：
    assistant message（proposal） → PendingAction → 用户 confirm → 新修订行
并且每一步都可从数据库验证。
"""

from __future__ import annotations

import json
import uuid

import pytest

from research_vnext.core.application import domain_service as DS
from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import (
    ClaimRow,
    DomainEventRow,
    EvidenceCardRow,
    PendingActionRow,
    ProblemMapRow,
    ResearchBlueprintRow,
    ResearchIntakeRow,
    ResearchMapRow,
    ReviewFindingRow,
)
from research_vnext.core.domain import rv_domain_rules as R
from research_vnext.core.llm import gateway as gw
from tests.integration.conftest import auth_headers, create_session
from tests.integration.test_messages_cr6 import StubBackend


def _proposal(kind: str, payload: dict, text: str = "已生成提案") -> str:
    return json.dumps({"kind": kind, "text": text, "payload": payload}, ensure_ascii=False)


@pytest.fixture()
def stub_llm():
    def _install(text: str) -> StubBackend:
        b = StubBackend(text)
        gw.set_backend(b)
        return b

    yield _install
    gw.set_backend(None)


@pytest.fixture()
def sess(client, new_ids, ctx_factory):
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    assert r.status_code == 201, r.text
    return {
        "ctx": ctx,
        "session_id": uuid.UUID(r.json()["session"]["id"]),
        "tenant_id": new_ids["tenant_id"],
        "project_id": new_ids["project_id"],
        "actor_id": uuid.UUID(ctx["actor_id"]),
    }


def _send(client, sess, text: str = "请分析") -> dict:
    r = client.post(
        f"/api/research-v1/sessions/{sess['session_id']}/messages",
        json={"message": text},
        headers=auth_headers(sess["ctx"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pending(client, sess) -> list[dict]:
    r = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}/pending-actions",
        headers=auth_headers(sess["ctx"]),
    )
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _accept(client, sess, pid: str, rev: int | None = None) -> tuple[int, dict]:
    body = {} if rev is None else {"expected_revision": rev}
    r = client.post(
        f"/api/research-v1/sessions/{sess['session_id']}/pending-actions/{pid}:accept",
        json=body,
        headers=auth_headers(sess["ctx"]),
    )
    return r.status_code, (r.json() if r.content else {})


# ==========================================================================
# 1 · proposal 不落状态，只创建 PendingAction
# ==========================================================================


def test_intake_proposal_creates_pending_action_without_writing_object(client, sess, stub_llm) -> None:
    stub_llm(
        _proposal(
            "intake",
            {
                "statement": "优化配送路径",
                "subproblems": ["如何建模", "如何求解"],
                "constraints": [{"text": "车辆容量有限", "origin": "USER"}],
                "unknowns": [{"text": "需求分布未知", "origin": "INFERENCE"}],
            },
        )
    )
    snap = _send(client, sess)

    # 尚未确认：不得有任何正式对象
    with session_scope() as db:
        assert db.query(ResearchIntakeRow).filter(
            ResearchIntakeRow.session_id == sess["session_id"]
        ).count() == 0, "LLM 输出**不得**直接写入正式状态"

    items = _pending(client, sess)
    assert len(items) == 1, items
    assert items[0]["object_ref"] == "intake"
    assert items[0]["proposal_kind"] == "intake"
    assert items[0]["status"] == "PENDING"

    # assistant message 里必须是 **typed proposal**，不是 HTML
    assert snap["timeline"][-1]["role"] == "assistant"
    assert snap["artifactFocus"] is None, "未确认前不应出现产物聚焦"


def test_confirming_intake_writes_revision_and_emits_event(client, sess, stub_llm) -> None:
    stub_llm(
        _proposal(
            "intake",
            {
                "statement": "配送路径优化",
                "subproblems": ["建模"],
                "constraints": [{"text": "容量", "origin": "USER"}],
            },
        )
    )
    _send(client, sess)
    pid = _pending(client, sess)[0]["id"]

    code, body = _accept(client, sess, pid)
    assert code == 200, body
    assert body["confirmed_object"]["object_ref"] == "intake"
    assert body["confirmed_object"]["revision"] == 1

    with session_scope() as db:
        rows = db.query(ResearchIntakeRow).filter(
            ResearchIntakeRow.session_id == sess["session_id"]
        ).all()
        assert len(rows) == 1
        assert rows[0].confirmed_at is not None
        assert rows[0].confirmed_by == sess["actor_id"]
        assert rows[0].content_hash

        types = [
            e.event_type
            for e in db.query(DomainEventRow)
            .filter(DomainEventRow.session_id == sess["session_id"])
            .order_by(DomainEventRow.id)
            .all()
        ]
        assert "session.proposal_confirmed" in types


def test_pending_action_can_only_be_accepted_once(client, sess, stub_llm) -> None:
    stub_llm(_proposal("intake", {"statement": "s", "subproblems": []}))
    _send(client, sess)
    pid = _pending(client, sess)[0]["id"]

    assert _accept(client, sess, pid)[0] == 200
    code, body = _accept(client, sess, pid)
    assert code == 409, body

    with session_scope() as db:
        n = db.query(ResearchIntakeRow).filter(
            ResearchIntakeRow.session_id == sess["session_id"]
        ).count()
    assert n == 1, "重复确认不得写出第二个修订行"


def test_rejected_proposal_never_writes_object(client, sess, stub_llm) -> None:
    stub_llm(_proposal("map", {"nodes": [{"id": "n1", "type": "question", "label": "怎么建模"}]}))
    _send(client, sess)
    pid = _pending(client, sess)[0]["id"]

    r = client.post(
        f"/api/research-v1/sessions/{sess['session_id']}/pending-actions/{pid}:reject",
        json={"reason": "不需要"},
        headers=auth_headers(sess["ctx"]),
    )
    assert r.status_code == 200, r.text
    with session_scope() as db:
        assert db.query(ResearchMapRow).filter(
            ResearchMapRow.session_id == sess["session_id"]
        ).count() == 0


def test_proposal_rejected_when_payload_violates_domain_rules(client, sess, stub_llm) -> None:
    """图有悬空边 ⇒ 语义校验拒绝 ⇒ **不创建 PendingAction**，诚实降级。"""
    stub_llm(
        _proposal(
            "map",
            {
                "nodes": [{"id": "n1", "type": "question", "label": "q"}],
                "edges": [{"from": "n1", "to": "missing", "type": "decomposes"}],
            },
        )
    )
    snap = _send(client, sess)
    assert _pending(client, sess) == [], "语义非法不得进入确认门"
    text = snap["timeline"][-1]["text"]
    assert "未通过领域校验" in text and "MAP_DANGLING_EDGE" in text, text
    assert "不会写入任何正式状态" in text


def test_unknown_node_type_is_rejected(client, sess, stub_llm) -> None:
    stub_llm(_proposal("map", {"nodes": [{"id": "n1", "type": "made_up_type", "label": "x"}]}))
    _send(client, sess)
    assert _pending(client, sess) == []


# ==========================================================================
# 2 · origin 语义（RV09）：推断不得冒充事实
# ==========================================================================


def test_inference_is_never_presented_as_user_fact(client, sess, stub_llm) -> None:
    """**新口径**（D-USER-1）：`origin=USER` 必须经服务端原文锚定才成立。

    决策记录：`RV07/DECISIONS-2026-09-14.md` §1。两级口径：

    1. **服务端构造**：`problemStatement` 由 `_validate_intake` 服务端强制标 `USER`
       （见 `_SERVER_AUTHORED_KEYS`），免校验；
    2. **模型声明 + 原文锚定**：模型对其他元素声明 `USER` 时**必须**附 `quote`
       —— 用户消息中的**逐字**片段。服务端校验 `quote` 非空且是原文子串，
       否则降级 `INFERENCE`。

    → 因此不带 `quote` 的 `"用户明确说的"` 现在**必须**降级为 `INFERENCE`；
    带 `quote="请分析"`（用户消息即 `"请分析"`）的元素**保持** `USER`。
    本用例此前钉住的是旧语义（"已知且接受"），现按裁定改为断言新语义。
    """
    stub_llm(
        _proposal(
            "intake",
            {
                "statement": "用户原话",
                "knowns": [
                    # 声明 USER 但**不给** quote ⇒ 无从核对 ⇒ 降级
                    {"text": "用户明确说的", "origin": "USER"},
                    # 声明 USER 且 quote 逐字命中用户消息 ⇒ 保留 USER
                    {"text": "用户说的（改写）", "origin": "USER", "quote": "请分析"},
                    {"text": "模型猜的", "origin": "INFERENCE"},
                    # 缺失 origin ⇒ 必须降级为 INFERENCE（保守方向）
                    {"text": "没标来源的"},
                ],
            },
        )
    )
    _send(client, sess)
    pid = _pending(client, sess)[0]["id"]
    _accept(client, sess, pid)

    with session_scope() as db:
        row = db.query(ResearchIntakeRow).filter(
            ResearchIntakeRow.session_id == sess["session_id"]
        ).one()
    knowns = row.content["knowns"]
    by_text = {k["text"]: k["origin"] for k in knowns}
    assert by_text["用户明确说的"] == "INFERENCE", "无 quote 的 USER 声明必须降级"
    assert by_text["用户说的（改写）"] == "USER", "quote 逐字命中原文 ⇒ 保留 USER"
    assert by_text["模型猜的"] == "INFERENCE"
    assert by_text["没标来源的"] == "INFERENCE", "缺失 origin 必须保守降级为 INFERENCE"
    assert row.content["origins"]["INFERENCE"] >= 3


def test_server_authored_problem_statement_stays_user(client, sess, stub_llm) -> None:
    """服务端构造的 `problemStatement` 即使没有 `quote` 也**保持** `USER`。

    这是 D-USER-1 最容易回归的点：调用方传入 `user_text` 后，若
    `server_authored_keys` 未生效，`problemStatement`（模型改写、无 quote）
    会被误降级为 `INFERENCE`。`_SERVER_AUTHORED_KEYS` 让它整段免裁决。
    """
    stub_llm(_proposal("intake", {"statement": "优化配送路径（模型改写）"}))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    with session_scope() as db:
        row = db.query(ResearchIntakeRow).filter(
            ResearchIntakeRow.session_id == sess["session_id"]
        ).one()
    assert row.content["problemStatement"][0]["origin"] == "USER"
    assert row.content["problemStatement"][0]["text"] == "优化配送路径（模型改写）"


def test_illegal_origin_is_downgraded_not_promoted(client, sess, stub_llm) -> None:
    """把 `origin` 写成 `SOURCE_FACT` 之类的伪造值，必须降级为 INFERENCE。"""
    stub_llm(
        _proposal(
            "intake",
            {"statement": "s", "constraints": [{"text": "看起来像事实", "origin": "OFFICIAL_FACT"}]},
        )
    )
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])
    with session_scope() as db:
        row = db.query(ResearchIntakeRow).filter(
            ResearchIntakeRow.session_id == sess["session_id"]
        ).one()
    assert row.content["constraints"][0]["origin"] == "INFERENCE"


# ==========================================================================
# 3 · 修订链（不可变追加）
# ==========================================================================


def test_two_confirmed_blueprints_produce_two_immutable_revisions(client, sess, stub_llm) -> None:
    with session_scope() as db:
        ev = DS.add_evidence(db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
                             source="s", label="l")
        ev_id = str(ev.id)

    stub_llm(
        _proposal("blueprint", {"problem": "p", "objective": "o1", "route": "route-A",
                                "evidenceNodes": [ev_id]})
    )
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    stub_llm(
        _proposal("blueprint", {"problem": "p", "objective": "o2", "route": "route-B",
                                "evidenceNodes": [ev_id]})
    )
    snap2 = _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    with session_scope() as db:
        rows = (
            db.query(ResearchBlueprintRow)
            .filter(ResearchBlueprintRow.session_id == sess["session_id"])
            .order_by(ResearchBlueprintRow.revision)
            .all()
        )
        assert [r.revision for r in rows] == [1, 2]
        assert rows[0].content["objective"] == "o1", "旧修订**不得**被就地修改"
        assert rows[1].content["objective"] == "o2"
        assert rows[0].content_hash != rows[1].content_hash


def test_blueprint_requires_route(client, sess, stub_llm) -> None:
    stub_llm(_proposal("blueprint", {"problem": "p", "objective": "o"}))
    _send(client, sess)
    assert _pending(client, sess) == [], "缺少 route 的 blueprint 不得进入确认门"


def test_unverified_assumption_must_be_inference(client, sess, stub_llm) -> None:
    stub_llm(
        _proposal(
            "blueprint",
            {
                "route": "r",
                "assumptions": [
                    {"text": "容量恒定", "unverified": True, "origin": "USER"},
                ],
            },
        )
    )
    _send(client, sess)
    assert _pending(client, sess) == []


# ==========================================================================
# 4 · claim → 证据 / 验证 / 重开（RV12）
# ==========================================================================


def test_claim_solved_requires_real_evidence(client, sess, stub_llm) -> None:
    """引用不存在的证据 ⇒ 拒绝（否则 SOLVED 可以凭空成立）。"""
    stub_llm(
        _proposal("claim", {"text": "该算法最优", "status": "SOLVED", "evidenceRefs": ["ev-not-real"]})
    )
    _send(client, sess)
    assert _pending(client, sess) == []


def test_solved_claim_without_evidence_is_rejected(client, sess, stub_llm) -> None:
    stub_llm(_proposal("claim", {"text": "该算法最优", "status": "SOLVED"}))
    _send(client, sess)
    assert _pending(client, sess) == []


def test_claim_proposal_cannot_self_declare_solved(client, sess, stub_llm) -> None:
    """RV12 测试演进：确认路径**不再**让模型自报的 status 成为权威状态。

    旧断言（`c.status == "SOLVED"`）建立在"模型说 SOLVED 就是 SOLVED"之上，
    与 RV12 的 `SOLVED 必须满足预先定义的 evidence+verification 判据` 直接冲突。
    新语义：有真实证据但**没有任何独立验证** ⇒ `CONDITIONAL`，并列出成立条件；
    模型的自报状态只作为 `audit.aiProposedStatus` 保留。
    """
    with session_scope() as db:
        ev = DS.add_evidence(
            db,
            session_id=sess["session_id"],
            tenant_id=sess["tenant_id"],
            source="paper",
            label="定理 3.1",
            asset_version="v1",
        )
        ev_id = str(ev.id)

    stub_llm(
        _proposal(
            "claim",
            {"text": "该算法最优", "status": "SOLVED", "supportQuality": "SUPPORTED",
             "evidenceRefs": [ev_id]},
        )
    )
    _send(client, sess)
    items = _pending(client, sess)
    assert len(items) == 1 and items[0]["object_ref"] == "claim"
    _accept(client, sess, items[0]["id"])

    with session_scope() as db:
        c = db.query(ClaimRow).filter(ClaimRow.session_id == sess["session_id"]).one()
    assert c.status != "SOLVED", "没有独立验证时不得 SOLVED"
    assert c.status == "CONDITIONAL"
    assert c.requires_review is True
    assert "VERIFICATION_PASS_PRESENT" in list(c.content.get("missing") or [])
    assert c.content["audit"]["aiProposedStatus"] == "SOLVED"  # 模型自称被如实留痕
    assert c.evidence_refs == [ev_id]


def test_invalidated_evidence_reopens_solved_claim(client, sess, stub_llm) -> None:
    """**核心 RV12 行为**：失效证据必须能够推翻 SOLVED —— 否则终态不可推翻。

    RV12 测试演进：SOLVED 现在需要一条独立验证 PASS，故先落一条验证记录，
    再使证据失效。这样这条用例仍然（且更严格地）钉住"终态可被推翻"。
    """
    with session_scope() as db:
        ev = DS.add_evidence(
            db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            source="paper", label="定理", asset_version="v1",
        )
        ev_id = ev.id

    stub_llm(
        _proposal("claim", {"text": "c", "status": "SOLVED", "supportQuality": "SUPPORTED",
                            "evidenceRefs": [str(ev_id)]})
    )
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    with session_scope() as db:
        c = db.query(ClaimRow).filter(ClaimRow.session_id == sess["session_id"]).one()
        assert c.status == "CONDITIONAL"
        DS.record_verification(
            db,
            session_id=sess["session_id"],
            tenant_id=sess["tenant_id"],
            claim_id=c.id,
            method="independent-recompute",
            result="PASS",
            independent=True,
        )
        assert db.get(ClaimRow, c.id).status == "SOLVED", "证据 + 独立验证 PASS 应达 SOLVED"

    with session_scope() as db:
        out = DS.invalidate_evidence(db, evidence_id=ev_id, reason="原文被撤回")

    assert out["reopened_claims"], "失效证据必须重开 SOLVED claim"
    reopened = out["reopened_claims"][0]
    assert reopened["reason"] == R.REOPEN_EVIDENCE_INVALIDATED
    assert reopened["to_status"] == "INCONCLUSIVE"

    with session_scope() as db:
        rows = (
            db.query(ClaimRow)
            .filter(ClaimRow.session_id == sess["session_id"])
            .order_by(ClaimRow.revision)
            .all()
        )
    assert len(rows) == 2, "重开必须写**新**修订行，旧行保留"
    assert rows[0].status == "SOLVED", "旧的 SOLVED 行必须原样保留（审计）"
    assert rows[1].status == "INCONCLUSIVE"
    assert rows[1].reopened_reason == R.REOPEN_EVIDENCE_INVALIDATED


def test_unknown_verification_does_not_make_claim_supported(client, sess) -> None:
    with session_scope() as db:
        ev = DS.add_evidence(db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
                             source="s", label="l")
        c = ClaimRow(
            id=uuid.uuid4(), session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            revision=1, content={}, content_hash="h", client_request_id="x",
            trace_id="a" * 32, created_at=DS._now(),
            text="t", status="INCONCLUSIVE", support_quality="UNSUPPORTED",
            evidence_refs=[str(ev.id)],
        )
        db.add(c)
        db.flush()
        cid = c.id
        DS.record_verification(
            db, session_id=sess["session_id"], tenant_id=sess["tenant_id"], claim_id=cid,
            method="independent-recompute", result="UNKNOWN",
        )
        row = db.get(ClaimRow, cid)
    assert row.support_quality == "PARTIAL", "只有 UNKNOWN 不得算 SUPPORTED"
    assert row.status == "INCONCLUSIVE"


def test_pass_verification_marks_supported(client, sess) -> None:
    with session_scope() as db:
        ev = DS.add_evidence(db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
                             source="s", label="l")
        c = ClaimRow(
            id=uuid.uuid4(), session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            revision=1, content={}, content_hash="h", client_request_id="x",
            trace_id="b" * 32, created_at=DS._now(),
            text="t", status="INCONCLUSIVE", support_quality="UNSUPPORTED",
            evidence_refs=[str(ev.id)],
        )
        db.add(c)
        db.flush()
        cid = c.id
        DS.record_verification(
            db, session_id=sess["session_id"], tenant_id=sess["tenant_id"], claim_id=cid,
            method="recompute", result="PASS", independent=True,
        )
        row = db.get(ClaimRow, cid)
    assert row.support_quality == "SUPPORTED"
    assert row.verification_refs


def test_fail_verification_marks_conflicted(client, sess) -> None:
    with session_scope() as db:
        ev = DS.add_evidence(db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
                             source="s", label="l")
        c = ClaimRow(
            id=uuid.uuid4(), session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            revision=1, content={}, content_hash="h", client_request_id="x",
            trace_id="c" * 32, created_at=DS._now(),
            text="t", status="INCONCLUSIVE", support_quality="UNSUPPORTED",
            evidence_refs=[str(ev.id)],
        )
        db.add(c)
        db.flush()
        cid = c.id
        DS.record_verification(
            db, session_id=sess["session_id"], tenant_id=sess["tenant_id"], claim_id=cid,
            method="recompute", result="FAIL",
        )
        row = db.get(ClaimRow, cid)
    assert row.support_quality == "CONFLICTED"


# ==========================================================================
# 5 · 实验运行（RV11）
# ==========================================================================


def test_experiment_run_never_asserts_claim_holds(client, sess) -> None:
    with session_scope() as db:
        out = DS.record_experiment_run(
            db,
            session_id=sess["session_id"],
            tenant_id=sess["tenant_id"],
            plan_id=None,
            code_ref="git:abc",
            dataset_ref="ds:1",
            exit_code=0,
            repro={
                "imageDigest": "sha256:deadbeef",
                "dependencyLockHash": "lock-1",
                "seed": 7,
                "codeHash": "code-1",
            },
            metrics={"acc": 0.99},
        )
    assert out["exit_code"] == 0
    assert out["claim_holds"] is None, "exit_code==0 绝不能被当成结论成立"
    assert out["reproducible"] is True


def test_non_reproducible_run_is_flagged(client, sess) -> None:
    with session_scope() as db:
        out = DS.record_experiment_run(
            db, session_id=sess["session_id"], tenant_id=sess["tenant_id"], plan_id=None,
            code_ref="c", dataset_ref="d", exit_code=0, repro={"imageDigest": "d"},
        )
    assert out["reproducible"] is False
    assert "dependencyLockHash" in out["not_reproducible_reason"]
    with session_scope() as db:
        runs = DS.non_reproducible_runs(db, sess["session_id"])
        assert len(runs) == 1


def test_experiment_plan_requires_dataset_ref(client, sess, stub_llm) -> None:
    stub_llm(_proposal("experiment_plan", {"objective": "o", "code_ref": "c"}))
    _send(client, sess)
    assert _pending(client, sess) == []


def test_experiment_executor_is_honest_when_unconfigured() -> None:
    """未配置沙箱执行器 ⇒ **诚实失败**，绝不假装跑过。"""
    import asyncio

    from research_vnext.core.execution.executor import (
        ExecutionRequest,
        ExecutorNotConfigured,
        NullExecutor,
        executor_configured,
        get_executor,
    )

    assert executor_configured() is False
    ex = get_executor()
    assert isinstance(ex, NullExecutor)
    with pytest.raises(ExecutorNotConfigured) as ei:
        asyncio.run(
            ex.run(
                ExecutionRequest(
                    job_id="j", session_id="s", plan_id=None, code_ref="c", dataset_ref="d"
                )
            )
        )
    assert "拒绝伪造一次实验运行" in str(ei.value)


def test_experiment_job_fails_permanently_without_executor(sess) -> None:
    """走 worker：RV11 起执行材料按 run 回读。

    (a) payload 缺 `run_id` ⇒ `INVALID_INPUT`（**永久**，不重试）；
    (b) 材料完整但未配置执行器 ⇒ `EXECUTOR_NOT_CONFIGURED`（**永久**，不重试）。
    """
    import asyncio
    import hashlib

    from research_vnext.core.application import job_service as js
    from research_vnext.core.db import models as M
    from research_vnext.core.worker import runtime as W

    # (a) 旧式 payload（缺 run_id）
    with session_scope() as db:
        job = js.enqueue(
            db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            job_type="experiment.run", input_payload={"code_ref": "c", "dataset_ref": "d"},
        )
        jid = job.id
    out = asyncio.run(W.run_job(jid))
    assert out["status"] == "FAILED", out
    with session_scope() as db:
        job = js.get_job(db, jid)
    assert job.error_code == "INVALID_INPUT"
    assert job.retryable is False, "schema 缺陷重试毫无意义"

    # (b) 材料完整（run+plan 已确认）但无执行器
    code = "print(1)\n"
    with session_scope() as db:
        plan = M.ExperimentPlanRow(
            id=uuid.uuid4(), session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            revision=1, content={"code": code, "dataset_ref": "d"}, content_hash="0" * 64,
            confirmed_at=None, confirmed_by=sess["actor_id"], trace_id="0" * 32, created_at=None,
        )
        db.add(plan)
        db.flush()
        run = M.ExperimentRunRow(
            id=uuid.uuid4(), session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            plan_id=plan.id, status="QUEUED", executor="sandbox",
            code_ref="c", dataset_ref="d", environment={}, params={}, result={}, metrics={},
            logs_ref="", figures=[], exit_code=None, claim_holds=None,
            not_reproducible_reason="",
            repro={
                "codeSha256": hashlib.sha256(code.encode()).hexdigest(),
                "seed": 0,
                "effectiveLimits": {"cpuLimit": 1.0, "memoryLimitMb": 256, "pidsLimit": 64,
                                    "diskLimitMb": 64, "wallTimeMs": 5000,
                                    "outputLimitBytes": 65536, "network": "deny"},
                "environment": {}, "reproduceOf": None,
            },
            client_request_id="", trace_id="0" * 32, created_at=None,
        )
        db.add(run)
        db.flush()
        job2 = js.enqueue(
            db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
            job_type="experiment.run", input_payload={"run_id": str(run.id)},
        )
        jid2 = job2.id
    out2 = asyncio.run(W.run_job(jid2))
    assert out2["status"] == "FAILED", out2
    with session_scope() as db:
        job2 = js.get_job(db, jid2)
    assert job2.error_code == "EXECUTOR_NOT_CONFIGURED"
    assert job2.retryable is False, "没有执行器时重试毫无意义"


# ==========================================================================
# 6 · PaperSpec / ReviewFinding（RV13）
# ==========================================================================


def test_paper_patch_is_a_block_tree_not_latex(client, sess, stub_llm) -> None:
    stub_llm(
        _proposal(
            "paper_patch",
            {
                "baseRevision": 0,
                "rationale": "补充方法节",
                "blocks": [
                    {"id": "s1", "type": "section", "text": "方法"},
                    {"id": "p1", "type": "paragraph", "text": "我们采用……"},
                    {"id": "eq1", "type": "equation", "text": "y = Wx + b"},
                ],
            },
        )
    )
    _send(client, sess)
    items = _pending(client, sess)
    assert items and items[0]["object_ref"] == "paper_spec"

    # 未确认前：没有 PaperSpec
    from research_vnext.core.db.models import PaperSpecRow

    with session_scope() as db:
        assert db.query(PaperSpecRow).filter(
            PaperSpecRow.session_id == sess["session_id"]
        ).count() == 0

    _accept(client, sess, items[0]["id"])
    with session_scope() as db:
        spec = db.query(PaperSpecRow).filter(
            PaperSpecRow.session_id == sess["session_id"]
        ).one()
    blocks = spec.content["blocks"]
    assert [b["type"] for b in blocks] == ["section", "paragraph", "equation"]
    assert "\\documentclass" not in json.dumps(spec.content), "不得把 LaTeX 当规范模型"


def test_paper_block_type_must_be_in_taxonomy(client, sess, stub_llm) -> None:
    stub_llm(_proposal("paper_patch", {"blocks": [{"id": "x", "type": "raw_html", "text": "<b>"}]}))
    _send(client, sess)
    assert _pending(client, sess) == []


def test_review_finding_taxonomy_is_enforced(client, sess, stub_llm) -> None:
    stub_llm(
        _proposal(
            "review",
            {"findings": [{"category": "NOT_A_CATEGORY", "suggestion": "x"}]},
        )
    )
    _send(client, sess)
    assert _pending(client, sess) == []


def test_valid_review_findings_pass_validation(client, sess, stub_llm) -> None:
    stub_llm(
        _proposal(
            "review",
            {
                "findings": [
                    {"category": "REPRO_GAP", "severity": "major", "location": "§3.2",
                     "suggestion": "补 seed 与镜像摘要"},
                    {"category": "OVERCLAIM", "severity": "minor", "location": "摘要",
                     "suggestion": "弱化措辞"},
                ]
            },
        )
    )
    _send(client, sess, "帮我审一遍")
    items = _pending(client, sess)
    assert items, "合法 review 应进入确认门"
    _accept(client, sess, items[0]["id"])

    with session_scope() as db:
        rows = db.query(ReviewFindingRow).filter(
            ReviewFindingRow.session_id == sess["session_id"]
        ).all()
    assert {r.category for r in rows} == {"REPRO_GAP", "OVERCLAIM"}
    assert all(r.status == "OPEN" for r in rows)


# ==========================================================================
# 7 · 快照投影
# ==========================================================================


def test_artifact_focus_appears_after_confirmation(client, sess, stub_llm) -> None:
    stub_llm(_proposal("blueprint", {"problem": "P", "objective": "O", "route": "route-A"}))
    _send(client, sess)
    before = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    assert before["artifactFocus"] is None
    caps_before = {c["key"]: c["state"] for c in before["capabilities"]}
    assert caps_before["research-artifact"] == "unavailable"

    _accept(client, sess, _pending(client, sess)[0]["id"])

    after = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    assert after["artifactFocus"] is not None
    assert after["artifactFocus"]["blueprint"]["objective"] == "O"
    assert after["artifactFocus"]["blueprint"]["confirmationState"] == "confirmed"
    caps_after = {c["key"]: c["state"] for c in after["capabilities"]}
    assert caps_after["research-artifact"] == "available"


def test_evidence_appears_in_snapshot_and_capability(client, sess) -> None:
    before = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    assert before["evidence"] == []
    assert {c["key"]: c["state"] for c in before["capabilities"]}["research-evidence"] == "unavailable"

    with session_scope() as db:
        DS.add_evidence(db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
                        source="paper", label="Theorem 1", locator="p.3", snippet="...",
                        asset_version="v1")

    after = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    assert len(after["evidence"]) == 1
    e = after["evidence"][0]
    assert set(e) >= {"id", "source", "label", "locator", "snippet", "assetVersion", "authorized"}
    assert {c["key"]: c["state"] for c in after["capabilities"]}["research-evidence"] == "available"


def test_unauthorized_evidence_does_not_enable_claim(client, sess, stub_llm) -> None:
    """未授权证据既**不**进入可用集合，也**不**能支撑 claim。"""
    with session_scope() as db:
        ev = DS.add_evidence(db, session_id=sess["session_id"], tenant_id=sess["tenant_id"],
                             source="s", label="l", authorized=False)
        ev_id = str(ev.id)

    stub_llm(_proposal("claim", {"text": "c", "status": "SOLVED", "evidenceRefs": [ev_id]}))
    _send(client, sess)
    assert _pending(client, sess) == [], "未授权证据不得支撑 SOLVED"


def test_snapshot_contract_holds_after_confirmation(client, sess, stub_llm) -> None:
    stub_llm(_proposal("intake", {"statement": "s", "subproblems": []}))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])
    snap = client.get(
        f"/api/research-v1/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
    ).json()
    for k in ("timeline", "evidence", "capabilities"):
        assert isinstance(snap[k], list), k
    keys = [c["key"] for c in snap["capabilities"]]
    assert keys == [
        "research-session", "research-message", "research-stream",
        "research-evidence", "research-artifact",
    ]


def test_object_summary_reports_revisions(client, sess, stub_llm) -> None:
    stub_llm(_proposal("intake", {"statement": "s", "subproblems": []}))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])
    with session_scope() as db:
        summary = DS.object_summary(db, sess["session_id"])
    assert summary["intake"]["exists"] is True
    assert summary["intake"]["revisions"] == 1
    assert summary["claim"]["exists"] is False


def test_pending_action_audit_trail(client, sess, stub_llm) -> None:
    stub_llm(_proposal("intake", {"statement": "s", "subproblems": []}))
    _send(client, sess)
    pid = uuid.UUID(_pending(client, sess)[0]["id"])
    _accept(client, sess, str(pid))
    with session_scope() as db:
        trail = DS.audit_trail(db, pid)
    actions = [t["action"] for t in trail]
    assert "CREATED" in actions and "ACCEPTED" in actions


def test_message_never_creates_object_without_confirmation(client, new_ids, ctx_factory, stub_llm) -> None:
    """**总不变量**：无论模型输出什么 kind，未确认前正式对象表必须为空。

    每个 kind 用**独立会话**，避免"上一个 kind 已确认"污染断言。
    """
    from research_vnext.core.db.models import (
        ExperimentPlanRow,
        ModelSpecRow,
        PaperSpecRow,
        ProblemMapRow,
        ResearchMapRow,
    )

    tables = (
        ResearchIntakeRow, ProblemMapRow, ResearchMapRow, ModelSpecRow,
        ResearchBlueprintRow, ExperimentPlanRow, PaperSpecRow,
    )
    cases = (
        ("intake", {"statement": "s"}),
        ("map", {"nodes": [{"id": "n1", "type": "question", "label": "q"}]}),
        ("blueprint", {"route": "r", "problem": "p", "objective": "o"}),
        ("experiment_plan", {"dataset_ref": "d", "code_ref": "c"}),
        ("paper_patch", {"blocks": [{"id": "b", "type": "paragraph", "text": "t"}]}),
        ("review", {"findings": [{"category": "OVERCLAIM", "suggestion": "s"}]}),
    )

    for kind, payload in cases:
        ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
        r, _ = create_session(client, ctx, new_ids)
        assert r.status_code == 201, r.text
        sid = uuid.UUID(r.json()["session"]["id"])

        stub_llm(_proposal(kind, payload))
        resp = client.post(
            f"/api/research-v1/sessions/{sid}/messages",
            json={"message": f"提案 {kind}"},
            headers=auth_headers(ctx),
        )
        assert resp.status_code == 201, resp.text

        with session_scope() as db:
            for table in tables:
                assert (
                    db.query(table).filter(table.session_id == sid).count() == 0
                ), f"{table.__tablename__} 在未确认前被写入（kind={kind}）"
            assert db.query(ReviewFindingRow).filter(
                ReviewFindingRow.session_id == sid
            ).count() == 0, f"ReviewFinding 在未确认前被写入（kind={kind}）"

        # 该 kind 确实进入了确认门（证明"没有对象"不是因为提案被丢弃）
        pend = client.get(
            f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx)
        ).json()["items"]
        assert len(pend) == 1, f"kind={kind} 应恰好产生一个待确认提案，实际 {pend}"


def test_source_origin_without_grounded_evidence_is_downgraded(
    client, sess, stub_llm
) -> None:
    """`origin=SOURCE` 是**模型自述**，服务端必须裁决 —— 无证据即降级。

    G5 接线（2026-09-14 人工授权）：`prepare()` 现在用会话内
    `ACTIVE` 且非 stale 的证据卡裁决 `SOURCE`。本用例的会话没有任何证据卡，
    因此模型自称"来源"必须被降级为 INFERENCE ——
    否则前端会替它打出"来源"徽标，等于**让模型自证有文献背书**。
    """
    stub_llm(
        _proposal(
            "intake",
            {
                "statement": "s",
                "knowns": [{"text": "模型自称来自文献", "origin": "SOURCE"}],
            },
        )
    )
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    with session_scope() as db:
        row = db.query(ResearchIntakeRow).filter(
            ResearchIntakeRow.session_id == sess["session_id"]
        ).one()
    assert row.content["knowns"][0]["origin"] == "INFERENCE", (
        "无证据卡背书时，模型自称 SOURCE 必须被服务端降级"
    )
    assert row.content["origins"]["SOURCE"] == 0, (
        f"降级后不应残留任何 SOURCE 计数：{row.content['origins']}"
    )
    assert row.content["origins"]["INFERENCE"] >= 1
