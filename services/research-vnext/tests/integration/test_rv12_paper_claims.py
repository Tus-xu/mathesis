"""RV12 → RV13 · 论文域的 **claim 门禁**测试（先红后绿）。

为什么论文测试属于 RV12 的收尾：论文是 claim 的**下游消费者**。
如果论文可以引用"未发布 / 未验证 / 跨会话"的结论，那么上游所有
"AI 只 propose、人工确认才发布"的约束都会被一条 `claim_ref` 绕过。

四条门（决定这些测试长什么样）：

1. `claim_ref` 只能引用**已发布**的 claim（人工确认才发布关键 claim）；
2. `evidence_ref` 必须解析到本会话可用的证据（不解析 = 越权/凭空）；
3. `citation` 必须解析到真实 citation 行（RV07 canonical）；
4. 落库的 `claim_ref` 块必须携带 **claim 状态快照**（status / statusLabel /
   supportBand / gateId）—— 论文要能回答"这个结论凭什么成立"。
"""

from __future__ import annotations

import hashlib
import uuid

from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import CitationRow, PaperSpecRow
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
    seed_experiment_run,
)

BASE = "/api/research-v1"


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _claim_ref_patch(claim_id: str, **extra) -> str:
    return _proposal(
        "paper_patch",
        {
            "baseRevision": 0,
            "rationale": "结论节",
            "blocks": [
                {"id": "s1", "type": "section", "text": "结论"},
                {"id": "c1", "type": "claim_ref", "text": "见结论", "refs": [claim_id], **extra},
            ],
        },
    )


def _paper_spec_blocks(s) -> list[dict]:
    with session_scope() as db:
        rows = (
            db.query(PaperSpecRow)
            .filter(PaperSpecRow.session_id == s["session_id"])
            .order_by(PaperSpecRow.revision.desc())
            .all()
        )
        if not rows:
            return []
        return list(rows[0].content.get("blocks") or [])


def _seed_citation(s) -> str:
    cid = uuid.uuid4()
    csl = {
        "id": f"doi:10.1000/rv12.paper",
        "type": "article-journal",
        "title": "Queueing at campus pickup points",
        "author": [{"family": "王", "given": "小明"}],
        "issued": {"date-parts": [[2026, 1, 1]]},
        "container-title": "Journal of Research Verification",
    }
    with session_scope() as db:
        db.add(
            CitationRow(
                id=cid,
                tenant_id=s["tenant_id"],
                project_id=s["project_id"],
                session_id=s["session_id"],
                csl_json=csl,
                csl_hash=hashlib.sha256(str(sorted(csl.items(), key=lambda kv: kv[0])).encode()).hexdigest(),
                csl_type="article-journal",
                doi="10.1000/rv12.paper",
            )
        )
    return str(cid)


# ==========================================================================
# 1 · claim_ref 必须引用**已发布**的 claim
# ==========================================================================


def test_paper_claim_ref_rejects_unpublished_inconclusive_claim(client, sess, stub_llm) -> None:  # noqa: F811
    """INCONCLUSIVE（未发布）的 claim 不得作为论文结论。"""
    code, body = propose(client, sess, statement="未验证的结论", expected=0)
    assert code == 200, body
    cid = body["claim"]["id"]

    stub_llm(_claim_ref_patch(cid))
    _send(client, sess)
    assert _pending(client, sess) == [], "未发布 claim 的 claim_ref 必须被拒"


def test_paper_claim_ref_rejects_solved_but_unpublished_claim(client, sess, stub_llm) -> None:  # noqa: F811
    """SOLVED 但**未人工发布**的 claim 也不得进论文（发布是人工门）。"""
    ev = seed_evidence(sess)
    code, body = propose(client, sess, statement="会被发布的结论", evidence_refs=[ev])
    assert code == 200, body
    cid = body["claim"]["id"]
    run_verification(client, sess, cid, verifier="math.check", params=_MATH_PASS)
    code, body = evaluate(client, sess, cid)
    assert body["claim"]["status"] == "SOLVED"

    stub_llm(_claim_ref_patch(cid))
    _send(client, sess)
    assert _pending(client, sess) == [], "SOLVED 但未发布 ⇒ 不得进论文"


def test_paper_claim_ref_accepts_published_claim_and_carries_status_snapshot(client, sess, stub_llm) -> None:  # noqa: F811
    """已发布的 claim 可以进论文，且**必须**把状态快照带进块（论文能自证凭据）。"""
    ev = seed_evidence(sess)
    code, body = propose(client, sess, statement="已发布的结论", evidence_refs=[ev])
    assert code == 200, body
    cid = body["claim"]["id"]
    run_verification(client, sess, cid, verifier="math.check", params=_MATH_PASS)
    evaluate(client, sess, cid)
    code, body = publish(client, sess, cid)
    assert code == 200, body

    stub_llm(_claim_ref_patch(cid))
    _send(client, sess)
    items = _pending(client, sess)
    assert items and items[0]["object_ref"] == "paper_spec"
    _accept(client, sess, items[0]["id"])

    blocks = _paper_spec_blocks(sess)
    ref = next(b for b in blocks if b["type"] == "claim_ref")
    snap = ref.get("claimSnapshot") or {}
    assert snap.get("claimId") == cid
    assert snap.get("status") == "SOLVED"
    assert snap.get("statusLabel"), "状态必须有文本标签（不靠颜色）"
    assert snap.get("supportBand") in {"STRONG", "MODERATE", "WEAK"}
    assert snap.get("gateId") == "G_SOLVED"
    assert snap.get("published") is True


def test_paper_claim_ref_rejects_cross_session_claim(client, sess, new_ids, ctx_factory, stub_llm) -> None:  # noqa: F811
    """跨会话引用 claim = 越权：即使已发布也必须拒绝。"""
    from tests.integration.conftest import create_session

    other_ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, other_ctx, new_ids, title="other-paper")
    other = {
        "ctx": other_ctx,
        "session_id": uuid.UUID(r.json()["session"]["id"]),
        "tenant_id": new_ids["tenant_id"],
        "project_id": new_ids["project_id"],
        "actor_id": uuid.UUID(other_ctx["actor_id"]),
    }
    ev = seed_evidence(other)
    code, body = propose(client, other, statement="别的会话的结论", evidence_refs=[ev])
    assert code == 200, body
    cid = body["claim"]["id"]
    run_verification(client, other, cid, verifier="math.check", params=_MATH_PASS)
    evaluate(client, other, cid)
    code, body = publish(client, other, cid)
    assert code == 200, body

    stub_llm(_claim_ref_patch(cid))
    _send(client, sess)
    assert _pending(client, sess) == [], "跨会话 claim_ref 必须被拒"


# ==========================================================================
# 2 · evidence_ref / citation 必须可解析
# ==========================================================================


def test_paper_evidence_ref_must_resolve(client, sess, stub_llm) -> None:  # noqa: F811
    stub_llm(
        _proposal(
            "paper_patch",
            {"blocks": [{"id": "e1", "type": "evidence_ref", "text": "证据", "refs": ["ev-not-real"]}]},
        )
    )
    _send(client, sess)
    assert _pending(client, sess) == [], "不可解析的 evidence_ref 必须被拒"


def test_paper_evidence_ref_accepts_resolvable_evidence(client, sess, stub_llm) -> None:  # noqa: F811
    ev = seed_evidence(sess)
    stub_llm(
        _proposal(
            "paper_patch",
            {"blocks": [{"id": "e1", "type": "evidence_ref", "text": "证据", "refs": [ev]}]},
        )
    )
    _send(client, sess)
    items = _pending(client, sess)
    assert items and items[0]["object_ref"] == "paper_spec"
    _accept(client, sess, items[0]["id"])
    blocks = _paper_spec_blocks(sess)
    assert [b["type"] for b in blocks] == ["evidence_ref"]
    assert blocks[0]["refs"] == [ev]


def test_paper_citation_must_resolve_to_real_citation(client, sess, stub_llm) -> None:  # noqa: F811
    fake = str(uuid.uuid4())
    stub_llm(
        _proposal(
            "paper_patch",
            {"blocks": [{"id": "ct1", "type": "citation", "text": "", "refs": [fake]}]},
        )
    )
    _send(client, sess)
    assert _pending(client, sess) == [], "不存在的 citation 必须被拒"

    real = _seed_citation(sess)
    stub_llm(
        _proposal(
            "paper_patch",
            {"blocks": [{"id": "ct2", "type": "citation", "text": "", "refs": [real]}]},
        )
    )
    _send(client, sess)
    items = _pending(client, sess)
    assert items and items[0]["object_ref"] == "paper_spec"
    _accept(client, sess, items[0]["id"])
    blocks = _paper_spec_blocks(sess)
    assert blocks[-1]["refs"] == [real]


_MATH_PASS = {
    "checks": [
        {
            "name": "代数恒等 (a-b)^2 = a^2-2ab+b^2",
            "kind": "IDENTITY",
            "lhs": "(a-b)**2",
            "rhs": "a**2-2*a*b+b**2",
            "points": [{"a": 1, "b": 1}, {"a": 3, "b": 0.5}],
            "tolerance": 1e-9,
        }
    ]
}
