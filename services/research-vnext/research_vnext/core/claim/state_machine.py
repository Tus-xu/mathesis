"""RV12 · Claim 状态机 —— **确定性 Gate**。

核心主张（任务书 §必须完成 2）：

> status transition 有确定性 Gate：SOLVED 必须满足预先定义的
> evidence + verification criteria；CONDITIONAL 列出条件；
> INCONCLUSIVE 列出缺口；REFUTED 保留反证。

因此这里**没有**任何"模型判断"或"置信度阈值"：`CRITERIA` 是一张
**预先定义的判据表**，`derive_status()` 只做集合运算。

判定顺序（**反例优先**）：REFUTED 先于 SOLVED 评估。
一条存在可复现反例的结论即使"证据很充分"也不能标 SOLVED ——
否则反证会被"更漂亮的证据"淹没，而这正是伪科学最常见的形态。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import (
    ClaimCounterevidenceUnresolved,
    ClaimStatusUnknown,
    ClaimTransitionRejected,
)
from .types import (
    BAND_RANK,
    MIN_SOLVED_BAND,
    ClaimStatus,
    CounterEvidenceSource,
)
from .types import (
    REVIEW_CONFLICTED_EVIDENCE as _R_CONFLICT,
)
from .types import (
    REVIEW_COUNTEREXAMPLE_REPRODUCED as _R_CEX,
)
from .types import (
    REVIEW_EXPERIMENT_NOT_REPRODUCIBLE as _R_REPRO,
)
from .types import (
    REVIEW_REFERENCE_STALE as _R_REF,
)
from .types import (
    REVIEW_ROBUSTNESS_UNSTABLE as _R_ROBUST,
)
from .types import (
    REVIEW_SENSITIVITY_UNSTABLE as _R_SENS,
)
from .types import (
    REVIEW_VERIFICATION_FAIL as _R_VFAIL,
)
from .types import (
    REVIEW_VERIFICATION_UNKNOWN as _R_VUNKNOWN,
)

__all__ = [
    "CRITERIA",
    "ClaimGateDecision",
    "ClaimGateSnapshot",
    "CriterionSpec",
    "derive_status",
    "evaluate_gate",
    "validate_transition",
]


# --------------------------------------------------------------------------
# 判据表（**预先定义**，不是运行时发明的）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionSpec:
    code: str
    description: str
    required_for: tuple[str, ...]


def _c(code: str, desc: str, *required_for: str) -> tuple[str, CriterionSpec]:
    return code, CriterionSpec(code=code, description=desc, required_for=tuple(required_for))


CRITERIA: dict[str, CriterionSpec] = dict(
    [
        _c("EVIDENCE_LINK_PRESENT", "至少有一条支撑证据链接", "SOLVED", "CONDITIONAL"),
        _c("EVIDENCE_ALL_USABLE", "所有支撑证据当前可用（ACTIVE 且未 stale 且非撤稿）", "SOLVED"),
        _c("EVIDENCE_TYPE_SUPPORTS_CLAIM_KIND", "证据类型能支撑该主张种类（摘要不撑逐字引用）", "SOLVED"),
        _c(
            f"SUPPORT_BAND_MIN_{MIN_SOLVED_BAND}",
            f"聚合支持强度 >= {MIN_SOLVED_BAND}",
            "SOLVED",
        ),
        _c("EXPERIMENT_REPRODUCIBLE", "被引用的实验全部具备可复现元数据", "SOLVED"),
        _c("VERIFICATION_PASS_PRESENT", "存在独立验证 PASS 记录", "SOLVED"),
        _c("VERIFICATION_NO_FAIL", "不存在验证 FAIL 记录", "SOLVED"),
        _c("SENSITIVITY_STABLE", "敏感性验证没有失败", "SOLVED"),
        _c("ROBUSTNESS_STABLE", "robustness 验证没有失败", "SOLVED"),
        _c("VERIFICATION_COUNTEREXAMPLE_CLEAR", "反例搜索没有命中", "SOLVED"),
        _c("NO_COUNTER_EVIDENCE", "不存在未解决的反证", "SOLVED"),
        _c("REFERENCES_FRESH", "引用的证据/实验/验证版本未过期", "SOLVED"),
        _c("REFUTATION_WITNESS", "存在可复现的反证/见证物", "REFUTED"),
        _c("GAPS_DOCUMENTED", "缺口已被列出（不得静默）", "INCONCLUSIVE", "CONDITIONAL"),
    ]
)

#: SOLVED 需要的全部判据（顺序即展示顺序）。
SOLVED_REQUIRED: tuple[str, ...] = tuple(
    code for code, spec in CRITERIA.items() if "SOLVED" in spec.required_for
)

#: REFUTED 需要的判据。
REFUTED_REQUIRED: tuple[str, ...] = ("REFUTATION_WITNESS",)


# --------------------------------------------------------------------------
# 快照
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimGateSnapshot:
    """Gate 输入。**全部是已判定的事实**，不含任何模型自评。"""

    claim_kind: str = "PARAPHRASE"
    support_band: str = "NONE"
    quality: str = "UNSUPPORTED"
    evidence_links: tuple[dict[str, Any], ...] = ()
    experiment_links: tuple[dict[str, Any], ...] = ()
    verifications: tuple[dict[str, Any], ...] = ()
    counter_evidence: tuple[dict[str, Any], ...] = ()
    revision_fresh: bool = True
    reference_conflicts: tuple[str, ...] = ()

    @classmethod
    def of(cls, raw: Mapping[str, Any] | ClaimGateSnapshot) -> ClaimGateSnapshot:
        if isinstance(raw, ClaimGateSnapshot):
            return raw
        return cls(
            claim_kind=str(raw.get("claim_kind") or raw.get("claimKind") or "PARAPHRASE"),
            support_band=str(raw.get("support_band") or raw.get("supportBand") or "NONE"),
            quality=str(raw.get("quality") or "UNSUPPORTED"),
            evidence_links=tuple(dict(x) for x in (raw.get("evidence_links") or ())),
            experiment_links=tuple(dict(x) for x in (raw.get("experiment_links") or ())),
            verifications=tuple(dict(x) for x in (raw.get("verifications") or ())),
            counter_evidence=tuple(dict(x) for x in (raw.get("counter_evidence") or ())),
            revision_fresh=bool(raw.get("revision_fresh", True)),
            reference_conflicts=tuple(raw.get("reference_conflicts") or ()),
        )


@dataclass(frozen=True)
class ClaimGateDecision:
    status: str
    gate_id: str
    satisfied: tuple[str, ...]
    missing: tuple[str, ...]
    conditions: tuple[str, ...]
    refutation: tuple[str, ...]
    requires_review: bool
    review_reasons: tuple[str, ...]
    criteria: tuple[dict[str, Any], ...]
    #: 针对**目标状态**的 Gate 是否满足。
    #: 与 `missing` 刻意分开：`missing` 在 REFUTED 决策里承载的是
    #: "距离 SOLVED 还差什么"（信息性），不是"REFUTED 不成立"。
    gate_satisfied: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gateId": self.gate_id,
            "satisfied": list(self.satisfied),
            "missing": list(self.missing),
            "conditions": list(self.conditions),
            "refutation": list(self.refutation),
            "requiresReview": bool(self.requires_review),
            "reviewReasons": list(self.review_reasons),
            "criteria": [dict(c) for c in self.criteria],
            "gateSatisfied": bool(self.gate_satisfied),
        }


# --------------------------------------------------------------------------
# 判据求值
# --------------------------------------------------------------------------


def _supporting(snap: ClaimGateSnapshot) -> list[dict[str, Any]]:
    return [l for l in snap.evidence_links if str(l.get("role") or "SUPPORTS") != "REFUTES"]


def _link_usable(link: Mapping[str, Any]) -> bool:
    """一条证据链接是否可用。

    刻意**同时**看 `usable` 与 `blocking`：两者不一致时以"不可用"为准
    （fail closed）。快照的两个字段可能因为某条路径的疏漏而漂移，
    而"漂移被当成可用"正是 SOLVED 凭空成立的方式。
    """
    return bool(link.get("usable")) and not tuple(link.get("blocking") or ())


def _criterion_results(snap: ClaimGateSnapshot) -> dict[str, tuple[bool, str]]:
    support = _supporting(snap)
    results: dict[str, tuple[bool, str]] = {}

    results["EVIDENCE_LINK_PRESENT"] = (
        bool(support),
        f"{len(support)} 条支撑链接",
    )
    all_usable = bool(support) and all(_link_usable(l) for l in support)
    results["EVIDENCE_ALL_USABLE"] = (
        all_usable,
        "可用" if all_usable else "存在不可用证据",
    )
    incompatible = [
        str(l.get("evidenceId") or "")
        for l in support
        if "EVIDENCE_TYPE_INCOMPATIBLE" in tuple(l.get("blocking") or ())
    ]
    results["EVIDENCE_TYPE_SUPPORTS_CLAIM_KIND"] = (
        not incompatible,
        f"类型不匹配: {incompatible}" if incompatible else "类型匹配",
    )
    results[f"SUPPORT_BAND_MIN_{MIN_SOLVED_BAND}"] = (
        BAND_RANK.get(str(snap.support_band), 0) >= BAND_RANK[MIN_SOLVED_BAND],
        f"band={snap.support_band}",
    )
    missing_repro = [
        str(x.get("runId") or "")
        for x in snap.experiment_links
        if not bool(x.get("reproducible"))
    ]
    results["EXPERIMENT_REPRODUCIBLE"] = (
        not missing_repro,
        f"不可复现: {missing_repro}" if missing_repro else "全部可复现（或未引用实验）",
    )

    passes = [v for v in snap.verifications if str(v.get("result")) == "PASS"]
    fails = [v for v in snap.verifications if str(v.get("result")) == "FAIL"]
    unknowns = [v for v in snap.verifications if str(v.get("result")) == "UNKNOWN"]
    results["VERIFICATION_PASS_PRESENT"] = (bool(passes), f"{len(passes)} 条 PASS")
    results["VERIFICATION_NO_FAIL"] = (not fails, f"FAIL 记录: {len(fails)}")

    def _kind_fail(kind: str) -> list[dict[str, Any]]:
        return [v for v in fails if str(v.get("kind")) == kind]

    sens_fail = _kind_fail("SENSITIVITY")
    robust_fail = _kind_fail("ROBUSTNESS")
    cex_fail = _kind_fail("COUNTEREXAMPLE")
    results["SENSITIVITY_STABLE"] = (not sens_fail, f"敏感性失败 {len(sens_fail)} 条")
    results["ROBUSTNESS_STABLE"] = (not robust_fail, f"robustness 失败 {len(robust_fail)} 条")
    results["VERIFICATION_COUNTEREXAMPLE_CLEAR"] = (not cex_fail, f"反例命中 {len(cex_fail)} 条")

    blocking_cex = [c for c in snap.counter_evidence if str(c.get("severity", "BLOCKING")) == "BLOCKING"]
    results["NO_COUNTER_EVIDENCE"] = (not blocking_cex, f"反证 {len(blocking_cex)} 条")

    fresh = bool(snap.revision_fresh) and not snap.reference_conflicts
    results["REFERENCES_FRESH"] = (
        fresh,
        "引用版本为当前版本" if fresh else f"引用已过期: {list(snap.reference_conflicts)}",
    )

    results["REFUTATION_WITNESS"] = (
        bool(blocking_cex),
        f"见证物 {len(blocking_cex)} 个" if blocking_cex else "无反证见证物",
    )
    # INCONCLUSIVE 的"缺口已列出"永远为真：缺口由 missing 承载，不允许静默。
    results["GAPS_DOCUMENTED"] = (True, f"缺口 {len([c for c in SOLVED_REQUIRED if not results[c][0]])} 项")
    _ = unknowns
    return results


def _review_reasons(snap: ClaimGateSnapshot, results: Mapping[str, tuple[bool, str]]) -> list[str]:
    reasons: list[str] = []
    for link in _supporting(snap):
        if not _link_usable(link):
            reasons.extend(str(b) for b in (link.get("blocking") or ()) or ("EVIDENCE_NOT_USABLE",))
    if str(snap.quality) == "CONFLICTED":
        reasons.append(_R_CONFLICT)
    for v in snap.verifications:
        result = str(v.get("result"))
        if result == "UNKNOWN":
            reasons.append(_R_VUNKNOWN)
            reasons.extend(str(b) for b in (v.get("blocking") or ()))
        elif result == "FAIL":
            blocking = [str(b) for b in (v.get("blocking") or ())]
            reasons.extend(blocking or [_R_VFAIL])
    for c in snap.counter_evidence:
        if str(c.get("severity", "BLOCKING")) == "BLOCKING":
            reason = str(c.get("reason") or _R_CEX)
            reasons.append(reason)
    if not results["EXPERIMENT_REPRODUCIBLE"][0]:
        reasons.append(_R_REPRO)
    if not results["REFERENCES_FRESH"][0]:
        reasons.append(_R_REF)
    if not results["SENSITIVITY_STABLE"][0] and _R_SENS not in reasons:
        reasons.append(_R_SENS)
    if not results["ROBUSTNESS_STABLE"][0] and _R_ROBUST not in reasons:
        reasons.append(_R_ROBUST)
    if not results["VERIFICATION_COUNTEREXAMPLE_CLEAR"][0] and _R_CEX not in reasons:
        reasons.append(_R_CEX)
    if not results["EVIDENCE_TYPE_SUPPORTS_CLAIM_KIND"][0]:
        reasons.append("EVIDENCE_TYPE_INCOMPATIBLE")
    return list(dict.fromkeys(reasons))


def _refutation_lines(snap: ClaimGateSnapshot) -> list[str]:
    lines: list[str] = []
    for c in snap.counter_evidence:
        if str(c.get("severity", "BLOCKING")) != "BLOCKING":
            continue
        src = str(c.get("sourceKind") or CounterEvidenceSource.VERIFICATION.value)
        lines.append(f"[{src}] {c.get('summary') or c.get('refId') or '反证'}")
    for v in snap.verifications:
        if str(v.get("result")) == "FAIL" and str(v.get("kind")) == "COUNTEREXAMPLE":
            detail = v.get("detail") or {}
            witness = detail.get("witness") if isinstance(detail, Mapping) else None
            if witness:
                lines.append(f"[WITNESS] {witness}")
            elif not lines:
                lines.append(f"[COUNTEREXAMPLE] {v.get('recordId') or '反例命中'}")
    return list(dict.fromkeys(lines))


def evaluate_gate(
    raw: Mapping[str, Any] | ClaimGateSnapshot, target_status: str = ClaimStatus.SOLVED.value
) -> ClaimGateDecision:
    """对**指定目标状态**做 Gate 判定（不推断最终状态，只回答"够不够"）。"""
    snap = ClaimGateSnapshot.of(raw)
    target = str(target_status).upper()
    if target not in {s.value for s in ClaimStatus}:
        raise ClaimStatusUnknown(f"未知目标状态: {target_status!r}")

    results = _criterion_results(snap)
    review_reasons = _review_reasons(snap, results)
    required = SOLVED_REQUIRED if target == "SOLVED" else (REFUTED_REQUIRED if target == "REFUTED" else ())
    satisfied = tuple(code for code in required if results[code][0])
    missing = tuple(code for code in required if not results[code][0])
    gate_id = {
        "SOLVED": "G_SOLVED",
        "REFUTED": "G_REFUTED",
        "CONDITIONAL": "G_CONDITIONAL",
        "INCONCLUSIVE": "G_INCONCLUSIVE",
    }[target]
    criteria = tuple(
        {
            "code": code,
            "required": code in required,
            "satisfied": results[code][0],
            "detail": results[code][1],
            "description": CRITERIA[code].description,
        }
        for code in CRITERIA
    )
    return ClaimGateDecision(
        status=target,
        gate_id=gate_id,
        satisfied=satisfied,
        missing=missing,
        conditions=(),
        refutation=tuple(_refutation_lines(snap)) if target == "REFUTED" else (),
        requires_review=bool(review_reasons),
        review_reasons=tuple(review_reasons),
        criteria=criteria,
        gate_satisfied=not missing,
    )


def derive_status(raw: Mapping[str, Any] | ClaimGateSnapshot) -> ClaimGateDecision:
    """推导 claim 的**唯一**权威状态。

    顺序：REFUTED（有见证物）→ SOLVED（判据全满足）→ CONDITIONAL（有支撑但判据未满）
    → INCONCLUSIVE（连支撑都没有）。
    """
    snap = ClaimGateSnapshot.of(raw)
    solved = evaluate_gate(snap, "SOLVED")
    refuted = evaluate_gate(snap, "REFUTED")
    results = _criterion_results(snap)
    has_support = results["EVIDENCE_LINK_PRESENT"][0]

    if refuted.satisfied:
        return ClaimGateDecision(
            status=ClaimStatus.REFUTED.value,
            gate_id="G_REFUTED",
            satisfied=solved.satisfied,
            missing=solved.missing,
            conditions=tuple(
                f"{CRITERIA[code].description}（{results[code][1]}）" for code in solved.missing
            )
            + tuple(refuted.review_reasons),
            refutation=refuted.refutation,
            requires_review=True,
            review_reasons=tuple(dict.fromkeys((*refuted.review_reasons, _R_CEX))),
            criteria=solved.criteria,
            gate_satisfied=True,
        )

    if not solved.missing:
        return ClaimGateDecision(
            status=ClaimStatus.SOLVED.value,
            gate_id="G_SOLVED",
            satisfied=solved.satisfied,
            missing=(),
            conditions=(),
            refutation=(),
            requires_review=solved.requires_review,
            review_reasons=solved.review_reasons,
            criteria=solved.criteria,
            gate_satisfied=True,
        )

    if has_support:
        conditions = tuple(
            f"{CRITERIA[code].description} — 当前: {results[code][1]}" for code in solved.missing
        ) + tuple(solved.review_reasons)
        return ClaimGateDecision(
            status=ClaimStatus.CONDITIONAL.value,
            gate_id="G_CONDITIONAL",
            satisfied=solved.satisfied,
            missing=solved.missing,
            conditions=tuple(dict.fromkeys(conditions)),
            refutation=(),
            requires_review=True,
            review_reasons=solved.review_reasons,
            criteria=solved.criteria,
        )

    return ClaimGateDecision(
        status=ClaimStatus.INCONCLUSIVE.value,
        gate_id="G_INCONCLUSIVE",
        satisfied=solved.satisfied,
        missing=solved.missing,
        conditions=(),
        refutation=(),
        requires_review=True,
        review_reasons=solved.review_reasons,
        criteria=solved.criteria,
    )


# --------------------------------------------------------------------------
# 迁移校验
# --------------------------------------------------------------------------


def validate_transition(
    *,
    current: str,
    target: str,
    gate: ClaimGateDecision,
    counterevidence_addressed: bool = False,
) -> ClaimGateDecision:
    """校验一次状态迁移。不合法即抛（fail closed）。

    - 任何目标状态都必须落在封闭四值内；
    - `SOLVED` 必须**满足**对应 Gate（不允许"降级放行"）；
    - 从 `REFUTED` 回到 `SOLVED` 必须显式声明反证已被解决
      （否则反证会被"重新论证"轻轻抹掉）。
    """
    if str(current).upper() not in {s.value for s in ClaimStatus}:
        raise ClaimStatusUnknown(f"未知当前状态: {current!r}")
    if str(target).upper() not in {s.value for s in ClaimStatus}:
        raise ClaimStatusUnknown(f"未知目标状态: {target!r}")
    wanted = str(target).upper()
    if str(gate.status).upper() != wanted:
        raise ClaimTransitionRejected(
            f"Gate 判定的是 {gate.status}，与目标 {wanted} 不一致（不得复用别的 Gate 结果）",
            gateId=gate.gate_id,
        )
    if wanted in {"SOLVED", "REFUTED"} and not gate.gate_satisfied:
        raise ClaimTransitionRejected(
            f"{wanted} 的 Gate 未满足，拒绝迁移（缺失判据见 missing）",
            gateId=gate.gate_id,
            missing=list(gate.missing),
        )
    if wanted == "SOLVED" and str(current).upper() == "REFUTED" and not counterevidence_addressed:
        raise ClaimCounterevidenceUnresolved(
            "当前状态为 REFUTED：必须先显式解决反证（counterevidence_addressed=true）才能回到 SOLVED",
            gateId=gate.gate_id,
        )
    return gate


def _as_sequence(value: Any) -> Sequence[Any]:  # pragma: no cover - 兼容辅助
    return value if isinstance(value, Sequence) else ()
