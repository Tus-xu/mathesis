"""契约 9 · ToolPolicy（P00）。

五级（任务书原文，冻结集合）：
READ / PROPOSE / EXECUTE_ISOLATED / MUTATE_CONFIRMED / ADMIN

每个 tool 固化（任务书原文）：
JSON schema、scope、timeout、idempotency、side effect、risk、audit。

语义（DECISIONS §5、总手册 §12.4）：
- 模型无法自己注册工具或扩大 scope；
- `MUTATE_CONFIRMED` 必须 `requires_confirmation=True` 且
  `recheck_authorization_at_execute=True`（执行瞬间**重新查权**）；
- `ADMIN` 级 tool **不得**声明为 agent 可调用。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .base import VersionedModel
from .errors import ContractPolicyViolation, ContractValidationFailed

ToolLevel = Literal["READ", "PROPOSE", "EXECUTE_ISOLATED", "MUTATE_CONFIRMED", "ADMIN"]

TOOL_POLICY_LEVELS: tuple[str, ...] = (
    "READ",
    "PROPOSE",
    "EXECUTE_ISOLATED",
    "MUTATE_CONFIRMED",
    "ADMIN",
)

SideEffect = Literal["NONE", "WRITE", "EXTERNAL_CALL", "EXECUTE", "DELETE"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class IdempotencyPolicy(VersionedModel):
    mode: Literal["NONE", "FINGERPRINT", "NATURAL_KEY"] = "FINGERPRINT"
    key_field: str | None = Field(default=None, max_length=64)
    window_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _key_required_for_modes(self) -> IdempotencyPolicy:
        if self.mode in ("FINGERPRINT", "NATURAL_KEY") and not self.key_field:
            raise ContractValidationFailed(f"idempotency.mode={self.mode} 必须给出 key_field")
        return self


class ToolAudit(VersionedModel):
    emit_event: bool = True
    redact_fields: list[str] = Field(default_factory=list)
    retain_days: int | None = Field(default=None, ge=1)


class ToolPolicy(VersionedModel):
    tool_name: str = Field(min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    tool_version: str = Field(default="1.0.0", max_length=32)
    level: ToolLevel
    description: str | None = Field(default=None, max_length=512)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_scopes: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(ge=1, le=3600)
    idempotency: IdempotencyPolicy
    side_effect: SideEffect
    risk: RiskLevel
    requires_confirmation: bool
    recheck_authorization_at_execute: bool
    audit: ToolAudit
    agent_invocable: bool = True
    network_access: Literal["NONE", "ALLOWLISTED", "FULL"] = "NONE"

    @model_validator(mode="after")
    def _level_invariants(self) -> ToolPolicy:
        if self.level == "MUTATE_CONFIRMED":
            if not self.requires_confirmation:
                raise ContractPolicyViolation(
                    "MUTATE_CONFIRMED 必须 requires_confirmation=True（AI 只产生 Proposal/patch）"
                )
            if not self.recheck_authorization_at_execute:
                raise ContractPolicyViolation(
                    "MUTATE_CONFIRMED 必须 recheck_authorization_at_execute=True"
                    "（执行瞬间重新查权，防止 TOCTOU）"
                )
            if not self.required_scopes:
                raise ContractPolicyViolation(
                    "MUTATE_CONFIRMED 必须声明 required_scopes（对象级授权）"
                )
        if self.level == "ADMIN" and self.agent_invocable:
            raise ContractPolicyViolation("ADMIN 级 tool 不得声明 agent_invocable=True")
        if self.level == "READ" and self.side_effect not in ("NONE", "EXTERNAL_CALL"):
            raise ContractValidationFailed("READ 级 tool 不得有 WRITE/DELETE/EXECUTE 副作用")
        if self.level == "PROPOSE" and self.side_effect not in ("NONE", "WRITE"):
            raise ContractValidationFailed("PROPOSE 写正式 state 必须走 MUTATE_CONFIRMED")
        if self.side_effect == "DELETE" and self.level != "MUTATE_CONFIRMED":
            raise ContractValidationFailed("DELETE 副作用只允许出现在 MUTATE_CONFIRMED")
        if self.risk in ("HIGH", "CRITICAL") and not self.audit.emit_event:
            raise ContractPolicyViolation("HIGH/CRITICAL 风险 tool 必须 emit_event=True")
        return self


__all__ = ["ToolPolicy", "ToolLevel", "TOOL_POLICY_LEVELS", "IdempotencyPolicy", "ToolAudit"]
