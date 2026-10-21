"""Research 领域事件（RV02）。

与 P00 `DomainEventEnvelope` 字段对齐；**未知 event_type 与不支持的
schema_version 一律拒绝**，绝不静默放行（P00 硬约束）。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .errors import UnknownEventType, UnsupportedSchemaVersion

#: 本轮支持的事件 schema 版本（新增版本必须显式登记，否则拒绝）
SUPPORTED_EVENT_SCHEMA_VERSIONS: tuple[str, ...] = ("1.0.0",)

EVENT_SCHEMA_VERSION = "1.0.0"

#: 允许的事件类型白名单（namespaced）
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session.created",
        "session.resumed",
        "session.state_changed",
        "session.artifact_refs_changed",
        "session.summary_regenerated",
        "session.pending_action_created",
        "session.pending_action_accepted",
        "session.pending_action_rejected",
        "session.pending_action_expired",
        "session.pending_action_cancelled",
        "session.cancelled",
        # ---- CR-6（ADR-RV-003）：消息层 ----
        "session.message_created",
        "session.assistant_message_created",
        "session.proposal_confirmed",
        # ---- RV10：蓝图就绪（供 RV11 实验执行消费；只含 ID/version/hash） ----
        "research.blueprint_ready",
        # ---- RV13：论文修订（人工编辑 / 撤销 / 恢复；双标签同步依赖它） ----
        "paper.revised",
        # ---- RV03：job 生命周期（typed，前端不必按定时器猜进度） ----
        "job.queued",
        "job.claimed",
        "job.started",
        "job.progress",
        "job.stage_changed",
        "job.heartbeat",
        "job.retrying",
        "job.partial",
        "job.succeeded",
        "job.failed",
        "job.cancelled",
        "job.dlq",
        "job.replayed",
    }
)


def new_event_id(session_id: uuid.UUID, revision: int, salt: str = "") -> str:
    raw = f"{session_id}:{revision}:{salt}:{uuid.uuid4().hex}"
    return "evt-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


class DomainEvent(BaseModel):
    """领域事件（同时是 outbox 消息体与 SSE `data:` 载荷）。"""

    event_id: str = Field(min_length=8, max_length=128)
    event_type: str = Field(min_length=3, max_length=96)
    schema_version: str
    session_id: uuid.UUID
    revision: int = Field(ge=0)
    occurred_at: datetime
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_id: uuid.UUID | None = None
    aggregate_type: str = "session"

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        session_id: uuid.UUID,
        revision: int,
        payload: dict[str, Any],
        trace_id: str,
        schema_version: str = EVENT_SCHEMA_VERSION,
        actor_id: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> "DomainEvent":
        if "." not in event_type:
            raise UnknownEventType(f"event_type 必须 namespaced（<domain>.<verb>）: {event_type!r}")
        if event_type not in KNOWN_EVENT_TYPES:
            raise UnknownEventType(f"未知 event_type: {event_type!r}")
        if schema_version not in SUPPORTED_EVENT_SCHEMA_VERSIONS:
            raise UnsupportedSchemaVersion(
                f"不支持的 event schema_version: {schema_version!r}；"
                f"支持 {list(SUPPORTED_EVENT_SCHEMA_VERSIONS)}"
            )
        return cls(
            event_id=new_event_id(session_id, revision, event_type),
            event_type=event_type,
            schema_version=schema_version,
            session_id=session_id,
            revision=revision,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            trace_id=trace_id,
            payload=payload,
            actor_id=actor_id,
        )

    @field_validator("trace_id")
    @classmethod
    def _trace_id_lower(cls, v: str) -> str:
        return v.lower()


class OutboxRecord(BaseModel):
    """Outbox 持久化记录。

    RV02 **只到 durable record**：没有 relay、没有 worker、没有 published_at。
    任何"已完成"的假象都被禁止。
    """

    event_id: str
    session_id: uuid.UUID
    event_type: str
    schema_version: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str
    status: str = "PENDING"
    attempts: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
