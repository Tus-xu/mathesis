"""RV07 · Evidence Gateway 路由。

- `POST /evidence/cards` —— **提议**一条证据（模型只能走这里，不能直接写事实）；
- `GET  /evidence/cards` —— 列出（按 session/project，受 ACL 限制）；
- `GET  /evidence/cards/{id}` —— 单卡（跨租户 403）；
- `GET  /evidence/cards/{id}/locator` —— 回源（RV05 anchor 或可验证外部定位）；
- `POST /evidence/sufficiency` —— 充分性判定（UNKNOWN/UNSUPPORTED 明确拒答）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..api.deps import AccessContext, load_access_context
from ..db.engine import session_scope
from ..observability.redaction import redact_mapping
from .contracts import (
    Confidence,
    EvidenceACL,
    EvidenceProposal,
    LocatorBinding,
    Provenance,
    RightsInfo,
    SourceRef,
    StructuredFact,
)
from .errors import EvidenceError
from .frontend_projection import to_frontend_evidence_item
from .no_evidence import answer_or_refuse
from .service import (
    assess_session_evidence,
    card_public_dict,
    list_cards,
    propose_card,
    resolve_card_locator,
)
from .types import ClaimKind

evidence_router = APIRouter(tags=["research-evidence"])


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _error(exc: EvidenceError) -> JSONResponse:
    return JSONResponse(
        status_code=getattr(exc, "http_status", 400),
        content={"error": {"code": getattr(exc, "code", "EVIDENCE_ERROR"),
                           "message": str(exc),
                           "detail": redact_mapping(getattr(exc, "detail", {}) or {})}},
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _proposal_from_body(body: dict[str, Any]) -> EvidenceProposal:
    source = dict(body.get("source_ref") or {})
    locator_raw = dict(body.get("locator") or {})
    fact = body.get("structured_fact")
    return EvidenceProposal(
        evidence_type=body["evidence_type"],
        source_ref=SourceRef(
            provider=str(source.get("provider") or ""),
            provider_record_id=str(source.get("provider_record_id") or ""),
            record_kind=str(source.get("record_kind") or ""),
            fulltext_ref=str(source.get("fulltext_ref") or ""),
            abstract_ref=str(source.get("abstract_ref") or ""),
            url=str(source.get("url") or ""),
            doi=str(source.get("doi") or ""),
            source_hash=str(source.get("source_hash") or ""),
            upstream_card_ids=tuple(source.get("upstream_card_ids") or ()),
        ),
        locator=LocatorBinding(
            kind=str(locator_raw.pop("kind", "EXTERNAL")),
            payload=locator_raw,
        ),
        snippet=str(body.get("snippet") or ""),
        structured_fact=StructuredFact(**fact) if fact else None,
        provenance=Provenance(**dict(body.get("provenance") or {})),
        rights=RightsInfo(**dict(body.get("rights") or {})),
        retrieved_at=_parse_dt(body.get("retrieved_at")),
        created_by_run=str(body.get("created_by_run") or ""),
        confidence=Confidence(**dict(body.get("confidence") or {})),
        limitations=tuple(body.get("limitations") or ()),
        acl=EvidenceACL(**dict(body.get("acl") or {})),
        labels=tuple(body.get("labels") or ()),
    )


@evidence_router.post("/evidence/cards")
def propose_evidence_card(request: Request, body: dict[str, Any]):
    """AI 只能 propose：本端点只接受 proposal，不接受 card_id/status/version。"""
    ctx = _ctx(request)
    forbidden = {"card_id", "status", "version", "snippet_hash", "stale_state"}
    leaked = sorted(forbidden & set(body))
    if leaked:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "SERVER_OWNED_FIELDS",
                    "message": f"这些字段由服务端生成，不得由调用方提供: {leaked}",
                }
            },
        )
    try:
        proposal = _proposal_from_body(body)
    except EvidenceError as exc:
        return _error(exc)
    with session_scope() as db:
        try:
            row, created = propose_card(db, ctx, proposal)
            payload = card_public_dict(row)
        except EvidenceError as exc:
            return _error(exc)
    payload["created"] = created
    return JSONResponse(status_code=201 if created else 200, content=payload)


@evidence_router.get("/evidence/cards")
def list_evidence_cards(
    request: Request,
    session_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = 100,
    view: str = "internal",
):
    """列出证据卡。

    `view`（**加法式**，默认 `internal` 保持既有形状不变）：

    - `internal` —— 本域公开投影（`card_public_dict`），供服务端/调试消费；
    - `frontend` —— RV08 前端契约投影（`ResearchEvidenceItem` 形状，
      见 `frontend_projection.py`），字段名/类型与 `types/research.ts` 对齐。

    RV08 的 `EvidenceCard.vue`（`data-testid="rv07-evidence-card"`）已建好展示层，
    但其取数路径（snapshot.evidence）走的是 CR-6 扁平表；本参数提供一条
    **不改动 snapshot** 的旁路，供 RV08 选择消费。
    """
    if view not in ("internal", "frontend"):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": f"view 只支持 'internal' | 'frontend'，收到 {view!r}",
                }
            },
        )
    ctx = _ctx(request)
    with session_scope() as db:
        try:
            rows = list_cards(db, ctx, session_id=session_id, project_id=project_id, limit=limit)
            publics = [card_public_dict(r) for r in rows]
        except EvidenceError as exc:
            return _error(exc)
    payload = (
        [to_frontend_evidence_item(p) for p in publics] if view == "frontend" else publics
    )
    return {"cards": payload, "count": len(payload), "view": view}


@evidence_router.get("/evidence/cards/{card_id}")
def get_evidence_card(request: Request, card_id: uuid.UUID):
    ctx = _ctx(request)
    from .service import get_card

    with session_scope() as db:
        try:
            row = get_card(db, ctx, card_id)
            payload = card_public_dict(row)
        except EvidenceError as exc:
            return _error(exc)
    return payload


@evidence_router.get("/evidence/cards/{card_id}/locator")
def resolve_evidence_locator(request: Request, card_id: uuid.UUID):
    """点击证据 → 回源。**stale 时如实返回 stale，不静默指向新内容。**"""
    ctx = _ctx(request)
    with session_scope() as db:
        try:
            resolved = resolve_card_locator(db, ctx, card_id)
        except EvidenceError as exc:
            return _error(exc)
    return resolved.to_dict()


@evidence_router.post("/evidence/sufficiency")
def evidence_sufficiency(request: Request, body: dict[str, Any]):
    """充分性判定 + 拒答语义：UNKNOWN/UNSUPPORTED 一律 `citable=False`。"""
    ctx = _ctx(request)
    try:
        session_id = uuid.UUID(str(body["session_id"]))
        claim_kind = ClaimKind(str(body.get("claim_kind") or "PARAPHRASE"))
    except (KeyError, ValueError) as exc:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "BAD_REQUEST", "message": str(exc)}},
        )
    with session_scope() as db:
        try:
            sufficiency = assess_session_evidence(
                db, ctx, session_id=session_id, claim_kind=claim_kind
            )
        except EvidenceError as exc:
            return _error(exc)
    return {**sufficiency.to_dict(), **answer_or_refuse(sufficiency)}


__all__ = ["evidence_router"]
