"""Research VNext 领域错误（RV02）。

错误码一律可机器处理；`http_status` 由 API 层映射，领域层不认识 HTTP。
"""

from __future__ import annotations


class DomainError(Exception):
    code = "DOMAIN_ERROR"
    http_status = 400

    def __init__(self, message: str = "", **detail) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.detail}


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"
    http_status = 409


class ConfirmationRequired(InvalidTransition):
    code = "CONFIRMATION_REQUIRED"


class PartialIsNotCompleted(InvalidTransition):
    code = "PARTIAL_NOT_COMPLETED"


class PartialReasonRequired(InvalidTransition):
    code = "PARTIAL_REASON_REQUIRED"


class UnknownEventType(DomainError):
    code = "UNKNOWN_EVENT_TYPE"
    http_status = 422


class UnsupportedSchemaVersion(DomainError):
    code = "UNSUPPORTED_SCHEMA_VERSION"
    http_status = 422


class RevisionConflict(DomainError):
    """revision 冲突（409）。

    RV09 起额外携带 `conflict`：**冲突双方的内容**（当前已确认修订 + 本次提案），
    以及二者的结构化 diff。

    为什么必须带上而不是只回一个码：用户看到 `REVISION_CONFLICT` 之后要做的
    第一件事就是"我改的和现在库里那版差在哪"。只给码，用户只能重新想一遍；
    给 `conflict.current` + `conflict.incoming` + `conflict.diff`，前端才能并排显示。
    """

    code = "REVISION_CONFLICT"
    http_status = 409

    def __init__(
        self,
        expected: int,
        current: int,
        snapshot_ref: str = "",
        *,
        conflict: dict | None = None,
    ) -> None:
        detail: dict = {
            "expected": expected,
            "current": current,
            "snapshot_ref": snapshot_ref,
        }
        if conflict:
            detail["conflict"] = conflict
        super().__init__(
            f"revision 冲突：命令基于 {expected}，服务端当前 {current}",
            **detail,
        )


class IdempotencyConflict(DomainError):
    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409


class PendingActionExpired(DomainError):
    code = "PENDING_ACTION_EXPIRED"
    http_status = 409


class PendingActionNotFound(DomainError):
    code = "PENDING_ACTION_NOT_FOUND"
    http_status = 404


class SessionNotFound(DomainError):
    code = "SESSION_NOT_FOUND"
    http_status = 404


# ----------------------------- 授权（对象级） -----------------------------


class AuthorizationError(DomainError):
    code = "AUTHORIZATION_FAILED"
    http_status = 403


class AccessContextMissing(AuthorizationError):
    code = "ACCESS_CONTEXT_MISSING"
    http_status = 401


class AccessContextInvalidSignature(AuthorizationError):
    code = "ACCESS_CONTEXT_INVALID_SIGNATURE"
    http_status = 401


class TenantMismatch(AuthorizationError):
    code = "TENANT_MISMATCH"


class ProjectMismatch(AuthorizationError):
    code = "PROJECT_MISMATCH"


class CapabilityMissing(AuthorizationError):
    code = "CAPABILITY_MISSING"


class SecurityVersionRevoked(AuthorizationError):
    code = "SECURITY_VERSION_REVOKED"


class ObjectScopeMissing(AuthorizationError):
    code = "OBJECT_SCOPE_MISSING"


# ----------------------------- LLM / 流式（RV16） -----------------------------


class LlmTimeout(DomainError):
    """LLM 调用超时（504）。诚实失败，绝不伪装成功。"""

    code = "LLM_TIMEOUT"
    http_status = 504


class LlmUnavailable(DomainError):
    """LLM 不可用（503）。

    单账号单点 + X6 禁止跨账号换 key：主模型与同账号第二模型均失败、
    且确定性兜底不适用时，如实返回本码，**不得**伪造回答。
    """

    code = "LLM_UNAVAILABLE"
    http_status = 503


class LlmNotConfigured(DomainError):
    code = "LLM_NOT_CONFIGURED"
    http_status = 503


class Degraded(DomainError):
    """降级已发生（但仍有可用结果）。携带 `degraded_reason`。"""

    code = "DEGRADED"
    http_status = 200


class NotFound(DomainError):
    code = "NOT_FOUND"
    http_status = 404
