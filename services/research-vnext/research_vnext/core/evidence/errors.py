"""RV07 · 证据域错误。

分层与 RV06 一致：
- `EvidenceError` —— 运行时基类；
- `EvidenceContractError` —— 契约违规（构造期拒绝，同时是 `ValueError`）；
- 三个专用子类把"错在哪一类防线"写进类型，而不是只写进 message：
  `EvidenceRightsViolation`（权利）、`EvidenceLocatorError`（定位）、
  `EvidenceACLViolation`（访问控制）、`EvidenceFabricationRejected`（伪造）。
"""

from __future__ import annotations


class EvidenceError(Exception):
    """运行时基类。

    与 `domain.errors.DomainError` 同构：`code` 可机器处理，附加信息进 `detail`，
    HTTP 状态由 API 层映射（领域层不认识 HTTP）。
    """

    code = "EVIDENCE_ERROR"
    http_status = 400

    def __init__(self, message: str = "", **detail: object) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = dict(detail)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.detail}


class EvidenceContractError(EvidenceError, ValueError):
    """契约违规：构造期拒绝。"""

    code = "EVIDENCE_CONTRACT_ERROR"
    http_status = 422


class EvidenceFabricationRejected(EvidenceContractError):
    """伪造/冒充被拒。

    典型：摘要冒充论文原文证据；教材带 DOI；AI 直接声明 source hash。
    """

    code = "EVIDENCE_FABRICATION_REJECTED"


class EvidenceRightsViolation(EvidenceContractError):
    """权利违规：超出 rights 允许的引用长度 / 可见性 / 导出范围。"""

    code = "EVIDENCE_RIGHTS_VIOLATION"


class EvidenceLocatorError(EvidenceError):
    """定位不可用：anchor 缺失、跨 tenant、外部定位不可验证。

    刻意**不**继承 `EvidenceContractError`：这是**运行时**发现（源已变），
    不是构造期的静态违规。
    """

    code = "EVIDENCE_LOCATOR_UNAVAILABLE"
    http_status = 409


class EvidenceACLViolation(EvidenceContractError):
    """ACL 违规：租户/项目/可见性越界。"""

    code = "EVIDENCE_ACL_VIOLATION"
    http_status = 403


class NoEvidenceError(EvidenceError):
    """无证据（UNKNOWN / UNSUPPORTED）却被要求给出可引用结论。"""

    code = "NO_EVIDENCE"
    http_status = 409


__all__ = [
    "EvidenceError",
    "EvidenceContractError",
    "EvidenceFabricationRejected",
    "EvidenceRightsViolation",
    "EvidenceLocatorError",
    "EvidenceACLViolation",
    "NoEvidenceError",
]
