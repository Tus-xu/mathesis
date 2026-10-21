"""Research 侧 DomainEvent / SSE 契约（**只消费** P00 契约 3）。

`fallback` 只允许 `NONE` / `SNAPSHOT_REQUIRED`；
**任何"假设已追上"的取值一律拒绝**（P00 硬约束）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EVENT_SCHEMA_VERSION = "1.0.0"


class ResearchDomainEventEnvelope(BaseModel):
    event_id: str = Field(min_length=8, max_length=128)
    event_type: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_.]*$")
    schema_version: str = EVENT_SCHEMA_VERSION
    session_id: str
    revision: int = Field(ge=0)
    occurred_at: datetime
    trace_id: str
    payload_ref: str = Field(min_length=1, max_length=512)
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_id: str | None = None
    aggregate_type: Literal["session", "artifact", "job", "claim", "evidence", "memory"] | None = None


class ResearchSSECursor(BaseModel):
    session_id: str
    last_event_id: str | None = Field(default=None, max_length=128)
    fallback: Literal["NONE", "SNAPSHOT_REQUIRED"] = "NONE"
    revision: int = Field(ge=0)
    stream_epoch: int = Field(default=0, ge=0)


class ResearchSSEHeartbeat(BaseModel):
    session_id: str
    occurred_at: datetime
    revision: int = Field(ge=0)
