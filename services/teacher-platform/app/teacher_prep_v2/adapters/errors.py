"""Adapter 错误（R13）：契约错误码 → 平台 ApiError 的映射。

契约层（contracts/errors.py）保持纯数据；HTTP 化在此完成：
- 8000 返回 403 → ContractMembershipDenied（跨班访问一律拒绝，不外抛细节）；
- 8000 返回 409 → ContractIdempotencyConflict；
- 8000 不可达（网络/5xx）→ ContractAuthorityUnreachable（fail closed，绝不降级伪造）。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ApiError, ErrorCode
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractError,
    ContractErrorCode,
    ContractIdempotencyConflict,
    ContractMembershipDenied,
    ContractStateTransitionInvalid,
    ContractVersionUnsupported,
)

_HTTP_TO_CONTRACT: dict[int, type[ContractError]] = {
    400: ContractIdempotencyConflict,  # 8000 校验失败视为对账冲突（不重放错误载荷）
    403: ContractMembershipDenied,
    404: ContractMembershipDenied,  # 资源不可见按越权处理（不泄露存在性）
    409: ContractIdempotencyConflict,
    422: ContractIdempotencyConflict,
}


def from_http_status(status_code: int, detail: str) -> ContractError:
    """把 8000 HTTP 结果映射为契约错误（未覆盖的 5xx → 权威不可达）。"""
    if status_code >= 500:
        return ContractAuthorityUnreachable(f"8000 权威不可达: {detail}")
    cls = _HTTP_TO_CONTRACT.get(status_code)
    if cls is None:
        return ContractAuthorityUnreachable(f"8000 未知响应状态 {status_code}: {detail}")
    return cls(detail)


def requires_retry(exc: Exception) -> bool:
    """幂等重试判定：仅网络/5xx 类可重试；成员/状态/幂等冲突不可重试（语义已定）。"""
    return isinstance(exc, ContractAuthorityUnreachable) and not isinstance(
        exc, (ContractIdempotencyConflict, ContractMembershipDenied, ContractStateTransitionInvalid)
    )


_CONTRACT_TO_PLATFORM: dict[ContractErrorCode, ErrorCode] = {
    ContractErrorCode.VALIDATION: ErrorCode.VALIDATION,
    ContractErrorCode.VERSION_UNSUPPORTED: ErrorCode.BAD_REQUEST,
    ContractErrorCode.STATE_TRANSITION_INVALID: ErrorCode.CONFLICT,
    ContractErrorCode.IDEMPOTENCY_CONFLICT: ErrorCode.CONFLICT,
    ContractErrorCode.MEMBERSHIP_DENIED: ErrorCode.ROLE_DENIED,
    ContractErrorCode.AUTHORITY_UNREACHABLE: ErrorCode.DEPENDENCY_UNAVAILABLE,
    ContractErrorCode.MAPPING_MISSING: ErrorCode.NOT_FOUND_ITEM,
    ContractErrorCode.LEGACY_ALIAS_UNKNOWN: ErrorCode.BAD_REQUEST,
}


def to_api_error(exc: ContractError) -> ApiError:
    """把契约/适配层错误转为 8100 平台 ApiError（HTTP 信封）。"""
    platform = _CONTRACT_TO_PLATFORM.get(exc.code, ErrorCode.INTERNAL)
    return ApiError(platform, exc.message)


def raise_api_error(exc: Exception) -> None:
    """统一出口：抛契约层异常时保证 HTTP 化（不把原始 500 泄露给前端语义）。"""
    if isinstance(exc, ContractError):
        raise to_api_error(exc) from exc
    raise ApiError(ErrorCode.INTERNAL, f"adapter 内部错误: {exc}")


def dump_error(e: Exception) -> dict[str, Any]:
    """契约错误的结构化载荷（正在用于 provider contract test 断言）。"""
    if isinstance(e, ContractError):
        return {"code": e.code.value, "name": e.code.name, "message": e.message}
    return {"code": 50001, "name": "INTERNAL", "message": str(e)}


__all__ = [
    "from_http_status",
    "requires_retry",
    "to_api_error",
    "raise_api_error",
    "dump_error",
    "ContractVersionUnsupported",
]
