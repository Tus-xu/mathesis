"""契约 8 · AIRunRecord（P00）。

字段（任务书原文）：
run_id / domain / session / actor / prompt ref / context manifest / model / provider /
parameters / tool calls / tokens / cost / latency / schema validation / redaction /
status / trace。

语义（总手册 §12.3，DECISIONS §5）：
- 每次 AI Run 必须记录 prompt version、context refs、tool calls、tokens、cost、
  latency、**result schema validation**、**redaction status**；
- 生产**不得**使用未发布草稿 prompt（`release_status != RELEASED` → 拒绝）；
- `schema_validation.status == FAILED` 的运行不得标记为 SUCCEEDED。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .base import TraceId, UuidStr, VersionedModel
from .errors import ContractPolicyViolation, ContractValidationFailed

SchemaValidationStatus = Literal["PASSED", "FAILED", "NOT_APPLICABLE"]
RedactionStatus = Literal["APPLIED", "NOT_REQUIRED", "FAILED"]
RunStatus = Literal["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
RunEnvironment = Literal["PRODUCTION", "STAGING", "DEV", "TEST"]


class PromptRefInline(VersionedModel):
    """AIRunRecord 内的 prompt 引用（形状与契约 10 对齐，但允许精简）。"""

    prompt_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_status: Literal["DRAFT", "CANDIDATE", "RELEASED", "DEPRECATED"] = "RELEASED"


class ModelRef(VersionedModel):
    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(VersionedModel):
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: str = Field(default="1.0.0", max_length=32)
    level: Literal["READ", "PROPOSE", "EXECUTE_ISOLATED", "MUTATE_CONFIRMED", "ADMIN"] = "READ"
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_ref: str | None = Field(default=None, max_length=512)
    status: Literal["SUCCEEDED", "FAILED", "SKIPPED", "UNKNOWN"] = "SUCCEEDED"
    duration_ms: int | None = Field(default=None, ge=0)


class TokenUsage(VersionedModel):
    in_: int = Field(default=0, ge=0, alias="in")
    out: int = Field(default=0, ge=0)

    model_config = {"extra": "allow", "populate_by_name": True, "str_strip_whitespace": True}


class SchemaValidation(VersionedModel):
    status: SchemaValidationStatus
    schema_id: str | None = Field(default=None, max_length=128)
    errors: list[str] = Field(default_factory=list)


class RedactionInfo(VersionedModel):
    status: RedactionStatus
    policy_version: str = Field(min_length=1, max_length=32)
    redacted_fields: list[str] = Field(default_factory=list)


class AIRunRecord(VersionedModel):
    run_id: str = Field(min_length=1, max_length=128)
    domain: str = Field(min_length=1, max_length=64)
    session_id: UuidStr | None = None
    actor_id: UuidStr
    prompt_ref: PromptRefInline
    context_manifest_ref: str = Field(min_length=1, max_length=512)
    model: ModelRef
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tokens: TokenUsage
    cost: dict
    latency_ms: int = Field(ge=0)
    schema_validation: SchemaValidation
    redaction: RedactionInfo
    status: RunStatus
    trace_id: TraceId
    environment: RunEnvironment = "PRODUCTION"
    proposal_only: bool = True

    @model_validator(mode="after")
    def _production_must_use_released_prompt(self) -> AIRunRecord:
        if self.environment == "PRODUCTION" and self.prompt_ref.release_status not in (
            "RELEASED",
            "DEPRECATED",
        ):
            raise ContractPolicyViolation(
                f"生产不得使用未发布 prompt（release_status={self.prompt_ref.release_status}；"
                "先过 held-out eval gate 并发布）"
            )
        return self

    @model_validator(mode="after")
    def _failed_schema_validation_cannot_succeed(self) -> AIRunRecord:
        if self.schema_validation.status == "FAILED" and self.status == "SUCCEEDED":
            raise ContractValidationFailed(
                "schema_validation.status=FAILED 的运行不得标记为 SUCCEEDED"
                "（unknown/无效输出不得默认成成功）"
            )
        return self

    @model_validator(mode="after")
    def _redaction_failure_blocks_production_success(self) -> AIRunRecord:
        if (
            self.environment == "PRODUCTION"
            and self.redaction.status == "FAILED"
            and self.status == "SUCCEEDED"
        ):
            raise ContractPolicyViolation("redaction FAILED 的运行不得在生产标记 SUCCEEDED")
        return self

    @model_validator(mode="after")
    def _agent_cannot_mutate_directly(self) -> AIRunRecord:
        for call in self.tool_calls:
            if call.level == "ADMIN":
                raise ContractPolicyViolation("AIRunRecord 不得记录 ADMIN 级 tool call（普通 Agent 不具备）")
        return self


__all__ = [
    "AIRunRecord",
    "PromptRefInline",
    "ModelRef",
    "ToolCallRecord",
    "TokenUsage",
    "SchemaValidation",
    "RedactionInfo",
]
