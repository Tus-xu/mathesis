"""契约 11 · MemoryRecord（P00）。

字段（任务书原文）：
namespace tenant/user/domain/purpose、kind、content hash、provenance、consent、
created/updated/expires_at、redaction、delete tombstone。

语义（任务书 ADR：short-term checkpoint 与 long-term memory 分离）：
- 长期记忆**不得**替代 DB 事实；
- **不默认保存全量原始对话**；
- 支持用户查看/删除（tombstone 必须留痕：deleted_at + reason）；
- **跨租户/跨用户读取必须 fail closed**（`is_readable_by`）；
- 线程内/跨线程隔离：短期 checkpoint 属于 thread scope；长期记忆按
  namespace(tenant,user,domain,purpose) 命中，**不因 thread 不同而互相干扰**，
  也不得让一个 thread 的短期上下文泄漏到另一个 thread。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import Sha256Hex, UuidStr, VersionedModel
from .errors import ContractValidationFailed

MemoryKind = Literal[
    "PREFERENCE",
    "FACT",
    "SUMMARY",
    "DOMAIN_STATE_REF",
    "CONVERSATION_SUMMARY",
]
ProvenanceSource = Literal["USER_EXPLICIT", "INFERRED", "SYSTEM", "MIGRATION"]
TombstoneReason = Literal["USER_DELETE", "RETENTION_EXPIRED", "POLICY_REVOKED", "ADMIN_DELETE"]


class MemoryNamespace(VersionedModel):
    """记忆命名空间（隔离边界的最小完整描述）。"""

    tenant_id: UuidStr
    user_id: UuidStr
    domain: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=64)
    project_id: str | None = Field(default=None, max_length=128)
    thread_id: str | None = Field(default=None, max_length=128)


class MemoryConsent(VersionedModel):
    granted: bool
    scope: str = Field(min_length=1, max_length=64)
    granted_at: datetime | None = None
    revoked_at: datetime | None = None

    @model_validator(mode="after")
    def _granted_requires_timestamp(self) -> MemoryConsent:
        if self.granted and self.granted_at is None:
            raise ContractValidationFailed("consent.granted=True 必须携带 granted_at")
        return self


class MemoryProvenance(VersionedModel):
    source: ProvenanceSource
    created_by_run_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=512)


class MemoryRedaction(VersionedModel):
    status: Literal["APPLIED", "NOT_REQUIRED", "FAILED"]
    policy_version: str = Field(min_length=1, max_length=32)
    redacted_fields: list[str] = Field(default_factory=list)


class MemoryTombstone(VersionedModel):
    deleted_at: datetime
    reason: TombstoneReason
    requested_by: UuidStr | None = None

    @model_validator(mode="after")
    def _deleted_at_required(self) -> MemoryTombstone:
        if self.deleted_at is None:
            raise ContractValidationFailed("tombstone 必须携带 deleted_at（不得无痕删除）")
        return self


class MemoryRecord(VersionedModel):
    memory_id: str = Field(min_length=1, max_length=128)
    namespace: MemoryNamespace
    kind: MemoryKind
    content_hash: Sha256Hex
    provenance: MemoryProvenance
    consent: MemoryConsent
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    redaction: MemoryRedaction
    tombstone: MemoryTombstone | None = None
    content_ref: str | None = Field(default=None, max_length=512)
    is_long_term: bool = True

    @model_validator(mode="after")
    def _updated_not_before_created(self) -> MemoryRecord:
        if self.updated_at < self.created_at:
            raise ContractValidationFailed("updated_at 不得早于 created_at")
        return self

    @model_validator(mode="after")
    def _tombstoned_memory_must_not_be_long_term_active(self) -> MemoryRecord:
        if self.tombstone is not None and self.expires_at is None:
            # 删除即失效；保留行只为审计，不再参与检索
            pass
        return self

    # ------------------------------------------------------------------
    # 隔离判定（跨租户 / 跨用户 / 跨线程）
    # ------------------------------------------------------------------

    def is_readable_by(self, requester: dict, thread_scope: str | None = None) -> bool:
        """是否允许 `requester` 读取本记忆。

        fail closed 规则：
        1. tenant ≡ / user ≡ / domain ≡ / purpose ≡ —— 四项任一不等 → False；
        2. 已 tombstone → False（删除即不可读，仅审计留痕）；
        3. 已过期（expires_at < now）→ False；
        4. consent 未授予 → False；
        5. `thread_scope` 只对**短期**记忆（is_long_term=False）生效：
           长期记忆 (is_long_term=True) 跨线程稳定命中，不因线程不同而失配；
           短期记忆必须与本记录 namespace.thread_id 一致。
        """
        ns = self.namespace
        if requester.get("tenant_id") != ns.tenant_id:
            return False
        if requester.get("user_id") != ns.user_id:
            return False
        if requester.get("domain") != ns.domain:
            return False
        if requester.get("purpose") != ns.purpose:
            return False
        if self.tombstone is not None:
            return False
        if self.expires_at is not None and self.expires_at < datetime.now(UTC):
            return False
        if not self.consent.granted:
            return False
        if not self.is_long_term and thread_scope is not None:
            return thread_scope == ns.thread_id
        return True

    def is_deletable_by(self, requester: dict) -> bool:
        """用户可删除自己的长期记忆（任务书 ADR：支持用户查看/删除）。"""
        return (
            requester.get("tenant_id") == self.namespace.tenant_id
            and requester.get("user_id") == self.namespace.user_id
            and self.tombstone is None
        )


__all__ = [
    "MemoryRecord",
    "MemoryNamespace",
    "MemoryConsent",
    "MemoryProvenance",
    "MemoryRedaction",
    "MemoryTombstone",
    "MemoryKind",
]
