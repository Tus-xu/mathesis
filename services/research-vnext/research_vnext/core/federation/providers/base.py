"""RV06 · provider 适配器基类与错误分类。

错误码沿用 RV04 既有的 `PROVIDER_*` 口径（`PROVIDER_TIMEOUT` /
`PROVIDER_RATE_LIMITED` / `PROVIDER_ERROR`），只是从这里统一抛出，
使 orchestrator 能把它们**如实**保留进 `warnings` 与 `provider_status`，
而不是把失败悄悄变成"零结果"。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ..contracts import FederatedQuery, ProviderHit
from ..errors import FederationError
from ..scope import ResolvedScope
from ..types import EvidenceType, WarnCode


class ProviderError(FederationError):
    """provider 侧失败。`retryable` 决定 RV03/调用方是否值得重试。"""

    code: str = WarnCode.PROVIDER_ERROR.value
    http_status: int = 502
    retryable: bool = True

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.code)
        if retryable is not None:
            self.retryable = retryable
        self.detail = detail or {}

    @property
    def warn_code(self) -> WarnCode:
        return WarnCode(self.code)

    def as_warning(self) -> str:
        return self.warn_code.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "detail": self.detail,
        }


class ProviderTimeout(ProviderError):
    code = WarnCode.PROVIDER_TIMEOUT.value
    http_status = 504


class ProviderRateLimited(ProviderError):
    code = WarnCode.PROVIDER_RATE_LIMITED.value
    http_status = 429

    def __init__(
        self,
        message: str = "provider rate limited",
        *,
        retry_after: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        merged = {**(detail or {})}
        if retry_after is not None:
            merged["retry_after"] = retry_after
        super().__init__(message, detail=merged)
        self.retry_after = retry_after


class ProviderUnavailable(ProviderError):
    code = WarnCode.PROVIDER_ERROR.value
    http_status = 503
    retryable = True


class ProviderUnauthorized(ProviderError):
    code = WarnCode.PROVIDER_ERROR.value
    http_status = 502
    retryable = False


class ProviderDisabled(ProviderError):
    """被请求的 provider 处于 contract-only / disabled 状态。

    这不是"错误"，而是**设计态**：orchestrator 会把它记进 warnings 与
    provider_status，但不会因此把整体响应降级为 PARTIAL。
    """

    code = WarnCode.PROVIDER_DISABLED.value
    http_status = 409
    retryable = False


class FederatedProvider(ABC):
    """统一只读 provider 接口。

    实现方**只能**收窄 scope：可以少返回、不能多返回；
    越界命中由 orchestrator 剔除，但适配器本就不应该产生它们。
    """

    name: str = "base"
    version: str = "0"
    evidence_type: EvidenceType

    @abstractmethod
    def search(self, query: FederatedQuery, scope: ResolvedScope) -> Sequence[ProviderHit]:
        """返回候选证据命中。失败时抛 `ProviderError` 子类。"""

    def describe(self) -> dict[str, Any]:
        """健康/能力自述，供 `/federation/providers` 路由使用。"""
        return {
            "name": self.name,
            "version": self.version,
            "evidence_type": self.evidence_type.value,
        }


__all__ = [
    "ProviderError",
    "ProviderTimeout",
    "ProviderRateLimited",
    "ProviderUnavailable",
    "ProviderUnauthorized",
    "ProviderDisabled",
    "FederatedProvider",
]
