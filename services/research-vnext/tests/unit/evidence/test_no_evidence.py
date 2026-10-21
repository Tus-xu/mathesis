"""RV07 · 无证据行为：UNKNOWN/UNSUPPORTED 绝不变成可引用结论。

任务书要求 8：建立无证据拒答/降级语义。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence import no_evidence as NE  # noqa: E402
from research_vnext.core.evidence.errors import NoEvidenceError  # noqa: E402
from research_vnext.core.evidence.types import (  # noqa: E402
    ClaimKind,
    EvidenceStatus,
    NoEvidenceVerdict,
    StaleState,
)
from tests.unit.evidence import factories as F  # noqa: E402


def test_no_cards_yields_unknown_and_not_citable() -> None:
    s = NE.assess([], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.UNKNOWN
    assert s.citation_allowed is False
    assert NE.Reason.NO_EVIDENCE in s.reasons


def test_assert_citable_raises_for_unknown() -> None:
    s = NE.assess([], claim_kind=ClaimKind.PARAPHRASE)
    with pytest.raises(NoEvidenceError):
        NE.assert_citable(s)


def test_full_text_card_supports_paraphrase() -> None:
    s = NE.assess([F.paper_card()], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.SUPPORTED
    assert s.citation_allowed is True
    NE.assert_citable(s)


def test_abstract_cannot_support_a_verbatim_quote() -> None:
    """摘要冒充 quote：只有摘要 → VERBATIM_QUOTE 判 UNSUPPORTED。"""
    s = NE.assess([F.abstract_card()], claim_kind=ClaimKind.VERBATIM_QUOTE)
    assert s.verdict is NoEvidenceVerdict.UNSUPPORTED
    assert s.citation_allowed is False
    assert NE.Reason.ABSTRACT_CANNOT_SUPPORT_VERBATIM in s.reasons


def test_abstract_can_support_a_paraphrase() -> None:
    s = NE.assess([F.abstract_card()], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.SUPPORTED


def test_textbook_alone_cannot_support_a_literature_claim() -> None:
    s = NE.assess([F.textbook_card()], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.UNSUPPORTED
    assert s.citation_allowed is False
    assert NE.Reason.NO_LITERATURE_SUPPORT in s.reasons


def test_textbook_can_support_background() -> None:
    s = NE.assess([F.textbook_card()], claim_kind=ClaimKind.BACKGROUND)
    assert s.verdict is NoEvidenceVerdict.SUPPORTED
    assert s.citation_allowed is True


def test_derived_alone_is_unsupported_and_needs_upstream() -> None:
    s = NE.assess([F.derived_card()], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.UNSUPPORTED
    assert NE.Reason.DERIVED_ONLY in s.reasons


def test_all_stale_cards_yield_unknown() -> None:
    stale = F.paper_card(stale_state=StaleState.STALE, stale_reason="asset_hash_changed")
    s = NE.assess([stale], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.UNKNOWN
    assert NE.Reason.ALL_STALE in s.reasons
    assert s.citation_allowed is False


def test_inactive_cards_do_not_count() -> None:
    revoked = F.paper_card(status=EvidenceStatus.INVALIDATED)
    s = NE.assess([revoked], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.UNKNOWN
    assert NE.Reason.NO_ACTIVE_EVIDENCE in s.reasons


def test_mixed_evidence_keeps_only_usable_ids_in_support() -> None:
    good = F.paper_card(card_id="good")
    stale = F.paper_card(card_id="stale", stale_state=StaleState.STALE, stale_reason="asset_hash_changed")
    s = NE.assess([good, stale], claim_kind=ClaimKind.PARAPHRASE)
    assert s.verdict is NoEvidenceVerdict.SUPPORTED
    assert s.usable_card_ids == ("good",)
    assert s.blocked_card_ids == ("stale",)


def test_refusal_answer_is_explicit_and_not_a_conclusion() -> None:
    s = NE.assess([], claim_kind=ClaimKind.PARAPHRASE)
    answer = NE.answer_or_refuse(s)
    assert answer["citable"] is False
    assert answer["verdict"] == NoEvidenceVerdict.UNKNOWN.value
    assert answer["text"]
    assert "UNKNOWN" in answer["text"] or "证据" in answer["text"]


def test_unsupported_verdict_is_never_downgraded_to_citable() -> None:
    """UNSUPPORTED 与 UNKNOWN 都不得被渲染成可引用结论。"""
    for cards, kind in (([F.textbook_card()], ClaimKind.PARAPHRASE), ([], ClaimKind.PARAPHRASE)):
        s = NE.assess(cards, claim_kind=kind)
        answer = NE.answer_or_refuse(s)
        assert answer["citable"] is False
        assert "conclusion" not in answer


def test_verdict_is_total_over_claim_kinds() -> None:
    for kind in ClaimKind:
        s = NE.assess([F.paper_card()], claim_kind=kind)
        assert isinstance(s.citation_allowed, bool)
