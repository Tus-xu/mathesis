"""RV12 · 证据支持强度 rubric（**唯一**的支持强度入口）。

设计立场（任务书 §5）：

> 证据支持强度按 type/rights/locator/freshness 计算**可解释**结果，
> 不仅用 LLM 一个分数。

因此本模块把"这条证据能不能撑住这个主张"拆成**四个因子**，每个因子给出：
观测值（`value`）、是否通过（`passed`）、权重（`weight`）、机读理由（`reason`），
并在出现致命问题时写入 `blocking`（机读原因码的封闭集合）。

**任何因子的判据变化都必须改这里**，不允许在调用点写第二套判断 ——
"两处判据"必然漂移，而漂移的支持判定会让 SOLVED 变成一个随机事件。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..evidence.types import ClaimKind, EvidenceCardType, supports_claim_kind
from .errors import ClaimClaimKindUnknown, ClaimValidationError
from .types import BAND_RANK, EvidenceLinkRole, SupportBand

__all__ = [
    "FACTOR_WEIGHTS",
    "AggregateSupport",
    "EvidenceSupportAssessment",
    "FactorAssessment",
    "aggregate_support",
    "assess_evidence_support",
    "band_of",
]

#: 四个因子及其权重（和必须为 1）。改动这里等于改动产品的证据标准。
FACTOR_WEIGHTS: dict[str, float] = {
    "F_TYPE": 0.40,
    "F_ELIGIBILITY": 0.25,
    "F_LOCATOR": 0.20,
    "F_FRESHNESS": 0.15,
}

#: 支持带阈值。刻意**不是**线性映射：STRONG 需要"四个因子几乎全绿"。
_BAND_STRONG = 0.90
_BAND_MODERATE = 0.70

#: 新鲜度分档（天）。
_AGING_DAYS = 180
_EXPIRY_DAYS = 365


def band_of(score: float) -> str:
    if score >= _BAND_STRONG:
        return SupportBand.STRONG.value
    if score >= _BAND_MODERATE:
        return SupportBand.MODERATE.value
    if score > 0:
        return SupportBand.WEAK.value
    return SupportBand.NONE.value


@dataclass(frozen=True)
class FactorAssessment:
    factor: str
    passed: bool
    weight: float
    value: str
    reason: str
    blocking: bool = False
    factor_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "passed": self.passed,
            "weight": self.weight,
            "value": self.value,
            "reason": self.reason,
            "blocking": self.blocking,
            "factorScore": self.factor_score,
        }


@dataclass(frozen=True)
class EvidenceSupportAssessment:
    """一条证据对一个主张的支持评估。"""

    evidence_id: str
    evidence_type: str
    claim_kind: str
    role: str
    band: str
    score: float
    factors: tuple[FactorAssessment, ...]
    blocking: tuple[str, ...]
    method: str = "rubric-v1"

    @property
    def usable(self) -> bool:
        """能否作为**支撑**参与判定（blocking 非空即不可用）。"""
        return not self.blocking and self.score > 0

    def factor(self, code: str) -> FactorAssessment:
        for f in self.factors:
            if f.factor == code:
                return f
        raise KeyError(code)  # pragma: no cover - 因子集封闭

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidenceId": self.evidence_id,
            "evidenceType": self.evidence_type,
            "claimKind": self.claim_kind,
            "role": self.role,
            "band": self.band,
            "score": round(self.score, 6),
            "method": self.method,
            "usable": self.usable,
            "blocking": list(self.blocking),
            "factors": [f.to_dict() for f in self.factors],
        }


# --------------------------------------------------------------------------
# 因子
# --------------------------------------------------------------------------


def _type_factor(evidence_type: str, claim_kind: ClaimKind, view: Mapping[str, Any]) -> FactorAssessment:
    weight = FACTOR_WEIGHTS["F_TYPE"]
    try:
        ok = supports_claim_kind(EvidenceCardType(evidence_type), claim_kind)
    except ValueError as exc:
        raise ClaimValidationError(
            "未知证据类型", code="EVIDENCE_TYPE_UNKNOWN", evidenceType=evidence_type
        ) from exc
    reason = "TYPE_SUPPORTS_CLAIM_KIND" if ok else "EVIDENCE_TYPE_INCOMPATIBLE"
    return FactorAssessment(
        factor="F_TYPE",
        passed=ok,
        weight=weight,
        value=f"{evidence_type} -> {claim_kind.value}",
        reason=reason,
        blocking=not ok,
        factor_score=1.0 if ok else 0.0,
    )


def _eligibility_factor(view: Mapping[str, Any]) -> FactorAssessment:
    """rights + 生命周期 + 撤稿：一条证据"能不能被用"。

    刻意把这四件事放在**一个**因子里：它们都是"资格"问题（是/否），
    而不是"强度"问题（强/弱）。拆成四个因子会让人误以为可以互相补偿。
    """
    weight = FACTOR_WEIGHTS["F_ELIGIBILITY"]
    status = str(view.get("status") or "ACTIVE")
    stale_state = str(view.get("stale_state") or "FRESH")
    retracted = bool(view.get("retracted"))
    rights_status = str(view.get("rights_status") or "UNKNOWN")

    if retracted:
        return FactorAssessment(
            "F_ELIGIBILITY", False, weight, "来源已撤稿", "SOURCE_RETRACTED", True, 0.0
        )
    if status != "ACTIVE":
        return FactorAssessment(
            "F_ELIGIBILITY", False, weight, f"status={status}", "EVIDENCE_NOT_USABLE", True, 0.0
        )
    if stale_state == "STALE":
        return FactorAssessment(
            "F_ELIGIBILITY", False, weight, "stale_state=STALE", "EVIDENCE_STALE", True, 0.0
        )
    if rights_status == "UNKNOWN":
        return FactorAssessment(
            "F_ELIGIBILITY", False, weight, "rights=UNKNOWN", "RIGHTS_UNKNOWN", True, 0.0
        )
    if rights_status == "RESTRICTED":
        return FactorAssessment(
            "F_ELIGIBILITY",
            True,
            weight,
            "rights=RESTRICTED（可作事实支撑，不得逐字导出）",
            "RIGHTS_RESTRICTED_LIMITED",
            False,
            0.4,
        )
    return FactorAssessment("F_ELIGIBILITY", True, weight, f"rights={rights_status}", "ELIGIBLE", False, 1.0)


def _locator_factor(view: Mapping[str, Any]) -> FactorAssessment:
    weight = FACTOR_WEIGHTS["F_LOCATOR"]
    locator = view.get("locator") or {}
    if bool(view.get("locator_stale")):
        return FactorAssessment("F_LOCATOR", False, weight, "locator 指向的版本已变", "LOCATOR_STALE", True, 0.0)
    if not isinstance(locator, Mapping) or not dict(locator):
        return FactorAssessment("F_LOCATOR", False, weight, "无定位信息", "LOCATOR_UNRESOLVABLE", True, 0.0)
    kind = str(locator.get("kind") or "EXTERNAL")
    return FactorAssessment("F_LOCATOR", True, weight, f"{kind} 定位可解析", "RESOLVABLE", False, 1.0)


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _freshness_factor(view: Mapping[str, Any], now: datetime) -> FactorAssessment:
    weight = FACTOR_WEIGHTS["F_FRESHNESS"]
    retrieved = _parse_iso(view.get("retrieved_at"))
    if retrieved is None:
        # 无法解析的抓取时间是"未知"，**不是**"新鲜"。默认新鲜是常见的失真来源。
        return FactorAssessment("F_FRESHNESS", False, weight, "retrieved_at 不可解析", "RETRIEVED_AT_UNKNOWN", False, 0.0)
    age_days = (now - retrieved).days
    if age_days <= _AGING_DAYS:
        return FactorAssessment("F_FRESHNESS", True, weight, f"{age_days}d", "FRESH", False, 1.0)
    if age_days <= _EXPIRY_DAYS:
        return FactorAssessment("F_FRESHNESS", False, weight, f"{age_days}d", "EVIDENCE_AGING", False, 0.0)
    return FactorAssessment(
        "F_FRESHNESS", False, weight, f"{age_days}d", "EVIDENCE_TOO_OLD", True, 0.0
    )


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def assess_evidence_support(
    view: Mapping[str, Any],
    *,
    claim_kind: str | ClaimKind,
    role: str = EvidenceLinkRole.SUPPORTS.value,
    now: datetime | None = None,
) -> EvidenceSupportAssessment:
    """评估一条证据对一个主张的支持强度（确定性、可解释、无 LLM）。

    Args:
        view: 证据卡的行视图（与 `research_evidence_card` 同形）。
        claim_kind: 被支撑主张的种类（逐字引用 / 转述 / 数据事实 / 背景）。
        role: 链接角色；`REFUTES` 走同一套强度评估，只是聚合时被当作反证。
        now: 注入当前时间（测试可确定化）。

    Returns:
        `EvidenceSupportAssessment`；出现致命问题时 `band=NONE`、`score=0.0`，
        `blocking` 给出机读原因码。

    Raises:
        ClaimClaimKindUnknown: `claim_kind` 不在封闭词表内。
        ClaimValidationError: 证据类型不在 RV07 七类之内。
    """
    try:
        kind = ClaimKind(claim_kind)
    except ValueError as exc:
        raise ClaimClaimKindUnknown(f"未知 claim_kind: {claim_kind!r}") from exc

    current = now or datetime.now(timezone.utc)
    evidence_id = str(view.get("evidence_id") or "")
    evidence_type = str(view.get("evidence_type") or "")
    link_role = EvidenceLinkRole(role).value if role in {r.value for r in EvidenceLinkRole} else EvidenceLinkRole.SUPPORTS.value

    type_f = _type_factor(evidence_type, kind, view)
    elig_f = _eligibility_factor(view)
    loc_f = _locator_factor(view)
    fresh_f = _freshness_factor(view, current)

    factors = (type_f, elig_f, loc_f, fresh_f)
    blocking: list[str] = []
    if not type_f.passed:
        blocking.append("EVIDENCE_TYPE_INCOMPATIBLE")
        if kind is ClaimKind.VERBATIM_QUOTE and evidence_type == EvidenceCardType.PAPER_ABSTRACT.value:
            blocking.append("ABSTRACT_CANNOT_SUPPORT_VERBATIM")
        if evidence_type == EvidenceCardType.DERIVED.value and not list(view.get("upstream_card_ids") or []):
            blocking.append("DERIVED_REQUIRES_UPSTREAM")
    if elig_f.blocking:
        blocking.append(elig_f.reason)
    if loc_f.blocking:
        blocking.append(loc_f.reason)
    if fresh_f.blocking:
        blocking.append("EVIDENCE_EXPIRED")

    if blocking:
        score = 0.0
    else:
        score = round(sum(f.weight * f.factor_score for f in factors), 6)

    return EvidenceSupportAssessment(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        claim_kind=kind.value,
        role=link_role,
        band=band_of(score),
        score=score,
        factors=factors,
        blocking=tuple(dict.fromkeys(blocking)),
    )


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregateSupport:
    quality: str
    best_band: str
    best_score: float
    conflicted: bool
    supporting: tuple[EvidenceSupportAssessment, ...]
    refuting: tuple[EvidenceSupportAssessment, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality": self.quality,
            "bestBand": self.best_band,
            "bestScore": round(self.best_score, 6),
            "conflicted": self.conflicted,
            "reasons": list(self.reasons),
            "supporting": [a.to_dict() for a in self.supporting],
            "refuting": [a.to_dict() for a in self.refuting],
        }


def aggregate_support(
    supporting: Sequence[EvidenceSupportAssessment],
    *,
    refuting: Sequence[EvidenceSupportAssessment] = (),
) -> AggregateSupport:
    """把多条证据的支持评估聚合成一个**带 + 质量**。

    - 取**最强**支撑而不是平均：一条强证据不该被十条弱证据拉低
      （平均会让"多找几条弱引用"变成刷分手段）；
    - 只要存在**可用**的反证 ⇒ `CONFLICTED`（冲突不等于不支持，见 RV07 的教训）；
    - 没有任何可用支撑 ⇒ `UNSUPPORTED`。
    """
    usable = [a for a in supporting if a.usable]
    usable_refuting = [a for a in refuting if a.usable]
    best = max(usable, key=lambda a: a.score, default=None)
    best_band = best.band if best else SupportBand.NONE.value
    best_score = best.score if best else 0.0

    reasons: list[str] = []
    if usable_refuting:
        reasons.append("REFUTING_EVIDENCE_PRESENT")
    if not usable and supporting:
        for a in supporting:
            reasons.extend(a.blocking)
        if not reasons:
            reasons.append("NO_USABLE_SUPPORT")

    conflicted = bool(usable_refuting)
    if conflicted:
        quality = "CONFLICTED"
    elif best is None:
        quality = "UNSUPPORTED"
    elif BAND_RANK.get(best_band, 0) >= BAND_RANK["MODERATE"]:
        quality = "SUPPORTED"
    else:
        quality = "PARTIAL"

    return AggregateSupport(
        quality=quality,
        best_band=best_band,
        best_score=best_score,
        conflicted=conflicted,
        supporting=tuple(supporting),
        refuting=tuple(refuting),
        reasons=tuple(dict.fromkeys(reasons)),
    )
