"""RV12 · Claim / Verification API。

端点（prefix 由 `app.py` 注入）
--------------------------------
| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/sessions/{sid}/claims:propose` | AI/人 提出 claim（状态由服务端 Gate 推导） |
| GET | `/sessions/{sid}/claims` | 本会话的 claim 列表（每 claim 取最新修订） |
| GET | `/sessions/{sid}/claims/{cid}/chain` | claim→evidence→experiment→verification 链路 + 反证 + 未知 |
| POST | `/sessions/{sid}/claims/{cid}:link-evidence` | 关联证据（含角色：支撑/反驳） |
| POST | `/sessions/{sid}/claims/{cid}:link-experiment` | 关联实验运行 |
| POST | `/sessions/{sid}/claims/{cid}:evaluate` | 重跑 Gate（写迁移审计） |
| POST | `/sessions/{sid}/claims/{cid}:publish` | **人工**发布关键 claim（要求 SOLVED） |
| POST | `/sessions/{sid}/claims/{cid}/verifications:run` | 跑一次 verifier |
| GET | `/sessions/{sid}/claims/{cid}/verifications` | 验证记录列表 |

边界：只读写 claim/verification 域自己的表；`{cid}` 是**跨修订稳定**的 `claim_key`。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..application import experiment_run_service  # noqa: F401  (保留包导入一致性)
from ..claim import ledger as claim_ledger
from ..db.engine import session_scope
from ..verification import service as verification_service
from .deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    authorize_session,
    load_access_context,
)
from .routes import _err, _json, _trace

router = APIRouter(tags=["research-vnext-rv12"])


def _body(body_in: dict[str, Any] | None) -> dict[str, Any]:
    return dict(body_in or {})


#: 允许出现在**顶层**的 claim 字段（兼容 `{"claim": {...}, "evidenceRefs": [...]}`
#: 与"所有字段平铺"两种调用形状）。形状收敛只在这里做一次 ——
#: 漏掉 evidenceRefs 会让一条本该有证据的 claim 静默变成"无证据"。
_TOP_LEVEL_CLAIM_KEYS = (
    "statement",
    "scope",
    "assumptions",
    "claimKind",
    "claim_kind",
    "limitations",
    "proposedStatus",
    "proposed_status",
    "rationale",
    "evidenceRefs",
    "evidence_refs",
    "experimentRefs",
    "experiment_refs",
)


def _claim_payload(raw: dict[str, Any]) -> dict[str, Any]:
    nested = raw.get("claim")
    payload = dict(nested) if isinstance(nested, dict) else dict(raw)
    for key in _TOP_LEVEL_CLAIM_KEYS:
        if raw.get(key) is not None and payload.get(key) is None:
            payload[key] = raw[key]
    return payload


@router.post("/sessions/{session_id}/claims:propose")
def propose_claim(
    request: Request,
    session_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """AI 或人提出一个 claim。`proposedStatus` 只进审计，**不**成为权威状态。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = _body(body_in)
    claim_payload = _claim_payload(raw)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            row = claim_ledger.propose_claim(
                db,
                ctx,
                session_id=session_id,
                payload=claim_payload,
                expected_revision=(
                    int(raw["expectedRevision"]) if raw.get("expectedRevision") is not None else None
                ),
                client_request_id=str(raw.get("client_request_id") or ""),
                trace_id=trace.trace_id,
                owner_id=ctx.actor_id,
            )
            payload = claim_ledger.claim_public_dict(row)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"claim": payload}, 200, None, trace)


@router.get("/sessions/{session_id}/claims")
def list_claims(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            items = claim_ledger.list_claims(db, ctx, session_id=session_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"items": items}, 200, None, None)


@router.get("/sessions/{session_id}/claims/{claim_key}/chain")
def claim_chain(request: Request, session_id: uuid.UUID, claim_key: uuid.UUID) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            chain = claim_ledger.claim_chain(db, ctx, claim_key=claim_key)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"chain": chain}, 200, None, None)


@router.post("/sessions/{session_id}/claims/{claim_key}:link-evidence")
def link_evidence(
    request: Request,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = _body(body_in)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            row = claim_ledger.link_evidence(
                db,
                ctx,
                session_id=session_id,
                claim_key=claim_key,
                evidence_id=str(raw.get("evidenceId") or ""),
                role=str(raw.get("role") or "SUPPORTS"),
                claim_kind=raw.get("claimKind"),
                note=str(raw.get("note") or ""),
                expected_revision=(
                    int(raw["expectedRevision"]) if raw.get("expectedRevision") is not None else None
                ),
                trace_id=trace.trace_id,
            )
            payload = claim_ledger.claim_public_dict(row)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"claim": payload}, 200, None, trace)


@router.post("/sessions/{session_id}/claims/{claim_key}:link-experiment")
def link_experiment(
    request: Request,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = _body(body_in)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            row = claim_ledger.link_experiment(
                db,
                ctx,
                session_id=session_id,
                claim_key=claim_key,
                run_id=str(raw.get("runId") or ""),
                role=str(raw.get("role") or "SUPPORTS"),
                note=str(raw.get("note") or ""),
                expected_revision=(
                    int(raw["expectedRevision"]) if raw.get("expectedRevision") is not None else None
                ),
                trace_id=trace.trace_id,
            )
            payload = claim_ledger.claim_public_dict(row)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"claim": payload}, 200, None, trace)


@router.post("/sessions/{session_id}/claims/{claim_key}:evaluate")
def evaluate_claim(
    request: Request,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """重跑 Gate。状态是否变化都写一条迁移审计（否则无法复核"为什么变了"）。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = _body(body_in)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            row = claim_ledger.evaluate_claim(
                db,
                ctx,
                session_id=session_id,
                claim_key=claim_key,
                expected_revision=(
                    int(raw["expectedRevision"]) if raw.get("expectedRevision") is not None else None
                ),
                counterevidence_addressed=bool(raw.get("counterevidenceAddressed")),
                trace_id=trace.trace_id,
            )
            payload = claim_ledger.claim_public_dict(row)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"claim": payload}, 200, None, trace)


@router.post("/sessions/{session_id}/claims/{claim_key}:publish")
def publish_claim(
    request: Request,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """**人工**发布。要求已满足 SOLVED 判据且无待复核项。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = _body(body_in)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            row = claim_ledger.publish_claim(
                db,
                ctx,
                session_id=session_id,
                claim_key=claim_key,
                note=str(raw.get("note") or ""),
                trace_id=trace.trace_id,
            )
            payload = claim_ledger.claim_public_dict(row)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"claim": payload}, 200, None, trace)


@router.post("/sessions/{session_id}/claims/{claim_key}/verifications:run")
def run_verification(
    request: Request,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """跑一次 verifier。未知 verifier ⇒ 400（不认识的方法不能"默认通过"）。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = _body(body_in)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            outcome = verification_service.run_verification(
                db,
                ctx,
                session_id=session_id,
                claim_key=claim_key,
                verifier_id=str(raw.get("verifierId") or ""),
                params=raw.get("params") if isinstance(raw.get("params"), dict) else {},
                run_ref=str(raw.get("runRef") or ""),
                trace_id=trace.trace_id,
            )
            payload = outcome.to_dict()
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(payload, 200, None, trace)


@router.get("/sessions/{session_id}/claims/{claim_key}/verifications")
def list_verifications(
    request: Request, session_id: uuid.UUID, claim_key: uuid.UUID
) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            items = verification_service.list_verifications(
                db, ctx, session_id=session_id, claim_key=claim_key
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"items": items}, 200, None, None)


@router.get("/verification-verifiers")
def list_verifiers(request: Request) -> JSONResponse:
    """verifier 目录（前端据此渲染"可用的验证方法"，未知方法不给选项）。"""
    load_access_context(request)
    from ..verification.registry import VERIFIER_SPECS

    return _json(
        {"items": [spec.as_dict() for spec in VERIFIER_SPECS.values()]}, 200, None, None
    )
