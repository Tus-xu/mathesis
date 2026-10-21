"""契约 3 · DomainEvent / SSE envelope（P00）。

字段（任务书原文）：
event_id / type / schema_version / session_id / revision / occurred_at / trace_id / payload_ref；
必须支持 `Last-Event-ID` 与 snapshot 回退。

语义（总手册 §9.3/§13.1）：
- SSE 单向：AI token、job progress、domain event、heartbeat、断线续传；
  **不承载**高风险 command 或正式状态 mutation（那走 REST）；
- 每个 tab/workspace 复用**一条**可复用连接（见 ADR-SSE-BOUNDARY），
  禁止每张卡片各开一条流；
- 断线按 `last_event_id` 重连；保留窗口外必须回退 server snapshot，
  绝不允许"假设已追上"（`ASSUME_CAUGHT_UP` 之类语义被拒）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .base import OpaqueRef, Sha256Hex, TraceId, UuidStr, VersionedModel
from .errors import ContractUnknownEnumValue, ContractValidationFailed


class DomainEventEnvelope(VersionedModel):
    """领域事件信封（SSE `data:` 载荷或 outbox 消息体）。"""

    event_id: str = Field(min_length=8, max_length=128)
    event_type: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_.]*$")
    schema_version: str
    session_id: UuidStr
    revision: int = Field(ge=0)
    occurred_at: datetime
    trace_id: TraceId
    payload_ref: OpaqueRef
    actor_id: UuidStr | None = None
    aggregate_type: Literal["session", "artifact", "job", "claim", "evidence", "memory"] | None = None

    @field_validator("event_type")
    @classmethod
    def _event_type_must_be_namespaced(cls, v: str) -> str:
        if "." not in v:
            raise ContractValidationFailed("event_type 必须是 `<domain>.<verb>` 逆域名风格")
        return v


class SSECursor(VersionedModel):
    """SSE 续传游标（客户端重连请求 / 服务端握手应答共用）。

    `fallback` 枚举刻意只有两值：
    - `NONE`：last_event_id 命中保留窗口，增量续传；
    - `SNAPSHOT_REQUIRED`：窗口外，必须先拉 server snapshot 再续流。

    任何"假设已追上"的取值一律拒绝——它会把丢失事件的用户静默留在错误状态。
    """

    session_id: UuidStr
    last_event_id: str | None = Field(default=None, max_length=128)
    fallback: Literal["NONE", "SNAPSHOT_REQUIRED"] = "NONE"
    revision: int = Field(ge=0)
    stream_epoch: int = Field(default=0, ge=0)


class SSEHeartbeat(VersionedModel):
    """心跳（`sse_heartbeat_seconds`=15，见 8100 core config）。"""

    session_id: UuidStr
    occurred_at: datetime
    revision: int = Field(ge=0)


__all__ = ["DomainEventEnvelope", "SSECursor", "SSEHeartbeat"]
