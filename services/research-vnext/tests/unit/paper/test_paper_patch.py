"""RV13 · AI typed patch 安全门测试（先红后绿）。

钉住任务书第 5/6 条：
- AI 只返回 typed patch proposal（target node / revision / rationale / evidence refs）；
- schema、evidence coverage、output safety、人工确认后才能应用；
- 强结论必须链接 RV12 Claim；INCONCLUSIVE/REFUTED 不能被 AI 改写成确定结论。
"""

from __future__ import annotations

import pytest

from research_vnext.core.paper.errors import PaperSchemaError
from research_vnext.core.paper.patch import (
    PATCH_OPS,
    apply_ops,
    has_strong_conclusion,
    validate_ops,
)
from research_vnext.core.paper.nodes import validate_blocks


BASE = validate_blocks(
    [
        {"id": "s1", "type": "section", "text": "结果"},
        {"id": "p1", "type": "paragraph", "text": "方法如上。", "nodeRev": 1},
        {"id": "e1", "type": "equation", "latex": "y = x", "nodeRev": 1},
    ]
)


def _ok(ops: list[dict], blocks: list[dict] | None = None) -> list[dict]:
    """默认全部 evidence/claim/anchor 均可解析（解析失败场景单独测）。"""
    return apply_ops(
        blocks or BASE,
        validate_ops(
            ops,
            current_blocks=blocks or BASE,
            resolvable_evidence={"ev-1"},
            resolvable_claims={"claim-1"},
            claim_status={"claim-1": "SOLVED"},
            resolvable_citations={"cit-1"},
            resolvable_anchors={"anchor-1"},
        ),
    )


def test_op_vocabulary_is_closed() -> None:
    assert set(PATCH_OPS) == {"insert", "update", "delete", "move"}


# --------------------------------------------------------------------------
# 1 · op 形状：target / expectedNodeRev / rationale
# --------------------------------------------------------------------------


def test_update_requires_target_and_expected_node_rev() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        validate_ops(
            [{"op": "update", "node": {"id": "p1", "type": "paragraph", "text": "改"}}],
            current_blocks=BASE,
            resolvable_evidence=set(),
            resolvable_claims=set(),
            claim_status={},
            resolvable_citations=set(),
            resolvable_anchors=set(),
        )
    assert ei.value.code == "PAPER_OP_TARGET_REQUIRED"


def test_update_on_missing_target_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "update", "target": "ghost", "expectedNodeRev": 1,
              "node": {"id": "ghost", "type": "paragraph", "text": "x"},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_OP_TARGET_MISSING"


def test_stale_expected_node_rev_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "update", "target": "p1", "expectedNodeRev": 99,
              "node": {"id": "p1", "type": "paragraph", "text": "x"},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_NODE_REV_STALE"


def test_rationale_is_required_for_every_op() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "delete", "target": "p1", "expectedNodeRev": 1}])
    assert ei.value.code == "PAPER_OP_RATIONALE_REQUIRED"


# --------------------------------------------------------------------------
# 2 · 应用语义：nodeRev 递增、insert 唯一 id、delete 生效
# --------------------------------------------------------------------------


def test_update_bumps_node_rev_and_keeps_other_nodes() -> None:
    out = _ok([{"op": "update", "target": "p1", "expectedNodeRev": 1,
                "node": {"id": "p1", "type": "paragraph", "text": "方法如上（修订）"},
                "rationale": "润色"}])
    by_id = {n["id"]: n for n in out}
    assert by_id["p1"]["nodeRev"] == 2
    assert by_id["p1"]["text"] == "方法如上（修订）"
    assert by_id["e1"]["latex"] == "y = x" and by_id["e1"]["nodeRev"] == 1


def test_insert_appends_new_node_with_unique_id() -> None:
    out = _ok([{"op": "insert", "after": "p1",
                "node": {"id": "p-new", "type": "paragraph", "text": "新增段"},
                "rationale": "补充"}])
    ids = [n["id"] for n in out]
    assert ids.index("p-new") == ids.index("p1") + 1


def test_insert_with_duplicate_id_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "insert", "node": {"id": "p1", "type": "paragraph", "text": "x"},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_NODE_ID_DUPLICATE"


def test_delete_removes_node() -> None:
    out = _ok([{"op": "delete", "target": "e1", "expectedNodeRev": 1, "rationale": "冗余"}])
    assert all(n["id"] != "e1" for n in out)


def test_equation_op_with_unparsable_latex_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "insert", "node": {"id": "e2", "type": "equation", "latex": "\\frac{a{"},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_FORMULA_UNPARSABLE"


def test_equation_canonical_and_hash_are_server_recomputed() -> None:
    out = _ok([{"op": "insert",
                "node": {"id": "e2", "type": "equation", "latex": "\\dfrac{a}{b}"},
                "rationale": "r"}])
    node = out[-1]
    assert node["latex"] == "\\dfrac{a}{b}"
    assert node["canonical"] and node["astHash"], "服务端必须落 canonical + astHash"


# --------------------------------------------------------------------------
# 3 · server-owned 与 claim 状态锁
# --------------------------------------------------------------------------


def test_ai_cannot_supply_claim_snapshot() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "insert",
              "node": {"id": "p9", "type": "paragraph", "text": "x",
                       "claimSnapshot": {"status": "SOLVED"}},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_CLAIM_SNAPSHOT_SERVER_OWNED"


def test_strong_conclusion_without_claim_link_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "insert",
              "node": {"id": "p9", "type": "paragraph", "text": "本文证明了该算法严格优于基线"},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_CONCLUSION_UNLINKED"


def test_strong_conclusion_with_published_claim_is_accepted() -> None:
    out = _ok([{"op": "insert",
                "node": {"id": "p9", "type": "paragraph",
                         "text": "本文证明了该算法严格优于基线", "claimRef": "claim-1"},
                "rationale": "结论段"}])
    node = [n for n in out if n["id"] == "p9"][0]
    assert node["claimSnapshot"]["status"] == "SOLVED"


def test_claim_ref_must_resolve() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        apply_ops(
            BASE,
            validate_ops(
                [{"op": "insert",
                  "node": {"id": "p9", "type": "paragraph", "text": "结论", "claimRef": "ghost"},
                  "rationale": "r"}],
                current_blocks=BASE,
                resolvable_evidence=set(),
                resolvable_claims=set(),
                claim_status={},
                resolvable_citations=set(),
                resolvable_anchors=set(),
            ),
        )
    assert ei.value.code == "PAPER_CLAIM_REF_UNRESOLVED"


def test_unpublished_claim_cannot_back_a_conclusion() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        apply_ops(
            BASE,
            validate_ops(
                [{"op": "insert",
                  "node": {"id": "p9", "type": "paragraph",
                           "text": "本文证明了严格优于", "claimRef": "claim-1"},
                  "rationale": "r"}],
                current_blocks=BASE,
                resolvable_evidence=set(),
                resolvable_claims={"claim-1"},
                claim_status={"claim-1": "CONDITIONAL"},
                resolvable_citations=set(),
                resolvable_anchors=set(),
            ),
        )
    assert ei.value.code == "PAPER_CLAIM_REF_UNPUBLISHED"


def _base_with_refuted_claim() -> list[dict]:
    return validate_blocks(
        [
            {"id": "s1", "type": "section", "text": "结果"},
            {"id": "p1", "type": "paragraph", "text": "初步结果显示提升。", "nodeRev": 1,
             "claimRef": "claim-1"},
        ]
    )


def _refuted_ctx():
    return dict(
        resolvable_evidence={"ev-1"},
        resolvable_claims={"claim-1"},
        claim_status={"claim-1": "REFUTED"},
        resolvable_citations=set(),
        resolvable_anchors=set(),
    )


def test_ai_cannot_rewrite_refuted_claim_text_into_certainty() -> None:
    # 服务端在确认时给带 claimRef 的节点回填 claimSnapshot；
    # 单测里用带快照的基线模拟"已确认状态"。
    snapshot = {"claimId": "claim-1", "status": "REFUTED", "statusLabel": "已证伪"}
    base = [dict(n, claimSnapshot=snapshot) if n["id"] == "p1" else dict(n)
            for n in _base_with_refuted_claim()]
    with pytest.raises(PaperSchemaError) as ei:
        apply_ops(
            base,
            validate_ops(
                [{"op": "update", "target": "p1", "expectedNodeRev": 1,
                  "node": {"id": "p1", "type": "paragraph",
                           "text": "本文证明该方法严格优于基线", "claimRef": "claim-1"},
                  "rationale": "改写为确定结论"}],
                current_blocks=base,
                **_refuted_ctx(),
            ),
        )
    assert ei.value.code == "PAPER_CLAIM_STATUS_LOCKED"


def test_human_edit_path_is_not_blocked_by_claim_lock() -> None:
    """状态锁只约束 **AI patch**；人工编辑走 PUT /paper（actor=human）不受限。"""
    from research_vnext.core.paper.patch import lock_reason_for_claim_status

    assert lock_reason_for_claim_status("REFUTED", is_ai=True) is not None
    assert lock_reason_for_claim_status("REFUTED", is_ai=False) is None


# --------------------------------------------------------------------------
# 4 · evidence coverage
# --------------------------------------------------------------------------


def test_op_evidence_refs_must_resolve() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        apply_ops(
            BASE,
            validate_ops(
                [{"op": "update", "target": "p1", "expectedNodeRev": 1,
                  "node": {"id": "p1", "type": "paragraph", "text": "改"},
                  "rationale": "r", "evidenceRefs": ["ev-ghost"]}],
                current_blocks=BASE,
                resolvable_evidence={"ev-1"},
                resolvable_claims=set(),
                claim_status={},
                resolvable_citations=set(),
                resolvable_anchors=set(),
            ),
        )
    assert ei.value.code == "PAPER_EVIDENCE_UNRESOLVED"


def test_citation_ref_must_resolve_with_version() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "insert",
              "node": {"id": "c9", "type": "citation", "refs": ["cit-ghost"]},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_CITATION_UNRESOLVED"


def test_citation_version_mismatch_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "insert",
              "node": {"id": "c9", "type": "citation", "refs": ["cit-1"],
                       "versions": {"cit-1": "stalehash"}},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_CITATION_VERSION_MISMATCH"


def test_figure_anchor_must_resolve() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        _ok([{"op": "insert",
              "node": {"id": "f9", "type": "figure", "refs": ["anchor-ghost"],
                       "locator": {"page": 1}},
              "rationale": "r"}])
    assert ei.value.code == "PAPER_FIGURE_LOCATOR_UNRESOLVED"


# --------------------------------------------------------------------------
# 5 · 强结论标记词表（封闭常量，可测）
# --------------------------------------------------------------------------


def test_strong_conclusion_markers_are_a_closed_constant() -> None:
    assert has_strong_conclusion("本文证明了该方法最优")
    assert has_strong_conclusion("实验结果表明显著优于基线")
    assert not has_strong_conclusion("在部分场景下可能有所改善")
    assert not has_strong_conclusion("普通叙述句")


# --------------------------------------------------------------------------
# 6 · RV13.1 · apply_review：人工逐项处置与 ops 合并（索引稳定模型）
# --------------------------------------------------------------------------


from research_vnext.core.paper.patch import apply_review  # noqa: E402


_REVIEW_OPS = [
    {"op": "insert", "after": "p1",
     "node": {"id": "p-new", "type": "paragraph", "text": "其中 ρ = λ/μ。"},
     "rationale": "补参数定义"},
    {"op": "update", "target": "p1", "expectedNodeRev": 1,
     "node": {"id": "p1", "type": "paragraph", "text": "在部分场景下 W_q 可能有限。"},
     "rationale": "弱化表述"},
    {"op": "move", "target": "e1", "after": "p-new", "rationale": "公式移到定义之后"},
]


def test_review_none_returns_unchanged_copy() -> None:
    out = apply_review(_REVIEW_OPS, None)
    assert out == _REVIEW_OPS
    assert out is not _REVIEW_OPS
    assert apply_review(_REVIEW_OPS, {}) == _REVIEW_OPS


def test_review_reject_removes_only_that_op() -> None:
    out = apply_review(_REVIEW_OPS, {"rejected": [0]})
    assert [o["op"] for o in out] == ["update", "move"]
    # 索引稳定：move 原样保留（不做下标重排）


def test_review_reject_of_insert_breaks_later_reference() -> None:
    # move 的 after 指向被拒绝 insert 创建的 p-new ⇒ 断链必须被拦
    with pytest.raises(PaperSchemaError) as ei:
        apply_review(_REVIEW_OPS, {"rejected": [0], "accepted": [1, 2]})
    assert ei.value.code == "PAPER_REVIEW_BREAKS_SEQUENCE"


def test_review_reject_of_base_target_reference_is_fine() -> None:
    # update 指向基线节点 p1（非 review 产物）：拒绝别的 op 不影响它
    out = apply_review(_REVIEW_OPS, {"rejected": [2], "accepted": [0, 1]})
    assert len(out) == 2


def test_review_accept_reject_conflict_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        apply_review(_REVIEW_OPS, {"accepted": [1], "rejected": [1]})
    assert ei.value.code == "PAPER_REVIEW_CONFLICT"


def test_review_index_bounds() -> None:
    # 数组内坏元素（越界 / 非整数 / bool / 浮点 / 字符串元素）→ INDEX_INVALID
    for bad in ([-1], [3], ["0"], [True], ["x"], None if False else [1.5]):
        with pytest.raises(PaperSchemaError) as ei:
            apply_review(_REVIEW_OPS, {"rejected": bad})
        assert ei.value.code == "PAPER_REVIEW_INDEX_INVALID"
    # accepted/rejected 整体不是数组 → INVALID（形状错误，不是索引错误）
    with pytest.raises(PaperSchemaError) as ei:
        apply_review(_REVIEW_OPS, {"rejected": "0"})
    assert ei.value.code == "PAPER_REVIEW_INVALID"
    with pytest.raises(PaperSchemaError) as ei:
        apply_review(_REVIEW_OPS, {"edits": [{"index": 9, "text": "x"}]})
    assert ei.value.code == "PAPER_REVIEW_INDEX_INVALID"


def test_review_edit_applies_text_and_rationale() -> None:
    out = apply_review(
        _REVIEW_OPS,
        {"edits": [{"index": 1, "text": "在多数场景下 W_q 有限。", "rationale": "人工改写"}]},
    )
    assert out[1]["node"]["text"] == "在多数场景下 W_q 有限。"
    assert out[1]["rationale"] == "人工改写"
    assert out[0]["node"]["text"] == "其中 ρ = λ/μ。"  # 未编辑项不受影响


def test_review_edit_sanitizes_text() -> None:
    out = apply_review(
        _REVIEW_OPS,
        {"edits": [{"index": 1, "text": "<b>保持</b>中性"}]},
    )
    assert "<" not in out[1]["node"]["text"]


def test_review_edit_forbids_structural_fields() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        apply_review(_REVIEW_OPS, {"edits": [{"index": 0, "type": "heading"}]})
    assert ei.value.code == "PAPER_REVIEW_FIELD_FORBIDDEN"


def test_review_edit_on_nodeless_op_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        apply_review(_REVIEW_OPS, {"edits": [{"index": 2, "text": "move 没有正文"}]})
    assert ei.value.code == "PAPER_REVIEW_FIELD_FORBIDDEN"


def test_review_edit_equation_latex_revalidated_and_canonical_invalidated() -> None:
    ops = [
        {"op": "insert", "node": {"id": "e9", "type": "equation", "latex": "y = x"},
         "rationale": "r"},
    ]
    ok = apply_review(ops, {"edits": [{"index": 0, "latex": "z = \\frac{1}{2}x"}]})
    assert ok[0]["node"]["latex"] == "z = \\frac{1}{2}x"
    with pytest.raises(PaperSchemaError) as ei:
        apply_review(ops, {"edits": [{"index": 0, "latex": "\\frac{意外"}]})
    assert ei.value.code == "PAPER_FORMULA_UNPARSABLE"


def test_review_all_rejected_yields_empty() -> None:
    out = apply_review(_REVIEW_OPS, {"rejected": [0, 1, 2]})
    assert out == []
