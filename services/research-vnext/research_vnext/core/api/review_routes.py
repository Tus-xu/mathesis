"""RV15 · Review Finding 域 REST 路由。

前缀由 app.py 统一挂载（api_prefixes() 的每个前缀）：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/sessions/{sid}/review:propose` | Reviewer AI（或人）提案 findings → PendingAction |
| GET  | `/sessions/{sid}/review` | finding 列表（含豁免到期自动重开） |
| GET  | `/sessions/{sid}/review/blocking` | 未解决 Gate（BLOCKING-GATE.md 唯一实现） |
| POST | `/sessions/{sid}/review/{fid}:transition` | ACKNOWLEDGED / IN_PROGRESS / REOPENED |
| POST | `/sessions/{sid}/review/{fid}:resolve` | RESOLVED（修复修订 + 证据） |
| POST | `/sessions/{sid}/review/{fid}:waive` | WAIVED（理由/批准/到期） |
| POST | `/sessions/{sid}/review/{fid}:recheck` | stale 复验 |
| GET  | `/sessions/{sid}/review/taxonomy` | 封闭 taxonomy + severity 默认（前端契约） |
| POST | `/sessions/{sid}/review:run` | 发起一次 Review run（worker，review.review） |

边界：只读写 review 域自己的表；fid 是 finding 行 id。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from .deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    authorize_session,
    load_access_context,
)
from .routes import _err, _json, _trace

router = APIRouter(tags=["research-vnext-rv15"])


@router.post("/sessions/{session_id}/review:propose")
def propose_review(
    request: Request,
    session_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """Reviewer AI 提案入口：校验四门 → PendingAction（**不**写正式状态）。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = dict(body_in or {})
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            from ..review.service import propose_findings, propose_preview

            preview = propose_preview(
                findings=list(raw.get("findings") or []),
            )
            pa_id = propose_findings(
                db,
                session_id=session_id,
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                trace_id=trace.trace_id,
                findings=list(raw.get("findings") or []),
                created_by_run=str(raw.get("createdByRun") or ""),
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"pendingActionId": pa_id, "findings": preview}, 200, None, trace)


@router.get("/sessions/{session_id}/review")
def list_findings(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            from ..review import service as review_service

            items = review_service.list_findings(
                db, session_id=session_id, tenant_id=ctx.tenant_id
            )
            blocking = review_service.blocking_summary(db, session_id=session_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"items": items, "blocking": blocking}, 200, None, None)


@router.get("/sessions/{session_id}/review/blocking")
def blocking_gate(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            from ..review import service as review_service

            body = review_service.blocking_summary(db, session_id=session_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, None)


@router.get("/sessions/{session_id}/review/taxonomy")
def review_taxonomy(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    from ..review.taxonomy import (
        ALLOWED_TARGET_TYPES,
        FINDING_CATEGORIES,
        REQUIRES_EVIDENCE,
        SEVERITIES,
        SEVERITY_DEFAULTS,
        WAIVER_REASON_CODES,
    )

    return _json(
        {
            "categories": list(FINDING_CATEGORIES),
            "severities": list(SEVERITIES),
            "severityDefaults": dict(SEVERITY_DEFAULTS),
            "requiresEvidence": {k: bool(v) for k, v in REQUIRES_EVIDENCE.items()},
            "allowedTargetTypes": {k: sorted(v) for k, v in ALLOWED_TARGET_TYPES.items()},
            "waiverReasonCodes": sorted(WAIVER_REASON_CODES),
        },
        200,
        None,
        None,
    )


def _finding_action(
    request: Request,
    session_id: uuid.UUID,
    finding_id: uuid.UUID,
    action: str,
    body_in: dict[str, Any] | None,
) -> JSONResponse:
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = dict(body_in or {})
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            from ..review import service as review_service

            if action == "transition":
                body = {"finding": review_service.transition_finding(
                    db,
                    session_id=session_id, finding_id=finding_id,
                    actor_id=ctx.actor_id,
                    to=str(raw.get("to") or ""),
                    assignee=str(raw.get("assignee") or ""),
                    expected_revision=raw.get("expectedRevision"),
                    trace_id=trace.trace_id,
                )}
            elif action == "resolve":
                body = {"finding": review_service.resolve_finding(
                    db,
                    session_id=session_id, finding_id=finding_id,
                    actor_id=ctx.actor_id,
                    resolution=dict(raw.get("resolution") or {}),
                    trace_id=trace.trace_id,
                )}
            elif action == "waive":
                body = {"finding": review_service.waive_finding(
                    db,
                    session_id=session_id, finding_id=finding_id,
                    actor_id=ctx.actor_id,
                    waiver=dict(raw.get("waiver") or {}),
                    by_run=str(raw.get("createdByRun") or ""),
                    trace_id=trace.trace_id,
                )}
            else:  # recheck
                body = {"finding": review_service.recheck_finding(
                    db,
                    session_id=session_id, finding_id=finding_id,
                    actor_id=ctx.actor_id,
                    still_present=bool(raw.get("stillPresent", True)),
                    trace_id=trace.trace_id,
                )}
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.post("/sessions/{session_id}/review/{finding_id}:transition")
def finding_transition(
    request: Request, session_id: uuid.UUID, finding_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    return _finding_action(request, session_id, finding_id, "transition", body_in)


@router.post("/sessions/{session_id}/review/{finding_id}:resolve")
def finding_resolve(
    request: Request, session_id: uuid.UUID, finding_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    return _finding_action(request, session_id, finding_id, "resolve", body_in)


@router.post("/sessions/{session_id}/review/{finding_id}:waive")
def finding_waive(
    request: Request, session_id: uuid.UUID, finding_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    return _finding_action(request, session_id, finding_id, "waive", body_in)


@router.post("/sessions/{session_id}/review/{finding_id}:recheck")
def finding_recheck(
    request: Request, session_id: uuid.UUID, finding_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    return _finding_action(request, session_id, finding_id, "recheck", body_in)


@router.post("/sessions/{session_id}/review:run")
def review_run(request: Request, session_id: uuid.UUID) -> JSONResponse:
    """发起一次 Review run（确定性检测管线，worker `review.review`）。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            from ..review import service as review_service

            body = review_service.start_review_run(
                db,
                session_id=session_id,
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                trace_id=trace.trace_id,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


from ..db.engine import session_scope  # noqa: E402
