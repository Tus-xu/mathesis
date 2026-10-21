"""RV07 · EvidenceCard 测试构造器（非测试文件，pytest 不收集）。

存在的理由：EvidenceCard 的不变量很密（type ↔ locator ↔ rights ↔ snippet 长度），
如果每个用例都手写一遍，用例本身会变成"复制粘贴的合法构造"，一旦契约收紧，
失败信息指向的是复制品而不是被测行为。集中在这里，收紧契约时只改一处。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from research_vnext.core.evidence.contracts import (
    Confidence,
    EvidenceACL,
    EvidenceCard,
    EvidenceProposal,
    LocatorBinding,
    Provenance,
    RightsInfo,
    SourceRef,
    StructuredFact,
)
from research_vnext.core.evidence.types import (
    ConfidenceBand,
    EvidenceCardType,
    EvidenceStatus,
    LocatorKind,
    RightsStatus,
    StaleState,
)

TENANT = "4d10a311-ccc0-4626-9fab-e7beb680da08"
PROJECT = "a38faa46-6e4c-409c-adb2-26c88ff656a7"
SESSION = "11111111-2222-3333-4444-555555555555"
OWNER = "99999999-8888-7777-6666-555555555555"
RUN = "run-rv07-0001"
RETRIEVED_AT = datetime(2026, 9, 13, 5, 15, 7, tzinfo=timezone.utc)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def acl(**over) -> EvidenceACL:
    base = dict(
        tenant_id=TENANT,
        project_id=PROJECT,
        session_id=SESSION,
        owner_id=OWNER,
        visibility="project",
    )
    base.update(over)
    return EvidenceACL(**base)


def rights(status: RightsStatus = RightsStatus.OPEN_ACCESS, **over) -> RightsInfo:
    base = dict(
        rights_status=status,
        redistributable=True,
        fulltext_allowed=True,
        license_id="cc-by-4.0",
        rights_evidence_ref="crossref.license.cc-by-4.0",
    )
    base.update(over)
    return RightsInfo(**base)


def source_ref(**over) -> SourceRef:
    base = dict(
        provider="crossref",
        provider_record_id="10.1038/nature14539",
        record_kind="FULL_TEXT",
        fulltext_ref="anchor:pdf-fulltext",
        abstract_ref="",
        url="https://doi.org/10.1038/nature14539",
        doi="10.1038/nature14539",
        source_hash="a" * 64,
    )
    base.update(over)
    return SourceRef(**base)


def anchor_locator(**over) -> LocatorBinding:
    payload = dict(
        asset_id="22222222-2222-2222-2222-222222222222",
        asset_version=1,
        content_hash="a" * 64,
        anchor_id="anchor-0001",
        anchor_type="section",
    )
    payload.update(over)
    return LocatorBinding(kind=LocatorKind.ANCHOR, payload=payload)


def external_locator(**over) -> LocatorBinding:
    payload = dict(
        doi="10.1038/nature14539",
        url="https://doi.org/10.1038/nature14539",
        fetched_at="2026-09-13T05:15:07+00:00",
        content_hash="a" * 64,
    )
    payload.update(over)
    return LocatorBinding(kind=LocatorKind.EXTERNAL, payload=payload)


def textbook_locator(**over) -> LocatorBinding:
    payload = dict(source_id="src-1", section_id="sec-1", chunk_id="chunk-1")
    payload.update(over)
    return LocatorBinding(kind=LocatorKind.EXTERNAL, payload=payload)


def provenance(**over) -> Provenance:
    base = dict(
        source_hash="a" * 64,
        parser_version="teacher-direct-chunk@1.0.0",
        provider="crossref",
        provider_version="1.0.0",
        retrieved_at=RETRIEVED_AT.isoformat(),
        index_version="",
    )
    base.update(over)
    return Provenance(**base)


def confidence(band: ConfidenceBand = ConfidenceBand.HIGH, score: float = 0.9) -> Confidence:
    return Confidence(band=band, score=score, method="provider_metadata")


def fact() -> StructuredFact:
    return StructuredFact(
        predicate="reports_accuracy",
        subject="Deep learning",
        object="0.93",
        qualifier="ImageNet",
        unit="fraction",
        normalized="reports_accuracy(deep_learning, imagenet) = 0.93",
    )


def paper_card(**over) -> EvidenceCard:
    snippet = over.pop("snippet", "Deep learning allows computational models of multiple levels.")
    base = dict(
        card_id="c0000000-0000-0000-0000-000000000001",
        evidence_type=EvidenceCardType.PAPER_FULL_TEXT,
        source_ref=source_ref(),
        locator=anchor_locator(),
        snippet=snippet,
        structured_fact=None,
        snippet_hash=sha(snippet),
        provenance=provenance(),
        rights=rights(),
        retrieved_at=RETRIEVED_AT,
        created_by_run=RUN,
        confidence=confidence(),
        limitations=(),
        status=EvidenceStatus.ACTIVE,
        version=1,
        acl=acl(),
        stale_state=StaleState.FRESH,
    )
    base.update(over)
    if "snippet_hash" not in over:
        base["snippet_hash"] = sha(base["snippet"])
    return EvidenceCard(**base)


def abstract_card(**over) -> EvidenceCard:
    over.setdefault("evidence_type", EvidenceCardType.PAPER_ABSTRACT)
    over.setdefault("source_ref", source_ref(record_kind="ABSTRACT", fulltext_ref=""))
    over.setdefault("locator", external_locator())
    return paper_card(**over)


def textbook_card(**over) -> EvidenceCard:
    over.setdefault("evidence_type", EvidenceCardType.TEXTBOOK)
    over.setdefault(
        "source_ref",
        source_ref(
            provider="teacher_direct_chunk",
            provider_record_id="chunk-1",
            record_kind="TEXTBOOK_CHUNK",
            fulltext_ref="",
            abstract_ref="",
            url="",
            doi="",
        ),
    )
    over.setdefault("locator", textbook_locator())
    over.setdefault("rights", rights(RightsStatus.PERMITTED, redistributable=False, fulltext_allowed=False))
    return paper_card(**over)


def derived_card(**over) -> EvidenceCard:
    over.setdefault("evidence_type", EvidenceCardType.DERIVED)
    over.setdefault("source_ref", source_ref(record_kind="DERIVED", fulltext_ref="", upstream_card_ids=("c0",)))
    over.setdefault("locator", external_locator())
    return paper_card(**over)


def proposal(**over) -> EvidenceProposal:
    base = dict(
        evidence_type=EvidenceCardType.PAPER_FULL_TEXT,
        source_ref=source_ref(),
        locator=anchor_locator(),
        snippet="Deep learning allows computational models of multiple levels.",
        structured_fact=None,
        provenance=provenance(),
        rights=rights(),
        retrieved_at=RETRIEVED_AT,
        created_by_run=RUN,
        confidence=confidence(),
        limitations=(),
        acl=acl(),
    )
    base.update(over)
    return EvidenceProposal(**base)
