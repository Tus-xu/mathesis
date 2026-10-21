"""契约 4 · JobContract（P00）。

canonical 对外状态（任务书原文，7 值）：
QUEUED / RUNNING / RETRYING / PARTIAL / SUCCEEDED / FAILED / CANCELLED

字段（任务书原文）：
input hash、attempt、lease、fencing token、heartbeat、timeout、cancel_requested、
result/error refs、cost。

语义（总手册 §12.5/§13.4，DECISIONS §5）：
- 内部可有 CLAIMED，但**不得**暴露为产品状态；
- PARTIAL 必须说明哪些 stage 成功、哪些失败，**不能显示成绿色完成**；
- UNKNOWN 不得折算成 PASS/SUCCEEDED；
- cancel 是持久化 `cancel_requested_at`，worker 在安全点退出并保存 partial artifact；
- 超过 max attempts → DLQ（不再对外返回 RUNNING）。
- 8100 既有 `reliability_jobs` 的内部状态（PENDING/CLAIMED/COMPLETED/…）**保持不变**，
  本契约只做**对外投影**：PENDING→QUEUED，CLAIMED→RUNNING，COMPLETED→SUCCEEDED。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import OpaqueRef, Sha256Hex, TraceId, VersionedModel
from .errors import ContractUnknownEnumValue, ContractPolicyViolation, ContractValidationFailed

JobStatus = Literal[
    "QUEUED",
    "RUNNING",
    "RETRYING",
    "PARTIAL",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
]

JOB_CONTRACT_STATES: tuple[str, ...] = (
    "QUEUED",
    "RUNNING",
    "RETRYING",
    "PARTIAL",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
)

# 8100 内部状态 → 对外 canonical 状态的唯一投影表（禁止各域自行映射）
INTERNAL_STATUS_PROJECTION: dict[str, str] = {
    "PENDING": "QUEUED",
    "CLAIMED": "RUNNING",
    "COMPLETED": "SUCCEEDED",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
    "DLQ": "FAILED",
}


class CostRef(VersionedModel):
    currency: Literal["CNY", "USD"] = "CNY"
    amount: float = Field(default=0.0, ge=0.0)


class PartialReason(VersionedModel):
    """PARTIAL 必须携带的 stage 级说明（禁止"绿色完成"）。"""

    succeeded_stages: list[str] = Field(default_factory=list)
    failed_stages: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _must_name_at_least_one_side(self) -> PartialReason:
        if not self.succeeded_stages and not self.failed_stages:
            raise ContractValidationFailed("PARTIAL 必须列明成功或失败的 stage")
        return self


class JobContract(VersionedModel):
    """长任务对外契约（跨 Teacher / PPT / Research 统一形状）。"""

    job_id: str = Field(min_length=1, max_length=128)
    job_type: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_.]*$")
    owner_service: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = None
    input_hash: Sha256Hex
    status: JobStatus
    stage: str | None = Field(default=None, max_length=64)
    progress: float | None = Field(default=None, ge=0.0, le=1.0, description="仅可测量时给百分比")
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    lease_expires_at: datetime
    fencing_token: int = Field(ge=0)
    heartbeat_at: datetime
    timeout_seconds: int = Field(ge=1)
    cancel_requested: bool = False
    cancel_requested_at: datetime | None = None
    result_ref: OpaqueRef | None = None
    error_ref: OpaqueRef | None = None
    trace_id: TraceId
    cost: CostRef = Field(default_factory=CostRef)
    partial_reason: PartialReason | None = None
    dlq: bool = False
    unknown_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_invariants(self) -> JobContract:
        if self.status not in JOB_CONTRACT_STATES:
            raise ContractUnknownEnumValue(
                f"未知 job status {self.status!r}；允许 {JOB_CONTRACT_STATES}"
                "（不得默认成 SUCCEEDED）"
            )
        if self.attempt > self.max_attempts:
            raise ContractValidationFailed("attempt 不得大于 max_attempts")
        # PARTIAL 语义
        if self.status == "PARTIAL" and self.partial_reason is None:
            raise ContractPolicyViolation(
                "status=PARTIAL 必须携带 partial_reason（列明成功/失败 stage），"
                "不得显示成绿色完成"
            )
        if self.status != "PARTIAL" and self.partial_reason is not None:
            raise ContractValidationFailed("partial_reason 只在 status=PARTIAL 时允许")
        # CANCELLED 必须持久化请求时间
        if self.status == "CANCELLED" and self.cancel_requested_at is None:
            raise ContractValidationFailed("status=CANCELLED 必须携带 cancel_requested_at（持久化取消）")
        if self.cancel_requested and self.cancel_requested_at is None:
            raise ContractValidationFailed("cancel_requested=True 必须携带 cancel_requested_at")
        # SUCCEEDED 必须有 result_ref
        if self.status == "SUCCEEDED" and self.result_ref is None:
            raise ContractValidationFailed("status=SUCCEEDED 必须携带 result_ref")
        # FAILED 必须有 error_ref
        if self.status == "FAILED" and self.error_ref is None:
            raise ContractValidationFailed("status=FAILED 必须携带 error_ref（redacted 技术详情）")
        # 终态不得再声称可续租
        if self.status in ("SUCCEEDED", "FAILED", "CANCELLED") and not self.dlq:
            pass
        return self


def project_internal_status(internal_status: str) -> str:
    """把 8100/8200 内部 job 状态投影为对外 canonical 状态（未知 → fail closed）。"""
    try:
        return INTERNAL_STATUS_PROJECTION[internal_status]
    except KeyError as exc:
        raise ContractUnknownEnumValue(
            f"未知内部 job 状态 {internal_status!r}；投影表 {sorted(INTERNAL_STATUS_PROJECTION)}"
        ) from exc


__all__ = [
    "JobContract",
    "JobStatus",
    "JOB_CONTRACT_STATES",
    "PartialReason",
    "CostRef",
    "INTERNAL_STATUS_PROJECTION",
    "project_internal_status",
]
