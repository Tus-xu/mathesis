"""RV16 · token 流落库模型（StreamBuffer 的持久层）。

一帧一行：`(turn_id, seq)` 唯一。`seq` 在 **turn 内**单调递增（对齐 ARCHITECTURE §4.3：
`/stream` 的 `id = turn 内单调 seq`，与 `/events` 的 `domain_event.id` 天然分域）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

#: 帧类型（命名冻结，见 ARCHITECTURE §4.3）。
FrameType = Literal[
    "snapshot", "token", "thinking", "tool", "artifact", "finding", "done", "error"
]


class ResearchStreamChunkRow(Base):
    __tablename__ = "research_stream_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    turn_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StreamFrame(BaseModel):
    seq: int
    type: FrameType
    turn_id: uuid.UUID
    payload: dict[str, Any]
