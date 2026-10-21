"""W1 决策 D4：会话消息异步受理（进程内 asyncio 后台处理）+ SSE 进度事件。

职责（MASTER-PLAN D4 / ACCEPTANCE-MATRIX R6.2：发送后 AI 首反馈 ≤ 数秒）：
- 受理制：POST /prep/conversations 与 POST .../messages 默认「用户消息立即落库 →
  入队后台处理 → 立即返回 202」；意图解析（LLM 实测 73~165s）移出请求路径；
- 后台处理 = `asyncio.create_task`（进程内）+ 既有 Pub/Sub 事件流（与
  GET /prep/conversations/{id}/events 同通道 `live:prep:{session_id}`）；
- 进度事件双通道：durable（tp_session_events，SSE replay 权威源，断线重连可补齐）
  + live（Valkey Pub/Sub，实时推送）。事件序列：
  message.accepted → ai.stage(analyzing/locate_textbook/blueprint_proposing/drafting/done)
  → assistant.message.completed → conversation.updated；异常终态 → error；
- 同一会话串行化：进程内 per-session 锁 + 受理期在途检查（在途 → 明确 409，
  不并发写同一会话；排队反而堆积不可控）；
- 生命周期：后台任务异常必捕获（发布产品化 error 事件，不泄露堆栈）；任务句柄
  持注册表防 GC；uvicorn --reload 重启后任务随进程消亡（durable 事件 + run/job
  事实仍在 DB，重连 SSE / 重试可恢复）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.events import get_publisher
from app.teacher_prep.models import SessionEventRow, TeachingSessionRow

from .models import ConversationMessageRow
from .orchestrator import IdempotencyInFlightError

logger = logging.getLogger("teacher.prep.conversation.background")

OWNER_SERVICE = "teacher_prep"

# ── ai.stage 枚举（stage ∈ 固定集合）+ 中文 stage_label（UI 直接显示）──────────
STAGE_LABELS: dict[str, str] = {
    "locate_textbook": "正在定位教材内容",
    "analyzing": "正在理解教学要求",
    "blueprint_proposing": "正在生成教学蓝图",
    "drafting": "正在起草备课提案",
    "node_revising": "正在按要求修改教学环节",
    "done": "本轮备课已完成",
}

# ── error 事件产品化文案（error_key 稳定供 i18n；message 兜底展示；不泄露堆栈）──
PROCESSING_FAILED_KEY = "prep.error.processing_failed"
PROCESSING_FAILED_MESSAGE = "本轮处理出现异常，已停止。请点击重试或重新发送消息。"

ERROR_COPY: dict[str, tuple[str, str]] = {
    # error_label → (error_key, 产品化中文文案)
    "provider_timeout": ("prep.error.provider_timeout", "AI 服务响应超时，请稍后重试。"),
    "provider_unavailable": ("prep.error.provider_unavailable", "AI 服务暂时不可用，请稍后重试。"),
    "provider_error": ("prep.error.provider_error", "AI 服务暂时不可用，请稍后重试。"),
    "grounding_violation": ("prep.error.grounding_violation", "生成内容与教材依据不一致，已停止生成。请调整选择后重试。"),
    "proposal_invalid": ("prep.error.proposal_invalid", "生成内容未通过校验，请重试。"),
    "intent_invalid": ("prep.error.intent_invalid", "没能理解这条消息，请换一种说法或改用编辑表单。"),
    "quota_exceeded": ("prep.error.quota_exceeded", "AI 服务额度不足，请稍后重试。"),
    # R3 节点域（node_ops）
    "time_budget_exceeded": ("prep.error.time_budget_exceeded", "环节时长合计超出本课时预算，请调整时长后再试。"),
    "node_target_invalid": ("prep.error.node_target_invalid", "没有找到要修改的教学环节，请展开环节后重试。"),
    "node_revision_invalid": ("prep.error.node_revision_invalid", "AI 未能完成该环节的修改，请换一种说法重试。"),
}


def error_payload_of(
    error_label: str,
    *,
    message_id: str | None = None,
    run_id: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """error 事件载荷：error_key + 产品化文案（无堆栈 / 无 provider 原文）。

    `detail`：有界失败详情（≤300 字符；来自投影器的结构化 reason/issues，
    非堆栈、非 provider 原文）——供「失败可恢复」界面诚实展示失败原因；缺省不写入。
    """
    key, message = ERROR_COPY.get(error_label, ("prep.error.unknown", "生成失败，请重试。"))
    payload: dict[str, Any] = {"error_key": key, "message": message, "retryable": True}
    if message_id:
        payload["message_id"] = message_id
    if run_id:
        payload["run_id"] = run_id
    if detail:
        payload["detail"] = str(detail)[:300]
    return payload


# ── 进程内串行化 / 任务注册表 ────────────────────────────────────────────────

_locks: dict[str, asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()

# 受理在途判定 TTL：进程崩溃遗留 status="accepted" 的消息超过该时长视为死任务
# （不永久 409 卡死会话；事实链以 run/job + 重试恢复）
INFLIGHT_TTL = timedelta(minutes=10)


def session_lock(session_id: str) -> asyncio.Lock:
    lock = _locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[session_id] = lock
    return lock


def is_processing(session_id: str) -> bool:
    lock = _locks.get(session_id)
    return lock is not None and lock.locked()


def spawn_message_processing(coroutine: Awaitable[Any]) -> asyncio.Task:
    """spawn 后台处理任务：句柄入注册表（防 GC）；异常已在协程内捕获。"""
    task = asyncio.create_task(coroutine)  # type: ignore[arg-type]
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain_background_tasks() -> None:
    """测试/收尾辅助：等待全部在途后台处理任务退出（异常不外抛，任务内已捕获）。"""
    pending = [t for t in _tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ── durable + live 双通道进度事件 ────────────────────────────────────────────

def _live_disabled() -> bool:
    """live 镜像禁用条件：test 环境 / pytest 进程（不向真实 Valkey 广播测试事件）。"""
    import os

    return get_settings().environment == "test" or "PYTEST_CURRENT_TEST" in os.environ


async def publish_events_live(session_id: str, event_dtos: list[dict[str, Any]]) -> None:
    """把 durable 事件镜像到既有 live 通道（live:prep:{sid}；test/pytest 禁用）。"""
    if _live_disabled():
        return
    try:
        publisher = get_publisher()
        for dto in event_dtos:
            await publisher.publish_live(f"prep:{session_id}", str(dto.get("event_type")), dto)
    except Exception:  # noqa: BLE001 —— live 允许丢失（durable 已落库可重放）
        logger.exception("conversation live publish failed", extra={"session_id": session_id})


async def emit_session_events(
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    events: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """短事务追加 durable 进度事件（tp_session_events；会话行锁内分配 seq，可重放）
    → 提交后镜像 live。进度事件非教师命令：不产生 SessionCommandRow（command_id 可空）。"""
    from app.teacher_prep.service import event_dto

    dtos: list[dict[str, Any]] = []
    async with factory() as session:
        srow = (
            (
                await session.execute(
                    select(TeachingSessionRow)
                    .where(TeachingSessionRow.id == uuid.UUID(session_id))
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if srow is None:
            return []
        seq = srow.last_event_seq
        for event_type, payload in events:
            seq += 1
            row = SessionEventRow(
                id=uuid.uuid4(),
                session_id=srow.id,
                command_id=None,
                seq=seq,
                event_type=event_type,
                payload=dict(payload),
                parent_event_id=None,
                revision=srow.revision,
            )
            session.add(row)
            dtos.append(row)
        srow.last_event_seq = seq
        srow.updated_at = datetime.now(UTC)  # 显式赋值：避免 onupdate 列过期触发懒加载
        await session.flush()
        dtos = [event_dto(row) for row in dtos]
        await session.commit()
    await publish_events_live(session_id, dtos)
    return dtos


async def emit_stage(
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    stage: str,
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """ai.stage 进度事件（stage_label 中文直显；stage 枚举见 STAGE_LABELS）。"""
    if stage not in STAGE_LABELS:
        raise ValueError(f"未知 ai.stage {stage!r}（allowlist 之外）")
    payload: dict[str, Any] = {"stage": stage, "stage_label": STAGE_LABELS[stage]}
    if run_id:
        payload["run_id"] = run_id
    return await emit_session_events(factory, session_id, [("ai.stage", payload)])


async def emit_processing_error(
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    *,
    message_id: str | None = None,
    error_label: str = "processing_failed",
) -> list[dict[str, Any]]:
    """意外异常终态：error 事件（产品化文案 + error_key）+ 受理消息落 failed。"""
    async with factory() as session:
        if message_id:
            msg = (
                await session.execute(
                    select(ConversationMessageRow).where(ConversationMessageRow.id == uuid.UUID(message_id))
                )
            ).scalar_one_or_none()
            # 只把仍在受理态的消息落 failed；已被后续命令推进的不回写
            if msg is not None and msg.status == "accepted":
                msg.status = "failed"
        await session.commit()
    if error_label == "processing_failed":
        payload: dict[str, Any] = {
            "error_key": PROCESSING_FAILED_KEY,
            "message": PROCESSING_FAILED_MESSAGE,
            "retryable": True,
        }
    else:
        payload = error_payload_of(error_label)
    if message_id:
        payload["message_id"] = message_id
    return await emit_session_events(factory, session_id, [("error", payload)])


async def find_inflight_accepted(
    session: AsyncSession, session_id: uuid.UUID
) -> bool:
    """是否存在受理在途消息（status='accepted' 且未超 TTL）——受理 409 串行化依据。

    进程崩溃遗留的超时 accepted 消息不阻塞新消息（TTL 兜底防永久卡死）。
    """
    row = (
        (
            await session.execute(
                select(ConversationMessageRow)
                .where(
                    ConversationMessageRow.session_id == session_id,
                    ConversationMessageRow.status == "accepted",
                )
                .order_by(ConversationMessageRow.seq.desc())
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if row is None:
        return False
    created = row.created_at
    if created is None:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return datetime.now(UTC) - created < INFLIGHT_TTL


# ── 后台处理主体（受理后接管：意图解析 LLM → 复用同步事务链）───────────────────

async def process_accepted_message(
    *,
    service: Any,
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    message: str,
    idempotency_key: str,
    request_hash: str,
    accepted_msg_id: str,
    member_role: str,
    actor_id: str,
    token: str,
    adapter: Any,
    selection_ids: dict[str, str | None] | None = None,
) -> None:
    """受理后任务体：意图解析（LLM）→ send_message 事务链（与同步版完全一致）。

    - 事件序列由本协程（analyzing/locate_textbook/blueprint_proposing/done）+
      worker（drafting/done/assistant.message.completed/conversation.updated/error）
      共同拼出；durable 全落 tp_session_events，SSE replay 可补齐；
    - 意图解析失败 → basic mode（与同步同路径，error 事件由编排层发布）；
    - 任何意外异常 → emit_processing_error（产品化，进程不崩）。
    """
    lock = session_lock(session_id)
    async with lock:
        try:
            parsed: Any = None
            if selection_ids is None:
                await emit_stage(factory, session_id, "analyzing")
                # R2 对话快路径（闲聊/确认/提问/质疑）先行：确定性、零 LLM；
                # 未命中才走 LLM 意图解析（不持 DB 连接等待慢调用）
                from .intent import dialog_fast_intent

                parsed = dialog_fast_intent(message)
                if parsed is None:
                    # 只读输入先行并关闭会话 → 不持 DB 连接等待 LLM（73~165s）
                    async with factory() as parse_session:
                        inputs = await service.load_intent_inputs(parse_session, session_id=session_id)
                    parsed = await service.parse_intent_with(inputs, message=message)
            if not is_intent_failure(parsed):
                await emit_stage(factory, session_id, "locate_textbook")

            async with factory() as proc_session:
                ws: Any = await service.send_message(
                    proc_session,
                    adapter=adapter,
                    token=token,
                    actor_id=actor_id,
                    actor_role_hint=member_role,
                    session_id=session_id,
                    message=message,
                    expected_revision=None,  # 受理阶段已裁决 revision；后台内部调用不做乐观锁
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    selection_ids=selection_ids,
                    pre_parsed=parsed,
                    accepted_msg_id=accepted_msg_id,
                    membership_role=member_role,
                )
            if isinstance(ws, dict):
                # 幂等重放：同一受理已被处理并存储（重复 spawn / 重试）→ 不发重复事件
                return

            # 蓝图已入队 → blueprint_proposing（drafting/done/completed 由 worker 发布）；
            # 终态无 run（选择卡/错误卡/basic mode）→ done 收尾（失败态由 error 事件表达）
            from .models import MessageRunRow

            async with factory() as probe:
                run = (
                    (
                        await probe.execute(
                            select(MessageRunRow)
                            .where(MessageRunRow.message_id == uuid.UUID(accepted_msg_id))
                            .order_by(MessageRunRow.created_at.desc())
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            if run is not None and run.status in ("PENDING", "RUNNING"):
                await emit_stage(factory, session_id, "blueprint_proposing", run_id=str(run.id))
            elif not bool(getattr(ws, "basic_mode", False)):
                await emit_stage(factory, session_id, "done")
        except asyncio.CancelledError:  # 进程退出/重载：durable 事实已在 DB
            raise
        except IdempotencyInFlightError:
            # 同一受理的并发处理已由首任务接管（幂等壳在途）——静默退出，不发 error
            logger.info(
                "conversation processing already in flight",
                extra={"session_id": session_id, "message_id": accepted_msg_id},
            )
        except Exception:  # noqa: BLE001 —— 后台任务异常必须捕获并发布产品化 error 事件
            logger.exception(
                "conversation async processing failed",
                extra={"session_id": session_id, "message_id": accepted_msg_id},
            )
            try:
                await emit_processing_error(factory, session_id, message_id=accepted_msg_id)
            except Exception:  # noqa: BLE001 —— 双重兜底：绝不向上抛（服务不崩）
                logger.exception(
                    "conversation error event emission failed",
                    extra={"session_id": session_id, "message_id": accepted_msg_id},
                )


async def process_node_revision_message(
    *,
    service: Any,
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    message: str,
    idempotency_key: str,
    request_hash: str,
    accepted_msg_id: str,
    member_role: str,
    actor_id: str,
    target_node_id: str,
) -> None:
    """受理后任务体（R3 AI 定向修改）：target_node_id → LLM 只重写该节点 → 新 revision。

    事件序列与 process_accepted_message 同法（durable + live 双通道）；
    幂等重放（dict 响应）不重复发事件；失败 → 产品化 error（无堆栈）。
    """
    from .node_ops import run_node_revision

    try:
        await run_node_revision(
            service=service,
            factory=factory,
            session_id=session_id,
            message=message,
            accepted_msg_id=accepted_msg_id,
            member_role=member_role,
            actor_id=actor_id,
            target_node_id=target_node_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    except Exception:  # noqa: BLE001 —— run_node_revision 内已兜底；此处防 spawn 断裂
        logger.exception(
            "node revision processing escaped",
            extra={"session_id": session_id, "message_id": accepted_msg_id},
        )


def is_intent_failure(parsed: Any) -> bool:
    """ParsedIntent | IntentParseFailure 判别（dataclass 名匹配，避免 import 环）。"""
    return parsed is not None and parsed.__class__.__name__ == "IntentParseFailure"


__all__ = [
    "STAGE_LABELS",
    "ERROR_COPY",
    "PROCESSING_FAILED_KEY",
    "PROCESSING_FAILED_MESSAGE",
    "error_payload_of",
    "session_lock",
    "is_processing",
    "spawn_message_processing",
    "drain_background_tasks",
    "publish_events_live",
    "emit_session_events",
    "emit_stage",
    "emit_processing_error",
    "find_inflight_accepted",
    "process_accepted_message",
    "process_node_revision_message",
    "is_intent_failure",
]
