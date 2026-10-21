"""ResearchSession 应用服务（RV02）。

事务边界铁律：
- **domain event + outbox 在同一 DB transaction 写入**（总手册 §13.3）；
- revision 冲突、幂等冲突、授权失败一律**不落任何写入**；
- RV02 **不实现 worker**，outbox 只留 PENDING durable record。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..config import sse_retention_events, sse_retention_hours
from ..contracts.command import ResearchCommandEnvelope
from ..db.models import (
    ActorSecurityFloorRow,
    CommandRecordRow,
    DomainEventRow,
    OutboxEventRow,
    PendingActionAuditRow,
    PendingActionRow,
    ResearchSessionRow,
    SessionSummaryRow,
)
from ..domain.errors import (
    CapabilityMissing,
    ConfirmationRequired,
    DomainError,
    IdempotencyConflict,
    InvalidTransition,
    PartialIsNotCompleted,
    PartialReasonRequired,
    PendingActionExpired,
    PendingActionNotFound,
    RevisionConflict,
    SessionNotFound,
)
from ..domain.events import DomainEvent
from ..domain.session import ResearchSession, SessionMode, SessionState
from ..domain.state_machine import can_transition
from ..domain.summary import build_summary
from ..observability.redaction import redact_mapping
from ..observability.trace import TraceContext

PENDING_TTL_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_ref(session_id: uuid.UUID) -> str:
    return f"snapshot:{session_id}"


# --------------------------------------------------------------------------
# 读
# --------------------------------------------------------------------------


def _load(db: Session, session_id: uuid.UUID) -> ResearchSessionRow:
    row = db.get(ResearchSessionRow, session_id)
    if row is None:
        raise SessionNotFound(f"会话不存在: {session_id}")
    return row


def _to_aggregate(row: ResearchSessionRow) -> ResearchSession:
    return ResearchSession(
        id=row.id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        title=row.title,
        mode=SessionMode(row.mode),
        status=SessionState(row.status),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
        current_artifact_refs=list(row.current_artifact_refs or []),
        summary_ref=row.summary_ref,
        policy_refs=list(row.policy_refs or []),
        confirmation_count=row.confirmation_count,
        last_event_id=row.last_event_id,
        goal=row.goal or "",
    )


def _event_count(db: Session, session_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(DomainEventRow)
            .where(DomainEventRow.session_id == session_id)
        )
        or 0
    )


def _regenerate_summary(db: Session, row: ResearchSessionRow, trace: TraceContext) -> SessionSummaryRow:
    agg = _to_aggregate(row)
    pending = (
        db.query(PendingActionRow)
        .filter(PendingActionRow.session_id == row.id, PendingActionRow.status == "PENDING")
        .count()
    )
    data = build_summary(
        session_snapshot=agg.snapshot(),
        artifact_count=len(agg.current_artifact_refs),
        event_count=_event_count(db, row.id),
        pending_action_count=pending,
    )
    latest = (
        db.query(SessionSummaryRow)
        .filter(SessionSummaryRow.session_id == row.id)
        .order_by(desc(SessionSummaryRow.summary_version))
        .first()
    )
    version = (latest.summary_version + 1) if latest else 1
    data["summary_version"] = version
    rec = SessionSummaryRow(
        session_id=row.id,
        summary_version=version,
        contract_version=data["contract_version"],
        generated_by=data["generated_by"],
        content=data["content"],
        content_hash=data["content_hash"],
        created_at=_now(),
    )
    db.add(rec)
    row.summary_ref = f"summary:{row.id}@v{version}"
    return rec


# --------------------------------------------------------------------------
# 事件 + outbox（同事务）
# --------------------------------------------------------------------------


def _emit(db: Session, ev: DomainEvent) -> None:
    """domain event 与 outbox **同一 transaction** 写入。"""
    db.add(
        DomainEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            revision=ev.revision,
            occurred_at=ev.occurred_at,
            trace_id=ev.trace_id,
            payload=redact_mapping(ev.payload),
            actor_id=ev.actor_id,
            aggregate_type=ev.aggregate_type,
        )
    )
    db.add(
        OutboxEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            payload=redact_mapping(ev.payload),
            trace_id=ev.trace_id,
            status="PENDING",
            attempts=0,
            published_at=None,
            created_at=ev.occurred_at,
        )
    )


# --------------------------------------------------------------------------
# 幂等
# --------------------------------------------------------------------------

# 服务端权威摘要覆盖的字段。**决策依据（FIX-A1）**：
#
# 纳入 `command_type` + 业务 `payload` + `expected_revision`：
#   - `command_type` 决定命令语义；
#   - `payload` 是业务内容本体；
#   - `expected_revision` 是调用方声称的前置状态。**真正的重试必然重发同一个 revision**，
#     因此纳入它不会破坏合法重试；而"同一 crid + 同一 payload + 不同 revision"
#     在乐观锁语义下本就是不同的命令，理应冲突。
#
# 明确**排除**基础设施/环境字段：`traceparent`、`deadline`、`payload_ref`、客户端
# `idempotency_fingerprint`。理由是这些字段在"同一意图的两次重试"之间会自然变化
# （尤其 `deadline` 是每次重试重新计算的），纳入它们会让**合法重试变成一个假冲突**，
# 那是比漏检更糟的可用性故障。
#
# 关于 `payload_ref`：它是一个**指针/引用**（`inline:<sha>` 或对象键），不是内容本身。
# 本实现只对**内容**做摘要，并**不**把 `payload_ref` 差异当作冲突 —— 见
# docs/ADR-RV-002-idempotency-and-sse-cursor.md §2.3（该条为对评审建议的有意偏离）。
DIGEST_FIELDS = ("command_type", "payload", "expected_revision")


def canonical_digest_of(command_type: str, payload: dict, expected_revision: int | None) -> str:
    """**唯一**的规范式实现。

    `canonical_command_digest`（envelope 版本）与 CR-6 的 message 命令共用它，
    因此两条命令路径的幂等摘要**不可能**因为实现分叉而产生不同的判据。
    """
    material = {
        "command_type": command_type,
        "payload": dict(payload),
        "expected_revision": expected_revision,
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def canonical_command_digest(cmd: ResearchCommandEnvelope, payload: dict | None = None) -> str:
    """服务端权威命令摘要（sha256 hex）。

    **规范式（canonicalisation）**：`json.dumps(..., sort_keys=True, separators=(",", ":"),
    ensure_ascii=False)` —— 键序稳定、无多余空白、UTF-8 不转义。
    刻意不使用 `repr()` / `hash()` / 直接 `json.dumps(dict)`：
    前两者的稳定性不属于语言契约，后者受字典插入顺序影响 —— 三者的摘要都**不可复现**。

    摘要只依赖语义内容，因此**同一 payload 的不同键序产出同一摘要**（有测试守卫）。
    """
    return canonical_digest_of(
        cmd.command_type,
        dict(payload if payload is not None else cmd.domain_payload),
        cmd.expected_revision,
    )


def _check_idempotency_conflict(
    existing: CommandRecordRow, cmd: ResearchCommandEnvelope, payload: dict
) -> None:
    """在**已存在**幂等记录时判定这是否是同一条命令的重放。

    修复前的逻辑只比较 `existing.idempotency_fingerprint != cmd.idempotency_fingerprint`，
    即"把调用方自己给的字符串和这个字符串的存档副本比一比"—— payload 从未在服务端被哈希，
    因此调用方只要保持 fingerprint 不变就能让**语义完全不同的命令**被当作重放吞掉
    （评审实测：to=CANCELLED 的意图被静默丢弃，调用方却收到 200 REPLAYED）。

    修复后：以**服务端重算的摘要**为唯一权威判据。客户端 fingerprint 仍保留作契约字段
    （P00 conformance 需要），但**不再**作为唯一依据：它参与交叉校验，不一致即冲突。
    """
    server_digest = canonical_command_digest(cmd, payload)

    if existing.idempotency_fingerprint != cmd.idempotency_fingerprint:
        raise IdempotencyConflict(
            f"client_request_id {cmd.client_request_id!r} 已用于不同 payload"
            "（客户端 idempotency_fingerprint 不一致）"
        )

    stored = existing.canonical_digest
    if stored is None:
        # 迁移前写入的历史行没有服务端摘要。此时**不能**回退到"只比客户端 fingerprint"——
        # 那正是被修复的漏洞。无法证明等价，即按冲突处理（fail closed）。
        raise IdempotencyConflict(
            f"client_request_id {cmd.client_request_id!r} 已有历史记录但缺少服务端摘要，"
            "无法证明命令等价，拒绝按重放处理"
        )

    if not hmac.compare_digest(stored, server_digest):
        raise IdempotencyConflict(
            f"client_request_id {cmd.client_request_id!r} 已用于不同 payload"
            "（服务端摘要不一致）"
        )


def _find_idempotency(db: Session, cmd: ResearchCommandEnvelope, tenant_id: uuid.UUID) -> CommandRecordRow | None:
    return (
        db.query(CommandRecordRow)
        .filter(
            CommandRecordRow.tenant_id == tenant_id,
            CommandRecordRow.actor_id == uuid.UUID(cmd.actor_id),
            CommandRecordRow.client_request_id == cmd.client_request_id,
        )
        .one_or_none()
    )


def _save_idempotency(
    db: Session,
    cmd: ResearchCommandEnvelope,
    tenant_id: uuid.UUID,
    response: dict,
    payload: dict | None = None,
) -> None:
    db.add(
        CommandRecordRow(
            client_request_id=cmd.client_request_id,
            tenant_id=tenant_id,
            actor_id=uuid.UUID(cmd.actor_id),
            command_type=cmd.command_type,
            idempotency_fingerprint=cmd.idempotency_fingerprint,
            canonical_digest=canonical_command_digest(cmd, payload),
            payload_ref=cmd.payload_ref,
            session_id=uuid.UUID(response["session"]["id"]),
            response_body=response,
            created_at=_now(),
        )
    )


# --------------------------------------------------------------------------
# 写：create
# --------------------------------------------------------------------------


def create_session(
    db: Session,
    *,
    cmd: ResearchCommandEnvelope,
    payload: dict,
    trace: TraceContext,
    tenant_id: uuid.UUID,
) -> tuple[dict, str]:
    """创建会话；返回 (response, idempotency_status)。"""
    existing = _find_idempotency(db, cmd, tenant_id)
    if existing is not None:
        _check_idempotency_conflict(existing, cmd, payload)
        return existing.response_body or {}, "REPLAYED"

    session_id = uuid.uuid4()
    agg = ResearchSession.create(
        session_id=session_id,
        tenant_id=tenant_id,
        project_id=uuid.UUID(payload["project_id"]),
        owner_id=uuid.UUID(cmd.actor_id),
        title=payload["title"],
        mode=SessionMode(payload.get("mode", "MODELING")),
        policy_refs=payload.get("policy_refs") or ["policy:research.default@v1"],
        goal=payload.get("goal", ""),
        occurred_at=_now(),
    )

    row = ResearchSessionRow(
        id=agg.id,
        tenant_id=agg.tenant_id,
        project_id=agg.project_id,
        owner_id=agg.owner_id,
        title=agg.title,
        mode=agg.mode.value,
        goal=agg.goal,
        status=agg.status.value,
        revision=agg.revision,
        created_at=agg.created_at,
        updated_at=agg.updated_at,
        current_artifact_refs=agg.current_artifact_refs,
        summary_ref=agg.summary_ref,
        policy_refs=agg.policy_refs,
        confirmation_count=0,
        last_event_id=None,
    )
    db.add(row)
    db.flush()

    ev = DomainEvent.create(
        event_type="session.created",
        session_id=agg.id,
        revision=agg.revision,
        payload={"title": agg.title, "mode": agg.mode.value, "status": agg.status.value},
        trace_id=trace.trace_id,
        actor_id=agg.owner_id,
    )
    _emit(db, ev)
    row.last_event_id = ev.event_id
    _regenerate_summary(db, row, trace)
    db.flush()

    response = {
        "session": _to_aggregate(row).snapshot(),
        "last_event_id": ev.event_id,
        "revision": row.revision,
        "trace_id": trace.trace_id,
    }
    _save_idempotency(db, cmd, tenant_id, response, payload)
    return response, "EXECUTED"


# --------------------------------------------------------------------------
# 科研工作区 · 自由对话会话（宪法 §31）
#
# 与 `create_session` 的区别：对话会话由**项目**隐式持有（一个项目一个用户一段
# 对话），不需要客户端构造 CommandEnvelope；因此本函数是「取或建」，且**复用**
# 领域聚合 `ResearchSession.create` 与 `_emit`（不另造一条落库路径）。
# --------------------------------------------------------------------------

#: 对话会话标题 —— 兼作「一个 (tenant, project, owner) 只有一段科研对话」的锚点。
CONVERSATION_TITLE = "科研对话"


def find_conversation_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> ResearchSessionRow | None:
    """查找该项目属于该 owner 的对话会话；不存在返回 `None`。"""
    return (
        db.query(ResearchSessionRow)
        .filter(
            ResearchSessionRow.tenant_id == tenant_id,
            ResearchSessionRow.project_id == project_id,
            ResearchSessionRow.owner_id == owner_id,
            ResearchSessionRow.title == CONVERSATION_TITLE,
        )
        .order_by(desc(ResearchSessionRow.created_at))
        .first()
    )


def get_or_create_conversation_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    trace: TraceContext,
) -> ResearchSessionRow:
    """取或建 `(tenant, project, owner)` 的**唯一**对话会话。

    幂等性来自「按 `CONVERSATION_TITLE` 查找」：并发/重复进入不会产生第二段对话。
    """
    existing = find_conversation_session(
        db, tenant_id=tenant_id, project_id=project_id, owner_id=owner_id
    )
    if existing is not None:
        return existing

    session_id = uuid.uuid4()
    agg = ResearchSession.create(
        session_id=session_id,
        tenant_id=tenant_id,
        project_id=project_id,
        owner_id=owner_id,
        title=CONVERSATION_TITLE,
        mode=SessionMode.MODELING,
        policy_refs=["policy:research.default@v1"],
        goal="",
        occurred_at=_now(),
    )
    row = ResearchSessionRow(
        id=agg.id,
        tenant_id=agg.tenant_id,
        project_id=agg.project_id,
        owner_id=agg.owner_id,
        title=agg.title,
        mode=agg.mode.value,
        goal=agg.goal,
        status=agg.status.value,
        revision=agg.revision,
        created_at=agg.created_at,
        updated_at=agg.updated_at,
        current_artifact_refs=agg.current_artifact_refs,
        summary_ref=agg.summary_ref,
        policy_refs=agg.policy_refs,
        confirmation_count=0,
        last_event_id=None,
    )
    db.add(row)
    db.flush()

    ev = DomainEvent.create(
        event_type="session.created",
        session_id=agg.id,
        revision=agg.revision,
        payload={
            "title": agg.title,
            "mode": agg.mode.value,
            "status": agg.status.value,
            "kind": "conversation",
        },
        trace_id=trace.trace_id,
        actor_id=owner_id,
    )
    _emit(db, ev)
    row.last_event_id = ev.event_id
    _regenerate_summary(db, row, trace)
    db.flush()
    return row


# --------------------------------------------------------------------------
# 写：transition
# --------------------------------------------------------------------------


def transition(
    db: Session,
    *,
    session_id: uuid.UUID,
    cmd: ResearchCommandEnvelope,
    payload: dict,
    trace: TraceContext,
    tenant_id: uuid.UUID,
) -> tuple[dict, str]:
    existing = _find_idempotency(db, cmd, tenant_id)
    if existing is not None:
        _check_idempotency_conflict(existing, cmd, payload)
        return existing.response_body or {}, "REPLAYED"

    row = _load(db, session_id)
    agg = _to_aggregate(row)

    if cmd.expected_revision != agg.revision:
        raise RevisionConflict(cmd.expected_revision, agg.revision, _snapshot_ref(agg.id))

    target = SessionState(payload["to"])
    partial_reason = payload.get("partial_reason")

    # 显式拦截：PARTIAL -> COMPLETED（即便状态机已拦，这里给出明确错误码）
    if agg.status is SessionState.PARTIAL and target is SessionState.COMPLETED:
        raise PartialIsNotCompleted("PARTIAL 不是完成态，不得直接转 COMPLETED")
    if target is SessionState.PARTIAL and partial_reason is None:
        raise PartialReasonRequired("进入 PARTIAL 必须提供 partial_reason")

    # RV15 · Blocking gate：P0/P1 未解决 finding ⇒ 不可 COMPLETED（BLOCKING-GATE.md）。
    # 只拦状态迁移，**不删除任何 artifact**。
    if target is SessionState.COMPLETED:
        from .review.blocking import assert_no_blocking_findings

        assert_no_blocking_findings(db, session_id=session_id)

    try:
        can_transition(
            agg.status,
            target,
            confirmation_count=agg.confirmation_count,
            partial_reason=partial_reason,
        )
    except ConfirmationRequired:
        raise
    except InvalidTransition as e:
        raise InvalidTransition(str(e)) from e

    now = _now()
    from_status = agg.status
    row.status = target.value
    row.revision = agg.revision + 1
    row.updated_at = now
    # 离开 WAITING_CONFIRMATION 即计一次 confirmation 证据
    if from_status is SessionState.WAITING_CONFIRMATION and target is SessionState.ACTIVE:
        row.confirmation_count = agg.confirmation_count + 1
    db.flush()

    ev = DomainEvent.create(
        event_type="session.state_changed",
        session_id=row.id,
        revision=row.revision,
        payload={
            "from": from_status.value,
            "to": target.value,
            "partial_reason": partial_reason,
        },
        trace_id=trace.trace_id,
        actor_id=uuid.UUID(cmd.actor_id),
    )
    _emit(db, ev)
    row.last_event_id = ev.event_id

    if target is SessionState.WAITING_CONFIRMATION:
        _create_pending_action(db, row, cmd, trace, tenant_id)

    _regenerate_summary(db, row, trace)
    db.flush()

    response = {
        "session": _to_aggregate(row).snapshot(),
        "last_event_id": ev.event_id,
        "revision": row.revision,
        "trace_id": trace.trace_id,
    }
    _save_idempotency(db, cmd, tenant_id, response, payload)
    return response, "EXECUTED"


# --------------------------------------------------------------------------
# PendingAction
# --------------------------------------------------------------------------


def _create_pending_action(
    db: Session, row: ResearchSessionRow, cmd: ResearchCommandEnvelope, trace: TraceContext, tenant_id: uuid.UUID
) -> PendingActionRow:
    pa = PendingActionRow(
        id=uuid.uuid4(),
        session_id=row.id,
        tenant_id=tenant_id,
        action_type="confirm_state_change",
        payload={"status": row.status, "revision": row.revision},
        status="PENDING",
        revision=row.revision,
        required_capability="research.session.write",
        created_at=_now(),
        expires_at=_now() + timedelta(hours=PENDING_TTL_HOURS),
    )
    db.add(pa)
    db.flush()
    db.add(
        PendingActionAuditRow(
            pending_action_id=pa.id,
            action="CREATED",
            actor_id=uuid.UUID(cmd.actor_id),
            trace_id=trace.trace_id,
            at=_now(),
            detail={"revision": row.revision, "status": row.status},
        )
    )
    db.add(
        DomainEventRow(
            event_id=f"evt-{uuid.uuid4().hex[:32]}",
            session_id=row.id,
            event_type="session.pending_action_created",
            schema_version="1.0.0",
            revision=row.revision,
            occurred_at=_now(),
            trace_id=trace.trace_id,
            payload={"pending_action_id": str(pa.id)},
            actor_id=uuid.UUID(cmd.actor_id),
        )
    )
    return pa


def _resolve_pending(
    db: Session,
    *,
    session_id: uuid.UUID,
    pending_id: uuid.UUID,
    action: str,
    ctx_capabilities: list[str],
    actor_id: uuid.UUID,
    trace: TraceContext,
    expected_revision: int | None = None,
    detail: dict | None = None,
    transition_to_active: bool = False,
) -> dict:
    pa = db.get(PendingActionRow, pending_id)
    if pa is None or pa.session_id != session_id:
        raise PendingActionNotFound(f"pending action 不存在: {pending_id}")

    row = _load(db, session_id)

    # 1) 过期判定优先于 revision（过期的 action 不得因 revision 巧合而被接受）
    if pa.status == "PENDING" and pa.expires_at <= _now():
        pa.status = "EXPIRED"
        pa.resolved_at = _now()
        db.add(
            PendingActionAuditRow(
                pending_action_id=pa.id, action="EXPIRED", actor_id=actor_id,
                trace_id=trace.trace_id, at=_now(), detail={"reason": "ttl"},
            )
        )
        db.flush()
        raise PendingActionExpired(f"pending action 已过期: {pa.expires_at.isoformat()}")

    if pa.status != "PENDING":
        raise PendingActionExpired(f"pending action 已处于 {pa.status}")

    # 2) 接受前**重新校验 capability**
    if pa.required_capability and pa.required_capability not in ctx_capabilities:
        raise CapabilityMissing(f"缺少 capability: {pa.required_capability}")

    # 3) 接受前**重新校验 revision**
    if expected_revision is not None and expected_revision != row.revision:
        raise RevisionConflict(
            expected_revision,
            row.revision,
            _snapshot_ref(row.id),
            conflict=_conflict_material(db, pa),
        )

    pa.status = action
    pa.resolved_at = _now()
    pa.resolved_by = actor_id
    db.add(
        PendingActionAuditRow(
            pending_action_id=pa.id,
            action=action,
            actor_id=actor_id,
            trace_id=trace.trace_id,
            at=_now(),
            detail=detail or {},
        )
    )

    if transition_to_active and row.status == SessionState.WAITING_CONFIRMATION.value:
        now = _now()
        row.status = SessionState.ACTIVE.value
        row.revision += 1
        row.updated_at = now
        row.confirmation_count += 1
        db.flush()
        ev = DomainEvent.create(
            event_type="session.pending_action_accepted",
            session_id=row.id,
            revision=row.revision,
            payload={"pending_action_id": str(pa.id), "to": "ACTIVE"},
            trace_id=trace.trace_id,
            actor_id=actor_id,
        )
        _emit(db, ev)
        row.last_event_id = ev.event_id
        _regenerate_summary(db, row, trace)

    db.flush()
    return {
        "pending_action": {
            "id": str(pa.id),
            "status": pa.status,
            "session_id": str(pa.session_id),
            "revision": pa.revision,
        },
        "session": _to_aggregate(row).snapshot(),
    }


#: object_ref → 修订链表（`revision_diff` / 冲突素材按同一处取表，避免两套口径）。
_REVISION_TABLES: dict[str, str] = {
    "problem_map": "ProblemMapRow",
    "research_map": "ResearchMapRow",
    "route_arena": "RouteArenaRow",
    "intake": "ResearchIntakeRow",
    "blueprint": "ResearchBlueprintRow",
    "model_spec": "ModelSpecRow",
    "experiment_plan": "ExperimentPlanRow",
    "paper_spec": "PaperSpecRow",
    "claim": "ClaimRow",
}


def revision_table(object_ref: str):
    from ..db import models as M

    name = _REVISION_TABLES.get(object_ref)
    return getattr(M, name) if name else None


def _conflict_material(db, pa: PendingActionRow) -> dict:
    """冲突双方内容 + 结构化 diff（供前端并排显示）。

    `incoming` 是**尚未落库的提案**，因此 `revision` 为 None ——
    把它写成当前修订号会让用户以为"它已经在那儿了"。
    """
    from ..domain.revision_diff import diff_content

    object_ref = pa.object_ref or ""
    incoming = dict((pa.payload or {}).get("content") or {})
    table = revision_table(object_ref)
    current_row = None
    if table is not None:
        current_row = (
            db.query(table)
            .filter(table.session_id == pa.session_id)
            .order_by(table.revision.desc())
            .first()
        )
    current = None
    if current_row is not None:
        current = {
            "revision": int(current_row.revision),
            "objectId": str(current_row.id),
            "contentHash": current_row.content_hash or "",
            "content": dict(current_row.content or {}),
        }
    diff = diff_content((current or {}).get("content") or {}, incoming)
    conflict = {
        "objectRef": object_ref,
        "pendingActionId": str(pa.id),
        "baseRevision": pa.revision,
        "current": current,
        "incoming": {"pendingActionId": str(pa.id), "revision": None, "content": incoming},
        "diff": diff.as_dict(),
        "resolvable": bool(current) or bool(incoming),
    }
    if object_ref == "paper_spec":
        # RV13 · 论文冲突必须给**节点级** diff（块树按 id 配对，索引 diff 无意义）
        from ..paper.diff import diff_nodes
        from ..paper.nodes import validate_blocks

        def _blocks_of(content: dict) -> list:
            return [b for b in (content or {}).get("blocks") or [] if isinstance(b, dict)]

        try:
            current_blocks = (
                validate_blocks(_blocks_of((current or {}).get("content") or {}), stored=True)
                if current
                else []
            )
            if _blocks_of(incoming):
                incoming_blocks = validate_blocks(_blocks_of(incoming), stored=True)
            elif incoming.get("ops"):
                # ops 形状：投影到当前块树上，让用户看到"这份 patch 会造成什么"
                from ..paper.patch import apply_ops, validate_ops

                incoming_blocks = apply_ops(
                    current_blocks,
                    validate_ops(
                        incoming["ops"],
                        current_blocks=current_blocks,
                        resolvable_evidence=set(),
                        resolvable_claims=set(),
                        claim_status={},
                        resolvable_citations=set(),
                        resolvable_anchors=set(),
                    ),
                )
            else:
                incoming_blocks = []
            conflict["nodeDiff"] = diff_nodes(current_blocks, incoming_blocks)
        except Exception:  # noqa: BLE001 - 冲突展示失败不得掩盖 409 本身
            conflict["nodeDiff"] = {"added": [], "removed": [], "changed": [], "moved": [],
                                    "count": 0, "empty": True, "truncated": False}
    return conflict


def revise_pending(db, *, session_id, pending_id, ctx_capabilities, actor_id, trace, body) -> dict:
    """逐项 accept / reject / edit 一个**尚未确认**的提案。

    语义边界（重要）：本函数只改 `PendingAction.payload` 里的**提案草稿**，
    不触碰任何正式状态。正式状态仍然只在 `accept_pending()` 里写。
    这样"逐项编辑"与"确认落库"是两个可分别审计的动作。
    """
    pa = db.get(PendingActionRow, pending_id)
    if pa is None or pa.session_id != session_id:
        raise PendingActionNotFound(f"pending action 不存在: {pending_id}")
    if pa.status != "PENDING":
        raise PendingActionExpired(f"pending action 已处于 {pa.status}")
    if pa.expires_at <= _now():
        raise PendingActionExpired(f"pending action 已过期: {pa.expires_at.isoformat()}")
    if pa.required_capability and pa.required_capability not in ctx_capabilities:
        raise CapabilityMissing(f"缺少 capability: {pa.required_capability}")

    object_ref = pa.object_ref or ""
    payload = dict(pa.payload or {})
    content = dict(payload.get("content") or {})

    if object_ref == "problem_map":
        from ..domain import problem_map as PM

        try:
            content = PM.apply_corrections(
                content,
                accept=body.get("accept"),
                reject=body.get("reject"),
                edits=body.get("edits"),
            )
        except PM.ProblemMapRejected as e:
            # 领域拒绝码（例如"不允许改 origin"）必须原样透出给调用方，
            # 否则前端只能看到一句无信息量的 500。
            # `DomainError(message, **detail)`：错误码走 detail，而不是位置参数。
            raise DomainError(e.message, code=e.code, object_ref=object_ref) from e
    elif object_ref == "route_arena":
        # RV10：路线可整体 reject（≥2 有效线重校验）、字段可编辑（origin/kind/status 禁改）。
        # 编辑后服务端重算 rubric——比较矩阵永远由服务端推导。
        from ..domain import route_arena as RA

        try:
            content = RA.apply_corrections(
                content,
                accept=body.get("accept"),
                reject=body.get("reject"),
                edits=body.get("edits"),
            )
        except RA.RouteArenaRejected as e:
            raise DomainError(e.message, code=e.code, object_ref=object_ref) from e
    elif object_ref == "research_map":
        if body.get("accept") or body.get("reject") or body.get("edits"):
            # 视角是整体结构（依赖是否成环取决于全体），逐元素删改必须整体重校验；
            # 与其给出一个"改完之后可能成环"的半成品，不如明确拒绝并要求重新生成。
            raise DomainError(
                "EDIT_NOT_SUPPORTED_FOR_OBJECT",
                "research_map 的视角是整体结构，不支持逐元素编辑；请重新生成提案",
                object_ref=object_ref,
            )
    elif object_ref == "paper_spec":
        # RV13.1 · paper patch 逐项处置：accept / reject / edits 按 **op 下标** 寻址
        # （与 RV08 section-ref 不同，patch op 没有稳定业务 ref，下标即地址）。
        # 语义与 RV09 一致：整体替换、只改提案草稿；正式落库仍在 `:accept` ——
        # 那里会把"处置合并后的 ops"按**当前事实**再过一遍四道门。
        from ..paper.errors import PaperSchemaError
        from ..paper.patch import apply_review

        if not content.get("ops"):
            raise DomainError(
                "EDIT_NOT_SUPPORTED_FOR_OBJECT",
                "legacy blocks 形状的 paper patch 不支持逐项编辑；请重新生成提案",
                object_ref=object_ref,
            )
        review = {
            "accepted": list(body.get("accept") or []),
            "rejected": list(body.get("reject") or []),
            "edits": [dict(e) for e in (body.get("edits") or []) if isinstance(e, dict)],
        }
        try:
            apply_review([dict(o) for o in content.get("ops") or []], review)
        except PaperSchemaError as e:
            # 结构检查失败（索引越界 / 矛盾 / 拒绝后断链）原样透出拒绝码
            raise DomainError(e.message, code=e.code, object_ref=object_ref) from e
        content = {**content, "review": review}
    else:
        raise DomainError(
            "EDIT_NOT_SUPPORTED_FOR_OBJECT",
            f"object_ref={object_ref!r} 不支持逐项编辑",
            object_ref=object_ref,
        )

    payload["content"] = content
    payload["content_hash"] = _canonical_digest_of_content(content)
    pa.payload = payload
    db.add(
        PendingActionAuditRow(
            pending_action_id=pa.id,
            action="REVISED",
            actor_id=actor_id,
            trace_id=trace.trace_id,
            at=_now(),
            detail={
                "accepted": len(body.get("accept") or []),
                "rejected": len(body.get("reject") or []),
                "edited": len(body.get("edits") or []),
            },
        )
    )
    db.flush()
    return {
        "pending_action": {
            "id": str(pa.id),
            "status": pa.status,
            "session_id": str(pa.session_id),
            "revision": pa.revision,
            "object_ref": object_ref,
        },
        "proposal": {"objectRef": object_ref, "content": content},
    }


def _touch_checkpoint_after_confirm(
    db, *, session_id, tenant_id, object_ref: str, revision: int
) -> None:
    """确认之后推进短期记忆 checkpoint。

    为什么必须在这里做：`send_message` 写 checkpoint 时提案还只是提案 ——
    那时 `lastProblemMapRevision` 必然是 `None`。若不在确认时推进，
    刷新页面读到的 checkpoint 会说"还没有问题地图"，而库里明明已经有第 1 版。
    两处不一致时用户只会相信界面，于是他会以为确认没生效。
    """
    from ..memory import short_term as memory

    existing = memory.load_checkpoint(db, session_id=session_id, tenant_id=tenant_id)
    state = dict(existing.state) if existing is not None else {}
    state["stage"] = f"CONFIRMED:{object_ref}"
    state["lastIntent"] = f"confirm:{object_ref}"
    state["openQuestions"] = []
    if object_ref == "problem_map":
        state["lastProblemMapRevision"] = revision
    elif object_ref == "research_map":
        state["lastResearchMapRevision"] = revision
    memory.save_checkpoint(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        last_message_seq=int(existing.last_message_seq) if existing is not None else 0,
        state=state,
    )


def _canonical_digest_of_content(content: dict) -> str:
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_revision_audit(db, *, pa: PendingActionRow, applied: dict, trace, actor_id) -> None:
    """写一条**只追加**的修订因果审计。

    为什么单列一张表而不是给修订行加列：修订行是**内容**（其 `content_hash`
    必须只由内容决定），审计行是**因果关系**。混在一张表里，加一个审计字段
    就会改变内容哈希，从而让"这条修订是否被改过"无法判断。
    """
    from ..db.models import ObjectRevisionAuditRow, ResearchAiRunRow
    from ..domain.revision_diff import diff_content

    table = revision_table(pa.object_ref)
    previous = None
    if table is not None and applied.get("revision", 0) > 1:
        previous = (
            db.query(table)
            .filter(table.session_id == pa.session_id)
            .order_by(table.revision.desc())
            .offset(1)
            .first()
        )

    run = None
    if pa.source_message_id is not None:
        run = (
            db.query(ResearchAiRunRow)
            .filter(ResearchAiRunRow.message_id == pa.source_message_id)
            .one_or_none()
        )

    content = dict((pa.payload or {}).get("content") or {})
    prev_content = dict((previous.content or {}) if previous is not None else {})
    manifest = dict(getattr(run, "context_manifest", None) or {})
    trims = sorted(
        {s.get("trimReason") for s in (manifest.get("sections") or []) if s.get("trimReason")}
        | {s.get("trimReason") for s in (manifest.get("excluded") or []) if s.get("trimReason")}
    )

    db.add(
        ObjectRevisionAuditRow(
            session_id=pa.session_id,
            tenant_id=pa.tenant_id,
            object_ref=pa.object_ref,
            object_id=uuid.UUID(str(applied["id"])),
            revision=int(applied["revision"]),
            previous_object_id=uuid.UUID(str(previous.id)) if previous is not None else None,
            previous_revision=int(previous.revision) if previous is not None else None,
            content_hash=str((pa.payload or {}).get("content_hash") or ""),
            diff=diff_content(prev_content, content).as_dict(),
            pending_action_id=pa.id,
            ai_run_id=getattr(run, "id", None),
            prompt_ref=getattr(run, "prompt_ref", "") or "",
            prompt_version=int(getattr(run, "prompt_version", 0) or 0),
            context_manifest_ref=str(manifest.get("manifestId") or ""),
            context_trim_reasons=trims,
            model=getattr(run, "model", "") or "",
            provider=getattr(run, "provider", "") or "",
            cost_usd=getattr(run, "cost_usd", None),
            cost_basis=getattr(run, "cost_basis", "") or "",
            event_id=str(applied.get("event_id") or ""),
            client_request_id=f"accept:{pa.id}",
            actor_id=actor_id,
            trace_id=trace.trace_id,
            created_at=_now(),
        )
    )


def accept_pending(db, *, session_id, pending_id, ctx_capabilities, actor_id, trace, expected_revision) -> dict:
    """确认门：**唯一**允许把 proposal 写成正式状态的入口。

    路由逻辑（CR-6 / RV09–RV13）：
    - `pending_action.object_ref` 非空 ⇒ 这是一条 typed proposal，
      走 `proposal_handlers.apply()` 写新的**不可变修订行**；
    - `object_ref` 为空 ⇒ 这是 RV02 的 `confirm_state_change`，走原状态机迁移。
    """
    body = _resolve_pending(
        db, session_id=session_id, pending_id=pending_id, action="ACCEPTED",
        ctx_capabilities=ctx_capabilities, actor_id=actor_id, trace=trace,
        expected_revision=expected_revision, detail={"via": "accept"},
        transition_to_active=True,
    )

    pa = db.get(PendingActionRow, pending_id)
    object_ref = (pa.object_ref or "") if pa is not None else ""
    if not object_ref:
        return body

    # --- typed proposal：写正式状态（新修订行） ---
    from ..domain import proposal_handlers as H

    content = dict((pa.payload or {}).get("content") or {})
    try:
        applied = H.apply(
            db,
            session_id=session_id,
            tenant_id=pa.tenant_id,
            actor_id=actor_id,
            object_ref=object_ref,
            content=content,
            client_request_id=f"accept:{pa.id}",
            trace_id=trace.trace_id,
        )
    except H.ProposalRejected as e:
        # 确认时**复检**失败（例如证据在确认前失效）⇒ 拒绝并留痕。
        # 这是必需的第二道闸：第一道在提议时，状态可能已经变了。
        pa.status = "REJECTED_STALE"
        pa.resolved_at = _now()
        pa.resolved_by = actor_id
        db.add(
            PendingActionAuditRow(
                pending_action_id=pa.id, action="REJECTED_STALE", actor_id=actor_id,
                trace_id=trace.trace_id, at=_now(),
                detail={"code": e.code, "message": e.message},
            )
        )
        db.flush()
        raise IdempotencyConflict(  # 409：需要重新生成提案，而不是重试同一个
            f"确认时复检失败（{e.code}）：{e.message}。请重新生成提案。"
        ) from e
    row = _load(db, session_id)
    ev = DomainEvent.create(
        event_type="session.proposal_confirmed",
        session_id=session_id,
        revision=row.revision,
        payload={
            "pending_action_id": str(pa.id),
            "object_ref": object_ref,
            "object_id": applied["id"],
            "object_revision": applied["revision"],
        },
        trace_id=trace.trace_id,
        actor_id=actor_id,
    )
    _emit(db, ev)
    row.last_event_id = ev.event_id

    # RV10 · BlueprintReady（供 RV11 实验执行消费）：
    # **只在** research_blueprint 形状被确认后发布；事件只携带
    # ID / 版本 / 哈希 / 路线引用——大上下文留在修订行里，
    # 事件流不做第二份真相（总手册 §13 事件瘦身约定）。
    if object_ref == "blueprint" and str((pa.payload or {}).get("proposal_kind") or "") == "research_blueprint":
        content = dict((pa.payload or {}).get("content") or {})
        binds = dict(content.get("binds") or {})
        ready = DomainEvent.create(
            event_type="research.blueprint_ready",
            session_id=session_id,
            revision=row.revision,
            payload={
                "blueprintId": applied["id"],
                "revision": applied["revision"],
                "contentHash": applied.get("content_hash") or str((pa.payload or {}).get("content_hash") or ""),
                "routeId": str(content.get("routeId") or ""),
                "problemMapRevision": int(binds.get("problemMap", {}).get("revision") or 0),
                "researchMapRevision": int(binds.get("researchMap", {}).get("revision") or 0),
                "routeArenaRevision": int(binds.get("routeArena", {}).get("revision") or 0),
                "routeArenaContentHash": str(binds.get("routeArena", {}).get("contentHash") or ""),
            },
            trace_id=trace.trace_id,
            actor_id=actor_id,
        )
        _emit(db, ready)
        row.last_event_id = ready.event_id
    db.flush()

    # RV09 · 修订因果审计（只追加）：谁确认的、依据哪次 AI run、哪个 prompt 版本、
    # 上下文裁了什么、花了多少钱、对应哪个 event。
    applied["event_id"] = ev.event_id
    _write_revision_audit(db, pa=pa, applied=applied, trace=trace, actor_id=actor_id)
    _touch_checkpoint_after_confirm(
        db, session_id=session_id, tenant_id=pa.tenant_id, object_ref=object_ref, revision=applied["revision"]
    )
    db.flush()

    body["confirmed_object"] = applied
    body["last_event_id"] = ev.event_id
    body["revision"] = row.revision
    return body


def reject_pending(db, *, session_id, pending_id, ctx_capabilities, actor_id, trace, reason) -> dict:
    return _resolve_pending(
        db, session_id=session_id, pending_id=pending_id, action="REJECTED",
        ctx_capabilities=ctx_capabilities, actor_id=actor_id, trace=trace,
        expected_revision=None, detail={"reason": reason}, transition_to_active=False,
    )


def cancel_pending(db, *, session_id, pending_id, ctx_capabilities, actor_id, trace, reason) -> dict:
    return _resolve_pending(
        db, session_id=session_id, pending_id=pending_id, action="CANCELLED",
        ctx_capabilities=ctx_capabilities, actor_id=actor_id, trace=trace,
        expected_revision=None, detail={"reason": reason}, transition_to_active=False,
    )


def list_pending(db, session_id: uuid.UUID) -> list[dict]:
    """列出**仍待处理**的确认门。

    修复（CR-6 期间发现）：此前不过滤状态，`/pending-actions` 会把已 ACCEPTED /
    REJECTED / EXPIRED 的历史项一并返回。那不只是"多返回几行"——调用方会拿
    列表里的第一项去 accept，而它早已被接受，于是必然 409。列表端点返回
    "待处理项"是它的名字承诺的语义；历史留痕走审计接口。
    """
    rows = (
        db.query(PendingActionRow)
        .filter(
            PendingActionRow.session_id == session_id,
            PendingActionRow.status == "PENDING",
        )
        .order_by(PendingActionRow.created_at)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "session_id": str(r.session_id),
            "action_type": r.action_type,
            "status": r.status,
            "revision": r.revision,
            "required_capability": r.required_capability,
            "object_ref": r.object_ref or "",
            "proposal_kind": ((r.payload or {}).get("proposal_kind") or ""),
            # RV09：把提案内容一并返回，前端才能渲染"可逐项 accept/reject/edit"
            # 的 living map 卡。没有它，用户只能看到"有个提案"，看不到内容。
            "payload": dict(r.payload or {}),
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# 读：snapshot / resume / summary
# --------------------------------------------------------------------------


def get_snapshot(db: Session, session_id: uuid.UUID) -> dict:
    row = _load(db, session_id)
    agg = _to_aggregate(row)
    return {
        "session": agg.snapshot(),
        "revision": row.revision,
        "summary_ref": row.summary_ref,
        "trace_hint": row.last_event_id,
    }


def _parse_numeric_cursor(raw: str | None) -> int | None:
    """把纯数值 `Last-Event-ID` 解析成水位（`domain_event.id`）。"""
    if raw is None:
        return None
    text = raw.strip()
    if not text or not text.isdigit():
        return None
    try:
        return int(text)
    except ValueError:  # pragma: no cover - isdigit 已保证
        return None


def _resolve_cursor(
    db: Session, session_id: uuid.UUID, raw: str | None
) -> tuple[int, datetime] | None:
    """把客户端回传的游标解析成 `(数值水位, 该事件发生时间)`。支持两种形态：

    1. **纯数值** = `domain_event.id`（FIX-A2 之后 SSE 实际发出的形态，必然可续传）；
    2. **`evt-*` 字符串** = 历史/外部调用方仍在使用的 `event_id`。这里查库换算成数值，
       从而**保留向后兼容**，不因游标格式升级而打断既有调用方。

    第 2 种形态**即使该事件已落在保留窗口之外**也要返回其结果：调用方需要的是
    "窗口外 → SNAPSHOT_REQUIRED"这个诚实结论，而"认不出这个 id"与"窗口外"是两回事。
    游标事件压根不存在（或完全无法解析，如合成 `cursor-*`/`snap-*`）时返回 None。
    """
    numeric = _parse_numeric_cursor(raw)
    if numeric is not None:
        row = (
            db.query(DomainEventRow.id, DomainEventRow.occurred_at)
            .filter(DomainEventRow.session_id == session_id, DomainEventRow.id == numeric)
            .one_or_none()
        )
        return (int(row[0]), row[1]) if row is not None else None

    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    row = (
        db.query(DomainEventRow.id, DomainEventRow.occurred_at)
        .filter(DomainEventRow.session_id == session_id, DomainEventRow.event_id == text)
        .one_or_none()
    )
    return (int(row[0]), row[1]) if row is not None else None


def _ev_dict(e: DomainEventRow) -> dict:
    return {
        # `id` = 数值主键，是 SSE 的续传水位（唯一可续传的标识）
        "id": e.id,
        "event_id": e.event_id,
        "event_type": e.event_type,
        "schema_version": e.schema_version,
        "session_id": str(e.session_id),
        "revision": e.revision,
        "occurred_at": e.occurred_at.isoformat(),
        "trace_id": e.trace_id,
        "payload": e.payload or {},
        "actor_id": str(e.actor_id) if e.actor_id else None,
        "aggregate_type": e.aggregate_type,
    }


def _retention_window(db: Session, session_id: uuid.UUID) -> list[DomainEventRow]:
    """保留窗口 = **条数** 与 **时间** 双重上限，任一超界即视为窗口外。"""
    cutoff = _now() - timedelta(hours=sse_retention_hours())
    rows = (
        db.query(DomainEventRow)
        .filter(
            DomainEventRow.session_id == session_id,
            DomainEventRow.occurred_at >= cutoff,
        )
        .order_by(desc(DomainEventRow.id))
        .limit(sse_retention_events())
        .all()
    )
    return list(reversed(rows))


def resume(db: Session, session_id: uuid.UUID, last_event_id: str | None) -> dict:
    """断线续传：窗口内增量，窗口外强制 snapshot 回退。

    `last_event_id` 是客户端回传的 `Last-Event-ID`，修复后 SSE 一律发出**真实的数值
    `domain_event.id`**，因此客户端回传的值必然可续传。

    **判定规则（统一一条"窗口"规则，FIX-A2）**：令窗口 =「时间下界之内」∩「最新 N 条」
    的**已保留**事件集合，
      `window_floor` = 窗口内最小 id、`window_ceiling` = 窗口内最大 id（即"已应用到哪里"）。

    - 游标落在 `[window_floor, window_ceiling]` → `NONE`，只返回其后的增量事件
      （没有更新的事件时返回空列表 —— 空增量**不是**丢事件）。
    - 游标 **< window_floor** → 游标与窗口之间的中间事件已被裁掉 → `SNAPSHOT_REQUIRED`。
    - 游标 **> window_ceiling** → 服务端从未发出过这个 id（事件被裁掉/不存在/伪造），
      无法证明连续 → `SNAPSHOT_REQUIRED`。
    - 窗口为空（如 `RESEARCH_SSE_RETENTION_EVENTS=0`）→ 游标不可能落在窗口内 → 快照。

    绝不伪造连续：任何"无法证明没丢事件"的情形都回落到快照；反之，**能证明没丢**的
    情形（游标在窗口内）绝不误报快照 —— 否则每次自动重连都会全量回退。
    """
    row = _load(db, session_id)
    agg = _to_aggregate(row)
    recent = _retention_window(db, session_id)
    window_floor = recent[0].id if recent else None
    window_ceiling = recent[-1].id if recent else None
    latest_cursor = window_ceiling or 0

    def _snapshot_required(reason: str) -> dict:
        return {
            "session": agg.snapshot(),
            "revision": row.revision,
            "fallback": "SNAPSHOT_REQUIRED",
            "events": [],
            "snapshot_ref": _snapshot_ref(row.id),
            "reason": reason,
            "cursor": latest_cursor,
            "stream_epoch": 0,
        }

    if last_event_id is None:
        return {
            "session": agg.snapshot(),
            "revision": row.revision,
            "fallback": "NONE",
            "events": [_ev_dict(e) for e in recent],
            "cursor": latest_cursor,
            "stream_epoch": 0,
        }

    cursor = _resolve_cursor(db, session_id, last_event_id)
    if cursor is None:
        return _snapshot_required(
            f"Last-Event-ID {last_event_id!r} 不是可续传的游标"
            "（既不是数值水位，也不对应本会话的已知事件），无法证明事件连续"
        )
    cursor_id, _cursor_at = cursor

    if window_floor is None:
        return _snapshot_required(
            f"保留窗口为空（{sse_retention_events()} 条 / {sse_retention_hours()} 小时），"
            f"游标 {cursor_id} 无法落在窗口内，无法证明事件连续"
        )

    if cursor_id < window_floor:
        return _snapshot_required(
            f"游标 {cursor_id} 早于保留窗口下界 {window_floor}"
            f"（{sse_retention_events()} 条 / {sse_retention_hours()} 小时），中间事件已被裁掉"
        )

    if cursor_id > window_ceiling:
        return _snapshot_required(
            f"游标 {cursor_id} 超出服务端已发出的最大事件 id {window_ceiling}"
            "（事件不存在或已被裁掉），无法证明事件连续"
        )

    return {
        "session": agg.snapshot(),
        "revision": row.revision,
        "fallback": "NONE",
        "events": [_ev_dict(e) for e in recent if e.id > cursor_id],
        "cursor": latest_cursor,
        "stream_epoch": 0,
    }


def events_after(
    db: Session, session_id: uuid.UUID, after_id: int = 0, limit: int = 200
) -> list[dict]:
    """返回 `id > after_id` 的事件（**由数据库过滤**，不在 Python 里线性扫描）。

    FIX-A2：修复前 SSE 恒定调用 `after_id=0, limit=500`，即每次都把会话最早 500 条
    事件拉出来再用字符串游标在内存里比对 —— 超过 500 条时游标可能根本不在结果集里，
    于是要么重复下发，要么丢事件。现在传入真实水位，增量路径才真正生效。
    """
    rows = (
        db.query(DomainEventRow)
        .filter(DomainEventRow.session_id == session_id, DomainEventRow.id > after_id)
        .order_by(DomainEventRow.id)
        .limit(limit)
        .all()
    )
    return [_ev_dict(e) for e in rows]


def get_summary(db: Session, session_id: uuid.UUID) -> dict:
    row = _load(db, session_id)
    latest = (
        db.query(SessionSummaryRow)
        .filter(SessionSummaryRow.session_id == session_id)
        .order_by(desc(SessionSummaryRow.summary_version))
        .first()
    )
    if latest is None:
        raise SessionNotFound("summary 不存在")
    return {
        "session_id": str(session_id),
        "summary_version": latest.summary_version,
        "contract_version": latest.contract_version,
        "generated_by": latest.generated_by,
        "content": latest.content,
        "content_hash": latest.content_hash,
        "created_at": latest.created_at.isoformat(),
    }


def summary_history(db: Session, session_id: uuid.UUID) -> list[dict]:
    rows = (
        db.query(SessionSummaryRow)
        .filter(SessionSummaryRow.session_id == session_id)
        .order_by(SessionSummaryRow.summary_version)
        .all()
    )
    return [
        {
            "summary_version": r.summary_version,
            "generated_by": r.generated_by,
            "content": r.content,
            "content_hash": r.content_hash,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def set_security_floor(db: Session, actor_id: uuid.UUID, min_security_version: int) -> None:
    row = db.get(ActorSecurityFloorRow, actor_id)
    if row is None:
        db.add(
            ActorSecurityFloorRow(
                actor_id=actor_id, min_security_version=min_security_version, updated_at=_now()
            )
        )
    else:
        row.min_security_version = min_security_version
        row.updated_at = _now()
    db.flush()


def get_security_floor(db: Session, actor_id: uuid.UUID) -> int:
    row = db.get(ActorSecurityFloorRow, actor_id)
    return row.min_security_version if row else 1
