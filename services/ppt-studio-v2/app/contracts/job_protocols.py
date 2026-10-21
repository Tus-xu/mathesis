"""可恢复空流程 Job 协议（8200 中立契约）。

状态机（与 reliability 运行时一致）：
PENDING → CLAIMED → COMPLETED
              ├─(attempt 未满)→ PENDING(退避重试)
              ├─(attempt 达上限)→ DLQ
              ├─ cancel_requested → CANCELLED
              └─ deadline 过期 → DLQ
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

JobState = Literal["PENDING", "CLAIMED", "COMPLETED", "FAILED", "CANCELLED", "DLQ"]
JOB_STATES = ("PENDING", "CLAIMED", "COMPLETED", "FAILED", "CANCELLED", "DLQ")
RUNNING_STATES = ("PENDING", "CLAIMED")
TERMINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED", "DLQ")

# 允许的 job_type（R15 空流程 + R21 真实生成）
ALLOWED_JOB_TYPES = ("ppt.deck.prepare", "ppt.deck.generate")


class SubmitJobRequest(BaseModel):
    job_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=5, ge=1, le=50)
    timeout_s: int = Field(default=300, ge=10, le=3600)
    dedupe_key: str | None = Field(default=None, max_length=255)


class JobPayload(BaseModel):
    """作业载荷。prepare：deck_id 可选 + presenton_probe 透传位；
    generate：deck_id 必填（R21 真实生成）。"""

    deck_id: str | None = None
    presenton_probe: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)
