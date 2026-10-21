"""契约 7 · ProviderContract（P00）。

字段（任务书原文）：
name / version / capabilities / data policy / timeout / rate limit / cost /
retry / rights / health。

语义（总手册 §8.1/§8.2）：
- Provider 只返回**候选证据**，不得直接输出"最终 Claim"；
- `data_policy` 是合规硬字段（保留政策 / 训练用途 / 区域）；
- `health.status == UNKNOWN` **保持 UNKNOWN**，不得折算成 HEALTHY；
- 教材由 `FoundationalMathProvider` **只读**提供；Research 不 import Teacher 域。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import VersionedModel
from .errors import ContractValidationFailed

ProviderHealth = Literal["HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"]
BackoffStrategy = Literal["NONE", "FIXED", "EXPONENTIAL", "EXPONENTIAL_JITTER"]


class DataPolicy(VersionedModel):
    retention: Literal["NONE", "SESSION", "TERM", "PROVIDER_DEFAULT"]
    training_use: bool = False
    region: str = Field(min_length=2, max_length=32)
    pii_allowed: bool = False


class RateLimit(VersionedModel):
    rpm: int | None = Field(default=None, ge=1)
    tpm: int | None = Field(default=None, ge=1)
    concurrent: int | None = Field(default=None, ge=1)


class ProviderCost(VersionedModel):
    currency: Literal["CNY", "USD"] = "CNY"
    per_call: float = Field(default=0.0, ge=0.0)
    per_1k_tokens: float | None = Field(default=None, ge=0.0)
    budget_per_session: float | None = Field(default=None, ge=0.0)


class RetryPolicy(VersionedModel):
    max_attempts: int = Field(ge=1, le=10)
    backoff: BackoffStrategy = "EXPONENTIAL_JITTER"
    retry_on: list[Literal["TIMEOUT", "RATE_LIMIT", "UPSTREAM_5XX", "TRANSIENT_NETWORK"]] = Field(
        default_factory=list
    )


class ProviderHealthInfo(VersionedModel):
    status: ProviderHealth
    checked_at: datetime
    note: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _unknown_stays_unknown(self) -> ProviderHealthInfo:
        # 显式声明不变量：UNKNOWN 不是 HEALTHY（防止下游 `status != UNAVAILABLE` 式误判）
        if self.status == "UNKNOWN":
            note = self.note or ""
            if note.lower().startswith("assume"):
                raise ContractValidationFailed("UNKNOWN health 不得被描述为已假定健康")
        return self


class ProviderContract(VersionedModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    version: str = Field(min_length=1, max_length=32)
    capabilities: list[str] = Field(min_length=1)
    data_policy: DataPolicy
    timeout_seconds: int = Field(ge=1, le=3600)
    rate_limit: RateLimit
    cost: ProviderCost = Field(default_factory=ProviderCost)
    retry: RetryPolicy
    rights: dict
    health: ProviderHealthInfo
    read_only: bool = True

    @model_validator(mode="after")
    def _rights_must_have_evidence_ref(self) -> ProviderContract:
        if "rights_evidence_ref" not in self.rights:
            raise ContractValidationFailed("provider rights 必须携带 rights_evidence_ref")
        if "redistributable" not in self.rights:
            raise ContractValidationFailed("provider rights 必须显式声明 redistributable")
        return self


__all__ = [
    "ProviderContract",
    "DataPolicy",
    "RateLimit",
    "ProviderCost",
    "RetryPolicy",
    "ProviderHealthInfo",
    "ProviderHealth",
]
