"""research-vnext 持久模型（PostgreSQL 为事实权威）。

PostgreSQL 持有：snapshot / revision / event / proposal / pending action / job。
Valkey 只作协调（**不是**事实来源）。

命名与不变量：
- `RV02`：session/event/outbox/idempotency/pending action/summary/revocation floor；
- `CR-6`：message 层（会话记录）+ AI run 审计；
- `RV03`：durable job runtime（job / inbox / dlq / lease 全在 PG）；
- `RV09–RV13`：领域对象，统一走**不可变修订链**（`RevisionedObjectRow`）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchSessionRow(Base):
    __tablename__ = "research_session"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="MODELING")
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_artifact_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    policy_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confirmation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DomainEventRow(Base):
    """持久领域事件（SSE 读取的唯一权威来源）。"""

    __tablename__ = "domain_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False, default="session")


class OutboxEventRow(Base):
    """Transactional outbox。

    `RV02` 只到 durable record；`RV03` 补齐了真实 relay
    （`application/outbox_relay.py`），它用 `FOR UPDATE SKIP LOCKED` 领取行、
    发布到队列并回填 `published_at` / `attempts`。
    """

    __tablename__ = "outbox_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class CommandRecordRow(Base):
    """幂等记录：唯一 (tenant_id, actor_id, client_request_id)。

    `idempotency_fingerprint` 是**调用方提供**的字段，仅为 P00 契约兼容而保留；
    **权威**判据是服务端重算的 `canonical_digest`（见
    `application/session_service.canonical_command_digest`）。修复前只比较前者，
    导致"同一 crid + 同一 fingerprint + 不同 payload"被当作重放吞掉
    （静默丢失 mutation）—— 见 docs/ADR-RV-002-idempotency-and-sse-cursor.md。
    """

    __tablename__ = "command_record"
    __table_args__ = (
        UniqueConstraint("tenant_id", "actor_id", "client_request_id", name="uq_command_record_idem"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # 服务端权威摘要（sha256 hex，64 字符）。可空：0002 之前写入的历史行没有该值。
    canonical_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # `payload_ref` 仅留痕/审计，**不**参与冲突判定（见 ADR-RV-002 §2.3）。
    payload_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class PendingActionRow(Base):
    """确认门。`payload` 里携带 typed proposal；`object_ref` 指向目标对象族。"""

    __tablename__ = "pending_action"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    required_capability: Mapped[str] = mapped_column(String(128), nullable=False, default="research.session.write")
    #: 005 追加：proposal 落的**对象族**（problem_map / claim / ...）。
    object_ref: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 005 追加：触发它的 assistant message（用于审计"哪句话导致了这次确认"）。
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class PendingActionAuditRow(Base):
    """accept / reject / expire / cancel 全部留痕。"""

    __tablename__ = "pending_action_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pending_action_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SessionSummaryRow(Base):
    __tablename__ = "session_summary"
    __table_args__ = (UniqueConstraint("session_id", "summary_version", name="uq_session_summary_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    summary_version: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ActorSecurityFloorRow(Base):
    """撤权锚点：security_version 低于 floor 一律拒绝。

    这是"撤权检查"，与 token 是否有效无关 —— token 有效但被撤权仍须 403。
    """

    __tablename__ = "actor_security_floor"

    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    min_security_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


# ==========================================================================
# CR-6（ADR-RV-003）· 消息层
#
# **核心不变量**：`research_message` 是**会话记录**，不是正式领域真相。
# 正式状态（ProblemMap / Blueprint / Claim / PaperSpec）仍由 command +
# PendingAction 确认门守护；assistant message 里的 payload **只能是 proposal**。
# ==========================================================================


class ResearchMessageRow(Base):
    """会话消息（timeline 的唯一持久来源）。

    `seq` 是**每会话单调**序号：`created_at` 在并发写入下可能相同/回拨，
    只按时间排序会产出不稳定的 timeline；`(session_id, seq)` 唯一约束把它变成
    可证明的稳定序。
    """

    __tablename__ = "research_message"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_research_message_seq"),
        Index("ix_rm_session_seq", "session_id", "seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # 仅存 **typed proposal / capability notice**；绝不放原始 HTML/CSS/组件名。
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_by_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    #: 幂等键（ADR-RV-003：`client_message_id` 复用 `client_request_id` 语义）。
    #: 崩溃恢复时用它判断"user 行是否已落"，从而不重复插一条同样的用户消息。
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class ResearchAiRunRow(Base):
    """每一次 LLM 调用的审计记录（**不**记录 prompt 正文，只记 prompt ref）。

    `prompt_ref` 形如 `rv.assistant.propose@v1#<sha256[:16]>`：版本 + 内容哈希，
    因此"模型当时看到的提示词"可复现，而无需把可能含用户数据的正文落库。
    """

    __tablename__ = "research_ai_run"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_ref: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    # ---- RV09（0010）追加：上下文 / prompt 发布态 / 成本 --------------------
    #: `ContextManifest.to_record()` —— **只有引用与裁剪理由，不含任何正文**。
    #: 用户原文已经在 `research_message` 里，重复落库只会扩大泄露面。
    context_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="'{}'::jsonb")
    #: 发出版本号与"当时它是否为已发布版本"。
    #: 即便发布台账之后被改动，这一次运行的事实仍可复核。
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    prompt_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    #: 成本。**可空**：未知模型的成本是"未知"，不是 0。
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    #: 成本的**依据**（`table:<版本>` / `unknown-model` / `no-usage`），
    #: 让"0 美元"与"不知道多少钱"在数据上可区分。
    cost_basis: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")


# ==========================================================================
# RV03 · durable job runtime
#
# 事实权威一律在 PostgreSQL；Valkey 只做投递与租约加速。
# 丢掉 Valkey 不得丢真相 —— relay 会从 `outbox_event` 重建。
# ==========================================================================

#: **对外**规范状态（前端/用户可见的封闭集合）。
JOB_STATUSES: tuple[str, ...] = (
    "QUEUED", "RUNNING", "RETRYING", "PARTIAL", "SUCCEEDED", "FAILED", "CANCELLED",
)
#: **内部**状态：`CLAIMED` 表示"已被 worker 领走但尚未上报为 RUNNING"。
#: 它**绝不**作为用户可见的成功态出现（投影层显式映射成 `QUEUED`）。
JOB_STATUS_CLAIMED = "CLAIMED"


class JobRow(Base):
    """一个可恢复的后台作业。

    `fencing_token` 单调递增：worker 每次领取都拿到一个更大的 token，
    上报结果时必须回传**当前** token；过期 worker 的迟到结果会被拒（见
    `job_service.complete_job`）。
    """

    __tablename__ = "job_run"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: **内部**状态（可能是 CLAIMED）；对外投影一律经 `public_status()`。
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED", index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="rv03.job.v1")
    input_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: PARTIAL 必须说清哪些 stage 成功、哪些失败（禁止渲染成绿色成功）。
    partial_detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: "这是一次可重试失败"（临时）还是"重试也不会好"（永久）—— 由分类器判定。
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobInboxRow(Base):
    """幂等消费者收件箱：唯一键 `(event_id, job_id, attempt)`。

    重复投递（relay 重发、Valkey 重放、worker 重启后重读）在这里被吸收，
    因此 handler **不需要**自己实现幂等。
    """

    __tablename__ = "job_inbox"
    __table_args__ = (
        UniqueConstraint("event_id", "job_id", "attempt", name="uq_job_inbox_dedupe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="rv8300")
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumer: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class JobDlqRow(Base):
    """死信队列。**可重放**：保留 input ref + policy version + 脱敏 error + trace。

    重放是**显式管理命令**，绝不是自动无限循环。
    """

    __tablename__ = "job_dlq"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    input_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: **已脱敏**的错误（redaction.py 处理过），不含密钥/PII。
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    replayed_job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


# ==========================================================================
# RV09–RV13 · 领域对象
#
# 统一形态：**不可变修订链**（见 `RevisionedObjectRow`）。
# 模型输出**从不**直接写这里：必须经 Proposal → PendingAction → 用户 confirm。
# ==========================================================================


class RevisionedObjectRow(Base):
    """修订链对象的声明式基类（`__abstract__`，不单独建表）。"""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class EvidenceCardRow(Base):
    """证据卡 —— 只持有**引用与授权位**，证据本体属平台资产层。"""

    __tablename__ = "evidence_card"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    label: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    locator: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    asset_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 失效即触发 claim 重开判定（RV12：失效证据可以重开 SOLVED claim）。
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ResearchIntakeRow(RevisionedObjectRow):
    """RV09 · ResearchIntake：结构化的"我理解你在问什么"。"""

    __tablename__ = "research_intake"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_research_intake_rev"),)


class ProblemMapRow(RevisionedObjectRow):
    """RV09 · ProblemMap：子问题 / 约束 / 变量 / 已知未知 / 交付物 / 成功判据。

    每个元素都带 `origin`（USER / SOURCE / INFERENCE）——
    **inference 绝不能被呈现为来自问题陈述的事实**。
    """

    __tablename__ = "problem_map"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_problem_map_rev"),)


class ResearchMapRow(RevisionedObjectRow):
    """RV09 · ResearchMap：typed nodes + typed edges。"""

    __tablename__ = "research_map"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_research_map_rev"),)


class ModelSpecRow(RevisionedObjectRow):
    """RV10 · ModelSpec：一条候选建模路线（目标/变量/参数/假设/适用性）。"""

    __tablename__ = "model_spec"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_model_spec_rev"),)

    label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    constraints: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence_coverage: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class RouteArenaRow(RevisionedObjectRow):
    """RV10 · RouteArena：2–3 条候选路线 + 服务端 rubric（比较矩阵无总分）。

    竞技场是**被确认的比较事实**：哪几条路线参选、各自的结构声明、
    因什么被拒——blueprint 的绑定对象是它，而不是模型的一面之词。
    """

    __tablename__ = "route_arena"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_route_arena_rev"),)


class ResearchBlueprintRow(RevisionedObjectRow):
    """RV10 · ResearchBlueprint：被选定的路线 + 目标和证据节点。"""

    __tablename__ = "research_blueprint"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_research_blueprint_rev"),)


class ExperimentPlanRow(RevisionedObjectRow):
    """RV11 · ExperimentPlan：可执行实验的计划（执行本身归 WP-I executor）。"""

    __tablename__ = "experiment_plan"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_experiment_plan_rev"),)


class ExperimentRunRow(Base):
    """RV11 · ExperimentRun：一次真实运行的全部可复现元数据。

    **`exit_code == 0` 绝不等于"结论成立"**：
    `claim_holds` 是与 `exit_code` **分开**的字段，未判定时为 `None`（不是 True）。
    """

    __tablename__ = "experiment_run"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    executor: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    code_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    dataset_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    environment: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    logs_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    figures: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 结论是否成立 —— **独立于 exit_code**。None = 尚未判定（而非成立）。
    claim_holds: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    not_reproducible_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    #: imageDigest / dependencyLockHash / seed / codeHash / inputHash / outputHash
    repro: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ClaimRow(RevisionedObjectRow):
    """RV12 · Claim：结论 + 状态 + **独立于状态**的支持质量。

    RV12 追加（0012）：`claim_key`（跨修订稳定身份）+ owner/ACL + 派生投影列。
    状态仍由服务端 Gate 推导（见 `claim/state_machine.derive_status`）；
    这些列只是可被 SQL 过滤的**投影**，不是第二处真源。
    """

    __tablename__ = "claim"
    __table_args__ = (
        UniqueConstraint("session_id", "revision", name="uq_claim_rev"),
        Index("ix_claim_key", "claim_key"),
    )

    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: SOLVED | CONDITIONAL | INCONCLUSIVE | REFUTED
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="INCONCLUSIVE")
    #: UNSUPPORTED | PARTIAL | SUPPORTED | CONFLICTED —— **与 status 正交**
    support_quality: Mapped[str] = mapped_column(String(32), nullable=False, default="UNSUPPORTED")
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    experiment_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    verification_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # ---- RV12（0012）追加 ----
    #: 跨修订稳定身份：同一 claim 的所有修订共享它（NULL = 0012 之前的历史行）。
    claim_key: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    acl: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="'{}'::jsonb")
    claim_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PARAPHRASE", server_default="PARAPHRASE"
    )
    support_band: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NONE", server_default="NONE"
    )
    #: 需要人工复核（冲突/失效/不可复现/未知 verifier）——**不静默保持 SOLVED**。
    requires_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    review_reasons: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class ClaimEvidenceLinkRow(Base):
    """RV12 · claim ↔ evidence 关联（含每链接的**可解释**支持评估快照）。

    为什么单独一张表而不是塞进 `claim.content`：同一条证据可以在不同 claim 上
    以不同角色（支撑/反驳）出现，且"谁在什么时候加进来"必须可查。
    """

    __tablename__ = "claim_evidence_link"
    __table_args__ = (
        UniqueConstraint("claim_key", "evidence_card_id", "role", name="uq_claim_evidence_role"),
        Index("ix_claim_evidence_claim", "claim_key"),
        Index("ix_claim_evidence_card", "evidence_card_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_key: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_card_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SUPPORTS | REFUTES | CONTEXT
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="SUPPORTS")
    claim_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="PARAPHRASE")
    note: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ClaimTransitionRow(Base):
    """RV12 · claim 状态迁移审计（**只追加**）。

    每一行回答："这次为什么变成这个状态、哪些判据满足/缺失、是谁触发的"。
    状态变迁没有审计行 = 无法复核，因此 `evaluate` 无论状态是否变化都写一行。
    """

    __tablename__ = "claim_transition"
    __table_args__ = (Index("ix_claim_transition_claim", "claim_key", "revision"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_key: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    gate_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    satisfied: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    missing: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    refutation: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    review_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: AI | SYSTEM | HUMAN —— 谁有权让状态发生变化，这是审计的关键维度。
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="SYSTEM")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class VerificationRecordRow(Base):
    """RV12 · VerificationRecord：独立验证的结果。

    `result ∈ PASS | FAIL | UNKNOWN`，且 **UNKNOWN 永不被强制转成 PASS**：
    下游判定必须显式区分"没验证"与"验证通过"。

    RV12 追加（0012）：verifier 身份/版本、结构化 criteria、artifact refs、
    工具策略、PromptRef/ContextManifest/成本/trace —— 让"这次验证是怎么做的"
    在不读日志的前提下可复核。
    """

    __tablename__ = "verification_record"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    independent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    # ---- RV12（0012）追加 ----
    claim_key: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    verifier: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    kind: Mapped[str] = mapped_column(String(48), nullable=False, default="", server_default="")
    verifier_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", server_default=""
    )
    artifact_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    tool_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="'{}'::jsonb"
    )
    prompt_ref: Mapped[str] = mapped_column(String(160), nullable=False, default="", server_default="")
    context_manifest_ref: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=""
    )
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_basis: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")


class PaperSpecRow(RevisionedObjectRow):
    """RV13 · PaperSpec：**结构化 block 树**，不是 LaTeX 字符串。

    `content = {"blocks": [...]}`，块类型 ∈ section/heading/paragraph/equation/
    figure/table/citation/callout/claim_ref/evidence_ref/review_mark。
    每个节点带 `nodeRev`（内容变更即递增）；LaTeX 只是 import/export 格式。
    """

    __tablename__ = "paper_spec"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_paper_spec_rev"),)


class PaperEditAuditRow(Base):
    """RV13 · 论文编辑审计：人工编辑 / 撤销 / 恢复 / AI patch 落库全部留痕。

    为什么独立成表：`undo/redo` 本身也要写**新的不可变修订行**，
    "这次修订是撤销产生的、恢复了哪一版"只能放在修订行之外 ——
    放进 content 会破坏"恢复内容与被恢复版本逐字节一致"的性质。
    """

    __tablename__ = "paper_edit_audit"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    paper_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    #: MANUAL_EDIT | UNDO | REDO | AI_PATCH_ACCEPT
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ReviewFindingRow(Base):
    """RV13 · ReviewFinding：审稿发现。"""

    __tablename__ = "review_finding"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    paper_spec_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: EVIDENCE_GAP | MATH_ERROR | DATA_LEAKAGE | REPRO_GAP | CITATION_MISMATCH
    #: | OVERCLAIM | WRITING_ISSUE
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="minor")
    location: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    related_claim_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    related_evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    resolution_evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


# ==========================================================================
# RV15 · Review Finding v2（Review Review Board）
#
# 与上方 RV13 legacy `review_finding` 的关系：**权威表唯一真源**（与
# evidence_card / research_evidence_card 同一套裁决口径）。legacy 行按旧
# taxonomy 读取（`review.taxonomy.LEGACY_ALIASES` 映射），新写入只进 v2 表。
#
# 不变量（见 RV15/STATE-MACHINE.md / BLOCKING-GATE.md）：
#   - fingerprint 唯一于会话内非终态行：同指纹重审 → 更新或重开，绝不复制；
#   - RESOLVED 必须有 resolution 行（修复修订 + 证据）；WAIVED 必须有 waiver 行；
#   - 迁移/处置全部 append-only 审计（transition/resolution/waiver 三张子表）。
# ==========================================================================


class ReviewFindingV2Row(Base):
    """RV15 · ReviewFinding v2：审查发现（权威表）。

    `target` 形状：`{type, id, version, locator}` —— type ∈
    paper_node/claim/evidence/evidence_ref_node/citation/experiment_run/
    export_page（按 taxonomy 白名单，见 review.taxonomy.ALLOWED_TARGET_TYPES）；
    version 是发现时的 target 版本；locator 保证 finding 可精确回源。
    """

    __tablename__ = "review_finding_v2"
    __table_args__ = (
        Index("ix_rfv2_session_status", "session_id", "status"),
        Index("ix_rfv2_session_fp", "session_id", "fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    #: 封闭十类（review.taxonomy.FINDING_CATEGORIES）
    taxonomy: Mapped[str] = mapped_column(String(32), nullable=False)
    #: P0 | P1 | P2（默认值由类别决定）
    severity: Mapped[str] = mapped_column(String(8), nullable=False, default="P2")
    #: OPEN | ACKNOWLEDGED | IN_PROGRESS | RESOLVED | WAIVED | REOPENED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")

    target: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_by_run: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    assignee: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    due: Mapped[date | None] = mapped_column(Date(), nullable=True)

    #: 指纹命中的次数（同问题重审的次数 +1）
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_run: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    needs_recheck: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    resolution: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    waiver: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    #: finding 自身的乐观锁（transition 409 用），不是对象修订链
    row_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    audit: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class FindingTransitionRow(Base):
    """RV15 · finding 状态迁移审计（append-only）。"""

    __tablename__ = "review_finding_transition"
    __table_args__ = (Index("ix_rft_finding", "finding_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    from_status: Mapped[str] = mapped_column(String(24), nullable=False)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: 触发迁移的 review run id（系统触发）或空（人工触发）
    by_run: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class FindingResolutionRow(Base):
    """RV15 · RESOLVED 的证据闭环（append-only；修复修订 + 修复证据）。"""

    __tablename__ = "review_finding_resolution"
    __table_args__ = (Index("ix_rfr_finding", "finding_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: `{kind: paper_spec|claim|experiment|export, revision: int}` —— 修复产物
    revision_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class FindingWaiverRow(Base):
    """RV15 · WAIVED 的审批链（append-only；理由 + 批准人 + 到期）。"""

    __tablename__ = "review_finding_waiver"
    __table_args__ = (Index("ix_rfw_finding", "finding_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: `{code, text}`（code ∈ 封闭四码）
    reason: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    expiry: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    #: 到期自动重开后置 True（豁免的历史可追溯）
    expired_reopened: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class ReviewRunRow(Base):
    """RV15 · 一次 Review run 的审计（Reviewer AI run 记录）。"""

    __tablename__ = "review_run"
    __table_args__ = (Index("ix_rrun_session", "session_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: 提案数 / 新建 / 更新 / 重开（apply 回填）
    proposed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reopened_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROPOSED")
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


# RV04 · 资产与文献表（字节在对象存储；metadata ≠ Evidence）
from ..assets.models import AssetSignedUrlRow, ResearchAssetRow  # noqa: E402,F401
from ..literature.models import LiteratureDedupConflictRow, LiteratureRecordRow  # noqa: E402,F401

# RV06 · 检索 trace（只存 hash 与 ID，不存 query 原文/snippet）
from ..federation.models import RetrievalTraceRow  # noqa: E402,F401

# RV07 · EvidenceCard 事实行 + Citation canonical 行（**独立于** RV09–RV13 的 evidence_card）
from ..evidence.models import EvidenceCardV2Row  # noqa: E402,F401
from ..citation.models import CitationRow  # noqa: E402,F401


# ==========================================================================
# RV09 · 记忆与修订审计（0010）
#
# 三张表回答三个不同的问题，刻意**不**合并：
#   - `research_thread_checkpoint`：这条对话走到哪了（会话内，随会话生灭）；
#   - `research_preference`：这个人做研究希望被如何对待（跨会话，按命名空间隔离）；
#   - `object_revision_audit`：某个正式对象为什么变成现在这样（不可变，只追加）。
# ==========================================================================


class ResearchThreadCheckpointRow(Base):
    """短期记忆 checkpoint：`(session_id, thread_key)` 唯一。

    `thread_key` 让同一会话里的并行话题各有一条进度线（"主线程"默认 `main`），
    从而"两个话题互相当前提"在结构上不可能发生。
    """

    __tablename__ = "research_thread_checkpoint"
    __table_args__ = (
        UniqueConstraint("session_id", "thread_key", name="uq_thread_checkpoint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    #: 冗余进谓词：会话 id 万一被跨租户猜中，tenant 不匹配仍返回空。
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    thread_key: Mapped[str] = mapped_column(String(64), nullable=False, default="main")
    last_message_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: 白名单键的状态（见 `memory/short_term.py::sanitize_state`）。
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ResearchPreferenceRow(Base):
    """长期偏好：命名空间 = `tenant / user / research / purpose`。

    `research_id` 存**字符串**而不是 UUID：它表示"这个用户在这项研究下的偏好"，
    研究本身可能还没有（或永远不会有）一个领域对象 id；用字符串避免了
    "必须先建一个空研究才能存偏好"这种假依赖。
    """

    __tablename__ = "research_preference"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "research_id", "purpose", "key",
            name="uq_research_preference_ns",
        ),
        Index("ix_research_preference_ns", "tenant_id", "user_id", "research_id", "purpose"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    research_id: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(48), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 只有用户**明确设定**过的偏好才为 True；模型推断的恒为 False，且不进提示词。
    authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ObjectRevisionAuditRow(Base):
    """一次"提案被确认并落成新修订"的完整审计行（**只追加，永不更新**）。

    为什么不在 `RevisionedObjectRow` 上直接加列：修订行是**内容**，
    审计行是**因果关系**（是谁、依据哪次 AI run、提示词是什么版本、上下文裁了什么、
    花了多少钱）。把两者混在一张表里，内容哈希会随审计字段变化而变化。
    """

    __tablename__ = "object_revision_audit"
    __table_args__ = (
        Index("ix_object_revision_audit_obj", "session_id", "object_ref", "revision"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_object_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    previous_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 与上一修订的结构化 diff（新增/删除/修改），供 409 展示与"改了什么"回看。
    diff: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: 因果链：proposal → pending action → AI run → prompt / context / model / cost / event。
    pending_action_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    prompt_ref: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_manifest_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    context_trim_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_basis: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


# RV14 · 导出产物 + 下载审计（字节在 MinIO；这里只有 key/hash/manifest/审计）
from ..export.models import PaperExportArtifactRow, PaperExportAuditRow  # noqa: E402,F401
