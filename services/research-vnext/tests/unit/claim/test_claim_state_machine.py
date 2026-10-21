"""RV12 · claim 状态机的**确定性 Gate** 测试。

这里每一条都是任务书点名的"必须先失败"的判据：
无 evidence 的 SOLVED、摘要支持过强 claim、引用 stale/retracted/rights unknown、
反例命中、敏感性失败、不可复现实验、矛盾证据、未知 verifier、revision conflict。

设计原则（决定了为什么这些断言长这样）：
**判据必须在领域层是"预先定义"的，而不是模型或人当场发明的。**
因此每条断言都钉住一个**判据码**（criterion code），而不是钉住一句解释文字——
解释可以改，判据码不能悄悄消失。
"""

from __future__ import annotations

import pytest

from research_vnext.core.claim.state_machine import (
    CRITERIA,
    derive_status,
    evaluate_gate,
    validate_transition,
)
from research_vnext.core.claim.types import ClaimStatus

from .factories import (
    counter_evidence_view,
    evidence_link_view,
    experiment_link_view,
    solved_snapshot,
    verification_view,
)


def _codes(decision) -> set[str]:
    return set(decision.satisfied) | set(decision.missing) | set(decision.conditions)


# --------------------------------------------------------------------------
# 1. SOLVED 必须满足预先定义的 evidence + verification 判据
# --------------------------------------------------------------------------


def test_solved_requires_at_least_one_usable_evidence_link() -> None:
    snap = solved_snapshot(evidence_links=[], support_band="NONE", quality="UNSUPPORTED")
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "EVIDENCE_LINK_PRESENT" in d.missing
    assert d.status == ClaimStatus.INCONCLUSIVE.value


def test_all_gate_criteria_have_an_explicit_definition() -> None:
    """判据必须是**表**，不是散落在 if 里的魔法字符串。"""
    assert len(CRITERIA) >= 8
    for code, spec in CRITERIA.items():
        assert spec.code == code
        assert spec.description, f"{code} 缺少可读定义"
        assert isinstance(spec.required_for, tuple) and spec.required_for


def test_happy_path_is_solved_and_lists_every_satisfied_criterion() -> None:
    d = derive_status(solved_snapshot())
    assert d.status == ClaimStatus.SOLVED.value
    assert d.gate_id == "G_SOLVED"
    assert d.missing == ()
    assert set(d.satisfied) >= {
        "EVIDENCE_LINK_PRESENT",
        "EVIDENCE_ALL_USABLE",
        "EVIDENCE_TYPE_SUPPORTS_CLAIM_KIND",
        "SUPPORT_BAND_MIN_MODERATE",
        "EXPERIMENT_REPRODUCIBLE",
        "VERIFICATION_PASS_PRESENT",
        "VERIFICATION_NO_FAIL",
        "VERIFICATION_COUNTEREXAMPLE_CLEAR",
        "NO_COUNTER_EVIDENCE",
        "REFERENCES_FRESH",
    }


# --------------------------------------------------------------------------
# 2. 支持过强 / 类型不匹配
# --------------------------------------------------------------------------


def test_abstract_evidence_cannot_carry_a_solved_claim() -> None:
    """摘要撑不住逐字引用：类型判据必须不满足，且给出机读原因。"""
    snap = solved_snapshot(
        claim_kind="VERBATIM_QUOTE",
        support_band="NONE",
        quality="UNSUPPORTED",
        evidence_links=[
            evidence_link_view(
                evidence_id="ev-abstract",
                claim_kind="VERBATIM_QUOTE",
                band="NONE",
                score=0.0,
                blocking=("EVIDENCE_TYPE_INCOMPATIBLE",),
            )
        ],
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "EVIDENCE_TYPE_SUPPORTS_CLAIM_KIND" in d.missing


def test_support_band_below_moderate_is_never_solved() -> None:
    snap = solved_snapshot(support_band="WEAK", quality="PARTIAL")
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "SUPPORT_BAND_MIN_MODERATE" in d.missing


# --------------------------------------------------------------------------
# 3. 引用失效/撤权/未知权利
# --------------------------------------------------------------------------


def test_stale_or_invalidated_evidence_blocks_solved_and_flags_review() -> None:
    snap = solved_snapshot(
        quality="UNSUPPORTED",
        evidence_links=[
            evidence_link_view(
                evidence_id="ev-stale",
                usable=False,
                band="NONE",
                score=0.0,
                blocking=("EVIDENCE_NOT_USABLE",),
            )
        ],
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "EVIDENCE_ALL_USABLE" in d.missing
    assert d.requires_review is True
    assert "EVIDENCE_NOT_USABLE" in d.review_reasons


def test_rights_unknown_evidence_blocks_solved() -> None:
    snap = solved_snapshot(
        quality="UNSUPPORTED",
        evidence_links=[
            evidence_link_view(
                evidence_id="ev-rights",
                band="NONE",
                score=0.0,
                blocking=("RIGHTS_UNKNOWN",),
            )
        ],
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert d.requires_review is True
    assert "RIGHTS_UNKNOWN" in d.review_reasons


def test_retracted_evidence_blocks_solved() -> None:
    snap = solved_snapshot(
        quality="UNSUPPORTED",
        evidence_links=[
            evidence_link_view(
                evidence_id="ev-retracted",
                usable=False,
                band="NONE",
                score=0.0,
                blocking=("SOURCE_RETRACTED",),
            )
        ],
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "SOURCE_RETRACTED" in d.review_reasons


# --------------------------------------------------------------------------
# 4. 反例命中 ⇒ REFUTED，且反证必须被保留
# --------------------------------------------------------------------------


def test_counterexample_hit_refutes_instead_of_solving() -> None:
    snap = solved_snapshot(
        counter_evidence=[counter_evidence_view()],
        verifications=[
            verification_view(record_id="ver-math", kind="MATH_CHECK", result="PASS"),
            verification_view(
                record_id="ver-cex",
                kind="COUNTEREXAMPLE",
                result="FAIL",
                criteria_satisfied=False,
                blocking=("COUNTEREXAMPLE_REPRODUCED",),
            ),
        ],
        quality="CONFLICTED",
    )
    d = derive_status(snap)
    assert d.status == ClaimStatus.REFUTED.value
    assert d.gate_id == "G_REFUTED"
    # 反证必须原样保留在判定里（不能"因为结论变了就把它删掉"）
    assert any("反例" in w for w in d.refutation)


def test_refuted_requires_a_witness_not_just_a_bad_mood() -> None:
    """没有反证见证物时不得判 REFUTED —— 只能 INCONCLUSIVE/CONDITIONAL。"""
    snap = solved_snapshot(counter_evidence=[], quality="UNSUPPORTED", evidence_links=[], support_band="NONE")
    d = derive_status(snap)
    assert d.status != ClaimStatus.REFUTED.value


# --------------------------------------------------------------------------
# 5. 敏感性失败 / 不可复现实验 ⇒ 不得 SOLVED
# --------------------------------------------------------------------------


def test_sensitivity_failure_downgrades_to_conditional_with_condition_listed() -> None:
    snap = solved_snapshot(
        verifications=[
            verification_view(record_id="ver-math", kind="MATH_CHECK", result="PASS"),
            verification_view(
                record_id="ver-sens",
                kind="SENSITIVITY",
                result="FAIL",
                criteria_satisfied=False,
                blocking=("SENSITIVITY_UNSTABLE",),
            ),
        ]
    )
    d = derive_status(snap)
    assert d.status == ClaimStatus.CONDITIONAL.value
    assert "SENSITIVITY_STABLE" in d.missing
    assert any("SENSITIVITY_UNSTABLE" in c for c in d.conditions)


def test_not_reproducible_experiment_blocks_solved() -> None:
    snap = solved_snapshot(
        experiment_links=[
            experiment_link_view(
                run_id="run-norepro",
                reproducible=False,
                repro_missing=("imageDigest", "dependencyLockHash"),
            )
        ]
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "EXPERIMENT_REPRODUCIBLE" in d.missing
    assert d.requires_review is True
    assert "EXPERIMENT_NOT_REPRODUCIBLE" in d.review_reasons


def test_exit_code_zero_alone_is_not_verification() -> None:
    """`exit 0` 只说明程序跑完 —— 没有 VerificationRecord 时绝不是 SOLVED。"""
    snap = solved_snapshot(
        verifications=[],
        experiment_links=[experiment_link_view(exit_code=0, reproducible=True)],
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "VERIFICATION_PASS_PRESENT" in d.missing


# --------------------------------------------------------------------------
# 6. 矛盾证据 / 未知 verifier / revision conflict
# --------------------------------------------------------------------------


def test_conflicting_evidence_marks_requires_review_and_never_solved() -> None:
    snap = solved_snapshot(
        quality="CONFLICTED",
        counter_evidence=[counter_evidence_view(ref_id="ce-2", source_kind="EVIDENCE")],
        evidence_links=[
            evidence_link_view(evidence_id="ev-ok"),
            evidence_link_view(evidence_id="ev-contra", role="REFUTES", band="MODERATE", score=0.7),
        ],
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert d.requires_review is True
    assert "NO_COUNTER_EVIDENCE" in d.missing
    assert "CONFLICTED_EVIDENCE" in d.review_reasons


def test_unknown_verifier_result_is_never_treated_as_pass() -> None:
    snap = solved_snapshot(
        verifications=[
            verification_view(
                record_id="ver-unknown",
                kind="ROBUSTNESS",
                result="UNKNOWN",
                criteria_satisfied=False,
                independent=False,
                blocking=("VERIFIER_UNKNOWN",),
            )
        ]
    )
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "VERIFICATION_PASS_PRESENT" in d.missing
    assert d.requires_review is True
    assert "VERIFICATION_UNKNOWN" in d.review_reasons


def test_revision_conflict_blocks_solved_and_requires_review() -> None:
    snap = solved_snapshot(revision_fresh=False, reference_conflicts=("evidence:ev-1@v1",))
    d = derive_status(snap)
    assert d.status != ClaimStatus.SOLVED.value
    assert "REFERENCES_FRESH" in d.missing
    assert d.requires_review is True
    assert "REFERENCE_STALE" in d.review_reasons


# --------------------------------------------------------------------------
# 7. 迁移合法性 + 显式 Gate
# --------------------------------------------------------------------------


def test_validate_transition_rejects_solved_without_gate() -> None:
    gate = evaluate_gate(solved_snapshot(evidence_links=[], support_band="NONE", quality="UNSUPPORTED"))
    with pytest.raises(Exception) as ei:
        validate_transition(
            current=ClaimStatus.INCONCLUSIVE.value,
            target=ClaimStatus.SOLVED.value,
            gate=gate,
        )
    assert getattr(ei.value, "code", "") == "CLAIM_TRANSITION_REJECTED"
    assert "EVIDENCE_LINK_PRESENT" in ei.value.detail["missing"]


def test_validate_transition_accepts_solved_with_satisfied_gate() -> None:
    gate = evaluate_gate(solved_snapshot())
    decision = validate_transition(
        current=ClaimStatus.INCONCLUSIVE.value,
        target=ClaimStatus.SOLVED.value,
        gate=gate,
    )
    assert decision is gate
    assert decision.gate_id == "G_SOLVED"


def test_refuted_to_solved_requires_counterevidence_addressed() -> None:
    raising = evaluate_gate(solved_snapshot())
    try:
        validate_transition(
            current=ClaimStatus.REFUTED.value,
            target=ClaimStatus.SOLVED.value,
            gate=raising,
            counterevidence_addressed=False,
        )
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", "") == "CLAIM_COUNTEREVIDENCE_UNRESOLVED"
    else:  # pragma: no cover - 明确失败而非静默通过
        raise AssertionError("REFUTED → SOLVED 未解决反证时必须被拒")


def test_unknown_status_is_rejected() -> None:
    try:
        validate_transition(current="MAYBE", target=ClaimStatus.SOLVED.value, gate=evaluate_gate(solved_snapshot()))
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", "") == "CLAIM_STATUS_UNKNOWN"
    else:  # pragma: no cover
        raise AssertionError("未知状态必须被拒")


def test_gate_decision_is_total_and_machine_readable() -> None:
    d = derive_status(solved_snapshot())
    payload = d.to_dict()
    for key in ("status", "gateId", "satisfied", "missing", "conditions", "refutation", "requiresReview", "reviewReasons"):
        assert key in payload
    assert isinstance(payload["reviewReasons"], list)


def test_conditions_are_listed_for_conditional() -> None:
    snap = solved_snapshot(
        verifications=[verification_view(record_id="v1", kind="MATH_CHECK", result="UNKNOWN", criteria_satisfied=False, independent=True)]
    )
    d = derive_status(snap)
    assert d.status == ClaimStatus.CONDITIONAL.value
    assert d.conditions, "CONDITIONAL 必须列出条件"


def test_gaps_are_listed_for_inconclusive() -> None:
    d = derive_status(solved_snapshot(evidence_links=[], support_band="NONE", quality="UNSUPPORTED", verifications=[]))
    assert d.status == ClaimStatus.INCONCLUSIVE.value
    assert d.missing, "INCONCLUSIVE 必须列出缺口"
