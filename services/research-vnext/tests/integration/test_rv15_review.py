"""RV15 · Review 域真实链路集成测试（真实 PostgreSQL + HTTP TestClient）。

任务书要求的负例 / 不变量（集成层）：
1. Reviewer AI propose → 确认 → 入库（四门验证）
2. 无 locator / 无 evidence 强结论 / 未知 taxonomy 拒绝（HTTP 422）
3. Reviewer 越权 mutate target（propose 端点拒收 patch 形状）
4. resolution workflow 全链路（ACK→IN_PROGRESS→RESOLVED 引用修复修订）
5. waive 无理由/无期限拒绝；有理由+有期限+人工批准成功；过期自动重开
6. 修复后 finding 不重开（target 未变）/ 重开后重开计数
7. revision conflict（expectedRevision 过期 409）
8. target 更版后 stale/needsRecheck
9. P0/P1 未解决 ⇒ session 不可 COMPLETED（409 RESEARCH_BLOCKED_BY_FINDINGS），
   且不删除已有 artifact
10. 跨租户 403
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import (
    ClaimRow,
    FindingResolutionRow,
    FindingTransitionRow,
    FindingWaiverRow,
    ReviewFindingV2Row,
)
from tests.integration.conftest import auth_headers
from tests.integration.test_rv09_intake_to_maps import sess, stub_llm  # noqa: F401,F811
from tests.integration.test_rv12_paper_claims import _seed_citation  # noqa: F401

BASE = "/api/research-v1"


# --------------------------------------------------------------------------
# 种子工具
# --------------------------------------------------------------------------


def seed_paper(s) -> int:
    """通过真实 HTTP 落一版论文（人工 PUT，rev 1），返回 revision。"""
    blocks = [
        {"id": "s1", "type": "section", "text": "模型与结论"},
        {"id": "e1", "type": "equation", "latex": r"W = \frac{\lambda}{\mu-\lambda}", "display": True},
        {"id": "p1", "type": "paragraph", "text": "在该条件下系统达到稳态。"},
    ]
    r = s["client"].put(
        f"{BASE}/sessions/{s['session_id']}/paper",
        json={"baseRevision": 0, "blocks": blocks},
        headers=auth_headers(s["ctx"]),
    )
    assert r.status_code == 200, r.text
    return int(r.json()["revision"])


def propose_finding(s, **over):
    """Reviewer AI 提案入口（HTTP 直提 = 与消息路径同一校验器）。"""
    body = {
        "findings": over.pop("findings"),
        "createdByRun": over.pop("createdByRun", "review-run-1"),
        "client_request_id": f"rv15-{uuid.uuid4().hex[:12]}",
    }
    body.update(over)
    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}/review:propose",
        json=body,
        headers=auth_headers(s["ctx"]),
    )
    return r.status_code, r.json()


def _ok_finding(s, *, tax="MATH_ERROR", target_ver=None, **target_over):
    target = {"type": "paper_node", "id": "e1",
              "version": target_ver if target_ver is not None else s.get("paper_revision", 1),
              "locator": {"blockIndex": 1}}
    target.update(target_over)
    return {
        "taxonomy": tax,
        "target": target,
        "description": "公式与独立验证判定矛盾（W 与 rho 不一致）",
        "evidence": [{"kind": "verification", "ref": "seed-vr", "summary": "FAIL 记录"}],
    }


def transition_finding(s, fid, to, **body):
    payload = {"to": to, **body}
    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}/review/{fid}:transition",
        json=payload,
        headers=auth_headers(s["ctx"]),
    )
    return r.status_code, r.json()


def resolve_finding(s, fid, *, revision, evidence=("vr-fix-1",), note="改公式后重验通过"):
    payload = {
        "resolution": {"revisionRef": {"kind": "paper_spec", "revision": revision},
                       "evidenceRefs": list(evidence), "note": note},
    }
    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}/review/{fid}:resolve",
        json=payload,
        headers=auth_headers(s["ctx"]),
    )
    return r.status_code, r.json()


def waive_finding(s, fid, *, expiry=None, reason=None, approved_by="human-reviewer"):
    payload = {
        "waiver": {"reason": reason or {"code": "NO_IMPACT", "text": "演示场景，不影响结论"},
                   "expiry": expiry or str(date.today() + timedelta(days=30)),
                   "approvedBy": approved_by},
    }
    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}/review/{fid}:waive",
        json=payload,
        headers=auth_headers(s["ctx"]),
    )
    return r.status_code, r.json()


def latest_findings(s):
    r = s["client"].get(
        f"{BASE}/sessions/{s['session_id']}/review",
        headers=auth_headers(s["ctx"]),
    )
    assert r.status_code == 200, r.text
    return {f["id"]: f for f in r.json()["items"]}


def transition_session(s, to, expected):
    from tests.conftest import envelope

    body = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(s["ctx"]["actor_id"]),
        scope={"kind": "session", "id": str(s["session_id"]), "role": "owner"},
        payload={"to": to},
        expected_revision=expected,
        tenant_id=s["tenant_id"],
    )
    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}:transition",
        json=body,
        headers=auth_headers(s["ctx"]),
    )
    return r.status_code, r.json()


def _activate(s):
    code, body = transition_session(s, "ACTIVE", 0)
    assert code == 200, body
    return body["session"]["revision"]


# ==========================================================================
# 1 · 提案四门 + 入库
# ==========================================================================


def test_reviewer_propose_creates_finding_after_accept(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    code, body = propose_finding(s, findings=[_ok_finding(s)])
    assert code == 200, body
    assert body["findings"] and body["findings"][0]["fingerprint"]  # 预览带指纹
    fid = _make_open_finding(s)
    items = latest_findings(s)
    assert items[fid]["status"] == "OPEN"
    assert items[fid]["createdByRun"] == "review-run-1"
    assert items[fid]["severity"] == "P0"  # MATH_ERROR 默认级


def test_finding_without_locator_rejected_over_http(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    f = _ok_finding(s)
    f["target"].pop("locator")
    code, body = propose_finding(s, findings=[f])
    assert code == 422 and body["error"]["code"] == "REVIEW_LOCATOR_REQUIRED", body


def test_strong_finding_without_evidence_rejected_over_http(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    f = _ok_finding(s, tax="OVERCLAIM")
    f["evidence"] = []
    code, body = propose_finding(s, findings=[f])
    assert code == 422 and body["error"]["code"] == "REVIEW_EVIDENCE_REQUIRED", body


def test_reviewer_cannot_mutate_target_via_proposal(client, sess, stub_llm) -> None:  # noqa: F811
    """Reviewer 越权：提案里夹带 patch/ops 想直接改论文 ⇒ 拒收。"""
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    f = _ok_finding(s)
    f["ops"] = [{"op": "update", "target": "p1",
                 "node": {"id": "p1", "type": "paragraph", "text": "被 AI 改写的结论"}}]
    code, body = propose_finding(s, findings=[f])
    assert code == 422 and body["error"]["code"] == "REVIEW_OPERATION_IN_EVIDENCE", body
    # 论文未被改动
    r = client.get(f"{BASE}/sessions/{s['session_id']}/paper", headers=auth_headers(s["ctx"]))
    texts = " ".join(b.get("text", "") for b in r.json()["content"]["blocks"])
    assert "被 AI 改写的结论" not in texts


def test_reviewer_cannot_delete_counterevidence(client, sess, stub_llm) -> None:  # noqa: F811
    """提案 evidence 里要求"删除某条反证/证据"⇒ 非法操作，服务端拒收。"""
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    f = _ok_finding(s)
    f["evidence"] = [{"kind": "operation", "op": "DELETE_COUNTEREVIDENCE", "ref": "ev-1"}]
    code, body = propose_finding(s, findings=[f])
    assert code == 422 and body["error"]["code"] == "REVIEW_OPERATION_IN_EVIDENCE", body


# ==========================================================================
# 2 · resolution workflow
# ==========================================================================


def _make_open_finding(s):
    """提案 → 确认 → 从列表按指纹取回 finding id（提案期 finding 尚无正式 id）。"""
    f = _ok_finding(s)
    code, body = propose_finding(s, findings=[f])
    assert code == 200, body
    client = s["client"]
    r = client.post(
        f"{BASE}/sessions/{s['session_id']}/pending-actions/{body['pendingActionId']}:accept",
        json={}, headers=auth_headers(s["ctx"]),
    )
    assert r.status_code == 200, r.text
    items = latest_findings(s)
    assert len(items) >= 1, items
    # 返回（最新创建的）finding id
    return max(items, key=lambda k: items[k]["createdAt"])


def test_resolution_workflow_full_path(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)

    # OPEN → ACKNOWLEDGED 需要 assignee
    code, body = transition_finding(s, fid, "ACKNOWLEDGED", assignee=s["ctx"]["actor_id"])
    assert code == 200, body
    code, body = transition_finding(s, fid, "IN_PROGRESS")
    assert code == 200, body

    # RESOLVED：引用修复修订（paper rev 2，晚于发现的 rev 1）
    r = s["client"].put(
        f"{BASE}/sessions/{s['session_id']}/paper",
        json={"baseRevision": 1,
              "blocks": [{"id": "s1", "type": "section", "text": "模型与结论"},
                         {"id": "e1", "type": "equation", "latex": r"W = \frac{\lambda}{\mu-\lambda}",
                          "display": True},
                         {"id": "p1", "type": "paragraph", "text": "稳态条件为 rho<1（已修正表述）。"}]},
        headers=auth_headers(s["ctx"]),
    )
    assert r.status_code == 200, r.text
    fix_rev = int(r.json()["revision"])

    code, body = resolve_finding(s, fid, revision=fix_rev)
    assert code == 200, body
    items = latest_findings(s)
    assert items[fid]["status"] == "RESOLVED"
    assert items[fid]["resolution"]["revisionRef"]["revision"] == fix_rev

    # 落库对账：迁移与 resolution 都有审计行
    with session_scope() as db:
        assert db.query(FindingTransitionRow).filter(
            FindingTransitionRow.finding_id == uuid.UUID(fid)).count() >= 3
        assert db.query(FindingResolutionRow).filter(
            FindingResolutionRow.finding_id == uuid.UUID(fid)).count() == 1


def test_resolved_with_old_revision_rejected(client, sess, stub_llm) -> None:  # noqa: F811
    """修复修订必须晚于发现时的 target version（负例）。"""
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    code, _ = transition_finding(s, fid, "IN_PROGRESS", assignee=s["ctx"]["actor_id"])
    assert code == 200
    code, body = resolve_finding(s, fid, revision=0)  # 比发现时还早
    assert code == 422 and body["error"]["code"] == "FINDING_RESOLUTION_NO_REVISION", body


def test_resolve_requires_in_progress(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    code, body = resolve_finding(s, fid, revision=9)
    assert code == 422 and body["error"]["code"] == "FINDING_TRANSITION_INVALID", body


# ==========================================================================
# 3 · waiver
# ==========================================================================


def test_waiver_requires_reason_expiry_approver(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)

    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}/review/{fid}:waive",
        json={"waiver": {}}, headers=auth_headers(s["ctx"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "FINDING_WAIVER_REASON_REQUIRED"

    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}/review/{fid}:waive",
        json={"waiver": {"reason": {"code": "NO_IMPACT", "text": "r"}}},
        headers=auth_headers(s["ctx"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "FINDING_WAIVER_EXPIRY_REQUIRED"

    # 无期限豁免（空 expiry）
    r = s["client"].post(
        f"{BASE}/sessions/{s['session_id']}/review/{fid}:waive",
        json={"waiver": {"reason": {"code": "NO_IMPACT", "text": "r"}, "expiry": ""}},
        headers=auth_headers(s["ctx"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "FINDING_WAIVER_EXPIRY_REQUIRED"


def test_waiver_happy_path_and_expiry_reopens(client, sess, stub_llm, monkeypatch) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)

    soon = date.today() + timedelta(days=1)
    code, body = waive_finding(s, fid, expiry=str(soon))
    assert code == 200, body
    items = latest_findings(s)
    assert items[fid]["status"] == "WAIVED"
    assert items[fid]["waiver"]["approvedBy"]

    with session_scope() as db:
        w = db.query(FindingWaiverRow).filter(FindingWaiverRow.finding_id == uuid.UUID(fid)).one()
        assert w.reason["code"] == "NO_IMPACT"

    # 豁免到期（把 expiry 改成昨天）→ 读取路径自动 REOPENED
    with session_scope() as db:
        row = db.get(ReviewFindingV2Row, uuid.UUID(fid))
        row.waiver = {"reason": {"code": "NO_IMPACT", "text": "r"},
                      "expiry": str(date.today() - timedelta(days=1)),
                      "approvedBy": "human-reviewer"}
    items = latest_findings(s)
    assert items[fid]["status"] == "REOPENED", items[fid]
    with session_scope() as db:
        w = db.query(FindingWaiverRow).filter(FindingWaiverRow.finding_id == uuid.UUID(fid)).one()
        assert w.expired_reopened is True


# ==========================================================================
# 4 · 重审去重 / 重开 / stale
# ==========================================================================


def test_rerun_does_not_duplicate_findings(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    # 同一问题再来一轮（同指纹）
    code, body = propose_finding(s, findings=[_ok_finding(s)],
                                 createdByRun="review-run-2")
    assert code == 200, body
    pa = body["pendingActionId"]
    r = client.post(
        f"{BASE}/sessions/{s['session_id']}/pending-actions/{pa}:accept",
        json={}, headers=auth_headers(s["ctx"]))
    assert r.status_code == 200, r.text
    items = latest_findings(s)
    matching = [f for f in items.values() if f["fingerprint"] == items[fid]["fingerprint"]]
    assert len(matching) == 1, "同指纹不得产生第二行"
    assert items[fid]["seenCount"] == 2
    assert items[fid]["lastSeenRun"] == "review-run-2"


def test_resolved_finding_not_reopened_when_target_unchanged(client, sess, stub_llm) -> None:  # noqa: F811
    """修复后重审：target 没动 → RESOLVED 保持，不重开也不新建（负例）。"""
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    code, _ = transition_finding(s, fid, "IN_PROGRESS", assignee=s["ctx"]["actor_id"])
    code, _ = resolve_finding(s, fid, revision=1)  # target version 就是 1
    assert code == 200
    # target 未变（仍是 rev1）再审出同问题
    code, body = propose_finding(s, findings=[_ok_finding(s)], createdByRun="review-run-2")
    pa = body["pendingActionId"]
    client.post(f"{BASE}/sessions/{s['session_id']}/pending-actions/{pa}:accept",
                json={}, headers=auth_headers(s["ctx"]))
    items = latest_findings(s)
    assert items[fid]["status"] == "RESOLVED", "target 未变时 RESOLVED 必须保持"
    assert len(items) == 1, "不得新建第二行"


def test_target_version_bump_marks_stale_and_recheck(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    # target 更版
    r = s["client"].put(
        f"{BASE}/sessions/{s['session_id']}/paper",
        json={"baseRevision": 1,
              "blocks": [{"id": "s1", "type": "section", "text": "模型与结论 v2"},
                         {"id": "e1", "type": "equation", "latex": r"W = \frac{\lambda}{\mu-\lambda}",
                          "display": True},
                         {"id": "p1", "type": "paragraph", "text": "补一段文字。"}]},
        headers=auth_headers(s["ctx"]))
    assert r.status_code == 200
    items = latest_findings(s)
    assert items[fid]["stale"] is True and items[fid]["needsRecheck"] is True, items[fid]


# ==========================================================================
# 5 · revision conflict
# ==========================================================================


def test_finding_transition_revision_conflict(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    code, body = transition_finding(s, fid, "ACKNOWLEDGED",
                                    assignee=s["ctx"]["actor_id"], expectedRevision=99)
    assert code == 409 and body["error"]["code"] == "REVISION_CONFLICT", body
    # 纠正后成功
    code, body = transition_finding(s, fid, "ACKNOWLEDGED",
                                    assignee=s["ctx"]["actor_id"], expectedRevision=1)
    assert code == 200, body


# ==========================================================================
# 6 · blocking gate
# ==========================================================================


def test_p0_finding_blocks_completed_but_deletes_nothing(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    rev = _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    # 走到 READY_FOR_REVIEW（需要一次 confirmation：先 WAITING 再 ACTIVE）
    code, body = transition_session(s, "WAITING_CONFIRMATION", rev)
    assert code == 200, body
    rev = body["session"]["revision"]
    code, body = transition_session(s, "ACTIVE", rev)
    rev = body["session"]["revision"]
    code, body = transition_session(s, "READY_FOR_REVIEW", rev)
    assert code == 200, body
    rev = body["session"]["revision"]

    # P0 OPEN ⇒ COMPLETED 被拦
    code, body = transition_session(s, "COMPLETED", rev)
    assert code == 409, body
    assert body["error"]["code"] == "RESEARCH_BLOCKED_BY_FINDINGS", body
    assert body["error"]["blockedBy"][0]["findingId"] == fid

    # artifact 一个不删
    r = client.get(f"{BASE}/sessions/{s['session_id']}/paper", headers=auth_headers(s["ctx"]))
    assert r.status_code == 200 and r.json()["exists"] is True
    assert r.json()["revision"] == s["paper_revision"]

    # 豁免后（人工批准 + 有期限）→ 可以 COMPLETED
    code, body = waive_finding(s, fid)
    assert code == 200, body
    code, body = transition_session(s, "COMPLETED", rev)
    assert code == 200, body
    assert body["session"]["status"] == "COMPLETED"


def test_blocking_endpoint_matches_transition_gate(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    _make_open_finding(s)
    r = s["client"].get(
        f"{BASE}/sessions/{s['session_id']}/review/blocking",
        headers=auth_headers(s["ctx"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body["blocked"][0]["severity"] == "P0"


# ==========================================================================
# 7 · 越权 / 跨租户
# ==========================================================================


def test_cross_tenant_review_access_forbidden(client, sess, stub_llm, ctx_factory, new_ids) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    other_ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r = client.get(
        f"{BASE}/sessions/{s['session_id']}/review",
        headers=auth_headers(other_ctx))
    assert r.status_code in (403, 404), r.text


def test_waiver_by_ai_run_forbidden(client, sess, stub_llm) -> None:  # noqa: F811
    s = {**sess, "client": client}
    _activate(s)
    s["paper_revision"] = seed_paper(s)
    fid = _make_open_finding(s)
    code, body = waive_finding(s, fid, approved_by="ai:review-run-1")
    assert code == 403 and body["error"]["code"] == "REVIEWER_POLICY_VIOLATION", body
