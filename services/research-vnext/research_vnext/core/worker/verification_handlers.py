"""RV12 · Verifier job handler（`verification.verify`）。

为什么验证要有异步路径：robustness 的多随机种子 / 数据复现可能要跑几十秒，
放同步 HTTP 里会撞 worker 的 stage 超时语义不一致；而 dispatch 只入队，
重试/围栏/取消全部复用 RV03 的既有语义。

**权限边界**：handler 与 service 走同一条路径，因此同样受
`ToolPolicy` 约束（无网络、不改实验、不删反证）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from ..db.engine import session_scope
from ..db.models import ClaimRow
from .runtime import HandlerContext, HandlerOutcome


async def verification_verify_handler(ctx: HandlerContext) -> HandlerOutcome:
    """执行一次验证：payload = `{claimKey, verifierId, params, runRef}`。"""
    from ..api.deps import WRITE_CAPABILITY, AccessContext
    from ..db.models import JobRow
    from ..verification import service as verification_service

    payload = dict(ctx.input_payload or {})
    claim_key_raw = str(payload.get("claimKey") or payload.get("claim_key") or "")
    verifier_id = str(payload.get("verifierId") or payload.get("verifier_id") or "")
    if not claim_key_raw or not verifier_id:
        return HandlerOutcome(
            failed_stages=["verify"],
            error_code="INVALID_INPUT",
            error_message="verification.verify payload 需要 claimKey 与 verifierId",
        )
    try:
        claim_key = uuid.UUID(claim_key_raw)
    except ValueError:
        return HandlerOutcome(
            failed_stages=["verify"], error_code="INVALID_INPUT", error_message="claimKey 不是 UUID"
        )

    if ctx.stopped():
        return HandlerOutcome(
            succeeded_stages=[], failed_stages=["verify"], error_code="CANCELLED",
            error_message="取消请求在安全点生效（验证前）",
        )
    await ctx.report(stage="verify", progress=10)

    with session_scope() as db:
        # worker 的 AccessContext 由**已验证的 job 行**构造（不是伪造的网关头）。
        # scope 收敛到该 job 自己的 session：worker 不能借这条路径访问别的会话。
        job = db.get(JobRow, ctx.job_id)
        scope_session = str(job.session_id) if job is not None else str(ctx.session_id)
        access = AccessContext(
            actor_id=(job.user_id if job is not None and job.user_id else ctx.tenant_id),
            tenant_id=ctx.tenant_id,
            workspace="research",
            domain="research-vnext",
            capabilities=[WRITE_CAPABILITY],
            resource_scopes=[{"kind": "session", "id": scope_session, "role": "worker"}],
            security_version=1,
            policy_version="worker",
            trace_id="",
            session_id=ctx.session_id,
        )
        try:
            outcome = verification_service.run_verification(
                db,
                access,
                session_id=ctx.session_id,
                claim_key=claim_key,
                verifier_id=verifier_id,
                params=payload.get("params") if isinstance(payload.get("params"), dict) else {},
                run_ref=str(payload.get("runRef") or ""),
                trace_id="",
            )
        except Exception as exc:  # noqa: BLE001
            # 未知 verifier 是**永久**错误：重试不会变好，直接 FAILED 并说清原因。
            code = getattr(exc, "code", "") or "VERIFIER_FAILED"
            permanent = code in {"UNKNOWN_VERIFIER", "CLAIM_NOT_FOUND", "TOOL_POLICY_VIOLATION"}
            return HandlerOutcome(
                succeeded_stages=[],
                failed_stages=["verify"],
                error_code=code,
                error_message=str(exc)[:500],
                result={"retryable": not permanent},
            )
        payload_out = outcome.to_dict()
        claim = (
            db.get(ClaimRow, uuid.UUID(payload_out["claim"]["revisionRowId"]))
            if payload_out.get("claim")
            else None
        )
        row_ref = str(claim.id) if claim is not None else ""

    if ctx.stopped():
        return HandlerOutcome(
            succeeded_stages=[], failed_stages=["verify"], error_code="CANCELLED",
            error_message="取消请求在安全点生效（落库后）",
        )
    return HandlerOutcome(
        result={**payload_out, "claimRowId": row_ref},
        succeeded_stages=["verify"],
        failed_stages=[],
    )


def _claim_exists(db: Any, claim_key: uuid.UUID) -> bool:  # pragma: no cover - 诊断辅助
    return db.execute(select(ClaimRow).where(ClaimRow.claim_key == claim_key)).first() is not None


def register_verification_jobs() -> None:
    from .runtime import register

    register("verification.verify", verification_verify_handler)


__all__ = ["register_verification_jobs", "verification_verify_handler"]
