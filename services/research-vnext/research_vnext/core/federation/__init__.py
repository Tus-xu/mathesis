"""RV06 · Knowledge Federation：统一只读检索契约与维度隔离。

对外入口：
- 契约：`FederatedQuery` / `ProviderHit` / `FederatedResult` / locator 家族；
- 编排：`federated_search`；
- 目录：`DEFAULT_CATALOG`（五类规范，后两类默认 disabled）。

不在此处 import 具体 provider 适配器，避免契约层 ↔ 适配器层循环依赖。
"""

from __future__ import annotations

from .catalog import (
    CONTRACT_ONLY_DISABLED,
    DEFAULT_CATALOG,
    GOLDEN_PATH_ENABLED,
    ProviderSpec,
    catalog_snapshot,
    enabled_provider_names,
    get_spec,
    is_enabled,
)
from .contracts import (
    LOCATOR_TYPES,
    AccessRights,
    AssetLocator,
    Budget,
    DatasetLocator,
    FederatedQuery,
    FederatedResult,
    HitScope,
    LiteratureLocator,
    Provenance,
    ProviderHit,
    ScoreComponents,
    TextbookLocator,
    WebLocator,
    snippet_hash,
)
from .errors import (
    DimensionLeak,
    DimensionMismatch,
    FederationContractError,
    FederationError,
    MissingLocator,
    ProviderSpecInvalid,
    QueryInvalid,
    ScopeViolation,
)
from .orchestrator import FederatedResponse, federated_search
from .scope import ResolvedScope, ViolationCode, check_hit_scope, resolve_scope
from .types import (
    CONTRACT_ONLY_TYPES,
    FOUNDATIONAL_MATH_LABELS,
    GOLDEN_PATH_TYPES,
    EvidenceType,
    ProviderHealth,
    ProviderStatus,
    ResponseStatus,
    TrustLevel,
    WarnCode,
)

__all__ = [
    "AccessRights",
    "AssetLocator",
    "Budget",
    "CONTRACT_ONLY_DISABLED",
    "CONTRACT_ONLY_TYPES",
    "DEFAULT_CATALOG",
    "DatasetLocator",
    "DimensionLeak",
    "DimensionMismatch",
    "EvidenceType",
    "FOUNDATIONAL_MATH_LABELS",
    "FederatedQuery",
    "FederatedResponse",
    "FederatedResult",
    "FederationContractError",
    "FederationError",
    "GOLDEN_PATH_ENABLED",
    "GOLDEN_PATH_TYPES",
    "HitScope",
    "LOCATOR_TYPES",
    "LiteratureLocator",
    "MissingLocator",
    "Provenance",
    "ProviderHealth",
    "ProviderHit",
    "ProviderSpec",
    "ProviderSpecInvalid",
    "ProviderStatus",
    "QueryInvalid",
    "ResolvedScope",
    "ResponseStatus",
    "ScoreComponents",
    "ScopeViolation",
    "TextbookLocator",
    "TrustLevel",
    "ViolationCode",
    "WarnCode",
    "WebLocator",
    "catalog_snapshot",
    "check_hit_scope",
    "enabled_provider_names",
    "federated_search",
    "get_spec",
    "is_enabled",
    "resolve_scope",
    "snippet_hash",
]
