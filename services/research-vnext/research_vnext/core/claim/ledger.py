"""RV12 · ClaimLedger —— claim 的**唯一**写入口。

职责边界（刻意保持窄）：

| 谁 | 能做什么 |
|---|---|
| 模型 | 只能产出 `ClaimProposal`（无权威 status） |
| 本模块（服务端） | 解析证据/实验/验证的真实版本，跑 Gate，写修订行 + 迁移审计 |
| Verifier | 只能**追加** `VerificationRecord`；它不写 claim（见 `apply_verification_effect`） |
| 人 | 只有人能 `publish`（发布关键 claim） |

三条不变量：

1. **状态永远由 Gate 推导**。任何入口（HTTP、消息提案、验证回灌）都不接受
   外部传入的权威 status；模型的 `proposedStatus` 只进 `audit`。
2. **反证只增不减**。`counterEvidence` 与已有内容取并集；"结论翻正了就把反证删掉"
   在数据结构层面不可能发生。
3. **修订链只追加**。每次变更写新行，旧行原样保留 —— "为什么现在是这个状态"
   可以逐版回放。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..api.deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    AccessContext,
    check_revocation,
    require_capability,
)
from ..db.models import (
    ClaimEvidenceLinkRow,
    ClaimRow,
    ClaimTransitionRow,
    ExperimentRunRow,
    VerificationRecordRow,
)
from ..domain.errors import ObjectScopeMissing, RevisionConflict, TenantMismatch
from ..evidence.contracts import REQUIRED_CARD_FIELDS  # noqa: F401  (契约存在性自检)
from ..evidence.models import EvidenceCardV2Row
from ..evidence.resolvable import resolvable_evidence_ids
from ..evidence.types import ClaimKind
from .aggregate import (
    ClaimAggregate,
    ClaimProposal,
    ClaimScope,
    CounterEvidence,
    ExperimentLink,
    aggregate_from_content,
    coerce_proposal,
    materialize_claim,
)
from .errors import (
    ClaimEvidenceUnresolved,
    ClaimExperimentUnresolved,
    ClaimPublishRejected,
    ClaimUnknown,
)
from .state_machine import (
    ClaimGateSnapshot,
    derive_status,
    validate_transition,
)
from .support import aggregate_support, assess_evidence_support
from .types import ClaimStatus, EvidenceLinkRole, SupportQuality

__all__ = [
    "apply_verification_effect",
    "claim_chain",
    "claim_public_dict",
    "evaluate_claim",
    "get_claim_row",
    "latest_claim_row",
    "link_evidence",
    "link_experiment",
    "list_claims",
    "propose_claim",
    "publish_claim",
    "snapshot_for_claim",
]

_AGGREGATE_KEYS = (
    "statement",
    "scope",
    "assumptions",
    "claimKind",
    "limitations",
    "evidenceLinks",
    "experimentLinks",
    "counterEvidence",
    "confidenceBasis",
    "audit",
    "supportQuality",
    "requiresReview",
    "reviewReasons",
    "gateId",
    "satisfied",
    "missing",
    "conditions",
    "refutation",
    "status",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(obj: Any) -> str:
    import json

    return _hash(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str))


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------


def get_claim_row(db: Session, claim_key: uuid.UUID) -> ClaimRow:
    row = (
        db.execute(
            select(ClaimRow).where(ClaimRow.claim_key == claim_key).order_by(ClaimRow.revision.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        # 兼容：0012 之前落的历史行没有 claim_key，按行 id 兜底（只读路径）。
        legacy = db.get(ClaimRow, claim_key)
        if legacy is None:
            raise ClaimUnknown(f"claim 不存在: {claim_key}")
        return legacy
    return row


def latest_claim_row(db: Session, claim_key: uuid.UUID) -> ClaimRow:
    return get_claim_row(db, claim_key)


def _authorize_claim(
    db: Session, ctx: AccessContext, row: ClaimRow, *, capability: str = READ_CAPABILITY
) -> ClaimRow:
    """claim 的对象级鉴权：tenant → session/project scope → capability → 撤权。"""
    check_revocation(db, ctx)
    require_capability(ctx, capability)
    if str(row.tenant_id) != str(ctx.tenant_id):
        raise TenantMismatch(
            "claim 不属于当前 tenant",
            claim_tenant=str(row.tenant_id),
            actor_tenant=str(ctx.tenant_id),
        )
    owner = str(row.owner_id or row.confirmed_by or "")
    if str(row.session_id) in ctx.session_scope_ids:
        return row
    if owner == str(ctx.actor_id):
        return row
    if ctx.project_scope_ids:
        # `claim` 行**不含** project_id（它是会话内对象）。调用方（路由）已用
        # `authorize_session` 把该 project scope 与会话绑定校验过一次；这里不再
        # 二次拒绝，否则"只有 project scope 的合法调用方"会被永久锁在门外。
        # 真正越界的路径（project 与会话不匹配）在上游已经被拒。
        return row
    raise ObjectScopeMissing("缺少对象级 scope（禁止仅凭 identity 读 claim）")


def list_claims(db: Session, ctx: AccessContext, *, session_id: uuid.UUID) -> list[dict[str, Any]]:
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    keys = [
        k
        for (k,) in db.execute(
            select(ClaimRow.claim_key).where(ClaimRow.session_id == session_id).distinct()
        ).all()
    ]
    out: list[dict[str, Any]] = []
    for key in keys:
        if key is None:
            continue
        row = get_claim_row(db, key)
        out.append(claim_public_dict(row))
    out.sort(key=lambda c: (c.get("createdAt") or "", c.get("claimId") or ""))
    return out


# --------------------------------------------------------------------------
# 修订写入
# --------------------------------------------------------------------------


def _next_revision(db: Session, session_id: uuid.UUID) -> int:
    current = db.scalar(select(func.max(ClaimRow.revision)).where(ClaimRow.session_id == session_id))
    return int(current or 0) + 1


def _current_revision(db: Session, *, session_id: uuid.UUID, claim_key: uuid.UUID | None) -> int:
    if claim_key is not None:
        row = (
            db.execute(
                select(ClaimRow).where(ClaimRow.claim_key == claim_key).order_by(ClaimRow.revision.desc())
            )
            .scalars()
            .first()
        )
        return int(row.revision) if row else 0
    current = db.scalar(select(func.max(ClaimRow.revision)).where(ClaimRow.session_id == session_id))
    return int(current or 0)


def _assert_revision(
    db: Session, *, session_id: uuid.UUID, claim_key: uuid.UUID | None, expected: int | None
) -> None:
    """`expectedRevision` 冲突 ⇒ 409（附当前值，前端可据此并排显示 diff）。"""
    if expected is None:
        return
    current = _current_revision(db, session_id=session_id, claim_key=claim_key)
    if int(expected) != current:
        raise RevisionConflict(
            int(expected),
            current,
            conflict={"resource": "claim", "expected": int(expected), "current": current},
        )


def _write_revision(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    claim_key: uuid.UUID,
    content: dict[str, Any],
    status: str,
    support_quality: str,
    support_band: str,
    requires_review: bool,
    review_reasons: Sequence[str],
    owner_id: uuid.UUID | None,
    acl: dict[str, Any],
    actor_id: uuid.UUID | None,
    trace_id: str,
    client_request_id: str,
    text: str,
    relation: str,
    evidence_refs: Sequence[str],
    experiment_refs: Sequence[str],
    verification_refs: Sequence[str],
    published_at: datetime | None = None,
    published_by: uuid.UUID | None = None,
) -> ClaimRow:
    now = _now()
    row = ClaimRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        revision=_next_revision(db, session_id),
        content=content,
        content_hash=canonical_hash(content),
        client_request_id=client_request_id[:128],
        confirmed_at=now,
        confirmed_by=actor_id,
        trace_id=trace_id,
        created_at=now,
        text=text,
        status=status,
        support_quality=support_quality,
        relation=relation,
        evidence_refs=list(evidence_refs),
        experiment_refs=list(experiment_refs),
        verification_refs=list(verification_refs),
        claim_key=claim_key,
        owner_id=owner_id,
        acl=dict(acl),
        claim_kind=str(content.get("claimKind") or ClaimKind.PARAPHRASE.value),
        support_band=support_band,
        requires_review=bool(requires_review),
        review_reasons=list(review_reasons),
        published_at=published_at,
        published_by=published_by,
    )
    db.add(row)
    db.flush()
    return row


def _record_transition(
    db: Session,
    *,
    row: ClaimRow,
    previous: ClaimRow | None,
    decision: Any,
    actor_id: uuid.UUID | None,
    actor_kind: str,
    trace_id: str,
    action: str,
    published: bool = False,
) -> ClaimTransitionRow:
    transition = ClaimTransitionRow(
        claim_key=row.claim_key,
        claim_id=row.id,
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        revision=int(row.revision),
        from_status=str(previous.status) if previous is not None else "",
        to_status=str(row.status),
        gate_id=str(decision.gate_id if decision is not None else ""),
        satisfied=list(getattr(decision, "satisfied", ()) or ()),
        missing=list(getattr(decision, "missing", ()) or ()),
        conditions=list(getattr(decision, "conditions", ()) or ()),
        refutation=list(getattr(decision, "refutation", ()) or ()),
        review_reasons=list(getattr(decision, "review_reasons", ()) or ()),
        requires_review=bool(row.requires_review),
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action,
        published=bool(published),
        trace_id=trace_id,
        created_at=_now(),
    )
    db.add(transition)
    db.flush()
    return transition


# --------------------------------------------------------------------------
# 快照（把事实读成 Gate 输入）
# --------------------------------------------------------------------------


def _evidence_view(db: Session, *, evidence_id: str, session_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, Any]:
    """一张证据卡的行视图；不可解析（跨租户/不存在/未授权）时给出**不可用**视图。"""
    try:
        card = db.get(EvidenceCardV2Row, uuid.UUID(evidence_id))
    except ValueError:
        card = None
    if card is not None and str(card.tenant_id) == str(tenant_id):
        locator = dict(card.locator or {})
        locator.setdefault("kind", card.locator_kind or "EXTERNAL")
        return {
            "evidence_id": str(card.id),
            "evidence_type": card.evidence_type,
            "rights_status": card.rights_status,
            "status": card.status,
            "stale_state": card.stale_state,
            "locator": locator,
            "locator_stale": bool(card.stale_state == "STALE"),
            "retrieved_at": card.retrieved_at.isoformat() if card.retrieved_at else None,
            "citation_eligible": bool(card.citation_eligible),
            "source_record_kind": dict(card.source_ref or {}).get("record_kind", ""),
            "upstream_card_ids": list(dict(card.source_ref or {}).get("upstream_card_ids") or []),
            "snippet": card.snippet or "",
            "structured_fact": card.structured_fact,
            "retracted": bool(card.status == "INVALIDATED" and card.stale_state == "STALE"),
            "resolvable": True,
        }
    # 遗留表（RV09 `evidence_card`）：只有引用位，没有类型/权利事实。
    # 按"项目内自有资产 + PERMITTED"处理，并在视图里标明来源，避免伪装成文献证据。
    resolvable = evidence_id in resolvable_evidence_ids(db, session_id=session_id)
    return {
        "evidence_id": evidence_id,
        "evidence_type": "PROJECT_ASSET",
        "rights_status": "PERMITTED" if resolvable else "UNKNOWN",
        "status": "ACTIVE" if resolvable else "REJECTED",
        "stale_state": "FRESH",
        "locator": {"kind": "ANCHOR", "asset_id": evidence_id, "asset_version": 1,
                    "content_hash": _hash(evidence_id), "anchor_id": evidence_id, "anchor_type": "page"},
        "locator_stale": False,
        "retrieved_at": None,
        "citation_eligible": False,
        "source_record_kind": "PROJECT_ASSET",
        "upstream_card_ids": [],
        "snippet": "",
        "structured_fact": None,
        "retracted": False,
        "resolvable": resolvable,
        "legacy": True,
    }


def _experiment_view(db: Session, *, run_id: str, tenant_id: uuid.UUID) -> dict[str, Any]:
    from ..domain.experiment_run import reproducibility_missing

    try:
        run = db.get(ExperimentRunRow, uuid.UUID(run_id))
    except ValueError:
        run = None
    if run is None or str(run.tenant_id) != str(tenant_id):
        return {
            "run_id": run_id,
            "role": "SUPPORTS",
            "reproducible": False,
            "exit_code": None,
            "repro_missing": ("UNAVAILABLE",),
            "resolvable": False,
        }
    missing = reproducibility_missing(dict(run.repro or {}))
    return {
        "run_id": str(run.id),
        "role": "SUPPORTS",
        "reproducible": not missing,
        "exit_code": run.exit_code,
        "repro_missing": tuple(missing),
        "status": run.status,
        "resolvable": True,
    }


def _verification_views(db: Session, *, row: ClaimRow) -> list[dict[str, Any]]:
    stmt = select(VerificationRecordRow).where(
        or_(
            VerificationRecordRow.claim_id == row.id,
            VerificationRecordRow.claim_key == row.claim_key,
        )
    )
    records = list(db.execute(stmt.order_by(VerificationRecordRow.created_at)).scalars().all())
    out: list[dict[str, Any]] = []
    for r in records:
        blocking = list((r.detail or {}).get("blocking") or [])
        out.append(
            {
                "record_id": str(r.id),
                "kind": str(r.kind or r.method or ""),
                "result": str(r.result),
                "independent": bool(r.independent),
                "criteria_satisfied": all(
                    bool(c.get("passed")) for c in ((r.detail or {}).get("criteria") or [])
                )
                if (r.detail or {}).get("criteria")
                else str(r.result) == "PASS",
                "blocking": tuple(blocking),
                "detail": dict(r.detail or {}),
                "verifier_id": str(r.verifier or ""),
            }
        )
    return out


def _known_counter_evidence(content: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(c) for c in (content.get("counterEvidence") or ()) if isinstance(c, Mapping)]


#: 哪些验证失败构成**反证**（BLOCKING），哪些只是"结论不稳"（DEGRADING）。
#: 判据差异不是文风：数学/复现/反例失败说明"结论本身不成立"，
#: 而敏感性/robustness 失败说明"结论在边界上不稳" —— 后者应降级为 CONDITIONAL，
#: 不该被当成已证伪（否则任何参数敏感的正确结论都会被误判为推翻）。
_BLOCKING_VERIFICATION_KINDS: frozenset[str] = frozenset(
    {"MATH_CHECK", "DATA_REPRO", "CODE_REPRO", "COUNTEREXAMPLE", "BOUNDARY"}
)


def _verification_severity(kind: str) -> str:
    upper = str(kind or "").upper()
    if upper in _BLOCKING_VERIFICATION_KINDS:
        return "BLOCKING"
    if upper in {"SENSITIVITY", "ROBUSTNESS"}:
        return "DEGRADING"
    # 未分类的失败一律当 BLOCKING（fail closed：不认识就不要忽略它）。
    return "BLOCKING"


def _derive_counter_evidence(
    *,
    evidence_assessments: Sequence[Any],
    verifications: Sequence[Mapping[str, Any]],
    existing: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """反证 = 已有（**永不删除**） ∪ 本轮新发现的。

    新发现有两类：验证 FAIL（含见证物）、被标 REFUTES 且可用的证据。
    """
    merged: dict[str, dict[str, Any]] = {}
    for c in existing:
        key = f"{c.get('sourceKind')}:{c.get('refId')}"
        merged[key] = dict(c)

    for a in evidence_assessments:
        if str(getattr(a, "role", "")) == EvidenceLinkRole.REFUTES.value and getattr(a, "usable", False):
            key = f"EVIDENCE:{a.evidence_id}"
            merged.setdefault(
                key,
                CounterEvidence(
                    source_kind="EVIDENCE",
                    ref_id=a.evidence_id,
                    summary=f"证据 {a.evidence_id} 与主张相冲突（band={a.band}）",
                    severity="BLOCKING",
                    detected_by="support-rubric",
                    detected_at=_now().isoformat(),
                    witness={},
                ).as_dict(),
            )
    for v in verifications:
        if str(v.get("result")) != "FAIL":
            continue
        detail = dict(v.get("detail") or {})
        witness = dict(detail.get("witness") or {})
        kind = str(v.get("kind") or "VERIFICATION")
        severity = _verification_severity(kind)
        ref_id = str(v.get("record_id") or "")
        summary = f"{kind} 验证未通过"
        if witness:
            summary = f"{kind} 反例命中: {witness.get('name')} -> {witness.get('value')}"
        item = CounterEvidence(
            source_kind="VERIFICATION",
            ref_id=ref_id,
            summary=summary,
            severity=severity,
            detected_by=str(v.get("verifier_id") or "verifier"),
            detected_at=_now().isoformat(),
            witness=witness,
        ).as_dict()
        item["reason"] = "COUNTEREXAMPLE_REPRODUCED" if severity == "BLOCKING" else "VERIFICATION_FAIL"
        merged[f"VERIFICATION:{ref_id}"] = item
    return list(merged.values())


def _build_snapshot(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    claim_kind: str,
    evidence_refs: Sequence[str],
    experiment_refs: Sequence[str],
    link_rows: Sequence[ClaimEvidenceLinkRow],
    existing_content: Mapping[str, Any],
    verification_views: Sequence[Mapping[str, Any]],
) -> ClaimGateSnapshot:
    roles = {str(l.evidence_card_id): str(l.role) for l in link_rows}
    kind_by_evidence = {str(l.evidence_card_id): str(l.claim_kind or claim_kind) for l in link_rows}
    notes = {str(l.evidence_card_id): str(l.note or "") for l in link_rows}
    added_at = {
        str(l.evidence_card_id): (l.created_at.isoformat() if l.created_at else "") for l in link_rows
    }
    added_by = {str(l.evidence_card_id): str(l.created_by or "") for l in link_rows}

    assessments: list[Any] = []
    supporting: list[Any] = []
    refuting: list[Any] = []
    for eid in evidence_refs:
        view = _evidence_view(db, evidence_id=str(eid), session_id=session_id, tenant_id=tenant_id)
        role = roles.get(str(eid), EvidenceLinkRole.SUPPORTS.value)
        assessment = assess_evidence_support(
            view, claim_kind=kind_by_evidence.get(str(eid), claim_kind), role=role
        )
        assessments.append(assessment)
        (refuting if role == EvidenceLinkRole.REFUTES.value else supporting).append(assessment)

    agg = aggregate_support(supporting, refuting=refuting)

    experiment_views = [
        {**_experiment_view(db, run_id=str(rid), tenant_id=tenant_id), "role": "SUPPORTS"}
        for rid in experiment_refs
    ]
    counters = _derive_counter_evidence(
        evidence_assessments=assessments,
        verifications=verification_views,
        existing=_known_counter_evidence(existing_content),
    )
    evidence_links = [
        {
            "evidence_id": a.evidence_id,
            "role": a.role,
            "claim_kind": a.claim_kind,
            "band": a.band,
            "score": a.score,
            "usable": a.usable,
            "blocking": list(a.blocking),
            "factors": [f.to_dict() for f in a.factors],
            "note": notes.get(a.evidence_id, ""),
            "addedAt": added_at.get(a.evidence_id, ""),
            "addedBy": added_by.get(a.evidence_id, ""),
        }
        for a in assessments
    ]
    return ClaimGateSnapshot(
        claim_kind=claim_kind,
        support_band=agg.best_band,
        quality=agg.quality,
        evidence_links=tuple(evidence_links),
        experiment_links=tuple(
            {
                "run_id": e["run_id"],
                "role": e["role"],
                "reproducible": e["reproducible"],
                "exit_code": e["exit_code"],
                "repro_missing": list(e["repro_missing"]),
                "note": "",
                "addedAt": "",
                "status": e.get("status", ""),
            }
            for e in experiment_views
        ),
        verifications=tuple(dict(v) for v in verification_views),
        counter_evidence=tuple(counters),
        revision_fresh=True,
        reference_conflicts=(),
    )


def snapshot_from_refs(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    claim_kind: str,
    evidence_refs: Sequence[str],
    experiment_refs: Sequence[str],
    existing_content: Mapping[str, Any] | None = None,
) -> ClaimGateSnapshot:
    """只凭**引用**构造快照（用于"还没落库"的提案评估）。

    消息路径（pending action 确认）必须用它 —— 否则模型就能用一句
    `{"status": "SOLVED"}` 绕过 Gate。
    """
    return _build_snapshot(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        claim_kind=claim_kind,
        evidence_refs=evidence_refs,
        experiment_refs=experiment_refs,
        link_rows=(),
        existing_content=dict(existing_content or {}),
        verification_views=(),
    )


def snapshot_for_claim(db: Session, row: ClaimRow) -> ClaimGateSnapshot:
    """把 claim 行 + 真实证据/实验/验证读成 Gate 输入。"""
    claim_kind = str(row.claim_kind or ClaimKind.PARAPHRASE.value)
    link_rows = list(
        db.execute(
            select(ClaimEvidenceLinkRow).where(ClaimEvidenceLinkRow.claim_key == row.claim_key)
        ).scalars().all()
    )
    return _build_snapshot(
        db,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        claim_kind=claim_kind,
        evidence_refs=list(row.evidence_refs or []),
        experiment_refs=list(row.experiment_refs or []),
        link_rows=link_rows,
        existing_content=dict(row.content or {}),
        verification_views=_verification_views(db, row=row),
    )


# --------------------------------------------------------------------------
# 物化：把快照写回 content（**唯一**的状态权威入口）
# --------------------------------------------------------------------------


def _apply_decision(
    content: dict[str, Any],
    *,
    snapshot: ClaimGateSnapshot,
    decision: Any,
) -> dict[str, Any]:
    out = dict(content)
    out["status"] = decision.status
    best = max((l for l in snapshot.evidence_links if l["usable"]), key=lambda l: l["score"], default=None)
    out["supportQuality"] = snapshot.quality
    out["confidenceBasis"] = {
        "method": "rubric-v1",
        "band": snapshot.support_band,
        "score": round(float(best["score"]), 6) if best else 0.0,
        "factors": {f["factor"]: f["reason"] for f in (best["factors"] if best else [])},
        "computedAt": _now().isoformat(),
        "notes": "由 type/eligibility/locator/freshness 四因子加权得出；不含模型自评分。",
    }
    out["evidenceLinks"] = [dict(l) for l in snapshot.evidence_links]
    out["experimentLinks"] = [dict(x) for x in snapshot.experiment_links]
    out["counterEvidence"] = [dict(c) for c in snapshot.counter_evidence]
    out["requiresReview"] = bool(decision.requires_review)
    out["reviewReasons"] = list(decision.review_reasons)
    out["gateId"] = decision.gate_id
    out["satisfied"] = list(decision.satisfied)
    out["missing"] = list(decision.missing)
    out["conditions"] = list(decision.conditions)
    out["refutation"] = list(decision.refutation)
    return out


def recompute_claim(
    db: Session,
    *,
    row: ClaimRow,
    actor_id: uuid.UUID | None,
    trace_id: str,
    action: str,
    actor_kind: str = "SYSTEM",
) -> ClaimRow:
    """重跑 Gate 并写新修订（状态变化与否都留迁移审计）。"""
    snapshot = snapshot_for_claim(db, row)
    decision = derive_status(snapshot)
    content = _apply_decision(dict(row.content or {}), snapshot=snapshot, decision=decision)
    # 既有反证永不被删除：取并集后再写回（`_derive_counter_evidence` 已保证）。
    new_row = _write_revision(
        db,
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        claim_key=row.claim_key or row.id,
        content=content,
        status=decision.status,
        support_quality=snapshot.quality,
        support_band=snapshot.support_band,
        requires_review=decision.requires_review,
        review_reasons=decision.review_reasons,
        owner_id=row.owner_id,
        acl=dict(row.acl or {}),
        actor_id=actor_id,
        trace_id=trace_id,
        client_request_id=f"{action}:{row.id}",
        text=str(row.text or content.get("statement") or ""),
        relation="contradiction" if decision.status == ClaimStatus.REFUTED.value else (
            "support" if decision.status == ClaimStatus.SOLVED.value else "unknown"
        ),
        evidence_refs=list(row.evidence_refs or []),
        experiment_refs=list(row.experiment_refs or []),
        verification_refs=list(row.verification_refs or []),
        published_at=row.published_at,
        published_by=row.published_by,
    )
    _record_transition(
        db,
        row=new_row,
        previous=row,
        decision=decision,
        actor_id=actor_id,
        actor_kind=actor_kind,
        trace_id=trace_id,
        action=action,
    )
    return new_row


# --------------------------------------------------------------------------
# 写路径
# --------------------------------------------------------------------------


def propose_claim(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    payload: Mapping[str, Any],
    expected_revision: int | None,
    client_request_id: str = "",
    trace_id: str = "",
    owner_id: uuid.UUID | None = None,
) -> ClaimRow:
    """AI（或人）提出一个 claim。状态由服务端 Gate 决定，**永不**取 proposal 的 status。"""
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)
    _assert_revision(db, session_id=session_id, claim_key=None, expected=expected_revision)

    proposal: ClaimProposal = coerce_proposal(payload)
    evidence_refs = list(proposal.evidence_refs)
    experiment_refs = list(proposal.experiment_refs)

    # 证据/实验必须是**本会话可解析**的真实对象（越权引用当场拒绝）。
    resolvable = resolvable_evidence_ids(db, session_id=session_id)
    missing = [e for e in evidence_refs if e not in resolvable]
    if missing:
        raise ClaimEvidenceUnresolved(
            f"引用了不存在、未授权或已失效的证据: {missing}", evidenceIds=missing
        )
    for rid in experiment_refs:
        try:
            run = db.get(ExperimentRunRow, uuid.UUID(str(rid)))
        except ValueError:
            run = None
        if run is None or str(run.tenant_id) != str(ctx.tenant_id) or str(run.session_id) != str(session_id):
            raise ClaimExperimentUnresolved(f"引用了不存在的实验运行: {rid}", runIds=[str(rid)])

    claim_key = uuid.uuid4()
    acl = {
        "tenant_id": str(ctx.tenant_id),
        "session_id": str(session_id),
        "project_id": "",
        "owner_id": str(owner_id or ctx.actor_id),
        "visibility": "session",
        "allowed_roles": [],
    }
    aggregate: ClaimAggregate = materialize_claim(
        proposal,
        claim_id=str(claim_key),
        session_id=str(session_id),
        revision=1,
        owner_id=str(owner_id or ctx.actor_id),
        acl=acl,
    )

    # 提案即评估一次：证据可能已经可用（或已经失效），用户应当**立刻**看到真实状态，
    # 而不是等到某个后续动作才被告知"其实证据没了"。
    snapshot = _build_snapshot(
        db,
        tenant_id=ctx.tenant_id,
        session_id=session_id,
        claim_kind=proposal.claim_kind,
        evidence_refs=evidence_refs,
        experiment_refs=experiment_refs,
        link_rows=(),
        existing_content={},
        verification_views=(),
    )
    decision = derive_status(snapshot)

    content = {k: v for k, v in aggregate.to_dict().items() if k in _AGGREGATE_KEYS}
    content["claimKey"] = str(claim_key)
    content = _apply_decision(content, snapshot=snapshot, decision=decision)
    content["audit"] = aggregate.audit

    row = _write_revision(
        db,
        session_id=session_id,
        tenant_id=ctx.tenant_id,
        claim_key=claim_key,
        content=content,
        status=decision.status,
        support_quality=snapshot.quality,
        support_band=snapshot.support_band,
        requires_review=decision.requires_review,
        review_reasons=decision.review_reasons,
        owner_id=uuid.UUID(str(aggregate.owner_id)),
        acl=acl,
        actor_id=ctx.actor_id,
        trace_id=trace_id,
        client_request_id=client_request_id or f"claim.propose:{claim_key}",
        text=aggregate.statement,
        relation="unknown",
        evidence_refs=evidence_refs,
        experiment_refs=experiment_refs,
        verification_refs=[],
    )
    _record_transition(
        db,
        row=row,
        previous=None,
        decision=decision,
        actor_id=ctx.actor_id,
        actor_kind="AI" if str(payload.get("proposedBy") or "").upper() == "AI" else "HUMAN",
        trace_id=trace_id,
        action="claim.propose",
    )
    # 关联表落库：角色（支撑/反驳）与"谁在什么时候加的"必须可查，
    # 不能只存在于 content JSONB 里 —— 否则反证/角色的来源无从审计。
    for eid in evidence_refs:
        db.add(
            ClaimEvidenceLinkRow(
                claim_key=claim_key,
                claim_id=row.id,
                session_id=session_id,
                tenant_id=ctx.tenant_id,
                evidence_card_id=str(eid),
                role=EvidenceLinkRole.SUPPORTS.value,
                claim_kind=proposal.claim_kind,
                note="proposed-with-claim",
                created_by=ctx.actor_id,
                trace_id=trace_id,
                created_at=_now(),
            )
        )
    db.flush()
    return row


def link_evidence(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    evidence_id: str,
    role: str = "SUPPORTS",
    claim_kind: str | None = None,
    note: str = "",
    expected_revision: int | None = None,
    trace_id: str = "",
) -> ClaimRow:
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)
    row = _authorize_claim(db, ctx, get_claim_row(db, claim_key), capability=WRITE_CAPABILITY)
    if str(row.session_id) != str(session_id):
        raise ObjectScopeMissing("claim 不属于该 session")
    _assert_revision(db, session_id=session_id, claim_key=row.claim_key, expected=expected_revision)
    if evidence_id not in resolvable_evidence_ids(db, session_id=session_id):
        raise ClaimEvidenceUnresolved(f"证据不可解析: {evidence_id}", evidenceIds=[evidence_id])
    resolved_role = EvidenceLinkRole(role).value

    existing = db.execute(
        select(ClaimEvidenceLinkRow).where(
            ClaimEvidenceLinkRow.claim_key == row.claim_key,
            ClaimEvidenceLinkRow.evidence_card_id == str(evidence_id),
            ClaimEvidenceLinkRow.role == resolved_role,
        )
    ).scalars().first()
    if existing is None:
        db.add(
            ClaimEvidenceLinkRow(
                claim_key=row.claim_key,
                claim_id=row.id,
                session_id=row.session_id,
                tenant_id=row.tenant_id,
                evidence_card_id=str(evidence_id),
                role=resolved_role,
                claim_kind=str(claim_kind or row.claim_kind or "PARAPHRASE"),
                note=str(note)[:512],
                created_by=ctx.actor_id,
                trace_id=trace_id,
                created_at=_now(),
            )
        )
        db.flush()
    refs = list(dict.fromkeys([*(row.evidence_refs or []), str(evidence_id)]))
    row.evidence_refs = refs
    db.flush()
    return recompute_claim(
        db, row=row, actor_id=ctx.actor_id, trace_id=trace_id, action="claim.link-evidence"
    )


def link_experiment(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    run_id: str,
    role: str = "SUPPORTS",
    note: str = "",
    expected_revision: int | None = None,
    trace_id: str = "",
) -> ClaimRow:
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)
    row = _authorize_claim(db, ctx, get_claim_row(db, claim_key), capability=WRITE_CAPABILITY)
    if str(row.session_id) != str(session_id):
        raise ObjectScopeMissing("claim 不属于该 session")
    _assert_revision(db, session_id=session_id, claim_key=row.claim_key, expected=expected_revision)
    view = _experiment_view(db, run_id=str(run_id), tenant_id=row.tenant_id)
    if not view["resolvable"]:
        raise ClaimExperimentUnresolved(f"实验运行不可解析: {run_id}", runIds=[str(run_id)])
    refs = list(dict.fromkeys([*(row.experiment_refs or []), str(run_id)]))
    row.experiment_refs = refs
    link = ExperimentLink(run_id=str(run_id), role=EvidenceLinkRole(role).value, note=str(note)[:512])
    content = dict(row.content or {})
    content["experimentLinks"] = [
        *(content.get("experimentLinks") or []),
        link.as_dict(),
    ]
    row.content = content
    db.flush()
    return recompute_claim(
        db, row=row, actor_id=ctx.actor_id, trace_id=trace_id, action="claim.link-experiment"
    )


def evaluate_claim(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    expected_revision: int | None = None,
    counterevidence_addressed: bool = False,
    trace_id: str = "",
) -> ClaimRow:
    """人工/系统触发的重新评估（幂等：状态不变也写审计）。"""
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)
    row = _authorize_claim(db, ctx, get_claim_row(db, claim_key), capability=WRITE_CAPABILITY)
    if str(row.session_id) != str(session_id):
        raise ObjectScopeMissing("claim 不属于该 session")
    _assert_revision(db, session_id=session_id, claim_key=row.claim_key, expected=expected_revision)

    snapshot = snapshot_for_claim(db, row)
    decision = derive_status(snapshot)
    # 迁移合法性：REFUTED → SOLVED 需要显式声明反证已解决
    validate_transition(
        current=str(row.status),
        target=str(decision.status),
        gate=decision,
        counterevidence_addressed=counterevidence_addressed,
    )
    content = _apply_decision(dict(row.content or {}), snapshot=snapshot, decision=decision)
    new_row = _write_revision(
        db,
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        claim_key=row.claim_key,
        content=content,
        status=decision.status,
        support_quality=snapshot.quality,
        support_band=snapshot.support_band,
        requires_review=decision.requires_review,
        review_reasons=decision.review_reasons,
        owner_id=row.owner_id,
        acl=dict(row.acl or {}),
        actor_id=ctx.actor_id,
        trace_id=trace_id,
        client_request_id=f"claim.evaluate:{row.id}",
        text=str(row.text or ""),
        relation=str(row.relation or "unknown"),
        evidence_refs=list(row.evidence_refs or []),
        experiment_refs=list(row.experiment_refs or []),
        verification_refs=list(row.verification_refs or []),
        published_at=row.published_at,
        published_by=row.published_by,
    )
    _record_transition(
        db,
        row=new_row,
        previous=row,
        decision=decision,
        actor_id=ctx.actor_id,
        actor_kind="SYSTEM",
        trace_id=trace_id,
        action="claim.evaluate",
    )
    return new_row


def publish_claim(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    claim_key: uuid.UUID,
    note: str = "",
    trace_id: str = "",
) -> ClaimRow:
    """**只有人能发布关键 claim**。发布要求状态已达 SOLVED 且无待复核项。"""
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)
    row = _authorize_claim(db, ctx, get_claim_row(db, claim_key), capability=WRITE_CAPABILITY)
    if str(row.session_id) != str(session_id):
        raise ObjectScopeMissing("claim 不属于该 session")
    if str(row.status) != ClaimStatus.SOLVED.value or bool(row.requires_review):
        raise ClaimPublishRejected(
            "只有满足 SOLVED 判据且无待复核项的 claim 才能发布",
            status=str(row.status),
            missing=list(row.review_reasons or []),
        )
    content = dict(row.content or {})
    content["published"] = True
    content["publishedAt"] = _now().isoformat()
    content["publishedBy"] = str(ctx.actor_id)
    content["publishNote"] = str(note)[:512]
    new_row = _write_revision(
        db,
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        claim_key=row.claim_key,
        content=content,
        status=str(row.status),
        support_quality=str(row.support_quality),
        support_band=str(row.support_band),
        requires_review=bool(row.requires_review),
        review_reasons=list(row.review_reasons or []),
        owner_id=row.owner_id,
        acl=dict(row.acl or {}),
        actor_id=ctx.actor_id,
        trace_id=trace_id,
        client_request_id=f"claim.publish:{row.id}",
        text=str(row.text or ""),
        relation=str(row.relation or "unknown"),
        evidence_refs=list(row.evidence_refs or []),
        experiment_refs=list(row.experiment_refs or []),
        verification_refs=list(row.verification_refs or []),
        published_at=_now(),
        published_by=ctx.actor_id,
    )
    _record_transition(
        db,
        row=new_row,
        previous=row,
        decision=None,
        actor_id=ctx.actor_id,
        actor_kind="HUMAN",
        trace_id=trace_id,
        action="claim.publish",
        published=True,
    )
    return new_row


def apply_verification_effect(
    db: Session, *, claim_key: uuid.UUID | None, verification_record_id: uuid.UUID
) -> ClaimRow | None:
    """把一条**已落库**的验证记录回灌到 claim（由 claim 域负责改状态）。

    Verifier 自己不改 claim —— 它只追加记录；状态变更必须经过本函数，
    从而所有状态变化都经由 Gate 与迁移审计。
    """
    if claim_key is None:
        return None
    row = get_claim_row(db, claim_key)
    refs = list(dict.fromkeys([*(row.verification_refs or []), str(verification_record_id)]))
    row.verification_refs = refs
    db.flush()
    return recompute_claim(
        db, row=row, actor_id=None, trace_id=row.trace_id, action="claim.verification-applied"
    )


# --------------------------------------------------------------------------
# 投影
# --------------------------------------------------------------------------


def claim_public_dict(row: ClaimRow) -> dict[str, Any]:
    aggregate = aggregate_from_content(
        dict(row.content or {}),
        claim_id=str(row.claim_key or row.id),
        session_id=str(row.session_id),
        revision=int(row.revision),
        owner_id=str(row.owner_id or ""),
        acl=dict(row.acl or {}),
        created_at=row.created_at.isoformat() if row.created_at else "",
        published=row.published_at is not None,
        published_at=row.published_at.isoformat() if row.published_at else "",
        published_by=str(row.published_by or ""),
        status=str(row.status),
    )
    payload = aggregate.to_dict()
    payload["id"] = payload["claimId"]  # 前端/其它对象族统一用 `id`
    payload["revisionRowId"] = str(row.id)
    payload["supportBand"] = str(row.support_band or payload["confidenceBasis"]["band"])
    payload["supportQuality"] = str(row.support_quality or payload["supportQuality"])
    payload["requiresReview"] = bool(row.requires_review)
    payload["reviewReasons"] = list(row.review_reasons or payload["reviewReasons"])
    payload["confidenceBasis"]["band"] = payload["supportBand"]
    return payload


def claim_chain(db: Session, ctx: AccessContext, *, claim_key: uuid.UUID) -> dict[str, Any]:
    """claim → evidence → experiment → verification 的**可点击**链路 + 反证。"""
    row = _authorize_claim(db, ctx, get_claim_row(db, claim_key))
    payload = claim_public_dict(row)

    evidence: list[dict[str, Any]] = []
    for link in payload["evidenceLinks"]:
        card = db.get(EvidenceCardV2Row, _safe_uuid(link["evidenceId"]))
        entry = {
            "id": link["evidenceId"],
            "role": link["role"],
            "band": link["band"],
            "usable": link["usable"],
            "blocking": link["blocking"],
            "factors": link["factors"],
            "resolvable": card is not None,
        }
        if card is not None:
            locator = dict(card.locator or {})
            locator.setdefault("kind", card.locator_kind or "EXTERNAL")
            entry.update(
                {
                    "evidenceType": card.evidence_type,
                    "rightsStatus": card.rights_status,
                    "status": card.status,
                    "staleState": card.stale_state,
                    "locator": locator,
                    "snippetHash": card.snippet_hash,
                    "citationEligible": bool(card.citation_eligible),
                }
            )
        evidence.append(entry)

    experiments: list[dict[str, Any]] = []
    for link in payload["experimentLinks"]:
        run = db.get(ExperimentRunRow, _safe_uuid(link["runId"]))
        entry = {
            "id": link["runId"],
            "role": link["role"],
            "reproducible": link["reproducible"],
            "exitCode": link["exitCode"],
            "reproMissing": link["reproMissing"],
            "resolvable": run is not None,
        }
        if run is not None:
            entry.update({"status": run.status, "metrics": dict(run.metrics or {}),
                          "notReproducibleReason": run.not_reproducible_reason or ""})
        experiments.append(entry)

    verifications: list[dict[str, Any]] = []
    for v in _verification_views(db, row=row):
        record = db.get(VerificationRecordRow, _safe_uuid(v["record_id"]))
        verifications.append(
            {
                "id": v["record_id"],
                "kind": v["kind"],
                "result": v["result"],
                "independent": v["independent"],
                "verifierId": v["verifier_id"],
                "artifactRefs": list(record.artifact_refs or []) if record is not None else [],
                "promptRef": str(record.prompt_ref or "") if record is not None else "",
                "contextManifestRef": str(record.context_manifest_ref or "") if record is not None else "",
                "costBasis": str(record.cost_basis or "") if record is not None else "",
                "witness": dict((dict(record.detail or {}).get("witness") or {})) if record is not None else {},
            }
        )

    return {
        "claim": {
            "id": payload["claimId"],
            "revisionRowId": payload["revisionRowId"],
            "revision": payload["revision"],
            "statement": payload["statement"],
            "status": payload["status"],
            "statusLabel": payload["statusLabel"],
            "statusDescription": payload["statusDescription"],
            "supportBand": payload["supportBand"],
            "supportQuality": payload["supportQuality"],
            "requiresReview": payload["requiresReview"],
            "reviewReasons": payload["reviewReasons"],
            "missing": payload["missing"],
            "conditions": payload["conditions"],
            "published": payload["published"],
        },
        "evidence": evidence,
        "experiments": experiments,
        "verifications": verifications,
        "counterEvidence": payload["counterEvidence"],
        "refutation": payload["refutation"],
        "unknowns": _unknowns(payload),
    }


def _unknowns(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """明确的"未知"清单（不是把缺失藏起来）。"""
    out: list[dict[str, str]] = []
    for code in payload.get("missing") or ():
        out.append({"kind": "MISSING_CRITERION", "code": str(code)})
    for link in payload.get("evidenceLinks") or ():
        if not link.get("usable"):
            out.append(
                {
                    "kind": "EVIDENCE_NOT_USABLE",
                    "code": str(link.get("evidenceId")),
                    "reason": ",".join(link.get("blocking") or []),
                }
            )
    for v in payload.get("experimentLinks") or ():
        if not v.get("reproducible"):
            out.append({"kind": "EXPERIMENT_NOT_REPRODUCIBLE", "code": str(v.get("runId"))})
    return out


def _safe_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):  # pragma: no cover - 非法 id 走"不可解析"分支
        return uuid.UUID(int=0)


def quality_of(row: ClaimRow) -> str:
    return str(row.support_quality or SupportQuality.UNSUPPORTED.value)


def scope_from_content(content: Mapping[str, Any]) -> ClaimScope:  # pragma: no cover - 兼容辅助
    raw = dict(content.get("scope") or {})
    return ClaimScope(
        domain=str(raw.get("domain") or "unspecified"),
        population=str(raw.get("population") or ""),
        parameters=tuple(raw.get("parameters") or ()),
        boundary=str(raw.get("boundary") or ""),
    )
