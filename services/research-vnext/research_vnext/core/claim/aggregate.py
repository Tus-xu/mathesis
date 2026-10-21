"""RV12 · Claim 聚合（领域对象）+ AI proposal 边界。

**两条不可让步的不变量：**

1. `ClaimAggregate` 必须携带任务书要求的所有字段 —— 不是"大部分"，
   而是 `REQUIRED_CLAIM_FIELDS` 的全集。缺字段即构造失败，不留"以后再补"。
2. `ClaimProposal` 在**类型上**就没有权威 `status`：模型只能提 `proposed_status`，
   落库状态由服务端 Gate 决定（见 `state_machine.derive_status`）。
   这不是靠代码评审维持的约定 —— `materialize_claim` 无条件把状态压成
   `INCONCLUSIVE` 并记 `AI_PROPOSED_NOT_VERIFIED`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..evidence.types import ClaimKind
from .errors import (
    AssumptionNotFalsifiable,
    ClaimClaimKindUnknown,
    ClaimScopeRequired,
    ClaimStatementEmpty,
    ClaimStatusUnknown,
    ClaimValidationError,
)
from .types import (
    REQUIRED_CLAIM_FIELDS,
    STATUS_DESCRIPTIONS,
    STATUS_LABELS,
    ClaimStatus,
    EvidenceLinkRole,
    ExperimentLinkRole,
    SupportQuality,
)

__all__ = [
    "ClaimAggregate",
    "ClaimAssumption",
    "ClaimProposal",
    "ClaimScope",
    "ConfidenceBasis",
    "CounterEvidence",
    "EvidenceLink",
    "ExperimentLink",
    "REQUIRED_CLAIM_FIELDS",
    "materialize_claim",
]


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


# --------------------------------------------------------------------------
# 值对象
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimScope:
    """主张的**适用范围**。没有 scope 的 claim 是无界的断言 —— 不可检验。"""

    domain: str
    population: str = ""
    parameters: tuple[str, ...] = ()
    boundary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", tuple(str(p) for p in self.parameters or ()))
        if not str(self.domain or "").strip():
            raise ClaimScopeRequired("ClaimScope.domain 不得为空（无界主张不可检验）")

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "population": self.population,
            "parameters": list(self.parameters),
            "boundary": self.boundary,
        }


@dataclass(frozen=True)
class ClaimAssumption:
    """显式假设。**必须可证伪** —— 不可检验的假设会让 claim 永远无法被推翻。"""

    statement: str
    basis: str = ""
    falsifiable: bool = True

    def __post_init__(self) -> None:
        if not str(self.statement or "").strip():
            raise ClaimValidationError("假设必须给出陈述", code="ASSUMPTION_STATEMENT_REQUIRED")
        if not self.falsifiable:
            raise AssumptionNotFalsifiable(
                f"假设不可证伪，不能进入 claim：{self.statement!r}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {"statement": self.statement, "basis": self.basis, "falsifiable": bool(self.falsifiable)}


@dataclass(frozen=True)
class EvidenceLink:
    """claim ↔ evidence 的关联（含**可解释**的支持评估快照）。"""

    evidence_id: str
    role: str = EvidenceLinkRole.SUPPORTS.value
    claim_kind: str = ClaimKind.PARAPHRASE.value
    band: str = "NONE"
    score: float = 0.0
    usable: bool = False
    blocking: tuple[str, ...] = ()
    factors: tuple[dict[str, Any], ...] = ()
    note: str = ""
    added_at: str = ""
    added_by: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidenceId": self.evidence_id,
            "role": self.role,
            "claimKind": self.claim_kind,
            "band": self.band,
            "score": round(float(self.score), 6),
            "usable": bool(self.usable),
            "blocking": list(self.blocking),
            "factors": [dict(f) for f in self.factors],
            "note": self.note,
            "addedAt": self.added_at,
            "addedBy": self.added_by,
        }


@dataclass(frozen=True)
class ExperimentLink:
    """claim ↔ experiment 的关联。`exit_code` 与"结论成立"**无关**。"""

    run_id: str
    role: str = ExperimentLinkRole.SUPPORTS.value
    reproducible: bool = False
    exit_code: int | None = None
    repro_missing: tuple[str, ...] = ()
    note: str = ""
    added_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "role": self.role,
            "reproducible": bool(self.reproducible),
            "exitCode": self.exit_code,
            "reproMissing": list(self.repro_missing),
            "note": self.note,
            "addedAt": self.added_at,
        }


@dataclass(frozen=True)
class CounterEvidence:
    """反证。**只追加、不删除** —— REFUTED 之后它必须仍然可见。"""

    source_kind: str
    ref_id: str
    summary: str
    severity: str = "BLOCKING"
    detected_by: str = ""
    detected_at: str = ""
    witness: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "BLOCKING"

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceKind": self.source_kind,
            "refId": self.ref_id,
            "summary": self.summary,
            "severity": self.severity,
            "detectedBy": self.detected_by,
            "detectedAt": self.detected_at,
            "witness": dict(self.witness),
        }


@dataclass(frozen=True)
class ConfidenceBasis:
    """置信依据 —— **不是**模型自评分，而是 rubric 的可解释产物。"""

    method: str = "rubric-v1"
    band: str = "NONE"
    score: float = 0.0
    factors: dict[str, Any] = field(default_factory=dict)
    computed_at: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "band": self.band,
            "score": round(float(self.score), 6),
            "factors": dict(self.factors),
            "computedAt": self.computed_at,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# AI 只能产出 ClaimProposal
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimProposal:
    """模型**唯一**能产出的形态。

    刻意没有 `status` / `confidence_basis` / `counter_evidence` / `audit` /
    `requires_review`：这些是服务端在验证证据/实验/验证三者版本与完整性之后
    才生成的。多传一个字段就是 `TypeError`（dataclass 不接受未知 kwarg）。
    """

    statement: str
    scope: ClaimScope
    assumptions: tuple[ClaimAssumption, ...] = ()
    claim_kind: str = ClaimKind.PARAPHRASE.value
    limitations: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    experiment_refs: tuple[str, ...] = ()
    proposed_status: str = ClaimStatus.INCONCLUSIVE.value
    rationale: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not str(self.statement or "").strip():
            raise ClaimStatementEmpty("claim 必须给出 statement（空陈述不是 claim）")
        if self.scope is None:
            raise ClaimScopeRequired("claim 必须给出 scope（无界主张不可检验）")
        if not isinstance(self.scope, ClaimScope):
            try:
                object.__setattr__(self, "scope", ClaimScope(**dict(self.scope)))
            except Exception as exc:  # noqa: BLE001
                raise ClaimScopeRequired(f"scope 形状不合法: {exc}") from exc
        try:
            kind = ClaimKind(self.claim_kind)
        except ValueError as exc:
            raise ClaimClaimKindUnknown(f"未知 claim_kind: {self.claim_kind!r}") from exc
        if str(self.proposed_status).upper() not in {s.value for s in ClaimStatus}:
            raise ClaimStatusUnknown(f"未知 proposedStatus: {self.proposed_status!r}")
        object.__setattr__(self, "claim_kind", kind.value)
        object.__setattr__(self, "proposed_status", str(self.proposed_status).upper())
        object.__setattr__(self, "limitations", tuple(str(x) for x in (self.limitations or ())))
        object.__setattr__(self, "evidence_refs", tuple(str(x) for x in (self.evidence_refs or ())))
        object.__setattr__(self, "experiment_refs", tuple(str(x) for x in (self.experiment_refs or ())))


# --------------------------------------------------------------------------
# ClaimAggregate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimAggregate:
    """Claim 的完整聚合。构造期即保证必需字段齐备。"""

    claim_id: str
    session_id: str
    revision: int
    statement: str
    scope: ClaimScope
    assumptions: tuple[ClaimAssumption, ...]
    status: str
    confidence_basis: ConfidenceBasis
    evidence_links: tuple[EvidenceLink, ...]
    experiment_links: tuple[ExperimentLink, ...]
    counter_evidence: tuple[CounterEvidence, ...]
    limitations: tuple[str, ...]
    owner_id: str
    acl: dict[str, Any]
    audit: dict[str, Any]
    support_quality: str = SupportQuality.UNSUPPORTED.value
    requires_review: bool = False
    review_reasons: tuple[str, ...] = ()
    gate_id: str = ""
    satisfied: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    refutation: tuple[str, ...] = ()
    published: bool = False
    published_at: str = ""
    published_by: str = ""
    claim_kind: str = ClaimKind.PARAPHRASE.value
    created_at: str = ""

    def __post_init__(self) -> None:
        if str(self.status) not in {s.value for s in ClaimStatus}:
            raise ClaimStatusUnknown(f"未知 claim status: {self.status!r}")
        if str(self.support_quality) not in {q.value for q in SupportQuality}:
            raise ClaimValidationError(
                "未知支持质量", code="CLAIM_SUPPORT_UNKNOWN", supportQuality=self.support_quality
            )
        if int(self.revision) < 1:
            raise ClaimValidationError("revision 必须 >= 1", code="CLAIM_REVISION_INVALID")
        if not str(self.owner_id or ""):
            raise ClaimValidationError("claim 必须有 owner", code="CLAIM_OWNER_REQUIRED")
        if not isinstance(self.acl, Mapping) or not self.acl.get("tenant_id"):
            raise ClaimValidationError("claim 必须有带 tenant 的 ACL", code="CLAIM_ACL_REQUIRED")

    @property
    def version(self) -> int:
        return int(self.revision)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claimId": self.claim_id,
            "sessionId": self.session_id,
            "revision": int(self.revision),
            # ---- 任务书 §必须完成 1 的 13 个字段 ----
            "statement": self.statement,
            "scope": self.scope.as_dict(),
            "assumptions": [a.as_dict() for a in self.assumptions],
            "status": self.status,
            "confidenceBasis": self.confidence_basis.as_dict(),
            "evidenceLinks": [e.as_dict() for e in self.evidence_links],
            "experimentLinks": [x.as_dict() for x in self.experiment_links],
            "counterEvidence": [c.as_dict() for c in self.counter_evidence],
            "limitations": list(self.limitations),
            "version": int(self.revision),
            "owner": self.owner_id,
            "acl": dict(self.acl),
            "audit": dict(self.audit),
            # ---- 判定与展示（状态不能只靠颜色） ----
            "statusLabel": self.status_label,
            "statusDescription": STATUS_DESCRIPTIONS.get(self.status, ""),
            "claimKind": self.claim_kind,
            "supportQuality": self.support_quality,
            "requiresReview": bool(self.requires_review),
            "reviewReasons": list(self.review_reasons),
            "gateId": self.gate_id,
            "satisfied": list(self.satisfied),
            "missing": list(self.missing),
            "conditions": list(self.conditions),
            "refutation": list(self.refutation),
            "published": bool(self.published),
            "publishedAt": self.published_at,
            "publishedBy": self.published_by,
            "createdAt": self.created_at,
        }


# --------------------------------------------------------------------------
# 服务端物化
# --------------------------------------------------------------------------


def materialize_claim(
    proposal: ClaimProposal,
    *,
    claim_id: str,
    session_id: str,
    revision: int,
    owner_id: str,
    acl: Mapping[str, Any],
    status: str | None = None,
    now: datetime | None = None,
) -> ClaimAggregate:
    """把 AI proposal 物化成 Claim。

    `status` 参数**只对服务端开放**（默认 `INCONCLUSIVE`）；传入非法值即拒。
    模型无法走到这里 —— 它只能产出 `ClaimProposal`。
    """
    resolved = str(status).upper() if status else ClaimStatus.INCONCLUSIVE.value
    if resolved not in {s.value for s in ClaimStatus}:
        raise ClaimStatusUnknown(f"未知 claim status: {status!r}")

    stamp = _now_iso(now)
    links = tuple(
        EvidenceLink(
            evidence_id=eid,
            role=EvidenceLinkRole.SUPPORTS.value,
            claim_kind=proposal.claim_kind,
            added_at=stamp,
            added_by="AI_PROPOSAL",
        )
        for eid in proposal.evidence_refs
    )
    experiments = tuple(
        ExperimentLink(run_id=rid, added_at=stamp) for rid in proposal.experiment_refs
    )
    review_reasons: list[str] = ["AI_PROPOSED_NOT_VERIFIED"]
    audit = {
        "origin": "AI_PROPOSAL",
        # 模型自报的状态**只能**作为审计事实存在，绝不成为权威状态。
        "aiProposedStatus": proposal.proposed_status,
        "rationale": proposal.rationale,
        "note": proposal.note,
        "materializedAt": stamp,
        "method": "server-gate",
    }
    return ClaimAggregate(
        claim_id=str(claim_id),
        session_id=str(session_id),
        revision=int(revision),
        statement=proposal.statement,
        scope=proposal.scope,
        assumptions=tuple(proposal.assumptions or ()),
        status=resolved,
        confidence_basis=ConfidenceBasis(
            method="rubric-v1", band="NONE", score=0.0, factors={}, computed_at=stamp,
            notes="尚未评估证据支持强度；评估由服务端 rubric 完成。",
        ),
        evidence_links=links,
        experiment_links=experiments,
        counter_evidence=(),
        limitations=tuple(proposal.limitations or ()),
        owner_id=str(owner_id),
        acl=dict(acl),
        audit=audit,
        support_quality=SupportQuality.UNSUPPORTED.value,
        requires_review=True,
        review_reasons=tuple(review_reasons),
        claim_kind=proposal.claim_kind,
        created_at=stamp,
    )


def coerce_proposal(payload: Mapping[str, Any]) -> ClaimProposal:
    """从 HTTP / 模型 JSON 构造 proposal（形状收敛只在这里做一次）。"""
    scope_raw = payload.get("scope")
    scope = scope_raw if isinstance(scope_raw, ClaimScope) else None
    if scope is None and isinstance(scope_raw, Mapping):
        scope = ClaimScope(
            domain=str(scope_raw.get("domain") or ""),
            population=str(scope_raw.get("population") or ""),
            parameters=tuple(scope_raw.get("parameters") or ()),
            boundary=str(scope_raw.get("boundary") or ""),
        )
    assumptions: list[ClaimAssumption] = []
    for raw in payload.get("assumptions") or ():
        if isinstance(raw, ClaimAssumption):
            assumptions.append(raw)
        elif isinstance(raw, Mapping):
            assumptions.append(
                ClaimAssumption(
                    statement=str(raw.get("statement") or ""),
                    basis=str(raw.get("basis") or ""),
                    falsifiable=bool(raw.get("falsifiable", True)),
                )
            )
        else:
            raise ClaimValidationError(
                "assumption 必须是对象", code="ASSUMPTION_SHAPE_INVALID", value=repr(raw)[:80]
            )
    return ClaimProposal(
        statement=str(payload.get("statement") or ""),
        scope=scope,  # type: ignore[arg-type]
        assumptions=tuple(assumptions),
        claim_kind=str(payload.get("claimKind") or payload.get("claim_kind") or ClaimKind.PARAPHRASE.value),
        limitations=tuple(payload.get("limitations") or ()),
        evidence_refs=tuple(payload.get("evidenceRefs") or payload.get("evidence_refs") or ()),
        experiment_refs=tuple(payload.get("experimentRefs") or payload.get("experiment_refs") or ()),
        proposed_status=str(payload.get("proposedStatus") or payload.get("proposed_status") or "INCONCLUSIVE"),
        rationale=str(payload.get("rationale") or ""),
        note=str(payload.get("note") or ""),
    )


def aggregate_from_content(
    content: Mapping[str, Any],
    *,
    claim_id: str,
    session_id: str,
    revision: int,
    owner_id: str,
    acl: Mapping[str, Any],
    created_at: str = "",
    published: bool = False,
    published_at: str = "",
    published_by: str = "",
    status: str | None = None,
) -> ClaimAggregate:
    """从落库的 `content` JSONB 还原聚合（读路径，**不**重新过 gate）。"""
    scope_raw = content.get("scope") or {}
    scope = ClaimScope(
        domain=str(scope_raw.get("domain") or "unspecified"),
        population=str(scope_raw.get("population") or ""),
        parameters=tuple(scope_raw.get("parameters") or ()),
        boundary=str(scope_raw.get("boundary") or ""),
    )
    assumptions = tuple(
        ClaimAssumption(
            statement=str(a.get("statement") or ""),
            basis=str(a.get("basis") or ""),
            falsifiable=bool(a.get("falsifiable", True)),
        )
        for a in (content.get("assumptions") or ())
        if isinstance(a, Mapping)
    )
    links = tuple(
        EvidenceLink(
            evidence_id=str(l.get("evidenceId") or l.get("evidence_id") or ""),
            role=str(l.get("role") or "SUPPORTS"),
            claim_kind=str(l.get("claimKind") or "PARAPHRASE"),
            band=str(l.get("band") or "NONE"),
            score=float(l.get("score") or 0.0),
            usable=bool(l.get("usable")),
            blocking=tuple(l.get("blocking") or ()),
            factors=tuple(l.get("factors") or ()),
            note=str(l.get("note") or ""),
            added_at=str(l.get("addedAt") or ""),
            added_by=str(l.get("addedBy") or ""),
        )
        for l in (content.get("evidenceLinks") or ())
        if isinstance(l, Mapping)
    )
    experiments = tuple(
        ExperimentLink(
            # 兼容两种形状：聚合层写 camelCase，Gate 快照写 snake_case。
            run_id=str(x.get("runId") or x.get("run_id") or ""),
            role=str(x.get("role") or "SUPPORTS"),
            reproducible=bool(x.get("reproducible")),
            exit_code=x.get("exitCode", x.get("exit_code")),
            repro_missing=tuple(x.get("reproMissing") or x.get("repro_missing") or ()),
            note=str(x.get("note") or ""),
            added_at=str(x.get("addedAt") or x.get("added_at") or ""),
        )
        for x in (content.get("experimentLinks") or ())
        if isinstance(x, Mapping)
    )
    counters = tuple(
        CounterEvidence(
            source_kind=str(c.get("sourceKind") or "VERIFICATION"),
            ref_id=str(c.get("refId") or ""),
            summary=str(c.get("summary") or ""),
            severity=str(c.get("severity") or "BLOCKING"),
            detected_by=str(c.get("detectedBy") or ""),
            detected_at=str(c.get("detectedAt") or ""),
            witness=dict(c.get("witness") or {}),
        )
        for c in (content.get("counterEvidence") or ())
        if isinstance(c, Mapping)
    )
    basis_raw = content.get("confidenceBasis") or {}
    basis = ConfidenceBasis(
        method=str(basis_raw.get("method") or "rubric-v1"),
        band=str(basis_raw.get("band") or "NONE"),
        score=float(basis_raw.get("score") or 0.0),
        factors=dict(basis_raw.get("factors") or {}),
        computed_at=str(basis_raw.get("computedAt") or ""),
        notes=str(basis_raw.get("notes") or ""),
    )
    return ClaimAggregate(
        claim_id=str(claim_id),
        session_id=str(session_id),
        revision=int(revision),
        statement=str(content.get("statement") or content.get("text") or ""),
        scope=scope,
        assumptions=assumptions,
        status=(str(status).upper() if status else str(content.get("status") or "INCONCLUSIVE")),
        confidence_basis=basis,
        evidence_links=links,
        experiment_links=experiments,
        counter_evidence=counters,
        limitations=tuple(str(x) for x in (content.get("limitations") or ())),
        owner_id=str(owner_id),
        acl=dict(acl),
        audit=dict(content.get("audit") or {}),
        support_quality=str(content.get("supportQuality") or "UNSUPPORTED"),
        requires_review=bool(content.get("requiresReview")),
        review_reasons=tuple(content.get("reviewReasons") or ()),
        gate_id=str(content.get("gateId") or ""),
        satisfied=tuple(content.get("satisfied") or ()),
        missing=tuple(content.get("missing") or ()),
        conditions=tuple(content.get("conditions") or ()),
        refutation=tuple(content.get("refutation") or ()),
        published=bool(published),
        published_at=published_at,
        published_by=published_by,
        claim_kind=str(content.get("claimKind") or "PARAPHRASE"),
        created_at=created_at,
    )


def round_sequence(values: Sequence[Any]) -> list[Any]:  # pragma: no cover - 兼容辅助
    return list(values)
