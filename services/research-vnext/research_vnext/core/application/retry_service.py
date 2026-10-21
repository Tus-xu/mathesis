"""「重试这一轮」—— 单一供应商下失败必须**可恢复**。

## 为什么这是一个一等公民而不是附属功能

本平台只有**一家** LLM 供应商、**一个**账号（用户有意统一，见
`MODEL-INVENTORY.md` §3–§4）。因此没有供应商级容灾：该账号不可用、被限流、
或凭据配错时，**所有** LLM 路径一起失败。

在这种拓扑下,"失败"不是异常情况而是**预期会发生的正常运行状态**。
因此必须满足三件事（缺一不可）：

1. 用户消息**已持久化**（`send_message` 的 tx1 已经保证，且立即提交）；
2. 失败**被记录**（`research_ai_run.status = FAILED|REJECTED` + `error_code`）；
3. 这一轮**可以重试** —— 否则研究者唯一的出路是重复输入一遍，
   而重复输入会产生一条多余的用户消息，把实验记录污染掉。

## 幂等性

重试是针对**某一条已存在的 user message** 的，因此天然幂等：
失败重试一次不会有第二条用户消息。重试成功后写入的 assistant 行
按新的 `seq` 追加在 timeline 末尾（不修改历史）。

## 不允许重试的情形

若该轮的 assistant 消息**已经携带提案**，说明这一轮其实成功了 ——
再跑一次会生成第二个提案，并可能产生第二个 PendingAction。
那种情况下要的是"重新生成提案"，不是"重试失败轮"，因此显式拒绝
（`TURN_ALREADY_SUCCEEDED`），让调用方走正常的再发一条消息。
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..db.models import (
    PendingActionRow,
    ResearchAiRunRow,
    ResearchMessageRow,
    ResearchSessionRow,
)
from ..domain import proposals as P
from ..domain import proposal_handlers as H
from ..domain.errors import DomainError, SessionNotFound
from ..domain.events import DomainEvent
from ..domain.snapshot import build_snapshot
from ..llm import capacity as cap
from ..llm import gateway
from ..llm.prompts import registry as prompts
from ..observability.trace import TraceContext
from ..application import session_service as svc
from . import message_service as ms


class TurnNotRetryable(DomainError):
    code = "TURN_NOT_RETRYABLE"
    http_status = 409


class TurnAlreadySucceeded(DomainError):
    code = "TURN_ALREADY_SUCCEEDED"
    http_status = 409


class MessageNotFound(DomainError):
    code = "MESSAGE_NOT_FOUND"
    http_status = 404


def _load_user_message(db: Session, session_id: uuid.UUID, message_id: uuid.UUID) -> ResearchMessageRow:
    row = db.get(ResearchMessageRow, message_id)
    if row is None or row.session_id != session_id:
        raise MessageNotFound(f"消息不存在: {message_id}")
    if row.role != "user":
        raise TurnNotRetryable(
            f"只有 user 消息对应的轮次可以重试，该消息 role={row.role!r}",
            message_id=str(message_id), role=row.role,
        )
    return row


def _assistant_for(db: Session, session_id: uuid.UUID, user_seq: int) -> ResearchMessageRow | None:
    """找到该轮的 assistant 行：同一会话中 seq 最小的、seq > user_seq 的 assistant。"""
    return (
        db.query(ResearchMessageRow)
        .filter(
            ResearchMessageRow.session_id == session_id,
            ResearchMessageRow.role == "assistant",
            ResearchMessageRow.seq > user_seq,
        )
        .order_by(ResearchMessageRow.seq)
        .first()
    )


def turn_state(db: Session, session_id: uuid.UUID, message_id: uuid.UUID) -> dict:
    """某一轮的**可重试状态**（供前端决定是否显示"重试"按钮）。"""
    user_msg = _load_user_message(db, session_id, message_id)
    assistant = _assistant_for(db, session_id, user_msg.seq)
    if assistant is None:
        return {"retryable": True, "reason": "assistant_missing", "message_id": str(message_id)}

    payload = assistant.payload or {}
    has_proposal = bool((payload.get("proposal") or {}).get("kind"))
    run = (
        db.query(ResearchAiRunRow)
        .filter(ResearchAiRunRow.message_id == assistant.id)
        .order_by(ResearchAiRunRow.created_at.desc())
        .first()
    )
    error_code = (run.error_code if run is not None else "") or ""
    return {
        "retryable": (not has_proposal) and bool(error_code),
        "reason": "" if has_proposal else (error_code or "no_error_recorded"),
        "message_id": str(message_id),
        "assistant_message_id": str(assistant.id),
        "ai_run_status": run.status if run is not None else "",
        "error_code": error_code,
        "has_proposal": has_proposal,
    }


async def retry_turn(
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    message_id: uuid.UUID,
    trace: TraceContext,
) -> tuple[dict, int]:
    """重跑某一轮的 assistant 侧。返回 `(snapshot, http_status)`。

    **不**新增 user 消息：研究者不必为了重试而重复输入。
    """
    from ..db.engine import session_scope

    # ---- 预检（只读） ----
    with session_scope() as db:
        user_msg = _load_user_message(db, session_id, message_id)
        user_text = user_msg.text
        user_seq = int(user_msg.seq)
        assistant = _assistant_for(db, session_id, user_seq)
        if assistant is not None:
            payload = assistant.payload or {}
            if (payload.get("proposal") or {}).get("kind"):
                raise TurnAlreadySucceeded(
                    "该轮已产出提案，无需重试；请直接再发一条消息以生成新提案。",
                    message_id=str(message_id),
                )
            stale_assistant_id = assistant.id
            # 该轮先前的失败残留（assistant 提示行 + 可能存在的 PendingAction）清掉，
            # 否则重试成功后 timeline 会同时出现两条 assistant 行。
            db.query(PendingActionRow).filter(
                PendingActionRow.session_id == session_id,
                PendingActionRow.source_message_id == stale_assistant_id,
            ).delete(synchronize_session=False)
            db.delete(assistant)

    # ---- 容量闸（与 send_message 同一道闸；在发出请求之前） ----
    capacity = cap.get_capacity()
    lease = capacity.acquire(user_id=actor_id, session_id=session_id)

    # ---- 重跑模型（事务外） ----
    prompt = prompts.PROPOSE
    backend = gateway.get_backend()
    run_status = "FAILED"
    error_code = ""
    model_name = backend.model
    provider_name = getattr(backend, "name", "unknown")
    in_tok = out_tok = latency = 0
    proposal = None

    messages = [
        {"role": "system", "content": prompt.system},
        {
            "role": "user",
            "content": prompt.render(
                user_text=prompts.wrap_untrusted(user_text),
                title="",
                mode="",
                timeline_len=str(user_seq),
            ),
        },
    ]
    try:
        completion = await backend.acompletion(model=None, messages=messages, stream=False)
        model_name = completion.model
        provider_name = completion.provider
        in_tok, out_tok, latency = (
            completion.input_tokens,
            completion.output_tokens,
            completion.latency_ms,
        )
        proposal = P.parse_proposal(completion.text)
        run_status = "SUCCEEDED"
    except gateway.LlmGateError as e:
        error_code = e.code
        run_status = "FAILED"
    except P.ProposalSchemaError as e:
        error_code = e.code
        run_status = "REJECTED"
    except Exception as e:  # noqa: BLE001
        error_code = f"UNEXPECTED_{type(e).__name__}"
        run_status = "FAILED"
    finally:
        capacity.release(lease)

    # ---- 写入（与 send_message 的 tx2 同构） ----
    with session_scope() as db:
        row = (
            db.query(ResearchSessionRow)
            .filter(ResearchSessionRow.id == session_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise SessionNotFound(f"会话不存在: {session_id}")

        prepared = None
        if run_status == "SUCCEEDED" and proposal is not None:
            try:
                prepared = H.prepare(
                    db, proposal, session_id=session_id, actor_id=actor_id, user_text=user_text
                )
            except H.ProposalRejected as e:
                error_code = e.code
                run_status = "REJECTED"
                prepared = None

        assistant_id = uuid.uuid4()
        run_id = uuid.uuid4()
        seq = ms._next_seq(db, session_id)

        if run_status == "SUCCEEDED" and proposal is not None:
            assistant_text = proposal.text.strip() or "（模型未给出文字说明）"
            payload = {
                "proposal": {
                    "kind": proposal.kind,
                    "requires_confirmation": bool(proposal.requires_confirmation),
                    "object_ref": prepared.object_ref if prepared else "",
                    "content": prepared.content if prepared else {},
                },
                "retry_of": str(message_id),
            }
            state, reason = ms.STATE_AVAILABLE, ""
        else:
            assistant_text = ms._notice(error_code, model_name)
            payload = {
                "notice": {"error_code": error_code, "model": model_name},
                "retry_of": str(message_id),
            }
            state = (
                ms.STATE_UNAVAILABLE
                if error_code == "LLM_NOT_CONFIGURED"
                else ms.STATE_DISABLED
            )
            reason = ms._reason_code(error_code)

        db.add(
            ResearchMessageRow(
                id=assistant_id, session_id=session_id, tenant_id=tenant_id,
                role="assistant", text=assistant_text, payload=payload,
                evidence_refs=list(proposal.evidence_refs) if proposal else [],
                seq=seq, created_at=ms._now(), created_by_run_id=run_id,
                trace_id=trace.trace_id, client_request_id="",
            )
        )
        db.flush()
        db.add(
            ResearchAiRunRow(
                id=run_id, session_id=session_id, tenant_id=tenant_id,
                message_id=assistant_id, provider=provider_name, model=model_name,
                prompt_ref=prompt.ref, status=run_status, error_code=error_code,
                input_tokens=in_tok, output_tokens=out_tok, latency_ms=latency,
                trace_id=trace.trace_id, created_at=ms._now(),
            )
        )

        pending_id = None
        if prepared is not None and proposal is not None and proposal.requires_confirmation:
            from datetime import timedelta

            pa = PendingActionRow(
                id=uuid.uuid4(), session_id=session_id, tenant_id=tenant_id,
                action_type=f"confirm_{prepared.object_ref}",
                payload={
                    "proposal_kind": proposal.kind,
                    "object_ref": prepared.object_ref,
                    "content": prepared.content,
                    "content_hash": prepared.content_hash,
                    "assistant_text": assistant_text,
                },
                status="PENDING", revision=row.revision,
                required_capability="research.session.write",
                object_ref=prepared.object_ref, source_message_id=assistant_id,
                created_at=ms._now(), expires_at=ms._now() + timedelta(hours=ms.PENDING_TTL_HOURS),
            )
            db.add(pa)
            db.flush()
            pending_id = str(pa.id)

        row.revision += 1
        row.updated_at = ms._now()
        db.flush()
        ev = DomainEvent.create(
            event_type="session.assistant_message_created",
            session_id=session_id,
            revision=row.revision,
            payload={
                "seq": seq,
                "kind": (proposal.kind if proposal else "notice"),
                "model": model_name,
                "state": state,
                "pending_action_id": pending_id,
                # 让"这是一次重试"在事件流里可辨（审计/前端去重都需要）
                "retry_of": str(message_id),
            },
            trace_id=trace.trace_id,
            actor_id=actor_id,
        )
        ms._emit(db, ev)
        row.last_event_id = ev.event_id
        svc._regenerate_summary(db, row, trace)
        db.flush()
        snapshot = build_snapshot(db, row, message_state=state, message_reason=reason)

    return snapshot, 200
