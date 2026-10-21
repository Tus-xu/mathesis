"""R17 conversation.run 作业 handler + 惰性 worker 接线（不触碰 main.py dirty）。

执行语义（DECISIONS.md §4/§5：确定性工作流控权；LLM 只产 proposal）：
- JobDispatcher claim（fencing + dedupe_key=message_id）→ fenced_run → 本 handler；
- 业务写一律使用 worker 自有 session（短事务），不在 fenced_run 的长事务内持行锁调 LLM；
- 阶段 A（短事务）：加载 run/message/context；run 已 COMPLETED → 幂等跳过
  （崩溃后重投不重复写）；cancel_requested → run CANCELLED + 事件（作业由 fenced_run 终态）；
- LLM 投影（renew 租约 + 取消检查）；provider/校验失败 → 显式 basic mode（不编造）；
- 阶段 B（短事务）：proposal 写入（verified_role，成员关系在请求阶段裁决）+ 蓝图卡 +
  evidence 消息 + run COMPLETED + durable 事件，同事务提交；
- 幂等防重：proposal 写入与 run=COMPLETED 同事务；崩溃窗口分析见 DECISION-DELTA。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.reliability.jobs import JobEngine, JobReconciler
from app.reliability.models import ReliabilityJob

from .. import state_machines as sm
from ..blueprint.projector import (
    BlueprintProjectionError,
    GroundingViolation,
    project_blueprint,
)
from ..context.models import TextbookTeachingContext
from ..diagnostic.models import DiagnosticRevisionRow, DiagnosticTaskRow
from ..diagnostic.projector import (
    DiagnosticProposalInvalid,
    ScopeViolation,
    project_diagnostic,
)
from ..diagnostic.projector import (
    GroundingViolation as DiagnosticGroundingViolation,
)
from ..diagnostic.prompt import ContextBudgetExceeded
from ..diagnostic.schema import DiagnosticProposal, draft_from_proposal
from ..models import BlueprintRevisionRow, TeachingBlueprintRow, TeachingSessionRow
from ..text_safety import strip_media_syntax, truncate_math_aware
from .catalog import validate_component
from .models import ContextSnapshotRow, ConversationMessageRow, MessageRunRow
from .orchestrator import JOB_TYPE_RUN, OWNER_SERVICE, _jsonable, _now

logger = logging.getLogger("teacher.prep.conversation")

LlmGenerate = Callable[[str], str | Awaitable[str]]


def _bounded_detail(exc: Exception, limit: int = 300) -> str:
    """失败详情（有界、无堆栈）：优先投影器结构化字段（reason/issues），兜底异常文本。

    只用于「失败可恢复」界面的诚实说明与排查；不含堆栈、不含 provider 原文。
    """
    reason = getattr(exc, "reason", None)
    issues = [str(i) for i in (getattr(exc, "issues", None) or [])]
    parts: list[str] = []
    if reason:
        parts.append(str(reason))
    if issues:
        parts.append("；".join(issues[:6]))
    text = " — ".join(p for p in parts if p) or f"{type(exc).__name__}: {exc}"
    return " ".join(text.split())[:limit]


async def _load_run_payload(
    session: AsyncSession,
    *,
    run_id: str,
    session_id: str,
) -> tuple[MessageRunRow, ConversationMessageRow | None, ContextSnapshotRow | None]:
    run = (
        (
            await session.execute(
                select(MessageRunRow).where(
                    MessageRunRow.id == uuid.UUID(run_id),
                    MessageRunRow.session_id == uuid.UUID(session_id),
                )
            )
        )
        .scalar_one_or_none()
    )
    if run is None:
        raise RuntimeError(f"run {run_id} 不存在或不属于会话 {session_id}")
    msg = None
    snap = None
    if run.message_id:
        msg = (
            await session.execute(
                select(ConversationMessageRow).where(ConversationMessageRow.id == run.message_id)
            )
        ).scalar_one_or_none()
        if msg is not None and msg.context_snapshot_id:
            snap = (
                await session.execute(
                    select(ContextSnapshotRow).where(ContextSnapshotRow.id == msg.context_snapshot_id)
                )
            ).scalar_one_or_none()
    return run, msg, snap


async def _mark_running(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    session_id: str,
    trace_id: str | None,
) -> MessageRunRow:
    """阶段 A：run → RUNNING + 事件（短事务；不持行锁调 LLM）。"""
    async with factory() as session:
        run, _, _ = await _load_run_payload(session, run_id=run_id, session_id=session_id)
        if run.status == "COMPLETED":
            return run  # 幂等跳过（崩溃后重投）
        if run.cancel_requested:
            return run
        srow = await _lock_session(session, session_id)
        srow.revision += 1
        run.status = "RUNNING"
        run.started_at = _now()
        run.trace_id = trace_id
        await _emit(session, srow, command_type="conversation.run", actor_id="00000000-0000-0000-0000-000000000000", actor_role="system", payload={"run_id": str(run.id)}, events=[("conversation.run.started", {"run_id": str(run.id)})])
        await session.commit()
        return run

async def _finish(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    session_id: str,
    outcome: dict[str, Any],
    proposal: dict[str, Any] | None,
    snap: ContextSnapshotRow | None,
    run_role: str,
    run_actor: str,
    error_label: str | None,
    error_detail: str | None = None,
) -> str:
    """阶段 B：proposal 写入（成功）或 basic_mode 卡（失败），run 终态 + 事件同事务。"""
    async with factory() as session:
        run, msg, current_snap = await _load_run_payload(session, run_id=run_id, session_id=session_id)
        if run.status == "COMPLETED":
            return "already_completed"
        if run.cancel_requested:
            run.status = "CANCELLED"
            run.completed_at = _now()
            await session.commit()
            return "cancelled"
        srow = await _lock_session(session, session_id)
        srow.revision += 1
        srow.last_command_at = _now()

        if proposal is not None:
            # 蓝图本体时长归一化（数据红线：Σ环节时长 == 课时预算）。
            # LLM 推导的 lesson_flow 合计常 ≠ 45×课时数（顶栏「课时分钟数」与流程自相矛盾），
            # 且超额蓝图会让教师后续节点编辑被时长守卫挡住。落库前用既有纯函数做
            # largest-remainder 整数归一化（Σ 恒等于预算、保序、保文字、保 source_ref、
            # 确定性；仅改 duration_minutes，环节数 > 预算时原样返回），使蓝图本体与
            # deck/教案投影（service.project_specs 同一归一化）一致。
            from ..specs.durations import normalize_flow_durations
            from .node_ops import lesson_budget_minutes

            proposal = normalize_flow_durations(proposal, lesson_budget_minutes(srow))

        if proposal is not None and current_snap is not None:
            brow = TeachingBlueprintRow(
                id=uuid.uuid4(),
                session_id=srow.id,
                status=sm.BLUEPRINT_PROPOSAL,
                revision=1,
                title=proposal.get("title") or f"备课 {srow.id}",
                body=_jsonable(proposal),
            )
            session.add(brow)
            # PostgreSQL enforces the parent FK immediately when the revision
            # row is flushed; make the new blueprint visible before inserting
            # its immutable revision snapshot.
            await session.flush()
            session.add(
                BlueprintRevisionRow(
                    blueprint_id=brow.id,
                    revision_no=1,
                    body=_jsonable(proposal),
                    verdict=sm.BLUEPRINT_PROPOSAL,
                    source="ai_suggestion",
                    actor_id=uuid.UUID(run_actor),
                    actor_role=run_role,
                )
            )
            await session.flush()
            run.result_ref = str(brow.id)
            run.status = "COMPLETED"
            run.completed_at = _now()
            run.provider_error = None

            bp_card = validate_component(
                "blueprint_card",
                await _blueprint_confirm_props(session, srow=srow, brow=brow, proposal=proposal, snap=current_snap),
            )
            # R2 G5：卡消息必须先有针对老师这一句的 prose（前端先渲染 prose 再渲染卡）
            from .reply import compose_reply, quote_of

            focus = "、".join(
                str(k) for k in ((srow.intent or {}).get("knowledge_points") or [])[:4]
            ) if isinstance(srow.intent, dict) else ""
            prose_text = await compose_reply(
                situation="blueprint_proposal",
                teacher_message=msg.text if msg is not None else None,
                state={"title": proposal.get("title") or brow.title or "", "focus": focus},
            )
            msg_seq = await _next_message_seq(session, srow.id)
            assistant = _make_message(
                srow.id, seq=msg_seq, role="assistant", kind="proposal",
                component_type="blueprint_card", component_props=bp_card,
                run_id=run.id, blueprint_id=brow.id,
                text=prose_text,
            )
            session.add(assistant)
            evidence = _make_message(
                srow.id, seq=msg_seq + 1, role="assistant", kind="evidence",
                component_type="evidence_rail",
                component_props={
                    "refs": _evidence_refs(current_snap, proposal),
                    "copy_keys": {"title": "prep.evidence.title"},
                },
                run_id=run.id, context_snapshot_id=current_snap.id,
                text=(
                    f"「{quote_of(msg.text if msg is not None else '')}」这轮方案的教材依据如下"
                    "（来自授权切片原文，可点开看全文）。"
                ),
            )
            session.add(evidence)
            events = [
                ("blueprint.proposed", {"source": "ai_suggestion", "revision_no": 1, "blueprint_id": str(brow.id)}),
                ("conversation.run.completed", {"run_id": str(run.id), "result": "blueprint"}),
            ]
        else:
            run.status = "BASIC_MODE" if error_label else "FAILED"
            run.completed_at = _now()
            run.provider_error = error_label
            run.result_ref = None
            msg_seq = await _next_message_seq(session, srow.id)
            # 降级卡携带**产品化文案 + 有界失败详情**：失败原因随 durable 消息落库，
            # 教师刷新/重进后仍能看到「为什么失败 + 可以重试」，而不是空白或假成功。
            from .background import error_payload_of
            from .reply import compose_reply as _compose_reply

            degraded = error_payload_of(error_label or "unknown_provider_error", detail=error_detail)
            fail_prose = await _compose_reply(
                situation="basic_mode",
                teacher_message=msg.text if msg is not None else None,
                state={"title": (srow.title or "") if srow is not None else ""},
            )
            assistant = _make_message(
                srow.id, seq=msg_seq, role="assistant", kind="basic_mode",
                component_type="basic_mode_card",
                component_props={
                    "error_label": error_label or "unknown_provider_error",
                    "error_message": degraded["message"],
                    "error_detail": degraded.get("detail"),
                    "error_code": 50300,
                    "retryable": True,
                    "editable": _editable_fields(current_snap),
                    "copy_keys": {"retry": "common.retry", "edit": "prep.basic_mode.edit", "submit": "prep.basic_mode.submit"},
                },
                run_id=run.id, editable=True,
                text=fail_prose,
            )
            session.add(assistant)
            events = [
                ("conversation.run.failed", {"run_id": str(run.id), "error_label": error_label}),
                ("conversation.basic_mode.entered", {"run_id": str(run.id), "error_label": error_label}),
            ]
        # 终态事件（D4，与同步版最终 assistant 消息一致；受理制 SSE 进度契约）：
        # ai.stage(done) → assistant.message.completed（全量最终消息，含 blueprint 引用）
        # → conversation.updated（revision/title）；失败态追加产品化 error（error_key，无堆栈）
        from .background import error_payload_of
        from .orchestrator import _assistant_completed_payload, _conversation_updated_payload
        from .views import message_view as _message_view

        await session.flush()
        events += [
            ("ai.stage", {"stage": "done", "stage_label": "本轮备课已完成", "run_id": str(run.id)}),
            (
                "assistant.message.completed",
                {
                    **_assistant_completed_payload([assistant]),
                    "evidence": (
                        _message_view(evidence).model_dump(mode="json")
                        if proposal is not None and current_snap is not None
                        else None
                    ),
                },
            ),
            ("conversation.updated", _conversation_updated_payload(srow)),
        ]
        if not (proposal is not None and current_snap is not None):
            events.append(
                (
                    "error",
                    error_payload_of(
                        error_label or "unknown_provider_error",
                        run_id=str(run.id),
                        detail=error_detail,
                    ),
                )
            )
        _, dtos = await _emit(session, srow, command_type="conversation.run", actor_id=run_actor, actor_role=run_role, payload={"run_id": str(run.id), **outcome}, events=events)
        await session.commit()
        await _publish_run_live(session_id, dtos)
        return run.status


async def conversation_run_handler(
    factory: async_sessionmaker[AsyncSession],
    llm_generate: LlmGenerate,
    *,
    lease_s: float,
    diagnostic_llm_generate: LlmGenerate | None = None,
) -> Callable[[AsyncSession, ReliabilityJob], Awaitable[dict[str, Any] | None]]:
    """构造 JobHandler：闭包持有 session_factory + LLM（含租约续期与取消检查）。

    R18：run_type="diagnostic" → 学前诊断投影分支（diagnostic_llm_generate，
    缺省回落 llm_generate）。
    """

    async def handler(*, session: AsyncSession, job: ReliabilityJob) -> dict[str, Any] | None:
        payload = job.payload or {}
        run_id = payload.get("run_id")
        session_id = payload.get("session_id")
        if not run_id or not session_id:
            raise RuntimeError("conversation.run payload 缺 run_id/session_id")

        # 阶段 A：幂等跳过 / 取消检查 / RUNNING
        run = await _mark_running(factory, run_id=run_id, session_id=session_id, trace_id=job.trace_id)
        if run.status == "COMPLETED":
            return {"status": "already_completed", "run_id": run_id}
        if run.status == "CANCELLED" or run.cancel_requested:
            return {"status": "cancelled", "run_id": run_id}

        if run.run_type == "diagnostic":
            return await _run_diagnostic_projection(
                factory,
                job=job,
                run=run,
                llm_generate=diagnostic_llm_generate or llm_generate,
                lease_s=lease_s,
            )

        async with factory() as rs:
            _, msg, snap = await _load_run_payload(rs, run_id=run_id, session_id=session_id)
            if snap is None:
                raise RuntimeError(f"run {run_id} 无上下文快照（无法投影）")
            context = TextbookTeachingContext.model_validate(snap.snapshot)

        # D4 进度：进入蓝图投影（LLM 生成中）——受理制 SSE drafting 阶段
        from .background import emit_stage as _emit_stage

        await _emit_stage(factory, session_id, "drafting", run_id=run_id)

        keepalive = asyncio.create_task(_keepalive(factory, job_id=job.id, claim_token=job.claim_token or "", lease_s=lease_s))
        error_label: str | None = None
        error_detail: str | None = None
        proposal: dict[str, Any] | None = None
        try:
            result = project_blueprint(
                context,
                _with_cancel_check(llm_generate, factory, run_id=run_id, session_id=session_id),
                max_context_tokens=8000,
                require_grounding=True,
            )
            if inspect.isawaitable(result):
                result = await result
            proposal = result.model_dump(mode="json")
        except GroundingViolation as exc:
            error_label = "grounding_violation"
            error_detail = f"引用校验未通过：{len(exc.refs)} 处出处不在本次教材上下文中"
            logger.warning("blueprint grounding violation", extra={"run_id": run_id, "refs": exc.refs[:5]})
        except BlueprintProjectionError as exc:
            error_label = "proposal_invalid"
            error_detail = _bounded_detail(exc)
            logger.warning("blueprint projection invalid", extra={"run_id": run_id, "reason": str(exc)[:300]})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— provider 异常 → 显式 basic mode
            name = type(exc).__name__.lower()
            error_label = "provider_timeout" if "timeout" in name else "provider_unavailable"
            logger.warning(
                "blueprint provider failure",
                extra={"run_id": run_id, "kind": error_label, "exc": f"{type(exc).__name__}: {exc}"[:400]},
            )
        finally:
            keepalive.cancel()
            try:
                await keepalive
            except asyncio.CancelledError:
                pass

        status = await _finish(
            factory,
            run_id=run_id,
            session_id=session_id,
            outcome={"status": "completed" if proposal else "basic_mode"},
            proposal=proposal,
            snap=snap,
            run_role=payload.get("verified_role") or "teacher",
            run_actor=payload.get("verified_actor") or "00000000-0000-0000-0000-000000000000",
            error_label=error_label,
            error_detail=error_detail,
        )
        return {"status": status, "run_id": run_id, "proposal": bool(proposal)}

    return handler


async def _run_diagnostic_projection(
    factory: async_sessionmaker[AsyncSession],
    *,
    job: ReliabilityJob,
    run: MessageRunRow,
    llm_generate: LlmGenerate,
    lease_s: float,
) -> dict[str, Any] | None:
    """R18 学前诊断 worker 分支：蓝图+上下文 → project_diagnostic 四重闸门 → proposal 落库。

    与 blueprint 分支同构：LLM 不持行锁；失败 → basic mode（不编造/不假成功）；
    ScopeViolation/GroundingViolation → 显式脱敏标签（范围/引用铁律 fail closed）。
    """
    payload = job.payload or {}
    session_id = str(run.session_id)
    blueprint_id = str(payload.get("blueprint_id") or run.result_ref or "")

    # 只读短事务：加载上下文快照 + 已确认蓝图
    async with factory() as rs:
        srow = (
            await rs.execute(
                select(TeachingSessionRow).where(TeachingSessionRow.id == run.session_id)
            )
        ).scalar_one_or_none()
        if srow is None or srow.context_snapshot_id is None:
            raise RuntimeError(f"diagnostic run {run.id} 无上下文快照（无法投影）")
        snap = (
            await rs.execute(
                select(ContextSnapshotRow).where(ContextSnapshotRow.id == srow.context_snapshot_id)
            )
        ).scalar_one_or_none()
        brow = (
            await rs.execute(
                select(TeachingBlueprintRow).where(TeachingBlueprintRow.id == uuid.UUID(blueprint_id))
            )
        ).scalar_one_or_none()
        if snap is None or brow is None:
            raise RuntimeError(f"diagnostic run {run.id} 上下文/蓝图缺失")
        context = TextbookTeachingContext.model_validate(snap.snapshot)
        # projector 校验 proposal.blueprint_id 一致性 → 蓝图 body 必须携带 id 锚点
        blueprint_body = {"id": blueprint_id, **dict(brow.body or {})}

    keepalive = asyncio.create_task(
        _keepalive(factory, job_id=job.id, claim_token=job.claim_token or "", lease_s=lease_s)
    )
    error_label: str | None = None
    proposal = None
    try:
        result = project_diagnostic(
            blueprint=blueprint_body,
            context=context,
            llm_generate=_with_cancel_check(
                llm_generate, factory, run_id=str(run.id), session_id=session_id
            ),
            max_context_tokens=5000,
            require_grounding=True,
            require_scope=True,
        )
        if inspect.isawaitable(result):
            result = await result
        proposal = result
    except ScopeViolation as exc:
        error_label = "scope_violation"
        logger.warning("diagnostic scope violation", extra={"run_id": str(run.id), "kps": exc.kps[:5]})
    except DiagnosticGroundingViolation as exc:
        error_label = "grounding_violation"
        logger.warning("diagnostic grounding violation", extra={"run_id": str(run.id), "refs": exc.refs[:5]})
    except DiagnosticProposalInvalid as exc:
        error_label = "proposal_invalid"
        logger.warning("diagnostic proposal invalid", extra={"run_id": str(run.id), "reason": str(exc)[:300]})
    except ContextBudgetExceeded:
        error_label = "context_budget_exceeded"
        logger.warning("diagnostic context budget exceeded", extra={"run_id": str(run.id)})
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 —— provider 异常 → 显式 basic mode
        name = type(exc).__name__.lower()
        error_label = "provider_timeout" if "timeout" in name else "provider_unavailable"
        logger.warning("diagnostic provider failure", extra={"run_id": str(run.id), "kind": error_label})
    finally:
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass

    status = await _finish_diagnostic(
        factory,
        run_id=str(run.id),
        session_id=session_id,
        proposal=proposal.model_dump(mode="json") if proposal is not None else None,
        blueprint_id=blueprint_id,
        run_role=payload.get("verified_role") or "teacher",
        run_actor=payload.get("verified_actor") or "00000000-0000-0000-0000-000000000000",
        error_label=error_label,
    )
    return {"status": status, "run_id": str(run.id), "proposal": bool(proposal)}


async def _finish_diagnostic(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    session_id: str,
    proposal: dict[str, Any] | None,
    blueprint_id: str,
    run_role: str,
    run_actor: str,
    error_label: str | None,
) -> str:
    """诊断阶段 B（短事务）：proposal → tp_diagnostic_tasks(+revision) + diagnostic_card；
    失败 → basic mode 卡 + 失败事件。run 终态与业务写同事务提交。"""
    async with factory() as session:
        run, diag_msg, _ = await _load_run_payload(session, run_id=run_id, session_id=session_id)
        if run.status == "COMPLETED":
            return "already_completed"
        if run.cancel_requested:
            run.status = "CANCELLED"
            run.completed_at = _now()
            await session.commit()
            return "cancelled"
        srow = await _lock_session(session, session_id)
        srow.revision += 1
        srow.last_command_at = _now()

        if proposal is not None:
            draft = draft_from_proposal(DiagnosticProposal.model_validate(proposal))
            body_json = _jsonable(draft.model_dump(mode="json"))
            drow = DiagnosticTaskRow(
                id=uuid.uuid4(),
                session_id=srow.id,
                blueprint_id=uuid.UUID(blueprint_id),
                status=sm.DIAGNOSTIC_PROPOSAL,
                revision=1,
                title=proposal.get("title") or "学前诊断",
                description=proposal.get("description"),
                estimated_duration_minutes=proposal.get("estimated_duration_minutes") or 5,
                body=body_json,
            )
            session.add(drow)
            session.add(
                DiagnosticRevisionRow(
                    diagnostic_id=drow.id,
                    revision_no=1,
                    body=body_json,
                    verdict=sm.DIAGNOSTIC_PROPOSAL,
                    source="ai_suggestion",
                    actor_id=uuid.UUID(run_actor),
                    actor_role=run_role,
                )
            )
            await session.flush()
            run.result_ref = str(drow.id)
            run.status = "COMPLETED"
            run.completed_at = _now()
            run.provider_error = None

            from ..diagnostic.mapping import diagnostic_card_props

            card = validate_component("diagnostic_card", diagnostic_card_props(drow))
            msg_seq = await _next_message_seq(session, srow.id)
            # R2 G5：卡消息必须先有针对老师这一句的 prose
            from .reply import compose_reply as _compose_diag_reply

            diag_prose = await _compose_diag_reply(
                situation="blueprint_proposal",
                teacher_message=diag_msg.text if diag_msg is not None else None,
                state={"title": srow.title or ""},
            )
            session.add(
                _make_message(
                    srow.id, seq=msg_seq, role="assistant", kind="diagnostic",
                    component_type="diagnostic_card", component_props=card,
                    run_id=run.id, text=diag_prose,
                )
            )
            events = [
                ("diagnostic.proposed", {"diagnostic_id": str(drow.id), "source": "ai_suggestion", "revision_no": 1}),
                ("conversation.run.completed", {"run_id": run_id, "result": "diagnostic"}),
            ]
        else:
            run.status = "BASIC_MODE" if error_label else "FAILED"
            run.completed_at = _now()
            run.provider_error = error_label
            msg_seq = await _next_message_seq(session, srow.id)
            # R2 G5：失败卡也必须先有针对老师这一句的 prose
            from .reply import compose_reply as _compose_fail_reply

            fail_prose = await _compose_fail_reply(
                situation="basic_mode",
                teacher_message=diag_msg.text if diag_msg is not None else None,
                state={"title": srow.title or ""},
            )
            session.add(
                _make_message(
                    srow.id, seq=msg_seq, role="assistant", kind="basic_mode",
                    component_type="basic_mode_card",
                    component_props={
                        "error_label": error_label or "unknown_provider_error",
                        "error_code": 50300,
                        "retryable": True,
                        "editable": {},
                        "copy_keys": {"retry": "common.retry"},
                    },
                    run_id=run.id, editable=True, text=fail_prose,
                )
            )
            events = [
                ("conversation.run.failed", {"run_id": run_id, "error_label": error_label}),
                ("conversation.diagnostic.failed", {"run_id": run_id, "error_label": error_label}),
            ]
        await _emit(session, srow, command_type="conversation.run", actor_id=run_actor, actor_role=run_role, payload={"run_id": run_id}, events=events)
        await session.commit()
        return run.status


def _with_cancel_check(
    llm_generate: LlmGenerate,
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    session_id: str,
) -> LlmGenerate:
    """包一层：LLM 调用前/后检查取消位（协作式取消）。"""

    async def checked(prompt: str) -> str:
        if await _is_cancelled(factory, run_id=run_id, session_id=session_id):
            raise asyncio.CancelledError("run cancelled")
        result = llm_generate(prompt)
        if inspect.isawaitable(result):
            result = await result
        if await _is_cancelled(factory, run_id=run_id, session_id=session_id):
            raise asyncio.CancelledError("run cancelled")
        return str(result)

    return checked


async def _is_cancelled(
    factory: async_sessionmaker[AsyncSession], *, run_id: str, session_id: str
) -> bool:
    async with factory() as session:
        row = (
            await session.execute(
                select(MessageRunRow.cancel_requested).where(
                    MessageRunRow.id == uuid.UUID(run_id),
                    MessageRunRow.session_id == uuid.UUID(session_id),
                )
            )
        ).first()
        return bool(row and row[0])


async def _keepalive(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    claim_token: str,
    lease_s: float,
) -> None:
    """LLM 期间续租（默认 30s 租约 > LLM 时长场景需要心跳）。"""
    while True:
        await asyncio.sleep(max(1.0, lease_s / 3))
        async with factory() as session:
            try:
                await JobEngine.renew(session, job_id, claim_token, lease_s=lease_s)
                await session.commit()
            except Exception:  # noqa: BLE001 —— 续租失败不致命（reconciler 兜底）
                logger.exception("job lease renew failed", extra={"job_id": str(job_id)})


# ── 展示辅助（只读投影，无编造）───────────────────────────────────────────────


async def _blueprint_confirm_props(
    session: AsyncSession,
    *,
    srow: Any,
    brow: Any,
    proposal: dict[str, Any],
    snap: ContextSnapshotRow,
) -> dict[str, Any]:
    """R2 确认卡形态（G10 / golden1）：备课信息 2×2 + 教学意图 3 bullets + 下一步建议。

    全部字段来自真实投影（快照 selection/source/section、srow.intent、proposal），
    缺数据用诚实占位（"未指定班级"），不编造。
    """
    from ..service import load_class_names
    from .node_ops import lesson_budget_minutes
    from .options import label_of

    ctx = snap.snapshot or {}
    src = ctx.get("source") or {}
    sec = ctx.get("section") or {}
    source_title = label_of(
        src.get("publisher"), src.get("edition"), src.get("grade"), src.get("volume")
    )
    class_name = "未指定班级"
    if getattr(srow, "class_id", None):
        names = await load_class_names(session, {srow.class_id})
        class_name = names.get(str(srow.class_id)) or "未指定班级"
    budget = lesson_budget_minutes(srow)
    hours = ((srow.intent or {}).get("lesson_hours") or 1) if isinstance(srow.intent, dict) else 1
    if hours > 6:  # 与展示层同一越界归一（45 分钟被误填成课时数的历史数据）
        hours = max(1, round(hours / 45))
    info = [
        {"label": "课题", "value": str(proposal.get("title") or sec.get("title") or brow.title or "本节课")[:200]},
        {"label": "教材", "value": (source_title or "未指定教材")[:200]},
        {"label": "年级班级", "value": class_name[:200]},
        {"label": "课时", "value": f"{hours} 课时（{budget} 分钟）"},
    ]

    def _bullet_texts(key: str) -> list[str]:
        items = proposal.get(key) or []
        out: list[str] = []
        for it in items:
            text = it.get("text") if isinstance(it, dict) else str(it)
            if text:
                out.append(str(text)[:300])
            if len(out) >= 3:
                break
        return out

    bullets = _bullet_texts("design_intent") or _bullet_texts("objectives") or _bullet_texts("key_points")
    return {
        "blueprint_id": str(brow.id),
        "revision": brow.revision,
        "status": "proposal",
        "title": proposal.get("title") or "",
        "buckets": _blueprint_buckets(proposal),
        "context_snapshot_hash": snap.snapshot_hash,
        "copy_keys": {"confirm": "prep.action.confirm_proposal", "reject": "prep.action.reject_proposal"},
        "info": info,
        "intent_bullets": bullets,
        "next_step": (
            "确认后我按这份蓝图生成教学流程，并继续生成课件与教案；"
            "想调整就直接说，比如「把例题换简单一点」。"
        ),
    }

def _blueprint_buckets(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    for key, title in (
        ("objectives", "教学目标"),
        ("prior_knowledge", "先备知识"),
        ("concepts", "概念"),
        ("theorems", "定理"),
        ("examples", "例题"),
        ("inquiries", "探究"),
        ("design_intent", "设计意图"),
        ("key_points", "重点"),
        ("difficult_points", "难点"),
    ):
        items = proposal.get(key) or []
        if items:
            buckets.append({"bucket": key, "title": title, "items": items})
    flow = proposal.get("lesson_flow") or []
    if flow:
        buckets.append(
            {
                "bucket": "lesson_flow",
                "title": "教学流程",
                "items": [
                    {"text": f"{s.get('title')}（{s.get('duration_minutes') or '?'} 分钟）"}
                    for s in flow
                ],
            }
        )
    return buckets


def _evidence_refs(snap: ContextSnapshotRow, proposal: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    data = snap.snapshot or {}

    def push(ref: str | None, kind: str, title: str, snippet: str) -> None:
        if not ref or ref in seen:
            return
        seen.add(ref)
        # T2.5 读时清洗：剥 markdown 图片/链接语法包装（与 views 同口径，存量行不回写）。
        # 先清洗后截断——先截断再清洗时，截断点落在损坏 URL 中间会漏剥。
        clean = strip_media_syntax(snippet) or strip_media_syntax(title)
        # 数学感知截断：截断点不切进 $…$ 定界符内部（防未闭合 $ 外泄到前端）；
        # 长度只减不增，仍满足 catalog EvidenceRefProps 的 max_length 闸门。
        refs.append(
            {
                "ref": ref,
                "kind": kind,
                "title": truncate_math_aware(clean, 200),
                "snippet": truncate_math_aware(clean, 400),
            }
        )

    for fact in data.get("facts") or []:
        push(fact.get("source_ref") or fact.get("chunk_ref"), fact.get("kind") or "fact", (fact.get("text") or "")[:60], fact.get("text") or "")
    for bucket in ("objectives", "prior_knowledge", "concepts", "theorems", "examples", "inquiries", "design_intent", "key_difficult_points"):
        for fact in data.get(bucket) or []:
            push(fact.get("source_ref") or fact.get("chunk_ref"), bucket, (fact.get("text") or "")[:60], fact.get("text") or "")
    for key in ("objectives", "prior_knowledge", "concepts", "theorems", "examples", "inquiries", "design_intent", "key_points", "difficult_points"):
        for item in proposal.get(key) or []:
            push(item.get("source_ref"), "blueprint", (item.get("text") or "")[:60], item.get("text") or "")
    for stage in proposal.get("lesson_flow") or []:
        for item in stage.get("items") or []:
            push(item.get("source_ref"), "blueprint", (item.get("text") or "")[:60], item.get("text") or "")
    return refs


def _editable_fields(snap: ContextSnapshotRow | None) -> dict[str, Any]:
    if snap is None:
        return {"source_id": None, "section_id": None, "class_id": None, "knowledge_points": [], "lesson_scope": None, "objectives": []}
    data = snap.snapshot or {}
    sel = data.get("selection") or {}
    return {
        "source_id": sel.get("source_id"),
        "section_id": sel.get("section_id"),
        "class_id": sel.get("class_id"),
        "knowledge_points": sel.get("knowledge_points") or [],
        "lesson_scope": sel.get("lesson_scope"),
        "objectives": [{"text": f.get("text"), "source_ref": f.get("source_ref")} for f in (data.get("objectives") or [])],
    }


def _make_message(
    session_id: uuid.UUID,
    *,
    seq: int,
    role: str,
    kind: str,
    component_type: str | None,
    component_props: dict[str, Any] | None,
    run_id: uuid.UUID | None = None,
    blueprint_id: uuid.UUID | None = None,
    context_snapshot_id: uuid.UUID | None = None,
    editable: bool = False,
    text: str | None = None,
) -> ConversationMessageRow:
    props = validate_component(component_type, component_props)
    return ConversationMessageRow(
        id=uuid.uuid4(),
        session_id=session_id,
        seq=seq,
        role=role,
        kind=kind,
        component_type=component_type,
        component_props=props,
        run_id=run_id,
        blueprint_id=blueprint_id,
        context_snapshot_id=context_snapshot_id,
        status="basic_mode" if editable else "applied",
        editable=editable,
        text=text,
    )


async def _next_message_seq(session: AsyncSession, session_id: uuid.UUID) -> int:
    """worker 短事务内的消息 seq 分配（会话行已锁 → 串行安全；UNIQUE 兜底）。"""
    row = (
        await session.execute(
            select(ConversationMessageRow.seq)
            .where(ConversationMessageRow.session_id == session_id)
            .order_by(ConversationMessageRow.seq.desc())
            .limit(1)
        )
    ).first()
    return (row[0] if row else 0) + 1


# ── 事件 / seq 分配（worker 短事务版）─────────────────────────────────────────

async def _emit(
    session: AsyncSession,
    srow: TeachingSessionRow,
    *,
    command_type: str,
    actor_id: str,
    actor_role: str,
    payload: dict[str, Any],
    events: list[tuple[str, dict[str, Any]]],
) -> tuple[uuid.UUID, list[dict[str, Any]]]:
    from ..models import SessionCommandRow, SessionEventRow

    cmd = SessionCommandRow(
        session_id=srow.id,
        command_type=command_type,
        actor_id=uuid.UUID(actor_id),
        actor_role=actor_role,
        class_id=srow.class_id,
        idempotency_key=f"run:{payload.get('run_id', '')}",
        request_hash=f"run:{payload.get('run_id', '')}",
        expected_revision=srow.revision,
        result_status="applied",
        payload=_jsonable(payload),
    )
    session.add(cmd)
    await session.flush()
    parent: uuid.UUID | None = None
    seq = srow.last_event_seq
    rows: list[Any] = []
    for event_type, ev_payload in [("command.applied", {})] + events:
        seq += 1
        ev_id = uuid.uuid4()
        row = SessionEventRow(
            id=ev_id,
            session_id=srow.id,
            command_id=cmd.id,
            seq=seq,
            event_type=event_type,
            payload=_jsonable(ev_payload),
            parent_event_id=parent,
            revision=srow.revision,
        )
        session.add(row)
        rows.append(row)
        if parent is None:
            parent = ev_id
    srow.last_event_seq = seq
    srow.updated_at = _now()  # 显式赋值：避免 onupdate 服务端生成列过期 → DTO 读取触发懒加载
    await session.flush()
    from app.teacher_prep.service import event_dto

    return cmd.id, [event_dto(row) for row in rows]


async def _publish_run_live(session_id: str, dtos: list[dict[str, Any]]) -> None:
    """run 终态事件镜像 live（live:prep:{session_id}；test 环境内部跳过；失败不影响落库结果）。"""
    from .background import publish_events_live

    try:
        await publish_events_live(session_id, dtos)
    except Exception:  # noqa: BLE001 —— live 允许丢失（durable 已落库可重放）
        logger.exception("run live publish failed", extra={"session_id": session_id})


async def _lock_session(session: AsyncSession, session_id: str) -> TeachingSessionRow:
    row = (
        (
            await session.execute(
                select(TeachingSessionRow)
                .where(TeachingSessionRow.id == uuid.UUID(session_id))
                .with_for_update()
            )
        )
        .scalar_one_or_none()
    )
    if row is None:
        raise RuntimeError(f"session {session_id} 不存在")
    return row


# ── worker 接线（惰性；不触碰 main.py）────────────────────────────────────────

_worker_task: asyncio.Task | None = None

#: 孤儿 run 收尾宽限期：合法 run 在任务结束后秒级收尾（同事务/紧邻提交），
#: 5 分钟仍 RUNNING/PENDING 且无活任务的即视为孤儿。
#: 实证（2026-10-10）：conversation.run 任务 5 次尝试耗尽标 FAILED 后，run 行
#: 永远停在 RUNNING → 前端每 3s 轮询"正在生成教学流程"无限转圈；dispatcher
#: 只管 job 生命周期、不回写 run，此处即最后防线（Failure UX：禁止无限 loading）。
_ORPHAN_RUN_GRACE_S = 300

_ORPHAN_RUN_SQL = text(
    """
    UPDATE tp_message_runs r
    SET status = 'FAILED',
        provider_error = '生成任务异常终止（系统已自动恢复），请重新生成',
        completed_at = now(),
        updated_at = now()
    WHERE r.status IN ('RUNNING', 'PENDING')
      AND r.updated_at < now() - make_interval(secs => :grace_s)
      AND NOT EXISTS (
          SELECT 1 FROM reliability_jobs j
          WHERE j.payload ->> 'run_id' = r.id::text
            AND j.status IN ('PENDING', 'CLAIMED')
      )
    """
)


async def _reconcile_orphaned_runs(factory: async_sessionmaker[AsyncSession]) -> int:
    """孤儿 run 收尾：run 卡在 RUNNING/PENDING 而其 job 已终态（或 job 创建失败）→ 置 FAILED。"""
    async with factory() as session:
        result = await session.execute(_ORPHAN_RUN_SQL, {"grace_s": _ORPHAN_RUN_GRACE_S})
        await session.commit()
        return result.rowcount or 0


async def ensure_conversation_worker(factory: async_sessionmaker[AsyncSession] | None = None) -> None:
    """惰性启动 conversation.run 的 JobDispatcher + JobReconciler（进程级单例）。

    - 首次消息/动作/SSE 连接时调用；API 重启后首个请求会重新拉起并消化 PENDING 积压；
    - 单元测试不触发（直接调用 handler / run_once）。
    """
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    from app.db.session import session_factory as default_factory

    factory = factory or default_factory()
    from app.llm.ai_runs import PostgresAiRunsSink
    from app.llm.gateway import LlmGateway

    async def _blueprint_llm(prompt: str) -> str:
        gateway = LlmGateway(PostgresAiRunsSink(factory))
        result = await gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            use_case="teacher_prep_blueprint",
            temperature=0.2,
        )
        return result.content

    async def _diagnostic_llm(prompt: str) -> str:
        gateway = LlmGateway(PostgresAiRunsSink(factory))
        result = await gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            use_case="teacher_prep_diagnostic",
            temperature=0.2,
        )
        return result.content

    from app.reliability.jobs import JobDispatcher

    handler = await conversation_run_handler(
        factory, _blueprint_llm, diagnostic_llm_generate=_diagnostic_llm, lease_s=30.0
    )
    dispatcher = JobDispatcher(
        factory,
        owner_service=OWNER_SERVICE,
        handlers={JOB_TYPE_RUN: handler},
        worker_name="conversation-worker",
        claim_lease_s=30.0,
        poll_interval_s=1.0,
        max_concurrency=4,
    )
    reconciler = JobReconciler(factory, owner_service=OWNER_SERVICE)
    last_reconcile = 0.0

    async def _loop() -> None:
        nonlocal last_reconcile
        while True:
            try:
                await dispatcher.run_once()
            except Exception:  # noqa: BLE001 —— 轮询循环必须存活
                logger.exception("conversation dispatcher run_once crashed")
            now = _now().timestamp()
            if now - last_reconcile >= 30.0:
                last_reconcile = now
                try:
                    await reconciler.run_once()
                except Exception:  # noqa: BLE001
                    logger.exception("conversation reconciler run_once crashed")
                try:
                    orphans = await _reconcile_orphaned_runs(factory)
                    if orphans:
                        logger.warning("conversation orphan runs reclaimed: %s", orphans)
                except Exception:  # noqa: BLE001
                    logger.exception("conversation orphan-run reconcile crashed")
            await asyncio.sleep(1.0)

    _worker_task = asyncio.create_task(_loop())


__all__ = [
    "conversation_run_handler",
    "ensure_conversation_worker",
    "_blueprint_buckets",
]
