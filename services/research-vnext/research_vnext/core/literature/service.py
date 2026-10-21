"""RV04 · 文献入库服务：分层去重 + 冲突不静默合并。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..api.deps import (
    AccessContext,
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    check_revocation,
    require_capability,
)
from ..domain.errors import DomainError, TenantMismatch
from ..observability.trace import TraceContext
from .fingerprint import title_author_year_fingerprint
from .models import LiteratureDedupConflictRow, LiteratureRecordRow
from .provider import LiteratureMetadata


class LiteratureNotFound(DomainError):
    code = "LITERATURE_NOT_FOUND"
    http_status = 404


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_doi(doi: str) -> str:
    d = (doi or "").strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(p):
            d = d[len(p) :]
    return d


def ingest_metadata(
    db: Session,
    ctx: AccessContext,
    meta: LiteratureMetadata,
    *,
    project_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    trace: TraceContext | None = None,  # noqa: ARG001
) -> tuple[LiteratureRecordRow, str]:
    """返回 (record, outcome)。outcome ∈ created|deduped|conflict_recorded。"""
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)

    doi = normalize_doi(meta.doi)
    fp = title_author_year_fingerprint(
        title=meta.title,
        authors=meta.authors,
        published_date=meta.published_date,
    )
    provider_rec = (meta.provider_record_id or doi or meta.title)[:256]

    # Layer 1: DOI
    if doi:
        existing = db.execute(
            select(LiteratureRecordRow).where(
                LiteratureRecordRow.tenant_id == ctx.tenant_id,
                LiteratureRecordRow.doi == doi,
            )
        ).scalars().first()
        if existing is not None:
            return _maybe_conflict(db, existing, meta, doi=doi, fingerprint=fp, reason="DOI_MATCH"), (
                "conflict_recorded" if _hash_differs(existing, meta) else "deduped"
            )

    # Layer 2: stable provider id
    existing = db.execute(
        select(LiteratureRecordRow).where(
            LiteratureRecordRow.tenant_id == ctx.tenant_id,
            LiteratureRecordRow.provider == meta.provider,
            LiteratureRecordRow.provider_record_id == provider_rec,
        )
    ).scalars().first()
    if existing is not None:
        return _maybe_conflict(db, existing, meta, doi=doi, fingerprint=fp, reason="PROVIDER_ID_MATCH"), (
            "conflict_recorded" if _hash_differs(existing, meta) else "deduped"
        )

    # Layer 3: title-author-year fingerprint
    existing = db.execute(
        select(LiteratureRecordRow).where(
            LiteratureRecordRow.tenant_id == ctx.tenant_id,
            LiteratureRecordRow.fingerprint == fp,
        )
    ).scalars().first()
    if existing is not None:
        return _maybe_conflict(db, existing, meta, doi=doi, fingerprint=fp, reason="FINGERPRINT_MATCH"), (
            "conflict_recorded" if _hash_differs(existing, meta) else "deduped"
        )

    row = LiteratureRecordRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        owner_id=ctx.actor_id,
        session_id=session_id,
        provider=meta.provider,
        provider_version=meta.provider_version,
        provider_record_id=provider_rec,
        request_id=meta.request_id,
        query=meta.query,
        doi=doi,
        title=meta.title[:4000],
        authors=meta.authors,
        published_date=meta.published_date,
        venue=meta.venue,
        abstract_available=meta.abstract_available,
        fulltext_available=meta.fulltext_available,
        rights_status=meta.rights_status or "UNKNOWN",
        rights_detail=meta.rights_detail or {},
        source_url=meta.source_url,
        retrieved_at=datetime.fromisoformat(meta.retrieved_at) if meta.retrieved_at else _utcnow(),
        raw_response_hash=meta.raw_response_hash,
        fingerprint=fp,
        record_kind="METADATA_ONLY",
    )
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # 并发撞唯一键：退回既有行（不静默吞掉真实错误）
        if doi:
            raced = db.execute(
                select(LiteratureRecordRow).where(
                    LiteratureRecordRow.tenant_id == ctx.tenant_id,
                    LiteratureRecordRow.doi == doi,
                )
            ).scalars().first()
        else:
            raced = None
        if raced is None:
            raced = db.execute(
                select(LiteratureRecordRow).where(
                    LiteratureRecordRow.tenant_id == ctx.tenant_id,
                    LiteratureRecordRow.provider == meta.provider,
                    LiteratureRecordRow.provider_record_id == provider_rec,
                )
            ).scalars().first()
        if raced is None:
            raced = db.execute(
                select(LiteratureRecordRow).where(
                    LiteratureRecordRow.tenant_id == ctx.tenant_id,
                    LiteratureRecordRow.fingerprint == fp,
                )
            ).scalars().first()
        if raced is not None:
            return _maybe_conflict(
                db, raced, meta, doi=doi, fingerprint=fp, reason="RACE_UNIQUE_KEY"
            ), (
                "conflict_recorded" if _hash_differs(raced, meta) else "deduped"
            )
        raise
    return row, "created"


def _hash_differs(existing: LiteratureRecordRow, meta: LiteratureMetadata) -> bool:
    return bool(meta.raw_response_hash) and existing.raw_response_hash != meta.raw_response_hash


def _maybe_conflict(
    db: Session,
    existing: LiteratureRecordRow,
    meta: LiteratureMetadata,
    *,
    doi: str,
    fingerprint: str,
    reason: str,
) -> LiteratureRecordRow:
    if _hash_differs(existing, meta):
        db.add(
            LiteratureDedupConflictRow(
                id=uuid.uuid4(),
                tenant_id=existing.tenant_id,
                doi=doi,
                fingerprint=fingerprint,
                existing_id=existing.id,
                incoming_hash=meta.raw_response_hash,
                existing_hash=existing.raw_response_hash,
                reason=reason,
                detail={
                    "incoming_title": meta.title[:200],
                    "existing_title": existing.title[:200],
                    "provider": meta.provider,
                },
            )
        )
        db.flush()
    return existing


def list_records(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[LiteratureRecordRow]:
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    q = select(LiteratureRecordRow).where(LiteratureRecordRow.tenant_id == ctx.tenant_id)
    if project_id:
        q = q.where(LiteratureRecordRow.project_id == project_id)
    q = q.order_by(LiteratureRecordRow.created_at.desc()).limit(limit)
    return list(db.execute(q).scalars())


def record_public_dict(row: LiteratureRecordRow) -> dict:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "project_id": str(row.project_id),
        "provider": row.provider,
        "provider_version": row.provider_version,
        "provider_record_id": row.provider_record_id,
        "request_id": row.request_id,
        "query": row.query,
        "doi": row.doi,
        "title": row.title,
        "authors": row.authors,
        "published_date": row.published_date,
        "venue": row.venue,
        "abstract_available": row.abstract_available,
        "fulltext_available": row.fulltext_available,
        "rights_status": row.rights_status or "UNKNOWN",
        "rights_detail": row.rights_detail,
        "source_url": row.source_url,
        "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else None,
        "raw_response_hash": row.raw_response_hash,
        "fingerprint": row.fingerprint,
        "record_kind": row.record_kind,
        "note": "metadata ≠ Evidence；abstract ≠ full text",
    }
