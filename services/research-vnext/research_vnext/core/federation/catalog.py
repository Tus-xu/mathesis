"""RV06 · Provider catalog：五类规范一处登记。

对齐 P00 契约 7（ProviderContract）的字段口径：
name / version / capabilities / data_policy / timeout / rate_limit / cost /
retry / rights / health。

本轮策略（任务书 §必须完成 1）：
- `literature` / `project_asset` / `foundational_math` 进 Golden Path（enabled=True）；
- `dataset` / `web_external` **保留 contract**（有版本、有能力声明、可 `describe()`），
  但默认 `enabled=False`。被请求时返回 `PROVIDER_DISABLED`，而不是静默消失 ——
  "contract 存在"必须是可断言的事实。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .errors import ProviderSpecInvalid
from .types import (
    CONTRACT_ONLY_TYPES,
    GOLDEN_PATH_TYPES,
    EvidenceType,
    ProviderHealth,
)


@dataclass(frozen=True)
class ProviderSpec:
    """单个 provider 的合规声明。"""

    name: str
    evidence_type: EvidenceType
    version: str
    capabilities: tuple[str, ...]
    read_only: bool
    enabled: bool
    data_policy: dict[str, Any]
    timeout_seconds: int
    rate_limit: dict[str, Any]
    retry: dict[str, Any]
    rights: dict[str, Any]
    health: ProviderHealth
    cost: dict[str, Any] = field(default_factory=dict)
    requires_project: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ProviderSpecInvalid("ProviderSpec.name 必填")
        if not self.version:
            raise ProviderSpecInvalid(f"{self.name}: ProviderSpec.version 必填")
        if not self.capabilities:
            raise ProviderSpecInvalid(f"{self.name}: 必须声明 capabilities")
        # P00 契约 7 的硬约束：rights 必须可溯源且显式声明可否再分发
        if "rights_evidence_ref" not in self.rights:
            raise ProviderSpecInvalid(
                f"{self.name}: provider rights 必须携带 rights_evidence_ref"
            )
        if "redistributable" not in self.rights:
            raise ProviderSpecInvalid(
                f"{self.name}: provider rights 必须显式声明 redistributable"
            )
        if self.timeout_seconds < 1:
            raise ProviderSpecInvalid(f"{self.name}: timeout_seconds 必须 >= 1")
        if self.health is ProviderHealth.UNKNOWN and self.assume_healthy:
            raise ProviderSpecInvalid(f"{self.name}: UNKNOWN health 不得被描述为已假定健康")

    @property
    def assume_healthy(self) -> bool:
        """`UNKNOWN` 永远不是 `HEALTHY`（防下游 `status != UNAVAILABLE` 式误判）。"""
        return False

    @classmethod
    def default(cls, name: str) -> ProviderSpec:
        return DEFAULT_CATALOG[name]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "evidence_type": self.evidence_type.value,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "read_only": self.read_only,
            "enabled": self.enabled,
            "data_policy": dict(self.data_policy),
            "timeout_seconds": self.timeout_seconds,
            "rate_limit": dict(self.rate_limit),
            "retry": dict(self.retry),
            "rights": dict(self.rights),
            "health": self.health.value,
            "cost": dict(self.cost),
            "requires_project": self.requires_project,
            "notes": self.notes,
        }


_CN_ONLY: dict[str, Any] = {
    "retention": "NONE",
    "training_use": False,
    "region": "CN",
    "pii_allowed": False,
}

_NO_REDISTRIBUTE: dict[str, Any] = {
    "rights_evidence_ref": "rv06-provider-catalog",
    "redistributable": False,
}


def _spec(**kw: Any) -> ProviderSpec:
    return ProviderSpec(**kw)


#: 五类规范的唯一登记表。
DEFAULT_CATALOG: Mapping[str, ProviderSpec] = MappingProxyType(
    {
        "literature": _spec(
            name="literature",
            evidence_type=EvidenceType.LITERATURE,
            version="1.0.0",
            capabilities=("search", "doi_lookup", "metadata", "venue_anchor"),
            read_only=True,
            enabled=EvidenceType.LITERATURE in GOLDEN_PATH_TYPES,
            data_policy=_CN_ONLY,
            timeout_seconds=15,
            rate_limit={"rpm": 30, "concurrent": 2},
            retry={"max_attempts": 3, "backoff": "EXPONENTIAL_JITTER", "retry_on": ["TIMEOUT", "RATE_LIMIT", "UPSTREAM_5XX"]},
            rights={**_NO_REDISTRIBUTE, "notes": "metadata-only；abstract ≠ full text"},
            health=ProviderHealth.HEALTHY,
            cost={"per_call": 0.0, "per_1k_tokens": None, "budget_per_session": None},
            requires_project=False,
            notes="RV04 Crossref metadata；本轮只读检索，不做全文抓取",
        ),
        "project_asset": _spec(
            name="project_asset",
            evidence_type=EvidenceType.PROJECT_ASSET,
            version="1.0.0",
            capabilities=("search", "project_acl", "version_bound", "anchor", "range_delivery"),
            read_only=True,
            enabled=EvidenceType.PROJECT_ASSET in GOLDEN_PATH_TYPES,
            data_policy=_CN_ONLY,
            timeout_seconds=10,
            rate_limit={"rpm": 120, "concurrent": 4},
            retry={"max_attempts": 2, "backoff": "FIXED", "retry_on": ["TIMEOUT"]},
            rights={**_NO_REDISTRIBUTE, "notes": "按资产自身 rights 字段判定；UNKNOWN 不外传全文"},
            health=ProviderHealth.HEALTHY,
            cost={"per_call": 0.0},
            requires_project=True,
            notes="RV04 资产元数据；检索只读，字节仍走 Gateway 签名交付",
        ),
        "foundational_math": _spec(
            name="foundational_math",
            evidence_type=EvidenceType.FOUNDATIONAL_MATH,
            version="1.0.0",
            # 刻意**不**声明任何 doi/journal/venue 能力：教材不是文献。
            capabilities=("search", "direct_chunk", "source_section_chunk", "rights_filtered"),
            read_only=True,
            enabled=EvidenceType.FOUNDATIONAL_MATH in GOLDEN_PATH_TYPES,
            data_policy={**_CN_ONLY, "notes": "只读 Teacher direct-chunk；不允许写回 Teacher"},
            timeout_seconds=10,
            rate_limit={"rpm": 60, "concurrent": 4},
            retry={"max_attempts": 2, "backoff": "FIXED", "retry_on": ["TIMEOUT", "TRANSIENT_NETWORK"]},
            rights={
                "rights_evidence_ref": "textbook_v2.rights_status",
                "redistributable": False,
                "notes": "仅 STAGED/ACTIVE 可见；QUARANTINED 永不进结果",
            },
            health=ProviderHealth.UNKNOWN,
            cost={"per_call": 0.0},
            requires_project=False,
            notes="ADR-VN-02 Option C：平台只读 adapter；结果标 TEXTBOOK/FOUNDATIONAL_MATH",
        ),
        "dataset": _spec(
            name="dataset",
            evidence_type=EvidenceType.DATASET,
            version="0.1.0",
            capabilities=("search", "schema", "row_locator"),
            read_only=True,
            enabled=EvidenceType.DATASET in GOLDEN_PATH_TYPES,
            data_policy=_CN_ONLY,
            timeout_seconds=10,
            rate_limit={"rpm": 30},
            retry={"max_attempts": 1, "backoff": "NONE", "retry_on": []},
            rights={**_NO_REDISTRIBUTE, "notes": "contract-only"},
            health=ProviderHealth.UNKNOWN,
            cost={"per_call": 0.0},
            requires_project=True,
            notes="contract 保留，本轮默认 disabled；无真实数据集接入",
        ),
        "web_external": _spec(
            name="web_external",
            evidence_type=EvidenceType.WEB_EXTERNAL,
            version="0.1.0",
            capabilities=("search", "fetch", "url_locator"),
            read_only=True,
            enabled=EvidenceType.WEB_EXTERNAL in GOLDEN_PATH_TYPES,
            data_policy={**_CN_ONLY, "pii_allowed": False},
            timeout_seconds=20,
            rate_limit={"rpm": 10, "concurrent": 1},
            retry={"max_attempts": 2, "backoff": "EXPONENTIAL", "retry_on": ["TIMEOUT", "UPSTREAM_5XX"]},
            rights={**_NO_REDISTRIBUTE, "notes": "contract-only；抓取需人工授权"},
            health=ProviderHealth.UNKNOWN,
            cost={"per_call": 0.0},
            requires_project=False,
            notes="contract 保留，本轮默认 disabled；不做真实抓取",
        ),
    }
)


def get_spec(name: str) -> ProviderSpec:
    return DEFAULT_CATALOG[name]


def is_enabled(name: str) -> bool:
    spec = DEFAULT_CATALOG.get(name)
    return bool(spec and spec.enabled)


def enabled_provider_names() -> tuple[str, ...]:
    return tuple(n for n, s in DEFAULT_CATALOG.items() if s.enabled)


def disabled_provider_names() -> tuple[str, ...]:
    return tuple(n for n, s in DEFAULT_CATALOG.items() if not s.enabled)


def spec_for_evidence_type(evidence_type: EvidenceType) -> ProviderSpec:
    for spec in DEFAULT_CATALOG.values():
        if spec.evidence_type is evidence_type:
            return spec
    raise KeyError(f"没有 provider 声明 {evidence_type.value}")


def catalog_snapshot() -> list[dict[str, Any]]:
    return [spec.as_dict() for spec in DEFAULT_CATALOG.values()]


#: 本轮进 Golden Path 的三类（供文档/断言引用）。
GOLDEN_PATH_ENABLED: tuple[str, ...] = enabled_provider_names()
CONTRACT_ONLY_DISABLED: tuple[str, ...] = disabled_provider_names()
CONTRACT_ONLY_EVIDENCE_TYPES: tuple[EvidenceType, ...] = CONTRACT_ONLY_TYPES


__all__ = [
    "ProviderSpec",
    "DEFAULT_CATALOG",
    "get_spec",
    "is_enabled",
    "enabled_provider_names",
    "disabled_provider_names",
    "spec_for_evidence_type",
    "catalog_snapshot",
    "GOLDEN_PATH_ENABLED",
    "CONTRACT_ONLY_DISABLED",
    "CONTRACT_ONLY_EVIDENCE_TYPES",
]
