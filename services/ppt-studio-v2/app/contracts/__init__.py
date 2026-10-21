"""8200 中立 contract 库（自包含，零 import 8100）。

- deck.py：TeachingDeckSpec v2 语义契约（V2 唯一事实来源的传输/校验形态）；
- job_protocols.py：可恢复空流程的 Job 类型/状态/载荷协议。
以上为 8200/8201 间及未来客户端共享的「中立契约」，任何 8100 业务模块都不得被引用。
"""
from __future__ import annotations

from app.contracts.deck import ENGINE_GEOMETRY_FIELD_NAMES, SCHEMA_VERSION, TeachingDeckSpec
from app.contracts.job_protocols import JOB_STATES, JobPayload, JobState, SubmitJobRequest

__all__ = [
    "SCHEMA_VERSION",
    "ENGINE_GEOMETRY_FIELD_NAMES",
    "TeachingDeckSpec",
    "JobPayload",
    "JobState",
    "SubmitJobRequest",
    "JOB_STATES",
]
