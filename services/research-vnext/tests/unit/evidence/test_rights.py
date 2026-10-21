"""RV07 · rights 强制：quote length / 原文可见性 / 导出包含性 / prompt·log 隔离。

任务书要求 5：rights 控制 quote length、原文可见性、导出包含性；
禁止把受限全文复制进 prompt/log/export。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence import rights as R  # noqa: E402
from research_vnext.core.evidence.errors import EvidenceRightsViolation  # noqa: E402
from research_vnext.core.evidence.types import RightsStatus  # noqa: E402
from tests.unit.evidence import factories as F  # noqa: E402


def test_unknown_rights_block_quote_visibility_and_export() -> None:
    policy = R.policy_for(F.rights(RightsStatus.UNKNOWN, redistributable=False, fulltext_allowed=False))
    assert policy.quote_max_chars == 0
    assert policy.reader_fulltext_visible is False
    assert policy.export_included is False
    assert policy.prompt_verbatim_allowed is False
    assert policy.log_verbatim_allowed is False


def test_restricted_rights_allow_preview_but_not_export_or_prompt() -> None:
    policy = R.policy_for(
        F.rights(RightsStatus.RESTRICTED, redistributable=False, fulltext_allowed=True)
    )
    assert policy.quote_max_chars > 0
    assert policy.export_included is False
    assert policy.prompt_verbatim_allowed is False
    assert policy.reader_fulltext_visible is True


def test_open_access_rights_allow_export_and_prompt() -> None:
    policy = R.policy_for(F.rights(RightsStatus.OPEN_ACCESS, redistributable=True, fulltext_allowed=True))
    assert policy.export_included is True
    assert policy.prompt_verbatim_allowed is True
    assert policy.reader_fulltext_visible is True


def test_licensed_rights_are_reader_visible_but_not_exportable() -> None:
    policy = R.policy_for(F.rights(RightsStatus.LICENSED, redistributable=False, fulltext_allowed=True))
    assert policy.reader_fulltext_visible is True
    assert policy.export_included is False
    assert policy.prompt_verbatim_allowed is False


def test_quote_is_truncated_to_policy_cap_with_warning() -> None:
    policy = R.policy_for(F.rights(RightsStatus.RESTRICTED, redistributable=False, fulltext_allowed=False))
    text = "汉字" * (policy.quote_max_chars)
    bounded, warnings = R.enforce_snippet(text, policy)
    assert len(bounded) <= policy.quote_max_chars
    assert R.Warn.QUOTE_TRUNCATED in warnings


def test_enforce_snippet_drops_text_entirely_when_cap_is_zero() -> None:
    policy = R.policy_for(F.rights(RightsStatus.UNKNOWN, redistributable=False, fulltext_allowed=False))
    bounded, warnings = R.enforce_snippet("任何原文", policy)
    assert bounded == ""
    assert R.Warn.SNIPPET_REDACTED_BY_RIGHTS in warnings


def test_export_payload_raises_for_non_exportable_card() -> None:
    card = F.paper_card(
        rights=F.rights(RightsStatus.RESTRICTED, redistributable=False, fulltext_allowed=False),
    )
    with pytest.raises(EvidenceRightsViolation):
        R.export_payload(card)


def test_export_payload_includes_quote_for_open_access_card() -> None:
    card = F.paper_card()
    payload = R.export_payload(card)
    assert payload["quote"] == card.snippet
    assert payload["license_id"] == card.rights.license_id


def test_prompt_payload_never_contains_verbatim_for_restricted_card() -> None:
    snippet = "受限原文片段，禁止进 prompt。"
    card = F.paper_card(
        snippet=snippet,
        rights=F.rights(RightsStatus.RESTRICTED, redistributable=False, fulltext_allowed=False),
    )
    payload = R.prompt_payload(card)
    assert snippet not in repr(payload)
    assert payload["verbatim_allowed"] is False
    assert payload["snippet_hash"] == card.snippet_hash


def test_log_payload_never_contains_verbatim_text() -> None:
    snippet = "逐字原文绝不应出现在日志里。"
    card = F.paper_card(snippet=snippet)
    payload = R.log_payload(card)
    assert snippet not in repr(payload)
    assert payload["snippet_hash"] == card.snippet_hash


def test_textbook_card_is_not_exportable_but_reader_visible_as_chunk() -> None:
    card = F.textbook_card()
    assert R.policy_for(card.rights).export_included is False
    with pytest.raises(EvidenceRightsViolation):
        R.export_payload(card)


def test_policy_is_total_over_all_rights_statuses() -> None:
    for status in RightsStatus:
        policy = R.policy_for(F.rights(status))
        assert policy.quote_max_chars >= 0
        assert isinstance(policy.export_included, bool)
