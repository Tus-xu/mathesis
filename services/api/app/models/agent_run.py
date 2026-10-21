"""Butler Kernel v2 运行账本（设计规格 §8 + 阶段 3B）

AgentRun / AgentStep / ToolInvocation：
- AgentRun 记录一次运行的入口、状态与预算消耗；
- AgentStep 记录管线各阶段（context/plan/policy/execute/compose）耗时与结果；
- ToolInvocation 记录每次工具调用的脱敏摘要与幂等键。

约束：
- UniqueConstraint(user_id, client_request_id)：重复请求不产生重复写；
- AgentStep(run_id, sequence) 唯一；
- 索引：user_id+created_at、run_id、tool_name；
- 账本不保存原始密钥、完整 Prompt、完整隐私文本、模型隐藏思维链或完整工具输入输出
  （只存脱敏摘要 digest / metadata）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentRun(Base):
    """一次 Butler 运行入口记录。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "client_request_id", name="uq_agent_runs_user_client_req"),
        Index("ix_agent_runs_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    scene: Mapped[str] = mapped_column(String(64), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="running"
    )  # running/succeeded/failed/fallback
    degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    model_request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, server_default="{}")  # 脱敏
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentStep(Base):
    """管线阶段记录（context/plan/policy/execute/compose）。"""

    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_steps_run_sequence"),
        Index("ix_agent_steps_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="running")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, server_default="{}")  # 脱敏
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ToolInvocation(Base):
    """工具调用记录（只存脱敏摘要与幂等键）。"""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        Index("ix_tool_invocations_run", "run_id"),
        Index("ix_tool_invocations_tool_name", "tool_name"),
        Index("ix_tool_invocations_idempotency_key", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="running")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    arguments_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
