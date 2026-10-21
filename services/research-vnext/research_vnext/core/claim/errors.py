"""RV12 · Claim 域错误码（**封闭**集合，越界即拒）。

为什么单独一个模块而不是复用 `domain.errors.DomainError` 的裸构造：
claim 状态机的拒绝理由必须能被前端与审计**机读**。"证据不够"和
"证据没了"是两种完全不同的运维动作，混成一个通用错误码等于没有信息。
"""

from __future__ import annotations

from ..domain.errors import DomainError


class ClaimDomainError(DomainError):
    code = "CLAIM_DOMAIN_ERROR"
    http_status = 422


class ClaimValidationError(ClaimDomainError):
    code = "CLAIM_VALIDATION_ERROR"


class ClaimStatusUnknown(ClaimValidationError):
    code = "CLAIM_STATUS_UNKNOWN"


class ClaimStatementEmpty(ClaimValidationError):
    code = "CLAIM_STATEMENT_EMPTY"


class ClaimScopeRequired(ClaimValidationError):
    code = "CLAIM_SCOPE_REQUIRED"


class ClaimClaimKindUnknown(ClaimValidationError):
    code = "CLAIM_KIND_UNKNOWN"


class AssumptionNotFalsifiable(ClaimValidationError):
    code = "ASSUMPTION_NOT_FALSIFIABLE"


class ClaimTransitionRejected(ClaimDomainError):
    """状态迁移被 Gate 拒绝（409：冲突而非参数错误）。"""

    code = "CLAIM_TRANSITION_REJECTED"
    http_status = 409


class ClaimCounterevidenceUnresolved(ClaimTransitionRejected):
    code = "CLAIM_COUNTEREVIDENCE_UNRESOLVED"


class ClaimPublishRejected(ClaimTransitionRejected):
    code = "CLAIM_NOT_PUBLISHABLE"


class ClaimEvidenceUnresolved(ClaimValidationError):
    code = "CLAIM_EVIDENCE_UNRESOLVED"


class ClaimExperimentUnresolved(ClaimValidationError):
    code = "CLAIM_EXPERIMENT_UNRESOLVED"


class ClaimUnknown(ClaimDomainError):
    code = "CLAIM_NOT_FOUND"
    http_status = 404


__all__ = [
    "AssumptionNotFalsifiable",
    "ClaimClaimKindUnknown",
    "ClaimCounterevidenceUnresolved",
    "ClaimDomainError",
    "ClaimEvidenceUnresolved",
    "ClaimExperimentUnresolved",
    "ClaimPublishRejected",
    "ClaimScopeRequired",
    "ClaimStatementEmpty",
    "ClaimStatusUnknown",
    "ClaimTransitionRejected",
    "ClaimUnknown",
    "ClaimValidationError",
]
