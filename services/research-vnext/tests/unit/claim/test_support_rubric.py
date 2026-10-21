"""RV12 · 证据支持强度 rubric 测试。

核心主张（任务书 §5）：**支持强度必须按 type / rights / locator / freshness
算出可解释结果，而不是只用一个 LLM 分数。** 因此这里断言的是：
每个因子都有名字、有观测值、有理由码、有权重，且权重之和为 1。
"""

from __future__ import annotations

import pytest

from research_vnext.core.claim.support import (
    FACTOR_WEIGHTS,
    aggregate_support,
    assess_evidence_support,
)

from .factories import NOW, evidence_view


def _factor(assessment, code: str):
    for f in assessment.factors:
        if f.factor == code:
            return f
    raise AssertionError(f"缺少因子 {code}：{[f.factor for f in assessment.factors]}")


# --------------------------------------------------------------------------
# 因子完备性与可解释性
# --------------------------------------------------------------------------


def test_all_four_factors_are_present_and_weights_sum_to_one() -> None:
    a = assess_evidence_support(evidence_view(), claim_kind="PARAPHRASE", now=NOW)
    assert {f.factor for f in a.factors} == {"F_TYPE", "F_ELIGIBILITY", "F_LOCATOR", "F_FRESHNESS"}
    assert pytest.approx(sum(FACTOR_WEIGHTS.values()), abs=1e-9) == 1.0
    assert a.method == "rubric-v1"


def test_result_is_not_a_single_opaque_score() -> None:
    a = assess_evidence_support(evidence_view(), claim_kind="PARAPHRASE", now=NOW)
    payload = a.to_dict()
    assert payload["method"] == "rubric-v1"
    assert len(payload["factors"]) == 4
    for f in payload["factors"]:
        assert f["factor"] and f["value"] is not None and f["reason"]
        assert "weight" in f and "passed" in f


# --------------------------------------------------------------------------
# type 因子
# --------------------------------------------------------------------------


def test_abstract_cannot_support_verbatim_quote() -> None:
    a = assess_evidence_support(
        evidence_view(evidence_type="PAPER_ABSTRACT", source_record_kind="ABSTRACT"),
        claim_kind="VERBATIM_QUOTE",
        now=NOW,
    )
    f = _factor(a, "F_TYPE")
    assert f.passed is False
    assert f.reason == "EVIDENCE_TYPE_INCOMPATIBLE"
    assert "ABSTRACT_CANNOT_SUPPORT_VERBATIM" in a.blocking
    assert a.band == "NONE"


def test_full_text_supports_verbatim_quote() -> None:
    a = assess_evidence_support(evidence_view(), claim_kind="VERBATIM_QUOTE", now=NOW)
    assert _factor(a, "F_TYPE").passed is True
    assert a.band in {"STRONG", "MODERATE"}


def test_textbook_cannot_support_a_literature_paraphrase() -> None:
    a = assess_evidence_support(
        evidence_view(
            evidence_type="TEXTBOOK",
            source_record_kind="TEXTBOOK_CHUNK",
            locator={"kind": "EXTERNAL", "source_id": "s", "section_id": "c", "chunk_id": "1"},
            citation_eligible=False,
        ),
        claim_kind="PARAPHRASE",
        now=NOW,
    )
    assert _factor(a, "F_TYPE").passed is False
    assert "EVIDENCE_TYPE_INCOMPATIBLE" in a.blocking


def test_derived_without_upstream_is_blocked() -> None:
    a = assess_evidence_support(
        evidence_view(
            evidence_type="DERIVED",
            source_record_kind="DERIVED",
            upstream_card_ids=[],
        ),
        claim_kind="PARAPHRASE",
        now=NOW,
    )
    assert "DERIVED_REQUIRES_UPSTREAM" in a.blocking


# --------------------------------------------------------------------------
# rights 因子
# --------------------------------------------------------------------------


def test_unknown_rights_blocks_support_entirely() -> None:
    a = assess_evidence_support(
        evidence_view(rights_status="UNKNOWN"), claim_kind="PARAPHRASE", now=NOW
    )
    assert _factor(a, "F_ELIGIBILITY").passed is False
    assert "RIGHTS_UNKNOWN" in a.blocking
    assert a.band == "NONE" and a.score == 0.0


def test_restricted_rights_degrades_but_does_not_block() -> None:
    a = assess_evidence_support(
        evidence_view(rights_status="RESTRICTED", snippet="受限片段"), claim_kind="PARAPHRASE", now=NOW
    )
    f = _factor(a, "F_ELIGIBILITY")
    assert f.passed is True
    assert f.reason == "RIGHTS_RESTRICTED_LIMITED"
    assert "RIGHTS_UNKNOWN" not in a.blocking
    assert a.band in {"MODERATE", "WEAK"}


# --------------------------------------------------------------------------
# 生命周期 / 撤稿
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["STALE", "INVALIDATED", "REJECTED", "PROPOSED"])
def test_non_active_evidence_is_not_usable(status: str) -> None:
    a = assess_evidence_support(
        evidence_view(status=status), claim_kind="PARAPHRASE", now=NOW
    )
    assert "EVIDENCE_NOT_USABLE" in a.blocking
    assert a.band == "NONE"


def test_stale_state_blocks_support() -> None:
    a = assess_evidence_support(
        evidence_view(stale_state="STALE"), claim_kind="PARAPHRASE", now=NOW
    )
    assert "EVIDENCE_STALE" in a.blocking


def test_retracted_source_blocks_support() -> None:
    a = assess_evidence_support(
        evidence_view(retracted=True), claim_kind="PARAPHRASE", now=NOW
    )
    assert "SOURCE_RETRACTED" in a.blocking


# --------------------------------------------------------------------------
# locator 因子
# --------------------------------------------------------------------------


def test_missing_locator_blocks_support() -> None:
    a = assess_evidence_support(evidence_view(locator={}), claim_kind="PARAPHRASE", now=NOW)
    assert _factor(a, "F_LOCATOR").passed is False
    assert "LOCATOR_UNRESOLVABLE" in a.blocking


def test_stale_locator_blocks_support() -> None:
    a = assess_evidence_support(
        evidence_view(locator_stale=True), claim_kind="PARAPHRASE", now=NOW
    )
    assert "LOCATOR_STALE" in a.blocking


# --------------------------------------------------------------------------
# freshness 因子
# --------------------------------------------------------------------------


def test_recent_evidence_is_fresh() -> None:
    a = assess_evidence_support(evidence_view(age_days=10), claim_kind="PARAPHRASE", now=NOW)
    f = _factor(a, "F_FRESHNESS")
    assert f.passed is True and f.reason == "FRESH"


def test_aging_evidence_loses_band() -> None:
    a = assess_evidence_support(evidence_view(age_days=300), claim_kind="PARAPHRASE", now=NOW)
    f = _factor(a, "F_FRESHNESS")
    assert f.passed is False
    assert f.reason == "EVIDENCE_AGING"
    assert "EVIDENCE_TOO_OLD" not in a.blocking  # 老化是降级，不是"证据无效"
    assert a.band in {"MODERATE", "WEAK"}


def test_expired_evidence_is_blocked() -> None:
    a = assess_evidence_support(evidence_view(age_days=900), claim_kind="PARAPHRASE", now=NOW)
    assert _factor(a, "F_FRESHNESS").reason == "EVIDENCE_TOO_OLD"
    assert "EVIDENCE_EXPIRED" in a.blocking


def test_unparsable_retrieved_at_is_unknown_not_fresh() -> None:
    a = assess_evidence_support(
        evidence_view(retrieved_at=None) | {"retrieved_at": "not-a-date"},
        claim_kind="PARAPHRASE",
        now=NOW,
    )
    assert _factor(a, "F_FRESHNESS").reason == "RETRIEVED_AT_UNKNOWN"


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------


def test_aggregate_reports_conflicted_when_refuting_link_exists() -> None:
    keep = assess_evidence_support(evidence_view(evidence_id="ev-1"), claim_kind="PARAPHRASE", now=NOW)
    refute = assess_evidence_support(
        evidence_view(evidence_id="ev-2"), claim_kind="PARAPHRASE", now=NOW, role="REFUTES"
    )
    agg = aggregate_support([keep], refuting=[refute])
    assert agg.quality == "CONFLICTED"
    assert agg.conflicted is True
    assert agg.best_band in {"STRONG", "MODERATE"}


def test_aggregate_is_unsupported_without_usable_support() -> None:
    blocked = assess_evidence_support(
        evidence_view(rights_status="UNKNOWN"), claim_kind="PARAPHRASE", now=NOW
    )
    agg = aggregate_support([blocked], refuting=[])
    assert agg.quality == "UNSUPPORTED"
    assert agg.best_band == "NONE"


def test_aggregate_takes_the_strongest_support_not_the_average() -> None:
    strong = assess_evidence_support(
        evidence_view(evidence_id="ev-strong"), claim_kind="PARAPHRASE", now=NOW
    )
    weak = assess_evidence_support(
        evidence_view(evidence_id="ev-weak", rights_status="RESTRICTED"),
        claim_kind="PARAPHRASE",
        now=NOW,
    )
    agg = aggregate_support([strong, weak], refuting=[])
    assert agg.quality == "SUPPORTED"
    assert agg.best_band == "STRONG"
