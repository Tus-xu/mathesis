"""RV15 · Review review domain 单元测试（**先红后绿**：模块存在性之前的 RED 证据）。

覆盖任务书要求的失败测试负例（单元层）：
- 无 locator 的 finding 拒绝
- 无 evidence 的强结论 finding 拒绝
- taxonomy 封闭集（10 类）+ 文档与代码一致
- 状态机：非法迁移、RESOLVED 无修复修订、WAIVED 无理由/无期限/无批准
- 指纹去重：同指纹不复制、RESOLVED 不被同指纹重开、修复后重版重开
- Reviewer AI 越权：mutate target / 删反证 / 无限期豁免
"""

from __future__ import annotations

import pytest


# ==========================================================================
# RED 哨兵：模块不存在时收集即失败（先红）
# ==========================================================================


def _review():
    from research_vnext.core import review

    return review


# --------------------------------------------------------------------------
# 1 · taxonomy
# --------------------------------------------------------------------------


def test_taxonomy_contains_all_ten_categories() -> None:
    review = _review()
    expected = {
        "EVIDENCE_GAP", "CITATION_ERROR", "OVERCLAIM", "MATH_ERROR",
        "METHOD_RISK", "REPRODUCIBILITY", "CONTRADICTION", "RIGHTS_PRIVACY",
        "EXPORT_MISMATCH", "ACCESSIBILITY",
    }
    assert expected <= set(review.FINDING_CATEGORIES), sorted(review.FINDING_CATEGORIES)
    assert set(review.FINDING_CATEGORIES) == expected, "封闭集：不多不少"


def test_taxonomy_severity_defaults_cover_every_category() -> None:
    review = _review()
    for cat in review.FINDING_CATEGORIES:
        assert review.SEVERITY_DEFAULTS[cat] in review.SEVERITIES, cat


def test_finding_proposal_rejects_unknown_category() -> None:
    review = _review()
    with pytest.raises(Exception) as ei:
        review.validate_finding_proposal({"taxonomy": "NOT_A_KIND", "target": {...: 1}})  # type: ignore[dict-item]
    assert getattr(ei.value, "code", "") == "REVIEW_TAXONOMY_UNKNOWN"


# --------------------------------------------------------------------------
# 2 · 提案 schema 门（locator / evidence）
# --------------------------------------------------------------------------


def _base_proposal(**over):
    proposal = {
        "taxonomy": "MATH_ERROR",
        "severity": None,
        "target": {"type": "paper_node", "id": "eq1", "version": 3, "locator": {"blockIndex": 4}},
        "description": "公式与 VerificationRecord FAIL 判定矛盾",
        "evidence": [
            {"kind": "verification", "ref": "vr-123",
             "summary": "记录 vr-123 判定 lhs != rhs"},
        ],
    }
    proposal.update(over)
    return proposal


def test_finding_without_locator_is_rejected() -> None:
    review = _review()
    p = _base_proposal()
    p["target"] = {"type": "paper_node", "id": "eq1", "version": 3}  # 无 locator
    with pytest.raises(Exception) as ei:
        review.validate_finding_proposal(p)
    assert getattr(ei.value, "code", "") == "REVIEW_LOCATOR_REQUIRED"


def test_finding_with_empty_locator_is_rejected() -> None:
    review = _review()
    p = _base_proposal()
    p["target"]["locator"] = {}
    with pytest.raises(Exception) as ei:
        review.validate_finding_proposal(p)
    assert getattr(ei.value, "code", "") == "REVIEW_LOCATOR_REQUIRED"


def test_strong_finding_without_evidence_is_rejected() -> None:
    """OVERCLAIM / MATH_ERROR / CONTRADICTION / RIGHTS_PRIVACY 是强结论类：
    没有任何 evidence 的提案必须被拒（不能只凭模型口气断言）。"""
    review = _review()
    for tax in ("OVERCLAIM", "MATH_ERROR", "CONTRADICTION", "RIGHTS_PRIVACY"):
        p = _base_proposal(taxonomy=tax, evidence=[])
        with pytest.raises(Exception) as ei:
            review.validate_finding_proposal(p)
        assert getattr(ei.value, "code", "") == "REVIEW_EVIDENCE_REQUIRED", tax


def test_weak_finding_still_needs_description() -> None:
    review = _review()
    p = _base_proposal(taxonomy="ACCESSIBILITY", evidence=[])
    p["description"] = ""
    with pytest.raises(Exception) as ei:
        review.validate_finding_proposal(p)
    assert getattr(ei.value, "code", "") == "REVIEW_DESCRIPTION_REQUIRED"


def test_proposal_cannot_supply_status_or_resolution() -> None:
    """Reviewer 只能 propose：status / resolution / waiver 是服务端域。"""
    review = _review()
    p = _base_proposal(status="RESOLVED")
    with pytest.raises(Exception) as ei:
        review.validate_finding_proposal(p)
    assert getattr(ei.value, "code", "") == "REVIEW_SERVER_OWNED_FIELD"
    p2 = _base_proposal(resolution={"revisionRef": 9})
    with pytest.raises(Exception) as ei:
        review.validate_finding_proposal(p2)
    assert getattr(ei.value, "code", "") == "REVIEW_SERVER_OWNED_FIELD"


# --------------------------------------------------------------------------
# 3 · 状态机
# --------------------------------------------------------------------------


def test_state_machine_illegal_transitions_rejected() -> None:
    review = _review()
    for src, dst in (
        ("RESOLVED", "IN_PROGRESS"),   # 终态不能回去（只能 REOPENED）
        ("WAIVED", "IN_PROGRESS"),
        ("OPEN", "RESOLVED"),          # 没认领就修：跳过审计链
        ("OPEN", "COMPLETED"),
        ("ACKNOWLEDGED", "RESOLVED"),  # 必须先 IN_PROGRESS
    ):
        with pytest.raises(Exception) as ei:
            review.validate_transition(src, dst)
        assert getattr(ei.value, "code", "") == "FINDING_TRANSITION_INVALID", (src, dst)


def test_state_machine_happy_path_allowed() -> None:
    review = _review()
    for src, dst in (
        ("OPEN", "ACKNOWLEDGED"), ("ACKNOWLEDGED", "IN_PROGRESS"),
        ("IN_PROGRESS", "RESOLVED"), ("IN_PROGRESS", "WAIVED"),
        ("RESOLVED", "REOPENED"), ("WAIVED", "REOPENED"),
        ("REOPENED", "IN_PROGRESS"),
    ):
        review.validate_transition(src, dst)  # 不抛即合法


def test_resolved_without_revision_ref_rejected() -> None:
    review = _review()
    with pytest.raises(Exception) as ei:
        review.validate_resolution({}, current_target_version=5)
    assert getattr(ei.value, "code", "") == "FINDING_RESOLUTION_NO_REVISION"


def test_resolved_with_stale_revision_rejected() -> None:
    """修复修订必须晚于发现时的 target version——旧修订不能冒充修复。"""
    review = _review()
    res = {"revisionRef": {"kind": "paper_spec", "revision": 3},
           "evidenceRefs": ["vr-1"], "note": "改了公式"}
    with pytest.raises(Exception) as ei:
        review.validate_resolution(res, current_target_version=7)
    assert getattr(ei.value, "code", "") == "FINDING_RESOLUTION_NO_REVISION"


def test_resolved_without_evidence_ref_rejected() -> None:
    review = _review()
    res = {"revisionRef": {"kind": "paper_spec", "revision": 9},
           "evidenceRefs": [], "note": "n"}
    with pytest.raises(Exception) as ei:
        review.validate_resolution(res, current_target_version=5)
    assert getattr(ei.value, "code", "") == "FINDING_RESOLUTION_NO_REVISION"


def test_waiver_requires_reason_expiry_approver() -> None:
    review = _review()
    with pytest.raises(Exception) as ei:
        review.validate_waiver({})
    assert getattr(ei.value, "code", "") == "FINDING_WAIVER_REASON_REQUIRED"

    with pytest.raises(Exception) as ei:
        review.validate_waiver({"reason": {"code": "NO_IMPACT", "text": "r"}})
    assert getattr(ei.value, "code", "") == "FINDING_WAIVER_EXPIRY_REQUIRED"

    with pytest.raises(Exception) as ei:
        review.validate_waiver({"reason": {"code": "NO_IMPACT", "text": "r"},
                                "expiry": "2099-01-01"})
    assert getattr(ei.value, "code", "") == "FINDING_WAIVER_APPROVER_REQUIRED"

    # 无期限（空 expiry）= 无限期豁免 = 拒绝
    with pytest.raises(Exception) as ei:
        review.validate_waiver({"reason": {"code": "NO_IMPACT", "text": "r"},
                                "expiry": "", "approvedBy": "human-1"})
    assert getattr(ei.value, "code", "") == "FINDING_WAIVER_EXPIRY_REQUIRED"


def test_waiver_expiry_must_be_in_the_future() -> None:
    review = _review()
    with pytest.raises(Exception) as ei:
        review.validate_waiver({"reason": {"code": "NO_IMPACT", "text": "r"},
                                "expiry": "2020-01-01", "approvedBy": "human-1"})
    assert getattr(ei.value, "code", "") == "FINDING_WAIVER_EXPIRY_INVALID"


def test_waiver_reason_code_closed_vocabulary() -> None:
    review = _review()
    with pytest.raises(Exception) as ei:
        review.validate_waiver({"reason": {"code": "BECAUSE_I_SAID_SO", "text": "r"},
                                "expiry": "2099-01-01", "approvedBy": "human-1"})
    assert getattr(ei.value, "code", "") == "FINDING_WAIVER_REASON_INVALID"


# --------------------------------------------------------------------------
# 4 · 指纹去重 / 重开
# --------------------------------------------------------------------------


def _fp_input(tax: str, tid: str, loc: dict, desc: str, ver: int = 3) -> dict:
    return {"taxonomy": tax, "target": {"type": "paper_node", "id": tid,
                                        "version": ver, "locator": loc},
            "description": desc}


def test_fingerprint_stable_and_distinct() -> None:
    review = _review()
    a = review.fingerprint_of(_fp_input("MATH_ERROR", "eq1", {"blockIndex": 4}, "公式矛盾"))
    b = review.fingerprint_of(_fp_input("MATH_ERROR", "eq1", {"blockIndex": 4}, "公式矛盾"))
    assert a == b
    c = review.fingerprint_of(_fp_input("MATH_ERROR", "eq1", {"blockIndex": 5}, "公式矛盾"))
    assert c != a, "不同 locator 必须不同指纹"


def test_dedupe_rule_does_not_duplicate_open_finding() -> None:
    review = _review()
    existing = {"status": "IN_PROGRESS", "targetVersion": 3, "id": "f1"}
    action = review.dedupe_action(
        review.fingerprint_of(_fp_input("MATH_ERROR", "eq1", {"blockIndex": 4}, "公式矛盾"), ),
        existing, new_target_version=3)
    assert action == "UPDATE_EXISTING"


def test_dedupe_rule_keeps_resolved_when_target_unchanged() -> None:
    review = _review()
    existing = {"status": "RESOLVED", "targetVersion": 3, "id": "f1"}
    action = review.dedupe_action(
        review.fingerprint_of(_fp_input("MATH_ERROR", "eq1", {"blockIndex": 4}, "公式矛盾")),
        existing, new_target_version=3)
    assert action == "KEEP_RESOLVED"


def test_dedupe_rule_reopens_resolved_after_target_moved() -> None:
    """修复后问题在同一位置再次出现（target 已前进）⇒ 重开，不新建第二行。"""
    review = _review()
    existing = {"status": "RESOLVED", "targetVersion": 3, "id": "f1"}
    action = review.dedupe_action(
        review.fingerprint_of(_fp_input("MATH_ERROR", "eq1", {"blockIndex": 4}, "公式矛盾")),
        existing, new_target_version=6)
    assert action == "REOPEN"


def test_dedupe_rule_reopens_expired_waiver() -> None:
    review = _review()
    existing = {"status": "WAIVED", "targetVersion": 3, "id": "f1",
                "waiver": {"expiry": "2020-01-01"}}
    action = review.dedupe_action(
        review.fingerprint_of(_fp_input("MATH_ERROR", "eq1", {"blockIndex": 4}, "公式矛盾")),
        existing, new_target_version=6)
    assert action == "REOPEN"


# --------------------------------------------------------------------------
# 5 · Reviewer AI 越权（类型层）
# --------------------------------------------------------------------------


def test_reviewer_tool_policy_forbids_mutation() -> None:
    review = _review()
    for op in ("MUTATE_PAPER", "MUTATE_CLAIM", "MUTATE_EXPERIMENT",
               "DELETE_COUNTEREVIDENCE", "MUTATE_EVIDENCE", "APPROVE_WAIVER",
               "RESOLVE_FINDING", "DELETE_FINDING"):
        with pytest.raises(Exception) as ei:
            review.assert_reviewer_operation_allowed(op)
        assert getattr(ei.value, "code", "") == "REVIEWER_POLICY_VIOLATION", op


def test_reviewer_tool_policy_allows_propose_only() -> None:
    review = _review()
    review.assert_reviewer_operation_allowed("PROPOSE_FINDING")
    review.assert_reviewer_operation_allowed("READ_TARGET")


# --------------------------------------------------------------------------
# 6 · 文档与代码一致（防漂移）
# --------------------------------------------------------------------------


def test_state_machine_docs_and_code_agree() -> None:
    from pathlib import Path

    review = _review()
    doc = Path(
        "D:/ppt-rescue-control/20260911-math-ai-platform-vnext/RV15/STATE-MACHINE.md"
    ).read_text(encoding="utf-8")
    # 解析文档中的迁移代码块：`SRC → A | B | C` 展开成对集合
    doc_pairs: set[tuple[str, str]] = set()
    for line in doc.splitlines():
        line = line.strip().strip("`")
        if "→" not in line or "|" not in line:
            continue
        src, _, rhs = line.partition("→")
        src = src.strip()
        if src not in review.FINDING_STATUSES:
            continue
        for dst in rhs.split("|"):
            dst = dst.strip()
            if dst in review.FINDING_STATUSES:
                doc_pairs.add((src, dst))
    code_pairs = {
        (src, dst) for src in review.FINDING_STATUSES
        for dst in review.ALLOWED_TRANSITIONS[src]
    }
    assert doc_pairs == code_pairs, {
        "code_only": code_pairs - doc_pairs,
        "doc_only": doc_pairs - code_pairs,
    }


def test_blocking_docs_and_code_agree() -> None:
    review = _review()
    blocking = review.BLOCKING_SEVERITIES
    assert set(blocking) == {"P0", "P1"}
