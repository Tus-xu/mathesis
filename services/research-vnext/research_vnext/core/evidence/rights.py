"""RV07 · rights 强制层。

`RightsInfo`（事实）→ `RightsPolicy`（边界）→ 四个出口各自的强制：
`export_payload` / `prompt_payload` / `log_payload` / `enforce_snippet`。

**所有出口都只从 policy 取上限**，没有第二处判断；因此"改了 rights 却忘了改
某个出口"这种漂移在结构上不可能发生。
"""

from __future__ import annotations

from typing import Any

from .contracts import EvidenceCard, RightsInfo, RightsPolicy, rights_policy_for
from .errors import EvidenceRightsViolation
from .types import Warn

__all__ = [
    "Warn",
    "policy_for",
    "enforce_snippet",
    "assert_quote_allowed",
    "export_payload",
    "prompt_payload",
    "log_payload",
]


def policy_for(rights: RightsInfo) -> RightsPolicy:
    """权利事实 → 可执行边界（唯一推导入口）。"""
    return rights_policy_for(rights)


def enforce_snippet(text: str, policy: RightsPolicy) -> tuple[str, list[str]]:
    """按 policy 裁剪待存储的原文预览。

    返回 `(bounded_text, warnings)`。`quote_max_chars == 0` 时**整体丢弃**原文
    —— 不是截断成 0 长度字符串的等价写法，而是明确记录"因权利被抹除"。
    """
    warnings: list[str] = []
    if policy.quote_max_chars <= 0:
        if text:
            warnings.append(Warn.SNIPPET_REDACTED_BY_RIGHTS.value)
        return "", warnings
    if len(text) > policy.quote_max_chars:
        return text[: policy.quote_max_chars], [Warn.QUOTE_TRUNCATED.value]
    return text, warnings


def assert_quote_allowed(text: str, policy: RightsPolicy) -> None:
    """逐字引用是否被允许（导出/prompt 之外的自定义出口用）。"""
    if policy.quote_max_chars <= 0:
        raise EvidenceRightsViolation(
            "rights 不允许任何逐字引用（quote_max_chars=0）", reason=Warn.SNIPPET_REDACTED_BY_RIGHTS.value
        )
    if len(text) > policy.quote_max_chars:
        raise EvidenceRightsViolation(
            f"引用长度 {len(text)} 超过权利允许的 {policy.quote_max_chars} 字符",
            reason=Warn.QUOTE_TRUNCATED.value,
        )


def export_payload(card: EvidenceCard) -> dict[str, Any]:
    """导出（论文/报告）出口：受限原文**不得**出现在导出物里。"""
    policy = policy_for(card.rights)
    if not policy.export_included:
        raise EvidenceRightsViolation(
            f"rights_status={card.rights.rights_status.value} 不允许把逐字引用放进导出物",
            reason=Warn.EXPORT_BLOCKED_BY_RIGHTS.value,
        )
    assert_quote_allowed(card.snippet, policy)
    return {
        "card_id": card.card_id,
        "type": card.evidence_type.value,
        "quote": card.snippet,
        "snippet_hash": card.snippet_hash,
        "license_id": card.rights.license_id,
        "rights_status": card.rights.rights_status.value,
        "citation_eligible": card.citation_eligible,
        "source_ref": card.source_ref.as_dict(),
        "locator": card.locator.as_dict(),
    }


def prompt_payload(card: EvidenceCard) -> dict[str, Any]:
    """prompt 出口：受限/许可档位只给指纹与结构化事实，**不给原文**。"""
    policy = policy_for(card.rights)
    payload: dict[str, Any] = {
        "card_id": card.card_id,
        "type": card.evidence_type.value,
        "verbatim_allowed": policy.prompt_verbatim_allowed,
        "snippet_hash": card.snippet_hash,
        "structured_fact": card.structured_fact.as_dict() if card.structured_fact else None,
        "rights_status": card.rights.rights_status.value,
        "locator": card.locator.as_dict(),
        "citation_eligible": card.citation_eligible,
        "limitations": list(card.limitations),
        "warnings": list(card.warnings),
    }
    if policy.prompt_verbatim_allowed:
        payload["quote"] = card.snippet
    else:
        payload["quote"] = ""
        payload.setdefault("warnings", [])
    return payload


def log_payload(card: EvidenceCard) -> dict[str, Any]:
    """日志出口：**永远**只出指纹与标识，逐字原文永不进日志。"""
    policy = policy_for(card.rights)
    return {
        "card_id": card.card_id,
        "type": card.evidence_type.value,
        "snippet_hash": card.snippet_hash,
        "snippet_len": len(card.snippet),
        "rights_status": card.rights.rights_status.value,
        "log_verbatim_allowed": policy.log_verbatim_allowed,
        "locator_dimension": card.locator.dimension,
        "stale_state": card.stale_state.value,
    }
