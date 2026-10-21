"""RV09–RV13 领域对象的应用服务（写入路径全部 confirm-gated）。

**唯一的写正式状态入口**：
- 经 AI 提议的对象 → `Proposal` → `PendingAction` → `accept_pending()` →
  `proposal_handlers.apply()`（新增不可变修订行）；
- 非 AI 提议的对象（验证记录、实验运行、证据卡） → 本模块的显式函数，
  它们**不**接受模型自由输出，只接受结构化字段 + 明确的授权调用方。

本模块里没有任何函数可以被 `parse_proposal()` 的输出直接调用 —— 这是刻意的。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    ClaimRow,
    EvidenceCardRow,
    ExperimentRunRow,
    PendingActionAuditRow,
    PendingActionRow,
    ResearchSessionRow,
    ReviewFindingRow,
    VerificationRecordRow,
)
from ..domain import rv_domain_rules as R
from ..domain.errors import DomainError
from ..domain.events import DomainEvent
from ..observability.redaction import redact_mapping


class DomainObjectError(DomainError):
    code = "DOMAIN_OBJECT_ERROR"
    http_status = 400


class ClaimNotFound(DomainObjectError):
    code = "CLAIM_NOT_FOUND"
    http_status = 404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _emit(db: Session, *, ev: DomainEvent) -> None:
    from ..db.models import DomainEventRow, OutboxEventRow

    db.add(
        DomainEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            revision=ev.revision,
            occurred_at=ev.occurred_at,
            trace_id=ev.trace_id,
            payload=redact_mapping(ev.payload),
            actor_id=ev.actor_id,
            aggregate_type="session",
        )
    )
    db.add(
        OutboxEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            payload=redact_mapping(ev.payload),
            trace_id=ev.trace_id,
            status="PENDING",
            attempts=0,
            published_at=None,
            created_at=ev.occurred_at,
        )
    )


# --------------------------------------------------------------------------
# 证据（引用层）
# --------------------------------------------------------------------------


def valid_evidence_ids(db: Session, session_id: uuid.UUID) -> set[str]:
    """**可用**证据 id 集合：存在、已授权、且未失效。

    未授权或已失效的证据不得支撑 claim —— 这是"授权"在产品上的实际含义。

    D-TABLE-1（2026-09-14）：本函数是**权威表 ∪ 遗留表**的并集
    （见 `evidence/resolvable.py`），否则 RV07 的证据卡无法支撑 claim。
    延迟 import 以维持既有依赖方向。
    """
    from ..evidence.resolvable import resolvable_evidence_ids

    return resolvable_evidence_ids(db, session_id=session_id)


def add_evidence(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    source: str,
    label: str,
    locator: str = "",
    snippet: str = "",
    asset_version: str = "",
    authorized: bool = True,
) -> EvidenceCardRow:
    row = EvidenceCardRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        source=source[:256],
        label=label[:512],
        locator=locator[:512],
        snippet=snippet[:4000],
        asset_version=asset_version[:128],
        authorized=authorized,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def _mark_invalidated(
    db: Session, *, evidence_id: uuid.UUID, reason: str
) -> uuid.UUID | None:
    """在**任一**证据表上打失效位，返回其 `session_id`；两表都没有则返回 None。

    D-TABLE-1：失效位必须能打在**权威表**（`research_evidence_card`）上，
    否则 RV07 卡一旦落库就永远无法失效，RV12 的"失效即重开"对它不成立。
    权威表 `session_id` 可为 NULL —— 此时返回值同样是 None，
    调用方需另行区分"不存在"与"存在但无会话"（见 `invalidate_evidence`）。
    """
    from ..evidence.models import EvidenceCardV2Row

    flat = db.get(EvidenceCardRow, evidence_id)
    if flat is not None:
        if flat.invalidated_at is None:
            flat.invalidated_at = _now()
            flat.invalidated_reason = reason[:512]
            db.flush()
        return flat.session_id

    v2 = db.get(EvidenceCardV2Row, evidence_id)
    if v2 is not None:
        if v2.status != "INVALIDATED":
            v2.status = "INVALIDATED"
            v2.updated_at = _now()
            db.flush()
        return v2.session_id

    return None


def invalidate_evidence(
    db: Session, *, evidence_id: uuid.UUID, reason: str, trace_id: str = ""
) -> dict[str, Any]:
    """使证据失效，并**重开**依赖它的 SOLVED claim。

    这是 RV12 的关键行为：如果没有它，"已解决"就是一个不可推翻的终态。
    D-TABLE-1：失效位可打在**任一**证据表上（权威表或遗留表），
    因此 RV07 卡也能被失效并触发重开判定。
    """
    marked = _mark_invalidated(db, evidence_id=evidence_id, reason=reason)
    if marked is None:
        # 权威表的 `session_id` 可为 NULL：此时失效位**已打上**，
        # 只是无法定位会话。必须显式区分"两表都不存在"与"存在但无会话"，
        # 否则会把合法的 NULL-session 证据误报为"证据不存在"。
        from ..evidence.models import EvidenceCardV2Row

        if (
            db.get(EvidenceCardRow, evidence_id) is None
            and db.get(EvidenceCardV2Row, evidence_id) is None
        ):
            raise DomainObjectError(f"证据不存在: {evidence_id}")
        # 存在但无法定位会话 ⇒ 跳过重开扫描。
        return {"evidence_id": str(evidence_id), "reopened_claims": []}

    ev_session_id = marked
    reopened: list[dict[str, Any]] = []
    valid = valid_evidence_ids(db, ev_session_id)
    claims = (
        db.query(ClaimRow)
        .filter(ClaimRow.session_id == ev_session_id, ClaimRow.status == "SOLVED")
        .all()
    )
    for c in claims:
        if str(evidence_id) not in list(c.evidence_refs or []):
            continue
        decision = R.reopen_decision(
            claim_status=c.status,
            evidence_refs=list(c.evidence_refs or []),
            valid_evidence_ids=valid,
            reproducible=True,
        )
        if decision["reopen"]:
            reopened.append(_reopen_claim(db, claim=c, decision=decision, trace_id=trace_id))

    return {"evidence_id": str(evidence_id), "reopened_claims": reopened}


def _reopen_claim(
    db: Session, *, claim: ClaimRow, decision: dict[str, Any], trace_id: str
) -> dict[str, Any]:
    """重开一个 claim：写**新修订行**（旧行不动），并把状态降为 INCONCLUSIVE。"""
    new_revision = int(
        db.scalar(select(func.max(ClaimRow.revision)).where(ClaimRow.session_id == claim.session_id)) or 0
    ) + 1
    content = dict(claim.content or {})
    content["status"] = decision.get("to_status", "INCONCLUSIVE")
    content["reopened_reason"] = decision["reason"]
    row = ClaimRow(
        id=uuid.uuid4(),
        session_id=claim.session_id,
        tenant_id=claim.tenant_id,
        revision=new_revision,
        content=content,
        content_hash=_hash(content),
        client_request_id=f"reopen:{claim.id}",
        confirmed_at=_now(),
        confirmed_by=None,
        trace_id=trace_id or claim.trace_id,
        created_at=_now(),
        text=claim.text,
        status=decision.get("to_status", "INCONCLUSIVE"),
        support_quality="UNSUPPORTED",
        relation=claim.relation,
        evidence_refs=list(claim.evidence_refs or []),
        experiment_refs=list(claim.experiment_refs or []),
        verification_refs=list(claim.verification_refs or []),
        reopened_at=_now(),
        reopened_reason=decision["reason"],
        # RV12：重开必须继承稳定身份，并显式标记"需要人工复核"。
        claim_key=claim.claim_key,
        owner_id=claim.owner_id,
        acl=dict(claim.acl or {}),
        claim_kind=str(claim.claim_kind or "PARAPHRASE"),
        support_band="NONE",
        requires_review=True,
        review_reasons=[decision["reason"]],
    )
    db.add(row)
    db.flush()
    return {
        "claim_id": str(claim.id),
        "new_claim_id": str(row.id),
        "new_revision": new_revision,
        "reason": decision["reason"],
        "to_status": row.status,
    }


# --------------------------------------------------------------------------
# 验证记录
# --------------------------------------------------------------------------


def record_verification(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID | None,
    method: str,
    result: str,
    evidence_refs: list[str] | None = None,
    independent: bool = False,
    detail: dict[str, Any] | None = None,
    trace_id: str = "",
) -> VerificationRecordRow:
    """写一条独立验证记录。`UNKNOWN` 保持 `UNKNOWN`（**不**转 PASS）。"""
    normalized = R.normalize_verification_result(result)
    row = VerificationRecordRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        claim_id=claim_id,
        method=method[:128],
        result=normalized,
        detail=redact_mapping(detail or {}),
        evidence_refs=list(evidence_refs or []),
        independent=independent,
        trace_id=trace_id,
        created_at=_now(),
    )
    db.add(row)
    db.flush()

    if claim_id is not None:
        claim = db.get(ClaimRow, claim_id)
        if claim is not None:
            refs = list(claim.verification_refs or []) + [str(row.id)]
            claim.verification_refs = refs
            results = [
                v.result
                for v in db.query(VerificationRecordRow)
                .filter(VerificationRecordRow.claim_id == claim_id)
                .all()
            ]
            claim.support_quality = R.derive_support_quality(
                evidence_refs=list(claim.evidence_refs or []),
                valid_evidence_ids=valid_evidence_ids(db, session_id),
                verification_results=results,
            )
            _refresh_claim_projections(db, claim=claim)
            db.flush()
    return row


def _refresh_claim_projections(db: Session, *, claim: ClaimRow) -> None:
    """RV12：验证落库后**立即**刷新 claim 的状态投影。

    为什么这条路径必须刷新（而不是等一次显式 evaluate）：
    "FAIL 验证已落库但 claim 仍显示 SOLVED" 是本产品最危险的状态不一致 ——
    用户会盯着一个已经站不住的结论继续写论文。

    与 `claim.ledger` 的差别（如实记录）：本函数**就地更新投影列**
    （`status` / `support_band` / `requires_review` / `review_reasons` / `content`），
    保持既有修订行数量不变（RV09 测试依赖行数语义）；RV12 的 API 路径
    （`claim.ledger.evaluate_claim` 等）则**追加新修订行**。
    """
    from ..claim import ledger as claim_ledger
    from ..claim.state_machine import derive_status

    if claim.claim_key is None:
        # 0012 之前的历史行：没有稳定身份就无法可靠地重跑 Gate（反证/链接表都按
        # claim_key 关联）。保持原状并在审计上暴露，而不是猜一个 key。
        return
    snapshot = claim_ledger.snapshot_for_claim(db, claim)
    decision = derive_status(snapshot)
    claim.status = decision.status
    claim.support_band = snapshot.support_band
    claim.support_quality = snapshot.quality
    claim.requires_review = bool(decision.requires_review)
    claim.review_reasons = list(decision.review_reasons)
    content = dict(claim.content or {})
    content.update(
        {
            "status": decision.status,
            "supportQuality": snapshot.quality,
            "requiresReview": bool(decision.requires_review),
            "reviewReasons": list(decision.review_reasons),
            "gateId": decision.gate_id,
            "satisfied": list(decision.satisfied),
            "missing": list(decision.missing),
            "conditions": list(decision.conditions),
            "refutation": list(decision.refutation),
            "counterEvidence": [dict(c) for c in snapshot.counter_evidence],
            "evidenceLinks": [dict(l) for l in snapshot.evidence_links],
            "experimentLinks": [dict(x) for x in snapshot.experiment_links],
        }
    )
    claim.content = content
    claim.content_hash = _hash(content)


# --------------------------------------------------------------------------
# 实验运行
# --------------------------------------------------------------------------


def record_experiment_run(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID | None,
    code_ref: str,
    dataset_ref: str,
    exit_code: int | None,
    repro: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    logs_ref: str = "",
    figures: list[str] | None = None,
    executor: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    """记录一次真实运行。

    **`exit_code == 0` 绝不等于"结论成立"**：本函数写入的 `claim_holds`
    由 `evaluate_run()` 固定为 `None`。
    """
    verdict = R.evaluate_run(exit_code=exit_code, repro=repro or {}, metrics=metrics or {})
    row = ExperimentRunRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        plan_id=plan_id,
        status="SUCCEEDED" if (exit_code == 0 and verdict["reproducible"]) else "PARTIAL",
        executor=executor[:64],
        code_ref=code_ref[:512],
        dataset_ref=dataset_ref[:512],
        environment=redact_mapping(environment or {}),
        params=redact_mapping(params or {}),
        result={"exit_code": exit_code},
        metrics=redact_mapping(metrics or {}),
        logs_ref=logs_ref[:512],
        figures=list(figures or []),
        exit_code=exit_code,
        claim_holds=verdict["claim_holds"],
        not_reproducible_reason=verdict["not_reproducible_reason"][:512],
        repro=redact_mapping(repro or {}),
        trace_id=trace_id,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return {
        "id": str(row.id),
        "exit_code": exit_code,
        "claim_holds": verdict["claim_holds"],
        "reproducible": verdict["reproducible"],
        "not_reproducible_reason": verdict["not_reproducible_reason"],
    }


def non_reproducible_runs(db: Session, session_id: uuid.UUID) -> list[ExperimentRunRow]:
    return (
        db.query(ExperimentRunRow)
        .filter(
            ExperimentRunRow.session_id == session_id,
            ExperimentRunRow.not_reproducible_reason != "",
        )
        .all()
    )


# --------------------------------------------------------------------------
# ReviewFinding
# --------------------------------------------------------------------------


def record_review_findings(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    findings: list[dict[str, Any]],
    paper_spec_id: uuid.UUID | None = None,
    trace_id: str = "",
) -> list[ReviewFindingRow]:
    """落审稿发现。类别必须来自封闭 taxonomy（越界即拒）。"""
    out: list[ReviewFindingRow] = []
    for f in findings:
        cat = str(f.get("category", "")).upper()
        if cat not in R.REVIEW_CATEGORIES:
            raise DomainObjectError(f"未知 review 类别: {cat!r}", category=cat)
        row = ReviewFindingRow(
            id=uuid.uuid4(),
            session_id=session_id,
            tenant_id=tenant_id,
            paper_spec_id=paper_spec_id,
            category=cat,
            severity=str(f.get("severity", "minor"))[:16],
            location=str(f.get("location", ""))[:512],
            related_claim_id=_maybe_uuid(f.get("relatedClaimId")),
            related_evidence_refs=[str(x) for x in (f.get("relatedEvidenceRefs") or [])],
            suggestion=str(f.get("suggestion", ""))[:4000],
            status="OPEN",
            resolution_evidence_refs=[],
            trace_id=trace_id,
            created_at=_now(),
        )
        db.add(row)
        out.append(row)
    db.flush()
    return out


def _maybe_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# 读：对象族投影
# --------------------------------------------------------------------------


def latest_revision(db: Session, table: Any, session_id: uuid.UUID) -> Any | None:
    return (
        db.query(table)
        .filter(table.session_id == session_id)
        .order_by(table.revision.desc())
        .first()
    )


def object_summary(db: Session, session_id: uuid.UUID) -> dict[str, Any]:
    """本会话所有领域对象的**存在性 + 修订号**（用于能力判定与调试）。"""
    from ..db.models import (
        ExperimentPlanRow,
        ModelSpecRow,
        PaperSpecRow,
        ProblemMapRow,
        ResearchBlueprintRow,
        ResearchIntakeRow,
        ResearchMapRow,
    )

    out: dict[str, Any] = {}
    for name, table in (
        ("intake", ResearchIntakeRow),
        ("problemMap", ProblemMapRow),
        ("researchMap", ResearchMapRow),
        ("modelSpec", ModelSpecRow),
        ("blueprint", ResearchBlueprintRow),
        ("experimentPlan", ExperimentPlanRow),
        ("paperSpec", PaperSpecRow),
        ("claim", ClaimRow),
    ):
        row = latest_revision(db, table, session_id)
        n = int(db.scalar(select(func.count()).select_from(table).where(table.session_id == session_id)) or 0)
        out[name] = {"exists": row is not None, "revisions": n, "latest": getattr(row, "revision", None)}
    return out


def pending_for_session(db: Session, session_id: uuid.UUID) -> list[PendingActionRow]:
    return (
        db.query(PendingActionRow)
        .filter(PendingActionRow.session_id == session_id, PendingActionRow.status == "PENDING")
        .order_by(PendingActionRow.created_at)
        .all()
    )


def audit_trail(db: Session, pending_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        db.query(PendingActionAuditRow)
        .filter(PendingActionAuditRow.pending_action_id == pending_id)
        .order_by(PendingActionAuditRow.at)
        .all()
    )
    return [
        {"action": r.action, "at": r.at.isoformat(), "actor_id": str(r.actor_id) if r.actor_id else None,
         "detail": r.detail or {}}
        for r in rows
    ]


def session_revision(db: Session, session_id: uuid.UUID) -> int:
    row = db.get(ResearchSessionRow, session_id)
    return int(row.revision) if row is not None else 0
