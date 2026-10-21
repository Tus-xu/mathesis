"""RV06 · provider 适配器。

- `literature` / `project_asset` / `foundational_math`：Golden Path 真实实现；
- `dataset` / `web_external`：contract 保留，默认 disabled，**无**真实抓取实现。
"""

from __future__ import annotations

from .base import (
    FederatedProvider,
    ProviderDisabled,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnauthorized,
    ProviderUnavailable,
)

__all__ = [
    "FederatedProvider",
    "ProviderDisabled",
    "ProviderError",
    "ProviderRateLimited",
    "ProviderTimeout",
    "ProviderUnauthorized",
    "ProviderUnavailable",
]
