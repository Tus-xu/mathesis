"""RV07 · EvidenceCard 契约：字段齐全 + 类型/定位/权利三方互锁。

覆盖任务书失败测试：
- 摘要冒充 quote；
- 教材 citation 伪 DOI（教材不得有 DOI/期刊/页码）；
- snippet hash 必须可验证；
- AI 只能 propose，schema/locator/rights/ACL 校验后才入库。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence.contracts import (  # noqa: E402
    REQUIRED_CARD_FIELDS,
    LocatorBinding,
    SourceRef,
)
from research_vnext.core.evidence.errors import (  # noqa: E402
    EvidenceContractError,
    EvidenceFabricationRejected,
    EvidenceRightsViolation,
)
from research_vnext.core.evidence.types import (  # noqa: E402
    EvidenceCardType,
    LocatorKind,
    RightsStatus,
)
from tests.unit.evidence import factories as F  # noqa: E402


def test_card_carries_every_required_field() -> None:
    card = F.paper_card()
    dumped = card.to_dict()
    missing = [f for f in REQUIRED_CARD_FIELDS if f not in dumped]
    assert not missing, f"EvidenceCard 缺字段: {missing}"
    assert REQUIRED_CARD_FIELDS == {
        "id",
        "type",
        "source_ref",
        "locator",
        "snippet",
        "structured_fact",
        "snippet_hash",
        "provenance",
        "rights",
        "retrieved_at",
        "created_by_run",
        "confidence",
        "limitations",
        "status",
        "version",
        "acl",
    }


def test_snippet_hash_is_verifiable_against_snippet() -> None:
    card = F.paper_card()
    assert card.snippet_hash == F.sha(card.snippet)
    with pytest.raises(EvidenceContractError):
        F.paper_card(snippet="tampered", snippet_hash=F.sha("original"))


def test_card_needs_snippet_or_structured_fact() -> None:
    with pytest.raises(EvidenceContractError):
        F.paper_card(snippet="", snippet_hash=F.sha(""))


def test_full_text_requires_a_fulltext_source_ref() -> None:
    """摘要冒充论文原文证据 → 构造期直接拒绝。"""
    with pytest.raises(EvidenceFabricationRejected):
        F.paper_card(
            source_ref=F.source_ref(record_kind="METADATA_ONLY", fulltext_ref=""),
        )


def test_abstract_card_cannot_claim_full_text_type() -> None:
    """source 只有摘要，却把 type 写成 PAPER_FULL_TEXT → 拒绝。"""
    with pytest.raises(EvidenceFabricationRejected):
        F.paper_card(source_ref=F.source_ref(record_kind="ABSTRACT", fulltext_ref=""))


def test_textbook_locator_rejects_doi_and_page_fields() -> None:
    """教材 citation 伪 DOI → 类型层拒绝。"""
    with pytest.raises(EvidenceContractError):
        LocatorBinding(
            kind=LocatorKind.EXTERNAL,
            payload={"source_id": "s", "section_id": "sec", "chunk_id": "c", "doi": "10.1/fake"},
        )
    with pytest.raises(EvidenceContractError):
        LocatorBinding(
            kind=LocatorKind.EXTERNAL,
            payload={"source_id": "s", "section_id": "sec", "chunk_id": "c", "page": 12},
        )


def test_textbook_card_is_not_citation_eligible_and_has_no_doi() -> None:
    card = F.textbook_card()
    assert card.citation_eligible is False
    assert card.type is EvidenceCardType.TEXTBOOK
    assert "doi" not in card.locator.payload
    assert "page" not in card.locator.payload
    assert "TEXTBOOK" in card.labels


def test_textbook_type_with_literature_locator_is_rejected() -> None:
    """教材类型配文献 locator（含 DOI）→ 维度不匹配。"""
    with pytest.raises(EvidenceContractError):
        F.textbook_card(locator=F.external_locator())


def test_type_must_match_locator_dimension() -> None:
    """PAPER_* 不得使用教材 direct-chunk 三元组 locator。"""
    with pytest.raises(EvidenceContractError):
        F.paper_card(locator=F.textbook_locator())


def test_derived_requires_upstream_card_ids() -> None:
    with pytest.raises(EvidenceFabricationRejected):
        F.derived_card(
            source_ref=SourceRef(
                provider="llm",
                provider_record_id="run-1",
                record_kind="DERIVED",
                fulltext_ref="",
                abstract_ref="",
                url="",
                doi="",
                source_hash="b" * 64,
                upstream_card_ids=(),
            )
        )


def test_rights_forbid_storing_verbatim_text_when_status_unknown() -> None:
    """rights 未声明 → quote_max_chars=0 → 不可能把原文存进 card。"""
    with pytest.raises(EvidenceRightsViolation):
        F.paper_card(
            rights=F.rights(RightsStatus.UNKNOWN, redistributable=False, fulltext_allowed=False),
        )


def test_restricted_rights_bound_the_stored_snippet_length() -> None:
    policy_cap = 240
    long_text = "x" * (policy_cap + 1)
    with pytest.raises(EvidenceRightsViolation):
        F.paper_card(
            snippet=long_text,
            rights=F.rights(RightsStatus.RESTRICTED, redistributable=False, fulltext_allowed=False),
        )


def test_card_version_and_status_are_validated() -> None:
    with pytest.raises(EvidenceContractError):
        F.paper_card(version=0)
    with pytest.raises(EvidenceContractError):
        F.paper_card(status="NOT_A_STATUS")


def test_acl_tenant_must_be_non_empty() -> None:
    with pytest.raises(EvidenceContractError):
        F.paper_card(acl=F.acl(tenant_id=""))


def test_card_to_dict_never_leaks_storage_keys() -> None:
    card = F.paper_card()
    blob = repr(card.to_dict())
    for forbidden in ("storage_key", "minio", "endpoint", "bucket"):
        assert forbidden not in blob.lower()
