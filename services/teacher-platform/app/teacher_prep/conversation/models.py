"""R17 Conversation-first 编排持久化（8100 权威，tp_ 前缀，additive 三表）。

设计约束（DECISIONS.md §4/§5/§7/§8）：
- 服务端状态机唯一权威：消息 / 意图 / 上下文 / run 全持久化；
  `localStorage` 只能存 UI 偏好，业务真相全部落在本表族；
- 消息只追加（不可变）：`seq` 会话内全序（UNIQUE(session_id, seq) 兜底双写）；
- 消息里的组件引用必须是 allowlisted 类型 + schema 校验后的 props（catalog 层强制）；
- LLM 只产生 intent/proposal 载荷；确认状态由 R14 闸门 + 教师命令推进；
- provider 失败 → 带标签可编辑 basic mode（不沉默 / 不编造 / 不假成功）；
- `tp_message_runs` 与 `reliability_jobs.conversation.run` 一一对应
  （fencing / dedupe / 重启续跑由 JobEngine 承担，本表是业务侧 run 事实）。

SQLite（测试）/ PG（生产）双方言兼容，做法同 reliability/models.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JsonB = JSONB().with_variant(JSON(), "sqlite")
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ── 消息 kind / run 状态（与 state_machines 对齐的口径）───────────────────────

MESSAGE_KINDS = (
    "text",          # 用户文本 / 助手纯文本
    "intent",        # 意图解析结果卡
    "context",       # 教材教学上下文卡
    "proposal",      # 蓝图提案卡
    "evidence",      # 证据 rail
    "selection",     # 选择卡（教材/章节/班级/知识点）
    "basic_mode",    # AI 失败 → 带标签可编辑降级
    "error",         # 显式错误卡（不沉默）
    "job",           # 作业状态卡
)

RUN_STATES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "BASIC_MODE")


class ConversationMessageRow(Base):
    """会话内 durable 消息（用户文本 / 助手卡片），append-only。

    - `component_type / component_props`：allowlisted 组件引用（catalog 层校验后写入）；
    - `intent`：parsed PrepIntent 快照（不可变；LLM 载荷，schema 校验后才是意图）；
    - `intent_hash`：意图规范 hash（可对账/审计）；
    - `context_snapshot_id / blueprint_id`：关联事实快照；
    - `run_id`：产生本消息的编排 run（FK 延迟补写）；
    - `status`：applied / failed / basic_mode（basic_mode 消息可编辑）；
    - `error_label`：脱敏 provider 失败标签（provider_unavailable/timeout/…），不落原文。
    """

    __tablename__ = "tp_conversation_messages"
    __table_args__ = (
        CheckConstraint("seq >= 1", name="ck_tp_conv_messages_seq"),
        UniqueConstraint("session_id", "seq", name="uq_tp_conv_messages_session_seq"),
        Index("ix_tp_conv_messages_session_seq", "session_id", "seq"),
        Index("ix_tp_conv_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    role: Mapped[str] = mapped_column(Text, nullable=False)  # user / assistant / system
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # MESSAGE_KINDS
    component_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    component_props: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    intent: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    intent_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_context_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_blueprints.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_message_runs.id", ondelete="SET NULL", use_alter=True, name="fk_tp_conv_messages_run"),
        nullable=True,
    )
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="applied")
    editable: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MessageRunRow(Base):
    """编排 run 事实：一条消息一次 AI 编排阶段（intent / blueprint）。

    - `status`：PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/BASIC_MODE；
    - `attempt`：局部重试次数（retry_run 复用同一 message，新建 run 行）；
    - `provider_error`：只存脱敏标签（不落模型原文 / 不落 secret）；
    - `result_ref`：产物引用（blueprint_id 等），用于幂等防重（崩溃后重投 → 跳过）。
    """

    __tablename__ = "tp_message_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED','BASIC_MODE')",
            name="ck_tp_conv_runs_status",
        ),
        CheckConstraint("attempt >= 1", name="ck_tp_conv_runs_attempt"),
        Index("ix_tp_conv_runs_session_created", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    run_type: Mapped[str] = mapped_column(Text, nullable=False)  # intent / blueprint / action
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    provider_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContextSnapshotRow(Base):
    """不可变 TextbookTeachingContext 快照（hash 锁定，确定性可复现）。

    - `snapshot`：R16 模型 `model_dump(mode='json')` 全文（不可变）；
    - `snapshot_hash`：canonical sha256（R16 canonical_snapshot_hash）；
    - source/section/class 摘要列：索引与对账用。
    """

    __tablename__ = "tp_context_snapshots"
    __table_args__ = (
        CheckConstraint("length(snapshot_hash) = 64", name="ck_tp_ctx_snapshots_hash"),
        Index("ix_tp_ctx_snapshots_session_created", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    section_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    class_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "ConversationMessageRow",
    "MessageRunRow",
    "ContextSnapshotRow",
    "MESSAGE_KINDS",
    "RUN_STATES",
]
