"""RV13 · PaperSpec API（结构化编辑 / 撤销恢复 / 绑定健康）。

端点（prefix 由 `app.py` 注入）
--------------------------------
| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/sessions/{sid}/paper` | 最新修订 + 绑定健康（citation/figure/claim stale 如实报告） |
| PUT  | `/sessions/{sid}/paper` | 人工编辑（baseRevision 冲突 → 409 + nodeDiff） |
| POST | `/sessions/{sid}/paper:undo` | 撤销（**新**修订行，append-only） |
| POST | `/sessions/{sid}/paper:redo` | 恢复（仅当最后一步是 UNDO） |
| GET  | `/sessions/{sid}/paper/revisions` | 修订历史（含每版动作审计） |
| POST | `/sessions/{sid}/paper:validate` | 纯校验（round-trip 报告，不落库） |

边界：写路径全部要求 `research.session.write` capability + 对象级鉴权；
`undo/redo` 不可用是 **409 状态冲突**，不是 500。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..db.engine import session_scope
from ..domain.proposal_handlers import ProposalRejected
from ..paper import service as paper_service
from .deps import READ_CAPABILITY, WRITE_CAPABILITY, authorize_session, load_access_context
from .routes import _err, _json, _trace

router = APIRouter(tags=["research-vnext-rv13"])


def _tenant_of(db, session_id: uuid.UUID) -> uuid.UUID:
    from ..db.models import ResearchSessionRow

    row = db.get(ResearchSessionRow, session_id)
    if row is None:
        from ..domain.errors import SessionNotFound

        raise SessionNotFound(f"会话不存在: {session_id}")
    return row.tenant_id


@router.get("/sessions/{session_id}/paper")
def get_paper(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            payload = paper_service.read_paper(db, session_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(payload, 200, None, None)


@router.put("/sessions/{session_id}/paper")
def put_paper(
    request: Request, session_id: uuid.UUID, body: dict[str, Any] | None = Body(default=None)
) -> JSONResponse:
    """人工编辑：人工是权威作者；AI patch 走提案确认门，不走这里。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = dict(body or {})
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            result = paper_service.manual_edit(
                db,
                session_id=session_id,
                tenant_id=_tenant_of(db, session_id),
                actor_id=ctx.actor_id,
                trace_id=trace.trace_id,
                base_revision=int(raw.get("baseRevision") or 0),
                blocks=[b for b in (raw.get("blocks") or []) if isinstance(b, dict)],
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(result, 200, None, trace)


@router.post("/sessions/{session_id}/paper:undo")
def undo_paper(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            result = paper_service.undo(
                db,
                session_id=session_id,
                tenant_id=_tenant_of(db, session_id),
                actor_id=ctx.actor_id,
                trace_id=trace.trace_id,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(result, 200, None, trace)


@router.post("/sessions/{session_id}/paper:redo")
def redo_paper(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            result = paper_service.redo(
                db,
                session_id=session_id,
                tenant_id=_tenant_of(db, session_id),
                actor_id=ctx.actor_id,
                trace_id=trace.trace_id,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(result, 200, None, trace)


@router.post("/sessions/{session_id}/paper:propose")
def propose_paper_patch(
    request: Request, session_id: uuid.UUID, body: dict[str, Any] | None = Body(default=None)
) -> JSONResponse:
    """AI（或人）提出 paper patch 提案 → PendingAction。

    与消息路径共用同一组校验器：schema / evidence coverage / output safety /
    状态锁四道门一道不少。确认仍走标准 `pending-actions/{id}:accept`。
    """
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = dict(body or {})
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            pending_id = paper_service.propose_patch(
                db,
                session_id=session_id,
                tenant_id=_tenant_of(db, session_id),
                actor_id=ctx.actor_id,
                trace_id=trace.trace_id,
                content=raw,
            )
        except ProposalRejected as e:
            # 提案被拒（四道门之一）⇒ 422 + 机读错误码；**不产生** PendingAction
            return JSONResponse(
                status_code=422,
                content={"error": {"code": e.code, "message": e.message, **e.detail}},
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"pendingActionId": pending_id}, 200, None, trace)


@router.get("/sessions/{session_id}/paper/revisions")
def paper_revisions(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            items = paper_service.revision_history(db, session_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"items": items}, 200, None, None)


@router.post("/sessions/{session_id}/paper:validate")
def validate_paper(
    request: Request, session_id: uuid.UUID, body: dict[str, Any] | None = Body(default=None)
) -> JSONResponse:
    """round-trip / schema 纯校验；不落库、不产生修订。"""
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            payload = paper_service.validate_only(list((body or {}).get("blocks") or []))
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(payload, 200, None, None)
