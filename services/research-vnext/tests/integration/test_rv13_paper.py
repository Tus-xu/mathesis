"""RV13 · PaperSpec canonical model / 结构化编辑 / AI patch / 冲突 / 撤销 集成测试。

先红后绿；覆盖任务书验收链路：
创建（提案确认）→ 人工编辑 → AI patch（typed ops）→ 引用/公式/图表绑定 →
expected_revision 冲突（node-level diff）→ 撤销/恢复（持久 revision）→ 刷新恢复 → 跨租户。
"""

from __future__ import annotations

import copy
import hashlib
import uuid

from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import (
    CitationRow,
    PaperEditAuditRow,
    PaperSpecRow,
)
from research_vnext.core.parser.models import AssetAnchorRow
from tests.integration.conftest import auth_headers
from tests.integration.test_rv09_intake_to_maps import (  # noqa: F401
    _accept,
    _pending,
    _proposal,
    _send,
    sess,
    stub_llm,
)
from tests.integration.test_rv12_claim_ledger import (  # noqa: F401
    evaluate,
    propose,
    publish,
    run_verification,
    seed_evidence,
)
from tests.integration.test_rv12_paper_claims import _MATH_PASS, _seed_citation  # noqa: F401

BASE = "/api/research-v1"


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _latest_paper(s) -> PaperSpecRow | None:
    with session_scope() as db:
        row = (
            db.query(PaperSpecRow)
            .filter(PaperSpecRow.session_id == s["session_id"])
            .order_by(PaperSpecRow.revision.desc())
            .first()
        )
        if row is None:
            return None
        db.refresh(row)
        row.content = copy.deepcopy(row.content)  # 脱离 session 后仍可读
        return row


def _paper_blocks(s) -> list[dict]:
    row = _latest_paper(s)
    return list((row.content or {}).get("blocks") or []) if row else []


def _ops_patch(ops: list[dict], *, base_revision: int = 0, rationale: str = "RV13") -> str:
    return _proposal(
        "paper_patch",
        {"baseRevision": base_revision, "rationale": rationale, "ops": ops},
    )


def _put_paper(client, s, *, base_revision: int, blocks: list[dict]):
    return client.put(
        f"{BASE}/sessions/{s['session_id']}/paper",
        json={"baseRevision": base_revision, "blocks": blocks},
        headers=auth_headers(s["ctx"]),
    )


def _get_paper(client, s):
    return client.get(
        f"{BASE}/sessions/{s['session_id']}/paper", headers=auth_headers(s["ctx"])
    )


def _seed_anchor(s, anchor_id: str = "anchor-rv13-1", asset_version: int = 3) -> str:
    with session_scope() as db:
        db.add(
            AssetAnchorRow(
                id=uuid.uuid4(),
                anchor_id=anchor_id,
                tenant_id=s["tenant_id"],
                asset_id=uuid.uuid4(),
                asset_version=asset_version,
                content_hash=hashlib.sha256(anchor_id.encode()).hexdigest(),
                parse_run_id=uuid.uuid4(),
                parser_name="rv05",
                parser_version="1",
                type="figure",
                page=4,
                bbox=[72, 120, 480, 300],
                section_path=["第三章", "3.2"],
                text_preview="图 1",
                text_hash="",
                region_hash="",
                page_width=612.0,
                page_height=792.0,
                confidence=0.99,
                warnings=[],
                payload={},
            )
        )
    return anchor_id


def _published_claim(s, client, *, statement: str = "稳态队长有限") -> tuple[str, str]:
    """发布一个 SOLVED claim；返回 (claim_id, evidence_id)。"""
    ev = seed_evidence(s)
    code, body = propose(client, s, statement=statement, evidence_refs=[ev])
    assert code == 200, body
    cid = body["claim"]["id"]
    run_verification(client, s, cid, verifier="math.check", params=_MATH_PASS)
    evaluate(client, s, cid)
    code, body = publish(client, s, cid)
    assert code == 200, body
    return cid, ev


# ==========================================================================
# 1 · AI typed ops patch：提案 → 确认 → canonical 落库
# ==========================================================================


def test_ai_typed_ops_patch_flow_end_to_end(client, sess, stub_llm) -> None:  # noqa: F811
    cid_claim, _ev = _published_claim(sess, client)
    cid_cit = _seed_citation(sess)
    anchor = _seed_anchor(sess)

    stub_llm(
        _ops_patch(
            [
                {
                    "op": "insert",
                    "node": {"id": "s1", "type": "section", "text": "方法与结果"},
                    "rationale": "建立结构",
                },
                {
                    "op": "insert",
                    "after": "s1",
                    "node": {"id": "e1", "type": "equation",
                             "latex": r"W_q = \frac{\lambda}{\mu - \lambda}", "display": True},
                    "rationale": "核心公式",
                },
                {
                    "op": "insert",
                    "after": "e1",
                    "node": {"id": "p1", "type": "paragraph",
                             "text": "本文证明了 W_q 有限", "claimRef": cid_claim},
                    "rationale": "结论段",
                    "evidenceRefs": [seed_evidence(sess)],
                },
                {
                    "op": "insert",
                    "after": "p1",
                    "node": {"id": "c1", "type": "citation", "refs": [cid_cit]},
                    "rationale": "文献",
                },
                {
                    "op": "insert",
                    "after": "c1",
                    "node": {"id": "f1", "type": "figure", "refs": [anchor],
                             "locator": {"page": 4}, "caption": "图 1"},
                    "rationale": "图",
                },
            ]
        )
    )
    _send(client, sess)
    items = _pending(client, sess)
    if not items:  # 诊断输出（失败时可见拒绝原因）
        import sys as _sys

        snap_diag = client.get(
            f"{BASE}/sessions/{sess['session_id']}", headers=auth_headers(sess["ctx"])
        ).json()
        print("REJECT-TEXT:", snap_diag["timeline"][-1]["text"], file=_sys.stderr)
    assert items and items[0]["object_ref"] == "paper_spec", items
    _accept(client, sess, items[0]["id"])

    blocks = _paper_blocks(sess)
    by_id = {b["id"]: b for b in blocks}
    eq = by_id["e1"]
    assert eq["canonical"] and eq["astHash"], "服务端必须重算 canonical/astHash"
    assert eq["nodeRev"] == 1
    para = by_id["p1"]
    snap = para.get("claimSnapshot") or {}
    assert snap.get("claimId") == cid_claim and snap.get("status") == "SOLVED"
    cit = by_id["c1"]
    csnap = cit.get("citationSnapshot") or {}
    assert csnap.get(cid_cit, {}).get("cslHash"), "citation 节点必须带版本快照"
    fig = by_id["f1"]
    lin = fig.get("lineage") or {}
    assert lin.get(anchor, {}).get("assetVersion") == 3, "figure 必须带 immutable lineage"


def test_ai_patch_with_unlinked_strong_conclusion_is_rejected(client, sess, stub_llm) -> None:  # noqa: F811
    stub_llm(
        _ops_patch(
            [{"op": "insert",
              "node": {"id": "p9", "type": "paragraph", "text": "本文证明了严格优于一切基线"},
              "rationale": "r"}]
        )
    )
    _send(client, sess)
    assert _pending(client, sess) == [], "无 claim 链接的强结论不得进入确认门"


def test_ai_cannot_rewrite_node_whose_claim_is_now_inconclusive(client, sess, stub_llm) -> None:  # noqa: F811
    """SOLVED 已发布 → 进论文 → 证据失效重开为 INCONCLUSIVE ⇒ AI 不得改写该节点文本。"""
    cid, ev_id = _published_claim(sess, client)
    stub_llm(
        _ops_patch(
            [{"op": "insert",
              "node": {"id": "p1", "type": "paragraph",
                       "text": "本文证明了 W_q 有限", "claimRef": cid},
              "rationale": "结论段"}]
        )
    )
    _send(client, sess)
    items = _pending(client, sess)
    assert items, "已发布 claim 的结论段应进入确认门"
    _accept(client, sess, items[0]["id"])

    # 证据失效 → claim 重开为 INCONCLUSIVE
    from research_vnext.core.application import domain_service as DS

    with session_scope() as db:
        out = DS.invalidate_evidence(db, evidence_id=uuid.UUID(ev_id), reason="撤回")
    assert out["reopened_claims"], "证据失效必须重开 SOLVED claim"

    stub_llm(
        _ops_patch(
            [{"op": "update", "target": "p1", "expectedNodeRev": 1,
              "node": {"id": "p1", "type": "paragraph",
                       "text": "本文证明了显著优于基线", "claimRef": cid},
              "rationale": "改写为更强结论"}],
            base_revision=1,
        )
    )
    _send(client, sess)
    assert _pending(client, sess) == [], "INCONCLUSIVE 的结论节点必须被锁"


# ==========================================================================
# 2 · 人工编辑（PUT）：sanitize + 新修订 + 审计
# ==========================================================================


def test_manual_edit_sanitizes_html_and_writes_revision(client, sess, stub_llm) -> None:  # noqa: F811
    stub_llm(_ops_patch([{"op": "insert", "node": {"id": "s1", "type": "section", "text": "引言"},
                          "rationale": "r"}]))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    blocks = _paper_blocks(sess)
    blocks.append({"id": "p1", "type": "paragraph",
                   "text": '<script>alert(1)</script>正常正文<span onclick="x()">点击</span>'})
    r = _put_paper(client, sess, base_revision=1, blocks=blocks)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 2
    saved = _paper_blocks(sess)
    text = next(b for b in saved if b["id"] == "p1")["text"]
    assert "<script" not in text and "onclick" not in text and "正常正文" in text

    with session_scope() as db:
        audits = (
            db.query(PaperEditAuditRow)
            .filter(PaperEditAuditRow.session_id == sess["session_id"])
            .all()
        )
    assert any(a.action == "MANUAL_EDIT" and a.paper_revision == 2 for a in audits)


def test_manual_edit_base_revision_conflict_returns_node_diff(client, sess, stub_llm) -> None:  # noqa: F811
    """双标签：标签 A 改到 rev2，标签 B 仍基于 rev1 提交 ⇒ 409 + nodeDiff。"""
    stub_llm(_ops_patch([{"op": "insert", "node": {"id": "s1", "type": "section", "text": "引言"},
                          "rationale": "r"}]))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    # 标签 A：rev1 → rev2
    blocks = [dict(b) for b in _paper_blocks(sess)]
    blocks.append({"id": "p1", "type": "paragraph", "text": "标签 A 的新段"})
    r = _put_paper(client, sess, base_revision=1, blocks=blocks)
    assert r.status_code == 200

    # 标签 B：基于 rev1 的旧视图改 s1
    stale = [{"id": "s1", "type": "section", "text": "标签 B 改的标题", "nodeRev": 1},
             {"id": "p1", "type": "paragraph", "text": "标签 B 的段", "nodeRev": 1}]
    r2 = _put_paper(client, sess, base_revision=1, blocks=stale)
    assert r2.status_code == 409, r2.text
    error = r2.json().get("error") or {}
    conflict = error.get("conflict") or {}
    nd = conflict.get("nodeDiff") or {}
    changed_ids = {c["id"] for c in nd.get("changed", [])}
    assert "s1" in changed_ids, f"nodeDiff 必须按节点报告变更：{nd}"
    # 当前库中已有标签 A 的 p1；标签 B 的 p1 文本不同 ⇒ 也应报 changed（而非 added）
    assert "p1" in changed_ids, f"p1 内容不同必须报 changed：{nd}"


# ==========================================================================
# 3 · 撤销 / 恢复（持久 revision）+ 刷新恢复
# ==========================================================================


def test_undo_redo_are_persistent_revisions_and_refresh_recovers(client, sess, stub_llm) -> None:  # noqa: F811
    stub_llm(_ops_patch([{"op": "insert", "node": {"id": "s1", "type": "section", "text": "v1"},
                          "rationale": "r"}]))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])
    rev1_blocks = _paper_blocks(sess)

    blocks = [dict(b) for b in rev1_blocks]
    blocks[0]["text"] = "v2 标题"
    r = _put_paper(client, sess, base_revision=1, blocks=blocks)
    assert r.status_code == 200 and r.json()["revision"] == 2
    rev2_blocks = _paper_blocks(sess)
    assert rev2_blocks[0]["text"] == "v2 标题"

    # undo：写**新**修订行（rev3），内容回到 rev1
    r = client.post(f"{BASE}/sessions/{sess['session_id']}/paper:undo",
                    headers=auth_headers(sess["ctx"]))
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 3
    after_undo = _paper_blocks(sess)
    assert after_undo[0]["text"] == rev1_blocks[0]["text"] == "v1"
    with session_scope() as db:
        assert (
            db.query(PaperSpecRow)
            .filter(PaperSpecRow.session_id == sess["session_id"])
            .count()
            == 3
        ), "撤销必须是持久修订，不得覆盖历史"

    # 刷新（重新 GET）读到的仍是撤销后的状态
    g = _get_paper(client, sess)
    assert g.status_code == 200
    refreshed = g.json()
    assert refreshed["revision"] == 3
    assert refreshed["content"]["blocks"][0]["text"] == "v1"

    # redo：rev4 内容回到 v2
    r = client.post(f"{BASE}/sessions/{sess['session_id']}/paper:redo",
                    headers=auth_headers(sess["ctx"]))
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 4
    assert _paper_blocks(sess)[0]["text"] == "v2 标题"

    # 线性撤销：redo 之后再 undo ⇒ 回到 R3 的内容（v1）—— 链上仍有历史 ⇒ 成功
    r = client.post(f"{BASE}/sessions/{sess['session_id']}/paper:undo",
                    headers=auth_headers(sess["ctx"]))
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 5
    assert _paper_blocks(sess)[0]["text"] == "v1"

    # 空 undo：全新会话 0 个修订 ⇒ 409（状态冲突，不是 500）
    from tests.integration.conftest import create_session as _create_session

    r_new, _ = _create_session(client, sess["ctx"], {
        "tenant_id": sess["tenant_id"], "project_id": sess["project_id"],
        "owner_id": sess["actor_id"],
    }, title="rv13-empty-undo")
    sid2 = uuid.UUID(r_new.json()["session"]["id"])
    r = client.post(f"{BASE}/sessions/{sid2}/paper:undo", headers=auth_headers(sess["ctx"]))
    assert r.status_code == 409, r.text
    assert (r.json().get("error") or {}).get("code") == "PAPER_NOTHING_TO_UNDO"

    # 审计：UNDO / REDO / MANUAL_EDIT 全部留痕
    with session_scope() as db:
        actions = [
            a.action
            for a in db.query(PaperEditAuditRow)
            .filter(PaperEditAuditRow.session_id == sess["session_id"])
            .order_by(PaperEditAuditRow.id)
            .all()
        ]
    assert "MANUAL_EDIT" in actions and "UNDO" in actions and "REDO" in actions


# ==========================================================================
# 4 · 引用版本 / 图表 locator 绑定健康（读取时如实标 stale）
# ==========================================================================


def test_citation_stale_binding_detected_on_read(client, sess, stub_llm) -> None:  # noqa: F811
    cid_cit = _seed_citation(sess)
    stub_llm(_ops_patch([{"op": "insert",
                          "node": {"id": "c1", "type": "citation", "refs": [cid_cit]},
                          "rationale": "r"}]))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])

    g = _get_paper(client, sess)
    binding = (g.json()["bindings"].get("c1") or {})
    assert binding.get("state") == "ok", g.json()

    # citation 更新（csl_hash 变化）→ 绑定 stale，**不静默指向新内容**
    with session_scope() as db:
        row = db.get(CitationRow, uuid.UUID(cid_cit))
        csl = dict(row.csl_json)
        csl["title"] = "UPDATED TITLE"
        row.csl_json = csl
        row.csl_hash = hashlib.sha256(
            str(sorted(csl.items(), key=lambda kv: kv[0])).encode()
        ).hexdigest()

    g2 = _get_paper(client, sess)
    binding2 = g2.json()["bindings"].get("c1") or {}
    assert binding2.get("state") == "stale", g2.json()
    ref = binding2.get("refs", {}).get(cid_cit) or {}
    assert ref.get("currentCslHash") != ref.get("boundCslHash")


def test_figure_locator_breakage_detected(client, sess, stub_llm) -> None:
    """figure 引用不存在的 anchor：提案被拒；已存在但 anchor 被删：读取时如实报 missing。"""
    stub_llm(_ops_patch([{"op": "insert",
                          "node": {"id": "f1", "type": "figure", "refs": ["ghost-anchor"],
                                   "locator": {"page": 1}},
                          "rationale": "r"}]))
    _send(client, sess)
    assert _pending(client, sess) == [], "locator 断开的 figure 不得进入确认门"

    anchor = _seed_anchor(sess, anchor_id="anchor-ok")
    stub_llm(_ops_patch([{"op": "insert",
                          "node": {"id": "f1", "type": "figure", "refs": [anchor],
                                   "locator": {"page": 4}},
                          "rationale": "r"}]))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])
    g = _get_paper(client, sess)
    assert (g.json()["bindings"].get("f1") or {}).get("state") == "ok"

    with session_scope() as db:
        row = db.query(AssetAnchorRow).filter(AssetAnchorRow.anchor_id == anchor).one()
        db.delete(row)
    g2 = _get_paper(client, sess)
    b = g2.json()["bindings"].get("f1") or {}
    assert b.get("state") == "missing", g2.json()


# ==========================================================================
# 5 · 读端点 / 历史 / 校验 / 越权
# ==========================================================================


def test_paper_read_before_any_content(client, sess) -> None:  # noqa: F811
    r = _get_paper(client, sess)
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False and body["revision"] == 0 and body["content"]["blocks"] == []


def test_paper_revision_history_lists_audit_actions(client, sess, stub_llm) -> None:  # noqa: F811
    stub_llm(_ops_patch([{"op": "insert", "node": {"id": "s1", "type": "section", "text": "x"},
                          "rationale": "r"}]))
    _send(client, sess)
    _accept(client, sess, _pending(client, sess)[0]["id"])
    r = client.get(f"{BASE}/sessions/{sess['session_id']}/paper/revisions",
                   headers=auth_headers(sess["ctx"]))
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["revision"] for i in items] == [1]
    assert items[0]["action"] == "AI_PATCH_ACCEPT"


def test_paper_validate_endpoint_reports_roundtrip(client, sess) -> None:  # noqa: F811
    r = client.post(
        f"{BASE}/sessions/{sess['session_id']}/paper:validate",
        json={"blocks": [
            {"id": "s1", "type": "section", "text": "x"},
            {"id": "e1", "type": "equation", "latex": r"\frac{a}{"},
        ]},
        headers=auth_headers(sess["ctx"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    codes = {i["code"] for i in body["issues"]}
    assert "PAPER_FORMULA_UNPARSABLE" in codes


def test_paper_endpoints_are_cross_tenant_forbidden(client, sess, ctx_factory) -> None:  # noqa: F811
    """跨租户读 paper = 403（对象级授权）；坏签名 = 401。"""
    ctx_other = ctx_factory(tenant_id=uuid.uuid4())
    rr = client.get(
        f"{BASE}/sessions/{sess['session_id']}/paper", headers=auth_headers(ctx_other)
    )
    assert rr.status_code == 403, rr.text
    r2 = client.get(f"{BASE}/sessions/{sess['session_id']}/paper",
                    headers={"X-Research-Access-Context": "totally-invalid"})
    assert r2.status_code == 401, r2.text
