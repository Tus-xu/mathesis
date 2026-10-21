"""RV12 · Verification 服务：执行 verifier、落结构化记录、回灌 claim。

调用链（顺序即权限边界）：

```
route → service.run_verification
         ├─ require_verifier(verifier_id)         # 未知 verifier ⇒ 400，fail closed
         ├─ assert_operation_allowed(RUN_CHECK)   # 工具策略白名单
         ├─ 读 claim / evidence / experiment       # **只读**
         ├─ 适配器算出 PASS|FAIL|UNKNOWN
         ├─ 写 VerificationRecord（+ 可选 ai_run 审计行）
         └─ claim.ledger.apply_verification_effect  # 状态变更由 claim 域负责
```

**Verifier 不写实验、不改证据、不删反证**：它只追加验证记录；
`apply_verification_effect` 属于 claim 域，且只会**追加新修订行**。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..api.deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    AccessContext,
    check_revocation,
    require_capability,
)
from ..db.models import ClaimRow, ExperimentRunRow, VerificationRecordRow
from ..evidence.models import EvidenceCardV2Row
from ..llm.context_manifest import (
    DEFAULT_TOTAL_BUDGET,
    SECTION_DOMAIN,
    SECTION_EVIDENCE,
    ContextManifest,
    ContextSection,
)
from ..llm.pricing import estimate_cost
from .adapters import run_offline_check
from .registry import VerifierSpec, require_verifier
from .types import (
    DEFAULT_TOOL_POLICY,
    RUN_CHECK,
    WRITE_ARTIFACT,
    WRITE_VERIFICATION_RECORD,
    VerificationRecord,
    assert_operation_allowed,
)

__all__ = [
    "VERIFIER_PROMPT_ID",
    "VerificationOutcome",
    "build_verifier_context_manifest",
    "list_verifications",
    "maybe_run_ai_audit",
    "record_to_dict",
    "run_verification",
    "verification_prompt_ref",
]

#: Verifier 审计提示词的 id（与 `llm/prompts/registry.py` 同源）。
VERIFIER_PROMPT_ID = "rv.verifier.audit"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(obj: Any) -> str:
    import json

    return _hash(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str))


def verification_prompt_ref() -> str:
    """取**已发布**的 verifier 审计提示词引用（未发布即抛，不静默用草稿）。"""
    from ..llm.prompts import registry as R

    return R.require_published(VERIFIER_PROMPT_ID).ref


# --------------------------------------------------------------------------
# 上下文清单
# --------------------------------------------------------------------------


def build_verifier_context_manifest(
    *,
    claim: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]] = (),
    run_refs: Sequence[str] = (),
    namespace: Mapping[str, str] | None = None,
    budget: int = DEFAULT_TOTAL_BUDGET,
) -> ContextManifest:
    """构造 verifier 的 ContextManifest。

    **只放引用与长度**，不放正文：清单会落 `research_ai_run.context_manifest`，
    而正文（主张原文 / 证据原文）已经在它们自己的表里 —— 重复落库只会扩大泄露面。
    """
    ns = {str(k): str(v) for k, v in dict(namespace or {}).items()}
    sections: list[ContextSection] = []
    statement = str(claim.get("statement") or "")
    sections.append(
        ContextSection(
            kind=SECTION_DOMAIN,
            ref=f"claim:{claim.get('claimId') or claim.get('claimKey') or 'unknown'}",
            chars=len(statement),
            included=True,
            namespace=ns.get("session", ""),
        )
    )
    for e in evidence:
        sections.append(
            ContextSection(
                kind=SECTION_EVIDENCE,
                ref=f"evidence:{e.get('evidenceId') or e.get('id') or ''}",
                chars=int(e.get("chars") or 0),
                included=True,
                authorized=bool(e.get("authorized", True)),
                namespace=ns.get("session", ""),
            )
        )
    for rid in run_refs:
        sections.append(
            ContextSection(
                kind=SECTION_DOMAIN,
                ref=f"experiment:{rid}",
                chars=0,
                included=True,
                namespace=ns.get("session", ""),
            )
        )
    used = sum(s.chars for s in sections)
    return ContextManifest(
        manifest_id=f"ctx-verifier-{uuid.uuid4().hex[:12]}",
        namespace=ns,
        budget=int(budget),
        used=used,
        truncated=False,
        sections=sections,
        excluded=[],
        injection_signals=[],
    )


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationOutcome:
    record: VerificationRecord
    claim_row: ClaimRow | None
    spec: VerifierSpec
    status_effect: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "claim": None if self.claim_row is None else {
                "claimId": str(self.claim_row.claim_key or self.claim_row.id),
                "revisionRowId": str(self.claim_row.id),
                "revision": int(self.claim_row.revision),
                "status": str(self.claim_row.status),
                "supportQuality": str(self.claim_row.support_quality),
                "supportBand": str(self.claim_row.support_band),
                "requiresReview": bool(self.claim_row.requires_review),
                "reviewReasons": list(self.claim_row.review_reasons or []),
            },
            "verifier": self.spec.as_dict(),
            "statusEffect": dict(self.status_effect),
        }


def _independent(spec: VerifierSpec, claim_row: ClaimRow, created_by: str) -> bool:
    """独立性判据：**不能由产生 claim 的同一次 AI run 自证**。"""
    return created_by != "AI_SELF" and bool(spec.verifier_id)


def run_verification(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    verifier_id: str,
    params: Mapping[str, Any] | None = None,
    run_ref: str = "",
    trace_id: str = "",
    apply_effect: bool = True,
) -> VerificationOutcome:
    """执行一次验证并落库（**只读事实、只追加记录**）。"""
    from ..claim import ledger as claim_ledger

    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)
    assert_operation_allowed(DEFAULT_TOOL_POLICY, RUN_CHECK)

    spec = require_verifier(verifier_id)  # 未知 verifier ⇒ UnknownVerifier(400)

    claim_row = claim_ledger.get_claim_row(db, claim_key)
    if str(claim_row.tenant_id) != str(ctx.tenant_id):
        from ..domain.errors import TenantMismatch

        raise TenantMismatch("claim 不属于当前 tenant")
    if str(claim_row.session_id) != str(session_id):
        from ..domain.errors import ObjectScopeMissing

        raise ObjectScopeMissing("claim 不属于该 session")

    # 只读地把事实取出来（用于规则检查与上下文清单）
    evidence_views: list[dict[str, Any]] = []
    for eid in list(claim_row.evidence_refs or []):
        card = _safe_get_card(db, str(eid), tenant_id=ctx.tenant_id)
        evidence_views.append(
            {
                "evidenceId": str(eid),
                "evidenceType": str(getattr(card, "evidence_type", "") or "PROJECT_ASSET"),
                "authorized": card is not None,
                "chars": len(getattr(card, "snippet", "") or ""),
            }
        )
    if run_ref and run_ref not in [str(x) for x in (claim_row.experiment_refs or [])]:
        # 允许验证一个**尚未登记**的实验运行，但必须真实存在且属于本租户。
        if _safe_get_run(db, run_ref, tenant_id=ctx.tenant_id) is None:
            from ..claim.errors import ClaimExperimentUnresolved

            raise ClaimExperimentUnresolved(f"实验运行不可解析: {run_ref}", runIds=[run_ref])

    outcome = run_offline_check(spec.kind, params or {})
    assert_operation_allowed(DEFAULT_TOOL_POLICY, WRITE_ARTIFACT)
    assert_operation_allowed(DEFAULT_TOOL_POLICY, WRITE_VERIFICATION_RECORD)

    manifest = build_verifier_context_manifest(
        claim={
            "claimId": str(claim_row.claim_key or claim_row.id),
            "statement": str(claim_row.text or ""),
            "claimKind": str(claim_row.claim_kind or ""),
        },
        evidence=evidence_views,
        run_refs=[str(x) for x in (claim_row.experiment_refs or [])] + ([run_ref] if run_ref else []),
        namespace={"tenant": str(ctx.tenant_id), "session": str(session_id)},
    )
    manifest_ref = manifest.manifest_id

    record_id = uuid.uuid4()
    artifacts = list(outcome.artifacts)
    if artifacts:
        # artifact ref 是**命名空间化**的：避免不同 claim 的验证产物互相覆盖。
        artifacts = [f"{a}#{record_id.hex[:8]}" for a in artifacts][: DEFAULT_TOOL_POLICY.max_artifact_refs]

    detail = dict(outcome.detail)
    detail["criteria"] = [dict(c) for c in outcome.criteria]
    detail["unknowns"] = list(outcome.unknowns)
    detail["blocking"] = sorted(
        {str(c.get("blocking")) for c in outcome.criteria if c.get("blocking")} or []
    )
    detail["manifestRef"] = manifest_ref
    detail["runRef"] = run_ref
    detail["inputHash"] = canonical_hash({"params": params or {}, "runRef": run_ref, "evidence": list(claim_row.evidence_refs or [])})
    if outcome.result != "PASS" and outcome.unknowns:
        # UNKNOWN/FAIL 的缺口必须可读，不得只留一个空 detail。
        detail["gaps"] = list(outcome.unknowns)

    cost_usd: float | None = None
    cost_basis = spec.cost_basis_default
    ai_run_id: uuid.UUID | None = None
    audit = maybe_run_ai_audit(
        db,
        ctx,
        session_id=session_id,
        claim_row=claim_row,
        spec=spec,
        record_fields={
            "result": outcome.result,
            "criteria": [dict(c) for c in outcome.criteria],
            "witness": dict(outcome.detail.get("witness") or {}),
        },
        manifest=manifest,
        trace_id=trace_id,
    )
    if audit is not None:
        cost_usd, cost_basis, ai_run_id = audit["cost_usd"], audit["cost_basis"], audit["ai_run_id"]
        detail["aiAudit"] = audit["summary"]

    row = VerificationRecordRow(
        id=record_id,
        session_id=session_id,
        tenant_id=ctx.tenant_id,
        claim_id=claim_row.id,
        claim_key=claim_row.claim_key,
        method=spec.verifier_id[:128],
        verifier=spec.verifier_id[:64],
        kind=spec.kind,
        verifier_version=spec.version,
        result=outcome.result,
        detail=detail,
        evidence_refs=[str(x) for x in (claim_row.evidence_refs or [])],
        independent=_independent(spec, claim_row, created_by="VERIFIER"),
        artifact_refs=artifacts,
        tool_policy=DEFAULT_TOOL_POLICY.as_dict(),
        prompt_ref=verification_prompt_ref(),
        context_manifest_ref=manifest_ref,
        cost_usd=cost_usd,
        cost_basis=cost_basis,
        ai_run_id=ai_run_id,
        created_by="VERIFIER",
        input_hash=detail["inputHash"],
        trace_id=trace_id,
        created_at=_now(),
    )
    db.add(row)
    db.flush()

    record = VerificationRecord(
        record_id=str(record_id),
        claim_id=str(claim_row.claim_key or claim_row.id),
        session_id=str(session_id),
        tenant_id=str(ctx.tenant_id),
        verifier_id=spec.verifier_id,
        verifier_version=spec.version,
        kind=spec.kind,
        result=outcome.result,
        criteria=outcome.to_criteria(),
        artifact_refs=tuple(artifacts),
        detail=detail,
        independent=row.independent,
        prompt_ref=row.prompt_ref,
        context_manifest_ref=manifest_ref,
        tool_policy=DEFAULT_TOOL_POLICY,
        cost_usd=cost_usd,
        cost_basis=cost_basis,
        ai_run_id=str(ai_run_id or ""),
        trace_id=trace_id,
        created_at=row.created_at.isoformat(),
    )

    updated: ClaimRow | None = None
    effect: dict[str, Any] = {"applied": False}
    if apply_effect:
        before_status = str(claim_row.status)
        updated = claim_ledger.apply_verification_effect(
            db, claim_key=claim_row.claim_key, verification_record_id=record_id
        )
        effect = {
            "applied": True,
            "fromStatus": before_status,
            "toStatus": str(updated.status) if updated is not None else "",
            "requiresReview": bool(updated.requires_review) if updated is not None else None,
            "reviewReasons": list(updated.review_reasons or []) if updated is not None else [],
        }
    return VerificationOutcome(record=record, claim_row=updated or claim_row, spec=spec, status_effect=effect)


def list_verifications(
    db: Session, ctx: AccessContext, *, session_id: uuid.UUID, claim_key: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    from sqlalchemy import select

    stmt = select(VerificationRecordRow).where(VerificationRecordRow.session_id == session_id)
    if claim_key is not None:
        stmt = stmt.where(VerificationRecordRow.claim_key == claim_key)
    rows = list(db.execute(stmt.order_by(VerificationRecordRow.created_at)).scalars().all())
    return [record_to_dict(r) for r in rows]


def record_to_dict(row: VerificationRecordRow) -> dict[str, Any]:
    detail = dict(row.detail or {})
    return {
        "recordId": str(row.id),
        "claimId": str(row.claim_key or row.claim_id or ""),
        "kind": str(row.kind or ""),
        "verifierId": str(row.verifier or ""),
        "verifierVersion": str(row.verifier_version or ""),
        "result": str(row.result),
        "independent": bool(row.independent),
        "criteria": list(detail.get("criteria") or []),
        "artifactRefs": list(row.artifact_refs or []),
        "unknowns": list(detail.get("unknowns") or []),
        "blocking": list(detail.get("blocking") or []),
        "witness": dict(detail.get("witness") or {}) if detail.get("witness") else {},
        "promptRef": str(row.prompt_ref or ""),
        "contextManifestRef": str(row.context_manifest_ref or ""),
        "toolPolicy": dict(row.tool_policy or {}),
        "costUsd": float(row.cost_usd) if row.cost_usd is not None else None,
        "costBasis": str(row.cost_basis or ""),
        "inputHash": str(row.input_hash or ""),
        "createdBy": str(row.created_by or ""),
        "traceId": str(row.trace_id or ""),
        "createdAt": row.created_at.isoformat() if row.created_at else "",
        "observationOnly": True,
    }


def maybe_run_ai_audit(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    claim_row: ClaimRow,
    spec: VerifierSpec,
    record_fields: Mapping[str, Any],
    manifest: ContextManifest,
    trace_id: str,
) -> dict[str, Any] | None:
    """可选的「AI 解释一次已算好的结果」步骤。

    **它不改判定**：`result` 已经由确定性适配器算出，模型只被要求解释。
    开关是环境变量 `RESEARCH_VERIFIER_AI_AUDIT`（默认关闭）——
    默认关闭的理由：没有这一步产品依然完整，而开了它就必须真的调用模型、
    真的记成本、真的可能失败。把它做成"可选且默认关"比做成"假装调用了"诚实。

    返回 `{cost_usd, cost_basis, ai_run_id, summary}`；未启用或不可用时返回 `None`。
    """
    import json
    import os

    if str(os.getenv("RESEARCH_VERIFIER_AI_AUDIT", "")).strip() not in {"1", "true", "True"}:
        return None

    from ..db.models import ResearchAiRunRow
    from ..llm import gateway as llm

    prompt = llm_prompt()
    backend = llm.get_backend()
    if not backend.available:
        return None

    context_text, _ = manifest_text(manifest)
    rendered = prompt.render(
        context_manifest=context_text,
        verifier_id=spec.verifier_id,
        verifier_version=spec.version,
        kind=spec.kind,
        result=str(record_fields.get("result") or ""),
        criteria_json=json.dumps(record_fields.get("criteria") or [], ensure_ascii=False),
        witness_json=json.dumps(record_fields.get("witness") or {}, ensure_ascii=False),
    )
    run_id = uuid.uuid4()
    started = _now()
    try:
        import asyncio

        completion = asyncio.run(
            backend.acompletion(
                model=llm.llm_model_long(),
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": rendered},
                ],
            )
        )
    except Exception as exc:  # noqa: BLE001 - AI 解释失败不得影响验证结果本身
        db.add(
            ResearchAiRunRow(
                id=run_id,
                session_id=session_id,
                tenant_id=ctx.tenant_id,
                provider=str(getattr(backend, "name", "")),
                model=str(getattr(backend, "model", "")),
                prompt_ref=prompt.ref,
                prompt_version=prompt.version,
                prompt_published=True,
                status="FAILED",
                error_code=type(exc).__name__[:64],
                context_manifest=manifest.to_record(),
                cost_usd=None,
                cost_basis="no-usage",
                trace_id=trace_id,
                created_at=started,
            )
        )
        db.flush()
        return None

    latency_ms = int((_now() - started).total_seconds() * 1000)
    cost = estimate_cost(
        model=completion.model,
        input_tokens=int(completion.input_tokens or 0),
        output_tokens=int(completion.output_tokens or 0),
        provider=completion.provider,
    )
    db.add(
        ResearchAiRunRow(
            id=run_id,
            session_id=session_id,
            tenant_id=ctx.tenant_id,
            provider=completion.provider,
            model=completion.model,
            prompt_ref=prompt.ref,
            prompt_version=prompt.version,
            prompt_published=True,
            status="SUCCEEDED",
            input_tokens=int(completion.input_tokens or 0),
            output_tokens=int(completion.output_tokens or 0),
            latency_ms=latency_ms,
            context_manifest=manifest.to_record(),
            cost_usd=cost.usd,
            cost_basis=cost.basis,
            trace_id=trace_id,
            created_at=started,
        )
    )
    db.flush()
    return {
        "cost_usd": cost.usd,
        "cost_basis": cost.basis,
        "ai_run_id": run_id,
        "summary": (completion.text or "")[:2000],
    }


def llm_prompt():  # noqa: ANN201 - 返回 Prompt（避免循环 import）
    from ..llm.prompts import registry as R

    return R.require_published(VERIFIER_PROMPT_ID)


def manifest_text(manifest: ContextManifest) -> tuple[str, dict[str, Any]]:
    from ..llm.context_manifest import render

    return render(manifest.sections), manifest.to_record()


def _safe_get_card(db: Session, evidence_id: str, *, tenant_id: uuid.UUID) -> EvidenceCardV2Row | None:
    try:
        card = db.get(EvidenceCardV2Row, uuid.UUID(evidence_id))
    except ValueError:
        return None
    if card is None or str(card.tenant_id) != str(tenant_id):
        return None
    return card


def _safe_get_run(db: Session, run_id: str, *, tenant_id: uuid.UUID) -> ExperimentRunRow | None:
    try:
        run = db.get(ExperimentRunRow, uuid.UUID(str(run_id)))
    except ValueError:
        return None
    if run is None or str(run.tenant_id) != str(tenant_id):
        return None
    return run
