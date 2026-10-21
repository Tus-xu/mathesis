"""RV07 · Evidence type 矩阵：7 类必须**不静默等价**。

任务书要求：`PAPER_FULL_TEXT/PAPER_ABSTRACT/PROJECT_ASSET/TEXTBOOK/DATASET/WEB/DERIVED`
明确区分，不同类型有不同可支持强度，不静默等价。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence.types import (  # noqa: E402
    SUPPORT_PROFILES,
    ClaimKind,
    EvidenceCardType,
    SupportStrength,
    profile_for,
    supports_claim_kind,
)

REQUIRED_TYPES = {
    "PAPER_FULL_TEXT",
    "PAPER_ABSTRACT",
    "PROJECT_ASSET",
    "TEXTBOOK",
    "DATASET",
    "WEB",
    "DERIVED",
}


def test_exactly_seven_types_and_names_frozen() -> None:
    assert {t.value for t in EvidenceCardType} == REQUIRED_TYPES
    assert len(EvidenceCardType) == 7


def test_every_type_has_a_support_profile() -> None:
    for t in EvidenceCardType:
        assert t in SUPPORT_PROFILES, f"{t} 缺少支持强度档案"


def test_no_two_types_are_silently_equivalent() -> None:
    """任意两类的支持档案必须不同 —— 否则就是"静默等价"。"""
    seen: dict[tuple, list[str]] = {}
    for t, p in SUPPORT_PROFILES.items():
        key = p.signature()
        seen.setdefault(key, []).append(t.value)
    collisions = {k: v for k, v in seen.items() if len(v) > 1}
    assert not collisions, f"以下类型支持档案完全一致（静默等价）: {collisions}"


def test_only_full_text_supports_verbatim_quote() -> None:
    assert profile_for(EvidenceCardType.PAPER_FULL_TEXT).supports_verbatim_quote is True
    for t in EvidenceCardType:
        if t is EvidenceCardType.PAPER_FULL_TEXT:
            continue
        assert profile_for(t).supports_verbatim_quote is False, (
            f"{t.value} 不得支持逐字引用（防止摘要/教材冒充论文原文证据）"
        )


def test_abstract_is_summary_strength_not_primary_verbatim() -> None:
    p = profile_for(EvidenceCardType.PAPER_ABSTRACT)
    assert p.strength is SupportStrength.PRIMARY_SUMMARY
    assert p.counts_as_literature_support is True


def test_textbook_and_derived_are_not_citation_eligible() -> None:
    assert profile_for(EvidenceCardType.TEXTBOOK).citation_eligible is False
    assert profile_for(EvidenceCardType.DERIVED).citation_eligible is False


def test_derived_requires_upstream_evidence() -> None:
    p = profile_for(EvidenceCardType.DERIVED)
    assert p.requires_upstream is True
    assert p.counts_as_literature_support is False


def test_textbook_cannot_support_literature_claims() -> None:
    assert profile_for(EvidenceCardType.TEXTBOOK).counts_as_literature_support is False


@pytest.mark.parametrize(
    ("card_type", "claim_kind", "expected"),
    [
        (EvidenceCardType.PAPER_FULL_TEXT, ClaimKind.VERBATIM_QUOTE, True),
        (EvidenceCardType.PAPER_ABSTRACT, ClaimKind.VERBATIM_QUOTE, False),
        (EvidenceCardType.TEXTBOOK, ClaimKind.VERBATIM_QUOTE, False),
        (EvidenceCardType.PAPER_ABSTRACT, ClaimKind.PARAPHRASE, True),
        (EvidenceCardType.TEXTBOOK, ClaimKind.BACKGROUND, True),
        (EvidenceCardType.DERIVED, ClaimKind.PARAPHRASE, False),
        (EvidenceCardType.PROJECT_ASSET, ClaimKind.DATA_FACT, True),
        (EvidenceCardType.WEB, ClaimKind.PARAPHRASE, True),
    ],
)
def test_claim_kind_support_matrix(card_type, claim_kind, expected) -> None:
    assert supports_claim_kind(card_type, claim_kind) is expected
