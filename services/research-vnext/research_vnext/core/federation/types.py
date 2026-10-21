"""RV06 · Knowledge Federation 枚举与常量。

五类规范在此**一处**定义：`EvidenceType` 是唯一区分数据语义的轴。
任何"来源维度"判断都必须读它，禁止用 provider 名或字段组合去猜。
"""

from __future__ import annotations

from enum import Enum


class EvidenceType(str, Enum):
    """五类规范（任务书 §必须完成 1）。

    前三类本轮进 Golden Path；后两类保留 contract 且默认 `disabled`。
    """

    LITERATURE = "LITERATURE"
    PROJECT_ASSET = "PROJECT_ASSET"
    FOUNDATIONAL_MATH = "FOUNDATIONAL_MATH"
    DATASET = "DATASET"
    WEB_EXTERNAL = "WEB_EXTERNAL"


#: 本轮允许进 Golden Path 的三类。
GOLDEN_PATH_TYPES: tuple[EvidenceType, ...] = (
    EvidenceType.LITERATURE,
    EvidenceType.PROJECT_ASSET,
    EvidenceType.FOUNDATIONAL_MATH,
)

#: 保留 contract 但默认 disabled 的两类。
CONTRACT_ONLY_TYPES: tuple[EvidenceType, ...] = (
    EvidenceType.DATASET,
    EvidenceType.WEB_EXTERNAL,
)


class ProviderStatus(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class ResponseStatus(str, Enum):
    """一个 provider 失败 → `PARTIAL`（绝不伪造全部成功）；全部失败才 `FAILED`。"""

    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ProviderHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class TrustLevel(str, Enum):
    """与 P00 ADR-PLAT-05 的 trust label 四值一致。"""

    UNTRUSTED_DATA = "UNTRUSTED_DATA"
    TRUSTED_SYSTEM = "TRUSTED_SYSTEM"
    TRUSTED_POLICY = "TRUSTED_POLICY"
    TRUSTED_USER_INSTRUCTION = "TRUSTED_USER_INSTRUCTION"


class WarnCode(str, Enum):
    """保留在 result / response `warnings` 的机读告警码。"""

    MISSING_LOCATOR = "MISSING_LOCATOR"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    DIMENSION_MISMATCH = "DIMENSION_MISMATCH"
    INJECTION_PATTERN_DETECTED = "INJECTION_PATTERN_DETECTED"
    DUPLICATE_LOCATOR_DISTINCT_SOURCE = "DUPLICATE_LOCATOR_DISTINCT_SOURCE"
    DROPPED_PAGE_RANGE = "DROPPED_PAGE_RANGE"
    RIGHTS_MISSING = "RIGHTS_MISSING"
    NO_RIGHTS_FULLTEXT_BLOCKED = "NO_RIGHTS_FULLTEXT_BLOCKED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_DISABLED = "PROVIDER_DISABLED"
    UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


#: 教材结果必须携带的标签（防「把教材伪装成学术文献」）。
FOUNDATIONAL_MATH_LABELS: tuple[str, ...] = ("TEXTBOOK", "FOUNDATIONAL_MATH")


__all__ = [
    "EvidenceType",
    "GOLDEN_PATH_TYPES",
    "CONTRACT_ONLY_TYPES",
    "ProviderStatus",
    "ResponseStatus",
    "ProviderHealth",
    "TrustLevel",
    "WarnCode",
    "FOUNDATIONAL_MATH_LABELS",
]
