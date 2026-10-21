"""RV12 · claim / verification 的领域规则（单元，无需数据库）。

核心断言（对应任务书的硬要求）：
- `exit_code == 0` **绝不**等于"结论成立"；
- `UNKNOWN` **绝不**被强制转成 `PASS`；
- 失效证据 / 不可复现实验可以**重开** SOLVED claim；
- claim 状态与支持质量**正交**。
"""

from __future__ import annotations

import pytest

from research_vnext.core.domain import rv_domain_rules as R


def test_exit_code_zero_does_not_mean_claim_holds() -> None:
    """最重要的一条：程序跑完 ≠ 假设成立。"""
    verdict = R.evaluate_run(
        exit_code=0,
        repro={
            "imageDigest": "sha256:abc",
            "dependencyLockHash": "lock-1",
            "seed": 42,
            "codeHash": "code-1",
        },
    )
    assert verdict["exit_code"] == 0
    assert verdict["claim_holds"] is None, "exit_code==0 不得被解释为结论成立"
    assert verdict["reproducible"] is True


def test_nonzero_exit_code_also_does_not_mean_claim_false() -> None:
    """反方向同样成立：退出码非 0 也不代表结论被证伪。"""
    verdict = R.evaluate_run(exit_code=1, repro={"imageDigest": "d", "dependencyLockHash": "l",
                                                 "seed": 1, "codeHash": "c"})
    assert verdict["claim_holds"] is None


@pytest.mark.parametrize("missing", ["imageDigest", "dependencyLockHash", "seed", "codeHash"])
def test_missing_repro_metadata_means_not_reproducible(missing: str) -> None:
    repro = {
        "imageDigest": "d",
        "dependencyLockHash": "l",
        "seed": 1,
        "codeHash": "c",
    }
    repro.pop(missing)
    verdict = R.evaluate_run(exit_code=0, repro=repro)
    assert verdict["reproducible"] is False
    assert missing in verdict["not_reproducible_reason"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PASS", "PASS"),
        ("pass", "PASS"),
        (" FAIL ", "FAIL"),
        ("unknown", "UNKNOWN"),
        ("passed", "UNKNOWN"),  # 拼错不得被当成通过
        ("ok", "UNKNOWN"),
        ("", "UNKNOWN"),
        (None, "UNKNOWN"),
        ("true", "UNKNOWN"),
        ("1", "UNKNOWN"),
    ],
)
def test_verification_result_is_fail_closed(raw: object, expected: str) -> None:
    assert R.normalize_verification_result(raw) == expected  # type: ignore[arg-type]


def test_unknown_never_becomes_pass() -> None:
    """显式锁定方向：任何无法识别的输入都落 UNKNOWN，而不是 PASS。"""
    for raw in ("maybe", "n/a", "unverified", "TBD", "sí"):
        assert R.normalize_verification_result(raw) == "UNKNOWN"


def test_unknown_verification_does_not_count_as_support() -> None:
    quality = R.derive_support_quality(
        evidence_refs=["e1"],
        valid_evidence_ids={"e1"},
        verification_results=["UNKNOWN"],
    )
    assert quality == "PARTIAL", "有证据但只有 UNKNOWN ⇒ PARTIAL（不是 SUPPORTED）"


def test_any_fail_verification_makes_claim_conflicted() -> None:
    quality = R.derive_support_quality(
        evidence_refs=["e1"],
        valid_evidence_ids={"e1"},
        verification_results=["PASS", "FAIL"],
    )
    assert quality == "CONFLICTED"


def test_no_usable_evidence_is_unsupported() -> None:
    assert (
        R.derive_support_quality(evidence_refs=["gone"], valid_evidence_ids=set(), verification_results=["PASS"])
        == "UNSUPPORTED"
    )


def test_solved_requires_valid_evidence() -> None:
    with pytest.raises(R.DomainRuleViolation) as ei:
        R.validate_claim_transition(
            current_status="INCONCLUSIVE",
            new_status="SOLVED",
            evidence_refs=["gone"],
            valid_evidence_ids=set(),
            support_quality="SUPPORTED",
        )
    assert ei.value.code == "CLAIM_SOLVED_WITHOUT_EVIDENCE"


def test_solved_rejected_when_conflicted() -> None:
    with pytest.raises(R.DomainRuleViolation) as ei:
        R.validate_claim_transition(
            current_status="INCONCLUSIVE",
            new_status="SOLVED",
            evidence_refs=["e1"],
            valid_evidence_ids={"e1"},
            support_quality="CONFLICTED",
        )
    assert ei.value.code == "CLAIM_SOLVED_WITH_WEAK_SUPPORT"


def test_solved_allowed_with_valid_support() -> None:
    R.validate_claim_transition(
        current_status="INCONCLUSIVE",
        new_status="SOLVED",
        evidence_refs=["e1"],
        valid_evidence_ids={"e1"},
        support_quality="SUPPORTED",
    )


# --------------------------------------------------------------------------
# 重开
# --------------------------------------------------------------------------


def test_invalidated_evidence_reopens_solved_claim() -> None:
    d = R.reopen_decision(
        claim_status="SOLVED",
        evidence_refs=["e1"],
        valid_evidence_ids=set(),  # e1 已失效
        reproducible=True,
    )
    assert d["reopen"] is True
    assert d["reason"] == R.REOPEN_EVIDENCE_INVALIDATED
    assert d["to_status"] == "INCONCLUSIVE", "证据消失是'不再成立'，不是'被证伪'"


def test_non_reproducible_experiment_reopens_solved_claim() -> None:
    d = R.reopen_decision(
        claim_status="SOLVED",
        evidence_refs=["e1"],
        valid_evidence_ids={"e1"},
        reproducible=False,
    )
    assert d["reopen"] is True
    assert d["reason"] == R.REOPEN_NOT_REPRODUCIBLE
    assert d["to_status"] == "INCONCLUSIVE"


def test_failed_verification_marks_reopen_for_review() -> None:
    eff = R.claim_effect_of_verification("FAIL")
    assert eff["reopen"] is True
    assert eff["reason"] == R.REOPEN_VERIFICATION_FAILED


def test_unknown_verification_changes_nothing() -> None:
    eff = R.claim_effect_of_verification("UNKNOWN")
    assert eff["reopen"] is False
    assert eff["action"] == "no_evidence"


def test_solved_claim_with_intact_evidence_is_not_reopened() -> None:
    d = R.reopen_decision(
        claim_status="SOLVED",
        evidence_refs=["e1"],
        valid_evidence_ids={"e1"},
        reproducible=True,
    )
    assert d["reopen"] is False


def test_non_solved_claim_is_never_reopened() -> None:
    for status in ("INCONCLUSIVE", "REFUTED", "CONDITIONAL"):
        d = R.reopen_decision(
            claim_status=status, evidence_refs=[], valid_evidence_ids=set(), reproducible=False
        )
        assert d["reopen"] is False, status


# --------------------------------------------------------------------------
# PaperSpec block 树
# --------------------------------------------------------------------------


def test_paper_patch_requires_matching_base_revision() -> None:
    with pytest.raises(R.DomainRuleViolation) as ei:
        R.apply_paper_patch([], [{"id": "b1", "type": "paragraph", "text": "x"}],
                            base_revision=1, current_revision=3)
    assert ei.value.code == "PAPER_BASE_REVISION_STALE"


def test_paper_patch_applies_on_matching_revision() -> None:
    out = R.apply_paper_patch(
        [{"id": "b1", "type": "paragraph", "text": "old"}],
        [{"id": "b1", "type": "paragraph", "text": "new"},
         {"id": "b2", "type": "equation", "text": "E=mc^2"}],
        base_revision=1,
        current_revision=1,
    )
    assert [b["id"] for b in out] == ["b1", "b2"]
    assert out[0]["text"] == "new"


def test_claim_status_and_support_are_orthogonal() -> None:
    """状态与支持质量是**两个**维度：SOLVED 不等于 SUPPORTED，反之亦然。"""
    assert set(R.CLAIM_STATUSES) == {"SOLVED", "CONDITIONAL", "INCONCLUSIVE", "REFUTED"}
    assert set(R.CLAIM_SUPPORT) == {"UNSUPPORTED", "PARTIAL", "SUPPORTED", "CONFLICTED"}
    assert not (set(R.CLAIM_STATUSES) & set(R.CLAIM_SUPPORT)), "两个集合不得共用取值"
