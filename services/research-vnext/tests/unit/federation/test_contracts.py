"""RV06 · federation 契约测试（维度隔离 / locator 分派）。

失败先行：本文件先于实现落地，用于钉死
- evidence_type ↔ locator 类型必须一致（防「教材被当文献」）；
- 教材 locator **类型层**不接受 DOI/期刊/页码（防伪 DOI、假页码）；
- 结果必须带 locator / rights / provenance / score 分项 / warnings。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from research_vnext.core.federation.contracts import (
    AccessRights,
    AssetLocator,
    Budget,
    FederatedQuery,
    FederatedResult,
    LiteratureLocator,
    Provenance,
    ScoreComponents,
    TextbookLocator,
)
from research_vnext.core.federation.errors import (
    DimensionLeak,
    DimensionMismatch,
    MissingLocator,
    QueryInvalid,
)
from research_vnext.core.federation.types import EvidenceType, TrustLevel


def _snippet_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rights(**over: object) -> AccessRights:
    base = {
        "rights_status": "UNKNOWN",
        "rights_evidence_ref": "rv06-test-evidence",
        "redistributable": False,
    }
    base.update(over)
    return AccessRights(**base)  # type: ignore[arg-type]


def _prov() -> Provenance:
    return Provenance(retrieved_at="2026-09-13T00:00:00Z", parser_version="rv06-test", source_hash="a" * 64)


def _score() -> ScoreComponents:
    return ScoreComponents(lexical=0.5, vector=0.4, rights_penalty=0.0, recency=1.0)


# --------------------------------------------------------------------------
# 1. 教材 locator：类型层拒绝 DOI / 期刊 / 页码
# --------------------------------------------------------------------------


def test_textbook_locator_from_mapping_rejects_doi() -> None:
    """教材 chunk 不得被赋予 DOI（防「把教材伪装成学术文献」）。"""
    with pytest.raises(DimensionLeak):
        TextbookLocator.from_mapping(
            {"source_id": "s1", "section_id": "sec1", "chunk_id": "c1", "doi": "10.1038/nature14539"}
        )


def test_textbook_locator_from_mapping_rejects_venue_and_journal() -> None:
    with pytest.raises(DimensionLeak):
        TextbookLocator.from_mapping(
            {"source_id": "s1", "section_id": "sec1", "chunk_id": "c1", "venue": "Nature"}
        )
    with pytest.raises(DimensionLeak):
        TextbookLocator.from_mapping(
            {"source_id": "s1", "section_id": "sec1", "chunk_id": "c1", "journal": "Science"}
        )


def test_textbook_locator_from_mapping_rejects_page_fields() -> None:
    """Teacher 表里真实存在 page_range，adapter 必须显式丢弃而不是投影成 locator。"""
    for key in ("page", "page_range", "page_ref", "page_refs", "pnn", "bbox"):
        with pytest.raises(DimensionLeak):
            TextbookLocator.from_mapping(
                {"source_id": "s1", "section_id": "sec1", "chunk_id": "c1", key: "12"}
            )


def test_textbook_locator_accepts_only_source_section_chunk() -> None:
    loc = TextbookLocator.from_mapping({"source_id": "s1", "section_id": "sec1", "chunk_id": "c1"})
    assert (loc.source_id, loc.section_id, loc.chunk_id) == ("s1", "sec1", "c1")
    assert loc.as_dict() == {"source_id": "s1", "section_id": "sec1", "chunk_id": "c1"}


def test_textbook_locator_requires_all_three_ids() -> None:
    with pytest.raises(MissingLocator):
        TextbookLocator.from_mapping({"source_id": "s1", "section_id": "sec1"})


# --------------------------------------------------------------------------
# 2. evidence_type ↔ locator 必须一致
# --------------------------------------------------------------------------


def test_foundational_math_result_cannot_carry_literature_locator() -> None:
    with pytest.raises(DimensionMismatch):
        FederatedResult(
            result_id="r1",
            evidence_type=EvidenceType.FOUNDATIONAL_MATH,
            provider="foundational_math",
            provider_version="1.0.0",
            locator=LiteratureLocator(doi="10.1/x"),
            snippet="text",
            snippet_hash=_snippet_hash("text"),
            rights=_rights(),
            provenance=_prov(),
            retrieved_at="2026-09-13T00:00:00Z",
            score_components=_score(),
        )


def test_literature_result_cannot_carry_textbook_locator() -> None:
    with pytest.raises(DimensionMismatch):
        FederatedResult(
            result_id="r1",
            evidence_type=EvidenceType.LITERATURE,
            provider="crossref",
            provider_version="1.0.0",
            locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id="c1"),
            snippet="text",
            snippet_hash=_snippet_hash("text"),
            rights=_rights(),
            provenance=_prov(),
            retrieved_at="2026-09-13T00:00:00Z",
            score_components=_score(),
        )


def test_matching_dimension_is_accepted() -> None:
    res = FederatedResult(
        result_id="r1",
        evidence_type=EvidenceType.LITERATURE,
        provider="crossref",
        provider_version="1.0.0",
        locator=LiteratureLocator(doi="10.1038/nature14539", venue="Nature"),
        snippet="deep learning",
        snippet_hash=_snippet_hash("deep learning"),
        rights=_rights(rights_status="PUBLIC", redistributable=True),
        provenance=_prov(),
        retrieved_at="2026-09-13T00:00:00Z",
        score_components=_score(),
    )
    assert res.evidence_type is EvidenceType.LITERATURE
    assert res.locator.doi == "10.1038/nature14539"


# --------------------------------------------------------------------------
# 3. 无 locator / 无语义结果不得产出
# --------------------------------------------------------------------------


def test_result_without_locator_is_rejected() -> None:
    with pytest.raises(MissingLocator):
        FederatedResult(
            result_id="r1",
            evidence_type=EvidenceType.LITERATURE,
            provider="crossref",
            provider_version="1.0.0",
            locator=None,  # type: ignore[arg-type]
            snippet="text",
            snippet_hash=_snippet_hash("text"),
            rights=_rights(),
            provenance=_prov(),
            retrieved_at="2026-09-13T00:00:00Z",
            score_components=_score(),
        )


def test_literature_without_doi_must_stay_empty_not_fabricated() -> None:
    """没有 DOI 时 locator.doi 必须是空串，绝不编造。"""
    loc = LiteratureLocator(venue="Journal of Testing")
    assert loc.doi == ""
    assert loc.fabricated is False


# --------------------------------------------------------------------------
# 4. 结果必填字段与 trust 语义
# --------------------------------------------------------------------------


def test_result_carries_required_fields_and_score_breakdown() -> None:
    res = FederatedResult(
        result_id="r1",
        evidence_type=EvidenceType.PROJECT_ASSET,
        provider="project_asset",
        provider_version="1.0.0",
        locator=AssetLocator(asset_id="a1", asset_version=1, content_hash="b" * 64),
        snippet="s",
        snippet_hash=_snippet_hash("s"),
        rights=_rights(),
        provenance=_prov(),
        retrieved_at="2026-09-13T00:00:00Z",
        score_components=_score(),
    )
    assert res.score_components.total > 0
    assert isinstance(res.warnings, tuple)
    assert res.provider_version


def test_snippet_hash_must_match_snippet() -> None:
    with pytest.raises(ValueError):
        FederatedResult(
            result_id="r1",
            evidence_type=EvidenceType.LITERATURE,
            provider="crossref",
            provider_version="1.0.0",
            locator=LiteratureLocator(doi="10.1/x"),
            snippet="hello",
            snippet_hash="c" * 64,
            rights=_rights(),
            provenance=_prov(),
            retrieved_at="2026-09-13T00:00:00Z",
            score_components=_score(),
        )


def test_retrieved_content_is_always_untrusted() -> None:
    res = FederatedResult(
        result_id="r1",
        evidence_type=EvidenceType.LITERATURE,
        provider="crossref",
        provider_version="1.0.0",
        locator=LiteratureLocator(doi="10.1/x"),
        snippet="text",
        snippet_hash=_snippet_hash("text"),
        rights=_rights(),
        provenance=_prov(),
        retrieved_at="2026-09-13T00:00:00Z",
        score_components=_score(),
    )
    assert res.trust_level is TrustLevel.UNTRUSTED_DATA
    assert res.untrusted is True


def test_untrusted_block_cannot_be_lifted_to_instruction() -> None:
    """检索文本只能作为数据块输出；trust_level 不可被调用方改写。"""
    res = FederatedResult(
        result_id="r1",
        evidence_type=EvidenceType.LITERATURE,
        provider="crossref",
        provider_version="1.0.0",
        locator=LiteratureLocator(doi="10.1/x"),
        snippet="text",
        snippet_hash=_snippet_hash("text"),
        rights=_rights(),
        provenance=_prov(),
        retrieved_at="2026-09-13T00:00:00Z",
        score_components=_score(),
    )
    block = res.to_untrusted_block()
    assert block["trust_level"] == TrustLevel.UNTRUSTED_DATA.value
    assert block["role"] == "data"
    with pytest.raises(TypeError):
        block["trust_level"] = "TRUSTED_SYSTEM"  # type: ignore[index]


def test_foundational_math_cannot_claim_citation_eligibility() -> None:
    """教材结果不得被用于声称「文献已支持」。"""
    with pytest.raises(ValueError):
        FederatedResult(
            result_id="r1",
            evidence_type=EvidenceType.FOUNDATIONAL_MATH,
            provider="foundational_math",
            provider_version="1.0.0",
            locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id="c1"),
            snippet="text",
            snippet_hash=_snippet_hash("text"),
            rights=_rights(),
            provenance=_prov(),
            retrieved_at="2026-09-13T00:00:00Z",
            score_components=_score(),
            citation_eligible=True,
        )


def test_foundational_math_labels_are_mandatory() -> None:
    res = FederatedResult(
        result_id="r1",
        evidence_type=EvidenceType.FOUNDATIONAL_MATH,
        provider="foundational_math",
        provider_version="1.0.0",
        locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id="c1"),
        snippet="text",
        snippet_hash=_snippet_hash("text"),
        rights=_rights(),
        provenance=_prov(),
        retrieved_at="2026-09-13T00:00:00Z",
        score_components=_score(),
        citation_eligible=False,
    )
    assert "TEXTBOOK" in res.labels
    assert "FOUNDATIONAL_MATH" in res.labels


def test_asset_locator_never_exposes_storage_key() -> None:
    with pytest.raises(DimensionLeak):
        AssetLocator.from_mapping(
            {
                "asset_id": "a1",
                "asset_version": 1,
                "content_hash": "b" * 64,
                "storage_key": "rvs/tenant/a1/v1",
            }
        )


# --------------------------------------------------------------------------
# 5. FederatedQuery 的受控字段（独立审查后收紧）
# --------------------------------------------------------------------------


def _query_kwargs(**over: object) -> dict:
    base = {
        "actor_id": "a1",
        "tenant_id": "t1",
        "project_id": "p1",
        "session_id": "s1",
        "purpose": "literature_review",
        "provider_allowlist": ("crossref",),
        "filters": {},
        "budget": Budget(max_results=10, max_provider_calls=2, cost_ceiling=0.0),
        "deadline_ms": 1000,
        "trace_id": "trace-1",
        "query_text": "q",
    }
    base.update(over)
    return base


def test_query_rejects_overlong_purpose() -> None:
    """purpose 落 varchar(64)：超长必须在契约层挡住，而不是跑完检索才炸在写入处。"""
    with pytest.raises(QueryInvalid):
        FederatedQuery(**_query_kwargs(purpose="p" * 65))


def test_query_rejects_overlong_prompt_version() -> None:
    with pytest.raises(QueryInvalid):
        FederatedQuery(**_query_kwargs(prompt_version="v" * 65))


def test_query_rejects_free_text_purpose() -> None:
    """purpose 是受控标识而非自由文本 —— 否则任意文本会被写进审计列。"""
    with pytest.raises(QueryInvalid):
        FederatedQuery(**_query_kwargs(purpose="我的研究目的"))


def test_query_accepts_controlled_identifier_shapes() -> None:
    for ok in ("literature_review", "retrieval_eval", "p1.v2:beta-1"):
        assert FederatedQuery(**_query_kwargs(purpose=ok)).purpose == ok


# --------------------------------------------------------------------------
# 6. locator 子类不得绕过维度校验
# --------------------------------------------------------------------------


def test_locator_subclass_carrying_extra_fields_is_rejected() -> None:
    """子类可以带上白名单外的字段；`isinstance` 会放行它，精确类型比较不会。"""

    @dataclass(frozen=True)
    class SneakyTextbookLocator(TextbookLocator):
        doi: str = ""

    with pytest.raises(DimensionMismatch):
        FederatedResult(
            result_id="r1",
            evidence_type=EvidenceType.FOUNDATIONAL_MATH,
            provider="foundational_math",
            provider_version="1.0.0",
            locator=SneakyTextbookLocator(source_id="s1", section_id="sec1", chunk_id="c1"),
            snippet="text",
            snippet_hash=_snippet_hash("text"),
            rights=_rights(),
            provenance=_prov(),
            retrieved_at="2026-09-13T00:00:00Z",
            score_components=_score(),
            citation_eligible=False,
        )


def test_literature_without_doi_gets_distinct_discriminators() -> None:
    """无 DOI 的文献不能共用一个固定去重键，否则不同记录会被折叠成一条。"""
    a = LiteratureLocator(venue="Journal A", section="s1")
    b = LiteratureLocator(venue="Journal B", section="s2")
    assert a.discriminator != b.discriminator
    assert a.discriminator == LiteratureLocator(venue="Journal A", section="s1").discriminator
    assert a.discriminator.startswith("unidentified:")
