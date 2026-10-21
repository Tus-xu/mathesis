"""契约 2 · CommandEnvelope（P00）。

字段（任务书原文）：
client_request_id、expected_revision、actor、scope、traceparent、deadline、
idempotency fingerprint。

语义（DECISIONS §5、总手册 §7.3/§13.1）：
- 同一 `client_request_id + actor + command_type` 只能产生一次效果；
- `expected_revision` 不匹配 → 409 + server snapshot/diff，前端不得静默覆盖；
- 高风险 mutate 在执行瞬间必须**重新查权**（见 ToolPolicy.recheck_authorization_at_execute）；
- `scope` 必须与 command 的对象级授权一致（禁止 identity-only 放行）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .access import ResourceScope, UuidStr
from .base import Traceparent, VersionedModel
from .errors import ContractValidationFailed

CommandKind = Literal[
    "session.create",
    "session.resume",
    "session.confirm_proposal",
    "session.accept_proposal",
    "session.reject_proposal",
    "session.cancel_job",
    "session.retry_job",
    "artifact.publish",
    "artifact.delete",
    "memory.delete",
    "unknown",
]


class CommandEnvelope(VersionedModel):
    """所有正式 command 的统一信封（REST body 或 header 归一）。"""

    client_request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    expected_revision: int = Field(ge=0)
    actor_id: UuidStr
    scope: ResourceScope
    traceparent: Traceparent
    deadline: datetime
    idempotency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    command_type: str = Field(min_length=3, max_length=64)
    payload_ref: str = Field(min_length=1, max_length=512)
    tenant_id: UuidStr | None = None

    @field_validator("idempotency_fingerprint")
    @classmethod
    def _fingerprint_must_be_full_sha256(cls, v: str) -> str:
        if len(v) != 64:
            raise ContractValidationFailed("idempotency_fingerprint 必须是 64 位 sha256 hex")
        return v.lower()

    @field_validator("payload_ref")
    @classmethod
    def _payload_ref_must_be_opaque(cls, v: str) -> str:
        # 禁止把原始正文/密钥塞进 command；只能传引用
        if any(ch in v for ch in ("\n", "\r", "\t")):
            raise ContractValidationFailed("payload_ref 不得包含换行/制表符")
        return v


__all__ = ["CommandEnvelope", "CommandKind"]
