"""RV07 · Citation Gateway 路由。

canonical 入口只认 **CSL-JSON**；BibTeX/RIS/DOI 是派生视图（含显式丢字段清单）。
`POST /citation/roundtrip` 把往返对账直接暴露成可调用接口，便于验收与排查。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..api.deps import AccessContext, load_access_context
from ..db.engine import session_scope
from ..observability.redaction import redact_mapping
from .errors import CitationError
from .roundtrip import roundtrip
from .service import (
    citation_from_csl_json,
    citation_public_dict,
    create_citation,
    get_citation,
    list_citations,
)

citation_router = APIRouter(tags=["research-citation"])


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _error(exc: CitationError, status: int = 422) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
                "detail": redact_mapping({}),
            }
        },
    )


def _uuid_or_none(value: Any) -> uuid.UUID | None:  # noqa: ANN401
    if not value:
        return None
    return uuid.UUID(str(value))


@citation_router.post("/citation")
def create_citation_route(request: Request, body: dict[str, Any]):
    ctx = _ctx(request)
    try:
        item = citation_from_csl_json(dict(body.get("csl") or {}))
        card_id = _uuid_or_none(body.get("evidence_card_id"))
        project_id = _uuid_or_none(body.get("project_id"))
        session_id = _uuid_or_none(body.get("session_id"))
    except CitationError as exc:
        return _error(exc)
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "BAD_REQUEST", "message": str(exc)}},
        )

    from ..domain.errors import DomainError

    with session_scope() as db:
        try:
            row, created = create_citation(
                db,
                ctx,
                item,
                evidence_card_id=card_id,
                project_id=project_id,
                session_id=session_id,
            )
            payload = citation_public_dict(row)
        except CitationError as exc:
            # 教材/AI 派生证据不得作为引用来源 → 403
            status = 403 if type(exc).__name__ == "CitationNotEligibleError" else 422
            return _error(exc, status)
        except DomainError as exc:
            return JSONResponse(
                status_code=exc.http_status,
                content={"error": exc.to_dict()},
            )
    payload["created"] = created
    return JSONResponse(status_code=201 if created else 200, content=payload)


@citation_router.get("/citation")
def list_citations_route(request: Request, project_id: uuid.UUID | None = None, limit: int = 100):
    ctx = _ctx(request)
    from ..domain.errors import DomainError

    with session_scope() as db:
        try:
            rows = list_citations(db, ctx, project_id=project_id, limit=limit)
        except DomainError as exc:
            return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})
    return {"citations": [citation_public_dict(r) for r in rows]}


@citation_router.get("/citation/{citation_id}")
def get_citation_route(request: Request, citation_id: uuid.UUID):
    ctx = _ctx(request)
    from ..domain.errors import DomainError

    with session_scope() as db:
        try:
            row = get_citation(db, ctx, citation_id)
        except DomainError as exc:
            return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})
    return citation_public_dict(row)


@citation_router.post("/citation/roundtrip")
def citation_roundtrip_route(request: Request, body: dict[str, Any]):
    """导出 → 回读 → 对账。返回 `lossless` 与 `lost`/`fabricated` 明细。"""
    _ctx(request)
    try:
        item = citation_from_csl_json(dict(body.get("csl") or {}))
    except CitationError as exc:
        return _error(exc)
    fmt = str(body.get("format") or "bibtex")
    try:
        parsed, report = roundtrip(item, fmt)
    except CitationError as exc:
        return _error(exc)
    return {
        "format": fmt,
        "report": report.to_dict(),
        "reparsed_csl": parsed.as_csl_json(),
    }


__all__ = ["citation_router"]
