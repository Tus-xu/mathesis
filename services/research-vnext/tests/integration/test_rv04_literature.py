"""RV04 integration · literature DOI dedup + conflict + real Crossref sample."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from research_vnext.core.api.deps import AccessContext
from research_vnext.core.db.engine import session_scope
from research_vnext.core.literature import service as lit_svc
from research_vnext.core.literature.models import LiteratureDedupConflictRow
from research_vnext.core.literature.provider import LiteratureMetadata, now_iso


def _ctx_from(c: dict) -> AccessContext:
    return AccessContext(
        actor_id=uuid.UUID(c["actor_id"]),
        tenant_id=uuid.UUID(c["tenant_id"]),
        workspace="research",
        domain="research-vnext",
        capabilities=list(c["capabilities"]),
    )


def _meta(doi: str, title: str, *, raw_hash: str = "h1", provider_rec: str | None = None) -> LiteratureMetadata:
    return LiteratureMetadata(
        provider="crossref",
        provider_version="1.0.0",
        provider_record_id=provider_rec or doi or f"rec-{title}",
        request_id="rid",
        query="q",
        doi=doi,
        title=title,
        authors=[{"name": "Ada Lovelace"}],
        published_date="2020",
        venue="J",
        abstract_available="NO",
        fulltext_available="UNKNOWN",
        rights_status="UNKNOWN",
        source_url=f"https://doi.org/{doi}" if doi else "",
        retrieved_at=now_iso(),
        raw_response_hash=raw_hash,
    )


def test_doi_dedup_and_conflict(app_env, ctx_factory):
    ctx = _ctx_from(ctx_factory())
    project = uuid.uuid4()
    doi = f"10.1234/rv04.{uuid.uuid4().hex[:8]}"
    with session_scope() as db:
        r1, o1 = lit_svc.ingest_metadata(db, ctx, _meta(doi, "Paper A"), project_id=project)
        r2, o2 = lit_svc.ingest_metadata(db, ctx, _meta(doi, "Paper A"), project_id=project)
        assert o1 == "created"
        assert o2 == "deduped"
        assert r1.id == r2.id

    with session_scope() as db:
        r3, o3 = lit_svc.ingest_metadata(
            db, ctx, _meta(doi, "Paper A RENAMED", raw_hash="h2"), project_id=project
        )
        assert o3 == "conflict_recorded"
        assert r3.id == r1.id
        assert r3.title == "Paper A"

    with session_scope() as db:
        conflicts = list(
            db.execute(
                select(LiteratureDedupConflictRow).where(LiteratureDedupConflictRow.tenant_id == ctx.tenant_id)
            ).scalars()
        )
        assert len(conflicts) == 1
        assert conflicts[0].reason == "DOI_MATCH"


def test_fingerprint_dedup_without_doi(app_env, ctx_factory):
    ctx = _ctx_from(ctx_factory())
    project = uuid.uuid4()
    m1 = _meta("", "Unique Title Here", raw_hash="a", provider_rec="rec-1")
    m2 = _meta("", "unique title here", raw_hash="a", provider_rec="rec-2")
    with session_scope() as db:
        r1, o1 = lit_svc.ingest_metadata(db, ctx, m1, project_id=project)
        r2, o2 = lit_svc.ingest_metadata(db, ctx, m2, project_id=project)
        assert o1 == "created"
        assert o2 == "deduped"
        assert r1.id == r2.id


def test_metadata_is_not_evidence_and_rights_unknown(app_env, ctx_factory):
    ctx = _ctx_from(ctx_factory())
    with session_scope() as db:
        row, _ = lit_svc.ingest_metadata(db, ctx, _meta(f"10.1/{uuid.uuid4().hex}", "T"), project_id=uuid.uuid4())
        pub = lit_svc.record_public_dict(row)
        assert pub["record_kind"] == "METADATA_ONLY"
        assert pub["rights_status"] == "UNKNOWN"


def test_real_crossref_ingest_3_to_5(app_env, ctx_factory):
    """真实 Crossref：检索并入库 3–5 条。网络失败 → skip（不伪造）。"""
    ctx = _ctx_from(ctx_factory())
    project = uuid.uuid4()
    from research_vnext.core.literature.crossref import CrossrefProvider

    provider = CrossrefProvider()
    try:
        metas = provider.search("graph neural networks", limit=4, request_id="rv04-real-sample")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Crossref unavailable: {e}")
    assert 3 <= len(metas) <= 5
    outcomes = []
    with session_scope() as db:
        for meta in metas:
            row, outcome = lit_svc.ingest_metadata(db, ctx, meta, project_id=project)
            outcomes.append(outcome)
            assert row.provider == "crossref"
            assert row.record_kind == "METADATA_ONLY"
            assert row.raw_response_hash
    assert outcomes[0] == "created"
    with session_scope() as db:
        _, outcome = lit_svc.ingest_metadata(db, ctx, metas[0], project_id=project)
        assert outcome == "deduped"
