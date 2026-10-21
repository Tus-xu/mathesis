"""RV07 · 无证据行为：拒答 / 降级语义。

铁律：**UNKNOWN 与 UNSUPPORTED 都不得变成可引用的结论。**

- `UNKNOWN`：没有可用证据（没有 / 全部失效 / 全部 stale）→ 不知道，不能说；
- `UNSUPPORTED`：有证据，但**这类证据撑不住这类主张**
  （摘要撑不住逐字引用、教材撑不住"文献已支持"、派生证据没有上游）
  → 明确说"证据不支持"，同样不产出可引用结论。

判定只看 `card.usable`（ACTIVE 且未 stale）与支持矩阵；**不**看 provider 名、
不看 snippet 长度、不看模型置信度 —— 那些都不是证据资格。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import EvidenceCard
from .errors import NoEvidenceError
from .types import (
    ClaimKind,
    EvidenceCardType,
    EvidenceStatus,
    NoEvidenceVerdict,
    supports_claim_kind,
)

__all__ = ["Reason", "Sufficiency", "assess", "assert_citable", "answer_or_refuse"]


class Reason:
    """机读原因码（字符串常量，便于直接进响应体）。"""

    NO_EVIDENCE = "NO_EVIDENCE"
    NO_ACTIVE_EVIDENCE = "NO_ACTIVE_EVIDENCE"
    ALL_STALE = "ALL_STALE"
    NO_LITERATURE_SUPPORT = "NO_LITERATURE_SUPPORT"
    DERIVED_ONLY = "DERIVED_ONLY"
    ABSTRACT_CANNOT_SUPPORT_VERBATIM = "ABSTRACT_CANNOT_SUPPORT_VERBATIM"
    NO_SUPPORTING_KIND = "NO_SUPPORTING_KIND"


@dataclass(frozen=True)
class Sufficiency:
    verdict: NoEvidenceVerdict
    citation_allowed: bool
    reasons: tuple[str, ...]
    usable_card_ids: tuple[str, ...] = ()
    blocked_card_ids: tuple[str, ...] = ()
    claim_kind: ClaimKind = ClaimKind.PARAPHRASE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "citation_allowed": self.citation_allowed,
            "reasons": list(self.reasons),
            "usable_card_ids": list(self.usable_card_ids),
            "blocked_card_ids": list(self.blocked_card_ids),
            "claim_kind": self.claim_kind.value,
        }


def assess(cards: list[EvidenceCard], *, claim_kind: ClaimKind) -> Sufficiency:
    """证据充分性判定。"""
    kind = ClaimKind(claim_kind)
    if not cards:
        return Sufficiency(
            verdict=NoEvidenceVerdict.UNKNOWN,
            citation_allowed=False,
            reasons=(Reason.NO_EVIDENCE,),
            claim_kind=kind,
        )

    usable = [c for c in cards if c.usable]
    blocked = [c for c in cards if not c.usable]
    blocked_ids = tuple(c.card_id for c in blocked)

    if not usable:
        # 有 ACTIVE 但全部 stale → ALL_STALE；否则就是根本没有生效证据。
        any_active = any(c.status is EvidenceStatus.ACTIVE for c in cards)
        reason = Reason.ALL_STALE if any_active else Reason.NO_ACTIVE_EVIDENCE
        return Sufficiency(
            verdict=NoEvidenceVerdict.UNKNOWN,
            citation_allowed=False,
            reasons=(reason,),
            blocked_card_ids=blocked_ids,
            claim_kind=kind,
        )

    supporting = [c for c in usable if supports_claim_kind(c.evidence_type, kind)]
    usable_ids = tuple(c.card_id for c in usable)

    if supporting:
        return Sufficiency(
            verdict=NoEvidenceVerdict.SUPPORTED,
            citation_allowed=True,
            reasons=(),
            usable_card_ids=usable_ids,
            blocked_card_ids=blocked_ids,
            claim_kind=kind,
        )

    reasons: list[str] = []
    if all(c.evidence_type is EvidenceCardType.DERIVED for c in usable):
        reasons.append(Reason.DERIVED_ONLY)
    if kind is ClaimKind.VERBATIM_QUOTE and any(
        c.evidence_type is EvidenceCardType.PAPER_ABSTRACT for c in usable
    ):
        reasons.append(Reason.ABSTRACT_CANNOT_SUPPORT_VERBATIM)
    if all(not c.profile.counts_as_literature_support for c in usable):
        reasons.append(Reason.NO_LITERATURE_SUPPORT)
    if not reasons:
        reasons.append(Reason.NO_SUPPORTING_KIND)

    return Sufficiency(
        verdict=NoEvidenceVerdict.UNSUPPORTED,
        citation_allowed=False,
        reasons=tuple(reasons),
        usable_card_ids=usable_ids,
        blocked_card_ids=blocked_ids,
        claim_kind=kind,
    )


def assert_citable(sufficiency: Sufficiency) -> None:
    """只有 `SUPPORTED` 才允许继续产出可引用结论。"""
    if sufficiency.verdict is not NoEvidenceVerdict.SUPPORTED:
        raise NoEvidenceError(
            f"证据不足（{sufficiency.verdict.value}）：{list(sufficiency.reasons)}",
            verdict=sufficiency.verdict.value,
            reasons=list(sufficiency.reasons),
        )


_REFUSAL_TEXT = {
    NoEvidenceVerdict.UNKNOWN: (
        "证据不足（UNKNOWN）：没有可回源的证据支撑该结论，"
        "因此不给出可引用结论。请先补充或刷新证据。"
    ),
    NoEvidenceVerdict.UNSUPPORTED: (
        "证据不支持（UNSUPPORTED）：现有证据的类型无法支撑该主张"
        "（例如摘要不能支撑逐字引用、教材不能支撑“文献已支持”、"
        "派生结论必须指回上游证据），因此不给出可引用结论。"
    ),
}


def answer_or_refuse(sufficiency: Sufficiency) -> dict[str, Any]:
    """把判定渲染成"能答"或"明确拒答"。

    **不产生 `conclusion` 字段**：无证据时连字段都不存在，
    下游无法把 UNKNOWN/UNSUPPORTED 误读成结论。
    """
    if sufficiency.verdict is NoEvidenceVerdict.SUPPORTED:
        return {
            "citable": True,
            "verdict": sufficiency.verdict.value,
            "reasons": [],
            "usable_card_ids": list(sufficiency.usable_card_ids),
            "blocked_card_ids": list(sufficiency.blocked_card_ids),
            "text": "",
        }
    return {
        "citable": False,
        "verdict": sufficiency.verdict.value,
        "reasons": list(sufficiency.reasons),
        "usable_card_ids": list(sufficiency.usable_card_ids),
        "blocked_card_ids": list(sufficiency.blocked_card_ids),
        "text": _REFUSAL_TEXT[sufficiency.verdict],
    }
