"""R17 ConversationService：Conversation-first 备课编排（确定性工作流控权，LLM 只产载荷）。

职责（DECISIONS.md §4/§5/§6/§7/§8）：
- 服务端状态机唯一权威：消息 / 意图 / 上下文 / run / PendingAction 全持久；
  `localStorage` 只存 UI 偏好（本层不输出任何 localStorage 业务真相指令）；
- 命令全部幂等（reliability_idempotency 五元键 + request_hash）+ 审计
  （tp_session_commands）+ durable 事件（tp_session_events + lineage），同事务落库；
- 消息发送：意图解析（LLM，事务外）→ 事务内：锁行 + revision 校验 + 8000 成员重验
  + 消息/意图/上下文快照 + 入队 conversation.run 作业 + 事件 + 幂等响应；
- 蓝图提案由 worker（JobDispatcher + fenced_run）异步执行（不持行锁调 LLM），
  写入使用请求阶段裁决的 `verified_role`（见 DECISION-DELTA：教师落定命令仍逐次
  JWT 执行瞬间复查，R14 _adjudicate 闸门原样复用）；
- **单幂等作用域**：action 命令直接在编排层执行（复用 R14 状态机闸门 +
  BlueprintRevisionRow + 审计/事件原语），不嵌套调用 R14 自带 dispatch 的公共方法
  （避免内层 dispatch 二次 commit 破坏外层事务，DECISION-DELTA 记录）；
- provider 失败 / 意图校验失败 → 带标签可编辑 basic mode（不沉默 / 不编造 / 不假成功）；
- 取消：cancel_requested 协作位（CLAIMED 作业）或直接终态（PENDING 作业）；
- 局部重试：仅重跑 blueprint 阶段（intent/context 已提交且不可变），成员关系现场重验。
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.reliability.idempotency import ReliabilityIdempotency, request_hash_of
from app.reliability.jobs import JobEngine
from app.reliability.models import ReliabilityJob
from app.reliability.trace import get_current
from app.teacher_prep.context.models import ContextSelection, TextbookTeachingContext
from app.teacher_prep.diagnostic.mapping import (
    build_task_assignment,
    default_due_at,
    diagnostic_items_to_quiz_items,
)
from app.teacher_prep.diagnostic.models import DiagnosticRevisionRow, DiagnosticTaskRow
from app.teacher_prep.diagnostic.schema import (
    DiagnosticDraft,
    allowed_scope_of,
    scope_violations,
)
from app.teacher_prep.models import (
    BlueprintRevisionRow,
    SessionCommandRow,
    SessionEventRow,
    TeachingBlueprintRow,
    TeachingSessionRow,
)
from app.teacher_prep.service import (
    MembershipDeniedError,
    RevisionConflictError,
    load_class_names,
)
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractIdempotencyConflict,
    ContractMembershipDenied,
)

from .. import state_machines as sm
from .catalog import validate_component
from .intent import (
    IntentParseFailure,
    ParsedIntent,
    apply_intent,
    dialog_fast_intent,
    intent_snapshot_hash,
    parse_intent,
    selection_complete,
)
from .models import ContextSnapshotRow, ConversationMessageRow, MessageRunRow
from .options import (
    apply_source_correction,
    is_authorized_source,
    label_of,
    list_section_options,
    list_textbook_options,
    reconcile_textbook_selection,
    resolve_section_title,
    resolve_source_title,
    section_belongs_to_source,
)
from .schema import WorkspaceSnapshot, intent_dto
from .views import build_workspace, message_view

logger = logging.getLogger("teacher.prep.conversation")

OWNER_SERVICE = "teacher_prep"
JOB_TYPE_RUN = "conversation.run"

# 选择不完整时的 missing 里装的是内部标识符（textbook/section/class），
# 结构化 missing 列表与 selection_card.kind 保持原样（契约不变）；中文展示文案
# 一律经 reply.compose_reply 生成（宪法 §20：不用系统机制话术拼句子）。


LivePublisher = Callable[[str, str, dict[str, Any]], Awaitable[Any] | Any]
LlmGenerate = Callable[[str], str | Awaitable[str]]


def _assistant_completed_payload(rows: list[ConversationMessageRow]) -> dict[str, Any]:
    """assistant.message.completed 载荷：终态 assistant 消息全量（与同步版最终消息一致，
    含 blueprint/artifacts 引用）。rows[0] 为主消息（proposal/basic_mode/selection/error 卡）。"""
    payload: dict[str, Any] = {
        "messages": [message_view(r).model_dump(mode="json") for r in rows],
    }
    if rows and rows[0].blueprint_id:
        payload["blueprint_id"] = str(rows[0].blueprint_id)
    return payload


def _conversation_updated_payload(srow: TeachingSessionRow) -> dict[str, Any]:
    """conversation.updated 载荷：revision/title/status 元数据（UI 直接消费）。"""
    return {
        "session_id": str(srow.id),
        "revision": srow.revision,
        "title": srow.title,
        "status": srow.status,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _class_ref(row: Any) -> str | None:
    """会话班级引用：None 表示未绑定班级（E-3 可选班级）；禁止编造 "None" 字符串。"""
    return str(row.class_id) if row.class_id is not None else None


def _require_class_id(action: str, row: Any) -> str:
    """班级域动作（发布诊断到班级 / 班级画像）的显式守卫：未绑定班级 → 明确拒绝。"""
    if row.class_id is None:
        raise InvalidActionError(action, "未绑定教学班级：该能力需要班级（可先在会话中绑定班级）")
    return str(row.class_id)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


class PrepSessionGone(ApiError):
    def __init__(self, session_id: str):
        super().__init__(ErrorCode.NOT_FOUND, f"备课会话不存在（session_id={session_id}）")


class ConversationMessageNotFound(ApiError):
    def __init__(self, message_id: str):
        super().__init__(ErrorCode.NOT_FOUND, f"会话消息不存在（message_id={message_id}）")


class RunNotFound(ApiError):
    def __init__(self, run_id: str):
        super().__init__(ErrorCode.NOT_FOUND, f"编排 run 不存在（run_id={run_id}）")


class InvalidActionError(ApiError):
    def __init__(self, action: str, reason: str):
        super().__init__(ErrorCode.CONFLICT, f"动作 {action!r} 不可执行：{reason}")


class AuthorityUnreachableError(ApiError):
    def __init__(self, message: str):
        super().__init__(ErrorCode.DEPENDENCY_UNAVAILABLE, message)


class IdempotencyInFlightError(ApiError):
    def __init__(self, message: str):
        super().__init__(ErrorCode.CONFLICT, message)


def _validate_idem(key: str) -> None:
    import re as _re

    if not _re.fullmatch(r"^[A-Za-z0-9._:-]{8,128}$", key):
        raise ApiError(ErrorCode.VALIDATION, "idempotency_key 不符合规范")


def _require_teaching_role_or_403(role: str) -> None:
    """R18 角色闸门（403 语义）：非教学角色落定诊断命令 → 40301（越权与跨班 403）。"""
    try:
        sm.assert_confirm_actor(role)
    except ValueError as exc:
        raise MembershipDeniedError(str(exc)) from exc


class ConversationService:
    """编排服务：send_message / actions / workspace / list；幂等 + 审计 + durable 事件。

    R18 新增：学前诊断四动作（propose/edit/publish/skip）。发布为高风险写操作：
    - 确认阶段经 **持久 PendingAction**（`app.agent.pending_actions` 迁移表 d17b2c3e4f5a）；
    - 执行瞬间复查 teacher role / class membership / request fingerprint / revision；
    - 8000 Coursework Adapter（R13）幂等创建 TaskAssignment（稳定键，重试不重建）；
    - 8000 不可达 / 成员失效 → 发布 run FAILED + `diagnostic.publish.failed` 事件，
      UI 不误报成功；再次确认走同一稳定键安全重试。
    """

    def __init__(
        self,
        *,
        llm_generate: LlmGenerate | None = None,
        live_publisher: LivePublisher | None = None,
        pending_store: Any | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._llm = llm_generate
        self._live = live_publisher
        self._pending = pending_store
        self._session_factory = session_factory
        # W1 D4：本次命令事务内产生的 durable 事件 DTO（提交后镜像 live；_dispatch 清算）
        self._emitted: list[dict[str, Any]] = []

    def _pending_store(self) -> Any:
        """惰性装配持久 PendingAction store（生产=PG 迁移表；测试注入 InMemory）。"""
        if self._pending is None:
            from app.agent.pending_actions import PostgresPendingActionStore

            if self._session_factory is None:
                from app.db.session import session_factory as default_factory

                self._session_factory = default_factory()
            self._pending = PostgresPendingActionStore(self._session_factory)
        return self._pending

    # ── 查询 ──────────────────────────────────────────────────────────────────

    async def list_conversations(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        teacher_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """继续候选列表：授权会话 + 最近消息摘要（跨日继续入口）。"""
        rows = (
            (
                await session.execute(
                    select(TeachingSessionRow)
                    .where(TeachingSessionRow.teacher_id == uuid.UUID(teacher_id))
                    .order_by(TeachingSessionRow.updated_at.desc())
                    .limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        items: list[dict[str, Any]] = []
        names = await load_class_names(session, {row.class_id for row in rows})
        for row in rows:
            try:
                await self._verify_membership(adapter, token=token, class_id=_class_ref(row))
            except MembershipDeniedError:
                continue  # 班已移除 → 不返回（数据最小化）
            last = (
                (
                    await session.execute(
                        select(ConversationMessageRow)
                        .where(ConversationMessageRow.session_id == row.id)
                        .order_by(ConversationMessageRow.seq.desc())
                        .limit(1)
                    )
                )
                .scalar_one_or_none()
            )
            items.append(
                {
                    "session_id": str(row.id),
                    "status": row.status,
                    "revision": row.revision,
                    "title": row.title,
                    "class_id": _class_ref(row),
                    "class_name": names.get(_class_ref(row)),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "last_message": last.text[:200] if last and last.text else None,
                    "last_message_at": last.created_at.isoformat() if last else None,
                }
            )
        return items

    # ── 新建备课（create + 首条消息；两条幂等子操作，重放不重复建会话）───────────

    async def create_conversation(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        class_id: str | None,
        message: str,
        idempotency_key: str,
        request_hash: str,
        title: str | None = None,
    ) -> WorkspaceSnapshot:
        _validate_idem(idempotency_key)
        from app.teacher_prep.service import TeacherPrepService

        prep = TeacherPrepService()
        created = await prep.create_session(
            session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            class_id=class_id,
            idempotency_key=f"{idempotency_key}:create",
            request_hash=request_hash_of(
                "POST", "/prep/conversations/create", {"class_id": class_id, "title": title}
            ),
            title=title,
        )
        session_id = created["session_id"]
        # 重放 create 时 revision 取自存量响应（可能陈旧）→ 重新读当前 revision
        live = await self._get_session(session, session_id)
        return await self.send_message(
            session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            actor_role_hint=actor_role_hint,
            session_id=session_id,
            message=message,
            expected_revision=live.revision,
            idempotency_key=f"{idempotency_key}:message",
            request_hash=request_hash_of("POST", "/prep/conversations/messages", {"message": message}),
        )

    async def create_conversation_accepted(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        class_id: str | None,
        message: str,
        idempotency_key: str,
        request_hash: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """受理制新建备课（D4）：create_session（确定性，无 LLM）+ accept_message（202 语义）。

        返回受理回执 dict（session_id/message_id/seq/revision/member_role）；
        意图解析与蓝图入队由后台任务接管（router spawn）。
        """
        _validate_idem(idempotency_key)
        from app.teacher_prep.service import TeacherPrepService

        prep = TeacherPrepService()
        created = await prep.create_session(
            session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            class_id=class_id,
            idempotency_key=f"{idempotency_key}:create",
            request_hash=request_hash_of(
                "POST", "/prep/conversations/create", {"class_id": class_id, "title": title}
            ),
            title=title,
        )
        session_id = created["session_id"]
        live = await self._get_session(session, session_id)
        return await self.accept_message(
            session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            session_id=session_id,
            message=message,
            expected_revision=live.revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash_of(
                "POST", "/prep/conversations/messages", {"message": message}
            ),
        )

    async def accept_message(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        session_id: str,
        message: str,
        expected_revision: int | None,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        """受理制（D4）：用户消息立即落库 + message.accepted / conversation.updated 事件
        → 返回受理回执（router 映射为 202 + code=0 信封）。

        - 不做意图解析/上下文构建（LLM 73~165s 移至后台任务，首响 ≤ 数秒）；
        - 同一会话串行化：后台任务在途 / 受理在途消息 / 生成在途 run → 明确 409；
        - 幂等：operation="conversation.accept_message"（与同步 send_message 分键作用域，
          同 key 重复提交重放同一受理结果）；
        - 成员裁决仍在请求路径执行瞬间完成（8000 fail closed），后台承接受理结果。
        """
        _validate_idem(idempotency_key)
        if not message or not message.strip():
            raise ApiError(ErrorCode.VALIDATION, "消息不能为空")
        if len(message) > 4000:
            raise ApiError(ErrorCode.VALIDATION, "消息过长（≤4000 字符）")

        from . import background as bg

        async def _exec() -> dict[str, Any]:
            srow = await self._lock_session(session, session_id)
            if expected_revision is not None and expected_revision != srow.revision:
                raise RevisionConflictError("会话", session_id, expected_revision, srow.revision)
            if srow.status in (sm.SESSION_CANCELLED, sm.SESSION_ARCHIVED):
                raise ApiError(ErrorCode.CONFLICT, f"会话已{srow.status}，不可继续备课")

            # 同一会话串行化（D4）：在途 → 明确 409（不排队堆积，不并发写同一会话）
            if bg.is_processing(session_id):
                raise ApiError(ErrorCode.CONFLICT, "上一条消息仍在处理中，请等它完成后再发送")
            if await bg.find_inflight_accepted(session, srow.id):
                raise ApiError(ErrorCode.CONFLICT, "上一条消息仍在处理中，请等它完成后再发送")
            pending_run = (
                (
                    await session.execute(
                        select(MessageRunRow)
                        .where(
                            MessageRunRow.session_id == srow.id,
                            MessageRunRow.status.in_(("PENDING", "RUNNING")),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if pending_run is not None:
                raise ApiError(
                    ErrorCode.CONFLICT, "上一条消息仍在生成中，可停止当前生成后再发送新消息"
                )

            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(srow)
            )
            now = _now()
            msg_seq = await self._next_message_seq(session, srow.id)
            user_msg = ConversationMessageRow(
                id=uuid.uuid4(),
                session_id=srow.id,
                seq=msg_seq,
                role="user",
                kind="text",
                component_type="user_text",
                component_props=validate_component("user_text", {"text": message}),
                text=message,
                status="accepted",  # 后台任务接管后置 applied / basic_mode / failed
            )
            session.add(user_msg)
            await session.flush()
            srow.revision += 1
            srow.last_command_at = now

            events = [
                ("message.accepted", {"message_id": str(user_msg.id), "seq": msg_seq}),
                ("conversation.updated", _conversation_updated_payload(srow)),
            ]
            cmd_id = await self._log_command(
                session, srow=srow, command_type="conversation.accept_message",
                actor_id=actor_id, actor_role=member_role,
                idempotency_key=idempotency_key, request_hash=request_hash,
                result_status="applied",
                payload={"message_id": str(user_msg.id), "seq": msg_seq},
            )
            await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
            return {
                "session_id": str(srow.id),
                "message_id": str(user_msg.id),
                "seq": msg_seq,
                "revision": srow.revision,
                "member_role": member_role,
            }

        return await self._dispatch(
            session,
            operation="conversation.accept_message",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    # ── 查询 ──────────────────────────────────────────────────────────────────

    async def list_messages(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        teacher_id: str,
        session_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        srow = await self._get_session(session, session_id)
        if str(srow.teacher_id) != teacher_id:
            raise MembershipDeniedError("非本人会话（跨教师访问拒绝）")
        await self._verify_membership(adapter, token=token, class_id=_class_ref(srow))
        rows = (
            (
                await session.execute(
                    select(ConversationMessageRow)
                    .where(ConversationMessageRow.session_id == srow.id)
                    # 长会话必须返回**最新** N 条（含最后一条）：升序 limit 会取最早的
                    # N 条，老师刚发的消息反而查不到。先按 seq 降序取尾部，再反转为
                    # 升序返回（前端按 seq 归并渲染，见 useConversation）。
                    .order_by(ConversationMessageRow.seq.desc())
                    .limit(min(limit, 500))
                )
            )
            .scalars()
            .all()
        )
        rows = list(reversed(rows))
        from .views import message_view

        return [
            message_view(m).model_dump(mode="json")
            for m in rows
        ]

    async def list_events_after(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        teacher_id: str,
        session_id: str,
        after_seq: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """durable 事件回放源（SSE replay：Last-Event-ID → after(seq)）。"""
        srow = await self._get_session(session, session_id)
        if str(srow.teacher_id) != teacher_id:
            raise MembershipDeniedError("非本人会话（跨教师访问拒绝）")
        await self._verify_membership(adapter, token=token, class_id=_class_ref(srow))
        from ..models import SessionEventRow
        from ..service import event_dto

        rows = (
            (
                await session.execute(
                    select(SessionEventRow)
                    .where(
                        SessionEventRow.session_id == srow.id,
                        SessionEventRow.seq > after_seq,
                    )
                    .order_by(SessionEventRow.seq)
                    .limit(min(limit, 1000))
                )
            )
            .scalars()
            .all()
        )
        return [event_dto(e) for e in rows]

    async def get_workspace(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        teacher_id: str,
        session_id: str,
    ) -> WorkspaceSnapshot:
        srow = await self._get_session(session, session_id)
        if str(srow.teacher_id) != teacher_id:
            raise MembershipDeniedError("非本人会话（跨教师访问拒绝）")
        await self._verify_membership(adapter, token=token, class_id=_class_ref(srow))
        return await self._assemble_workspace(session, srow)

    # ── 发送消息（新备 / 续备 / 自然语言修改 共用）─────────────────────────────

    async def send_message(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        session_id: str,
        message: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        available_sources: list[dict[str, str]] | None = None,
        available_sections: list[dict[str, str]] | None = None,
        selection_ids: dict[str, str | None] | None = None,
        pre_parsed: ParsedIntent | IntentParseFailure | None = None,
        accepted_msg_id: str | None = None,
        membership_role: str | None = None,
    ) -> WorkspaceSnapshot:
        _validate_idem(idempotency_key)
        if not message or not message.strip():
            raise ApiError(ErrorCode.VALIDATION, "消息不能为空")
        if len(message) > 4000:
            raise ApiError(ErrorCode.VALIDATION, "消息过长（≤4000 字符）")

        # 阶段 0：意图解析在事务外执行（LLM 不持行锁）；重放请求重复解析但幂等结果正确。
        # 受理制（D4）：后台任务解析完成后经 pre_parsed 注入；选择卡点击走结构化直传。
        if selection_ids:
            # 选择卡点击 → 结构化 id 直传：确定性合并，不经 LLM。
            # 选择卡的选项本身就带 id，让 4B 从中文散文里猜 UUID 既不可靠（实测输出
            # 3k-4.8k reasoning token 撞 60s 超时，或字段拼错触发 intent_invalid），
            # 也没必要。LLM 只保留在生成侧（蓝图/教案/课件）。
            # 自愈归正（对话闭环修复）：教材卡选项 id 实为 TextbookSource 主键，前端
            # 历史版本会把它误放进 section_id → 脏值经 intent 直写 srow.intent，教师
            # 从此永远缺章节（死局）。命中已授权教材主键 → 归正为 textbook_id，章节
            # 按缺失语义继续（章节选择卡兜底）。
            selection_ids = await self._normalize_selection_ids(session, selection_ids)
            from pydantic import ValidationError as _PydValidationError

            from .schema import PrepIntent

            try:
                intent = PrepIntent(
                    intent_type="modify",
                    textbook_id=selection_ids.get("textbook_id"),
                    section_id=selection_ids.get("section_id"),
                    class_id=selection_ids.get("class_id"),
                )
            except _PydValidationError as exc:
                raise ApiError(
                    ErrorCode.VALIDATION, f"选择 id 不合法：{exc.errors()[0]['msg']}"
                ) from exc
            parsed: ParsedIntent | IntentParseFailure = ParsedIntent(
                intent=intent, snapshot_hash=intent_snapshot_hash(intent)
            )
        elif pre_parsed is not None:
            # 受理后台任务已解析（LLM 已在请求路径外执行）；同步 wait=true 不传此参
            parsed = pre_parsed
        else:
            parsed = await self.parse_message_intent(session, session_id=session_id, message=message)

        async def _exec() -> WorkspaceSnapshot:
            srow = await self._lock_session(session, session_id)
            if expected_revision is not None and expected_revision != srow.revision:
                raise RevisionConflictError("会话", session_id, expected_revision, srow.revision)
            if srow.status in (sm.SESSION_CANCELLED, sm.SESSION_ARCHIVED):
                raise ApiError(ErrorCode.CONFLICT, f"会话已{srow.status}，不可继续备课")

            if membership_role is not None:
                # 受理制后台内部调用：承接受理阶段（请求路径）的执行瞬间成员裁决，
                # 语义同 worker payload.verified_role（DECISION-DELTA）
                member_role = membership_role
            else:
                member_role = await self._verify_membership(
                    adapter, token=token, class_id=_class_ref(srow)
                )
            opened = False
            if srow.status == sm.SESSION_DRAFT:
                sm.assert_transition("session", srow.status, sm.SESSION_ACTIVE)
                srow.status = sm.SESSION_ACTIVE
                opened = True

            now = _now()
            if accepted_msg_id is not None:
                # 受理制：用户消息已在受理事务落库（status="accepted"）→ 复用同一行
                user_msg = await self._get_message(session, accepted_msg_id)
                if user_msg.session_id != srow.id:
                    raise MembershipDeniedError("受理消息不属于该会话")
                msg_seq = user_msg.seq
                user_msg.status = "applied"
            else:
                msg_seq = await self._next_message_seq(session, srow.id)
                user_msg = ConversationMessageRow(
                    id=uuid.uuid4(),
                    session_id=srow.id,
                    seq=msg_seq,
                    role="user",
                    kind="text",
                    component_type="user_text",
                    component_props=validate_component("user_text", {"text": message}),
                    text=message,
                    status="applied",
                )
                session.add(user_msg)
                await session.flush()

            if isinstance(parsed, IntentParseFailure):
                return await self._handle_basic_mode(
                    session,
                    srow=srow,
                    actor_id=actor_id,
                    member_role=member_role,
                    user_msg=user_msg,
                    run_type="intent",
                    error_label=parsed.error_label,
                    error_detail=parsed.detail,
                    seq_hint=msg_seq,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    now=now,
                    opened=opened,
                    reuse_accepted=accepted_msg_id is not None,
                )

            return await self._handle_parsed_intent(
                session,
                srow=srow,
                adapter=adapter,
                token=token,
                actor_id=actor_id,
                member_role=member_role,
                user_msg=user_msg,
                parsed=parsed,
                msg_seq=msg_seq,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
                opened=opened,
                reuse_accepted=accepted_msg_id is not None,
                # 选择卡/决策卡 id 直传 = 老师已显式拍板（改选确认动作），不再二次询问
                selection_confirmed=selection_ids is not None,
            )

        return await self._dispatch(
            session,
            operation="conversation.send_message",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    async def _normalize_selection_ids(
        self, session: AsyncSession, selection_ids: dict[str, str | None]
    ) -> dict[str, str | None]:
        """选择卡 id 直传的自愈归正：section_id 命中教材主键 → 归正为 textbook_id。

        只修这一处直传入口；`apply_intent` 是共享纯函数（还有 apply_edits 调用方），
        不在此改动。归正规则（确定性，可单测）：
        - section_id 命中已授权教材主键（教材卡选项即 TextbookSource 主键）→
          textbook_id 缺省或同值时归正为 textbook_id、section_id 视为未提供；
        - textbook_id 已显式给出且不同 → 显式教材优先，脏 section_id 按 deltas 丢弃。
        归正后若章节缺失，由选择不完整分支按缺失语义继续（章节选择卡兜底）。
        """
        textbook_id = selection_ids.get("textbook_id") or None
        section_id = selection_ids.get("section_id") or None
        if section_id and await is_authorized_source(session, section_id):
            if not textbook_id or textbook_id == section_id:
                textbook_id, section_id = section_id, None
            else:
                section_id = None
        return {
            "textbook_id": textbook_id,
            "section_id": section_id,
            "class_id": selection_ids.get("class_id"),
        }

    async def parse_message_intent(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        message: str,
    ) -> ParsedIntent | IntentParseFailure:
        """阶段 0（事务外）：自然语言 → 结构化意图。同步路径与受理后台任务共用。

        章节候选与选择卡同源同层（level=2=节）；若不在此填充，意图提示词里永远没有
        "可选章节"，教师说"讲 1.1 集合的概念"也解析不出 section_id。

        对话快路径：先跑 dialog_fast_intent（闲聊/确认/提问/质疑，确定性、零 LLM），
        命中直接返回对话意图 —— 不消耗 LLM 也不硬套备课意图。
        """
        # ── R2 对话快路径（闲聊/确认/提问/质疑，确定性、零 LLM）──────────
        # 与受理后台任务同一入口 dialog_fast_intent：对话不是备课参数变更，
        # 走 prose 回应 / 真实 confirm 迁移，绝不硬套 modify → 整包蓝图重投影。
        fast = dialog_fast_intent(message)
        if fast is not None:
            return fast

        inputs = await self.load_intent_inputs(session, session_id=session_id)
        return await self.parse_intent_with(inputs, message=message)

    async def load_intent_inputs(
        self, session: AsyncSession, *, session_id: str
    ) -> dict[str, Any]:
        """意图解析输入（只读 DB 投影）：当前选择 + 授权教材/章节选项。

        受理后台任务先取输入并**关闭会话**，再等 LLM（不持连接等待慢调用）。
        """
        current = await self._current_selection(session, session_id)
        available_sections = None
        if current.get("source_id"):
            available_sections = await list_section_options(
                session, source_id=current["source_id"], level=2
            )
        return {
            "current": current,
            # include_topics：给 LLM 每册的章节导览（否则只能凭版本/册次标签瞎猜册次）
            "available_sources": await list_textbook_options(session, limit=100, include_topics=True),
            "available_sections": available_sections,
        }

    async def parse_intent_with(
        self, inputs: dict[str, Any], *, message: str
    ) -> ParsedIntent | IntentParseFailure:
        """LLM 意图解析（无 DB 连接持有；输入见 load_intent_inputs）。"""
        return await parse_intent(
            self._llm,  # type: ignore[arg-type]
            user_message=message,
            current=inputs.get("current"),
            available_sources=inputs.get("available_sources"),
            available_sections=inputs.get("available_sections"),
        )

    async def _handle_parsed_intent(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        member_role: str,
        user_msg: ConversationMessageRow,
        parsed: ParsedIntent,
        msg_seq: int,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
        opened: bool,
        reuse_accepted: bool = False,
        selection_confirmed: bool = False,
    ) -> WorkspaceSnapshot:
        intent = parsed.intent

        # ── casual_chat：闲聊/问候/寒暄 → 直接生成温暖回复，不走蓝图投影 ────
        # 这是修复"AI 回复太公式化/复读教材"的根因级改动 —— 任何输入都硬套备课
        # 意图导致 LLM 被迫往 JSON 里填字段，回复自然就机械了。
        if intent.intent_type == "casual_chat":
            return await self._handle_casual_chat(
                session,
                srow=srow,
                actor_id=actor_id,
                member_role=member_role,
                user_msg=user_msg,
                intent=intent,
                msg_seq=msg_seq,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
                opened=opened,
                reuse_accepted=reuse_accepted,
            )

        # ── R2 对话去表单机：question / confirm → prose 回应或真实确认迁移 ────
        if intent.intent_type == "question":
            situation = "feedback" if "feedback" in (intent.note or "") else "question"
            return await self._handle_dialog_reply(
                session,
                srow=srow,
                actor_id=actor_id,
                member_role=member_role,
                user_msg=user_msg,
                intent=intent,
                msg_seq=msg_seq,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
                opened=opened,
                reuse_accepted=reuse_accepted,
                situation=situation,
            )
        if intent.intent_type == "confirm":
            return await self._handle_confirm_chat(
                session,
                srow=srow,
                adapter=adapter,
                token=token,
                actor_id=actor_id,
                member_role=member_role,
                user_msg=user_msg,
                intent=intent,
                msg_seq=msg_seq,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
                opened=opened,
                reuse_accepted=reuse_accepted,
            )

        prev_selection = await self._current_selection(session, str(srow.id))
        selection = apply_intent(prev_selection, intent)
        prev_section = prev_selection.get("section_id") or None
        prev_source = prev_selection.get("source_id") or None
        new_section = selection.get("section_id") or None
        new_source = selection.get("source_id") or None
        # 内容正确性兜底（确定性、可复现）：LLM 选定的册次若其章节话题与本轮知识点完全不符，
        # 而恰好另一个已授权册次命中 → 改选该册次并清空旧册 section_id；0 命中/多候选/无知识点
        # 一律保持 LLM 原选择（不猜，交给选择卡）。只在 LLM 明确给出 textbook_id 时触发。
        source_correction: dict[str, Any] | None = None
        if intent.textbook_id is not None:
            source_correction = await reconcile_textbook_selection(
                session,
                source_id=str(selection.get("source_id") or ""),
                knowledge_points=intent.knowledge_points,
            )
            if source_correction is not None:
                selection = apply_source_correction(selection, source_correction)
        # ── 合法性校验（数据面 fail-closed，deltas 语义）────────────────────────
        # 章节必须属于当前 source 的授权章节：LLM 幻觉 id / 前端误传教材 UUID /
        # 历史脏数据都会产生非法组合。非法 section_id 按 deltas 语义视为「本轮未提供」
        # （丢弃，不报错），绝不写进 srow.intent——否则后续每轮都带着死组合。
        if selection.get("section_id") and not await section_belongs_to_source(
            session,
            source_id=selection.get("source_id"),
            section_id=str(selection.get("section_id") or ""),
        ):
            selection["section_id"] = None
        # 丢弃后重算 new_section（其上方的首次赋值在本段校验之前，可能含非法值）
        new_section = selection.get("section_id") or None
        # ── R2 改选确认（G6/G7）：章节将从 A 换到 B 且老师这句话不是显式拍板 →
        # prose 明说 + 明确确认动作（decision_card，非章节胶囊云），**不静默换章节**。
        # 点「确认改选为 X」走 selection id 直传路径（selection_confirmed=True）才落定。
        if (
            not selection_confirmed
            and prev_section
            and new_section
            and new_section != prev_section
        ):
            return await self._handle_section_change_confirm(
                session,
                srow=srow,
                actor_id=actor_id,
                member_role=member_role,
                user_msg=user_msg,
                intent=intent,
                msg_seq=msg_seq,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
                opened=opened,
                reuse_accepted=reuse_accepted,
                prev_section_id=prev_section,
                new_section_id=new_section,
                textbook_id=new_source,
            )

        # 选择不完整前不会写上下文快照，但合并后的选择必须跨轮持久：否则教师第 2 轮又回到
        # "未选教材"，备课选择永远补不齐（实测：tp_context_snapshots 0 行 → current 恒空）。
        srow.intent = _jsonable(selection)
        user_msg.intent = intent_dto(intent)
        user_msg.intent_hash = parsed.snapshot_hash

        # 班级变更 → 执行瞬间 8000 重验（fail closed）；角色取最新裁决
        if intent.class_id is not None and str(intent.class_id) != _class_ref(srow):
            member_role = await self._verify_membership(
                adapter, token=token, class_id=intent.class_id
            )
            srow.class_id = uuid.UUID(intent.class_id)
        srow.last_command_at = now

        # 受理制（D4）：用户消息已在受理事务以 message.accepted 发布，不重复发 message.created
        events: list[tuple[str, dict[str, Any]]] = []
        if source_correction is not None:
            # 校正必须留痕（可审计）：from/to/matched + 本轮知识点全部落事件，不静默改数据
            events.append(
                (
                    "conversation.intent.source_corrected",
                    {
                        "from": source_correction["from"],
                        "to": source_correction["to"],
                        "matched": list(source_correction["matched"]),
                        "knowledge_points": list(intent.knowledge_points or []),
                    },
                )
            )
        if not reuse_accepted:
            events.append(("conversation.message.created", {"seq": msg_seq}))
        if opened:
            srow.opened_at = now
            events.append(("session.opened", {"actor_id": actor_id}))

        # ── 裁决5：章节改选 → 会话标题同步（tp_sessions.title 回写）：列表与工作台
        # 读同一 title，章节变了标题必须跟着变（消除"列表椭圆/工作台直线"漂移）。
        if new_section and new_section != prev_section:
            section_title_text, _ = await resolve_section_title(session, str(new_section))
            if section_title_text:
                srow.title = section_title_text

        if not selection_complete(selection):
            srow.revision += 1
            from .reply import compose_reply

            cards = await self._selection_cards(session, selection)
            book_changed = bool(
                source_correction is not None
                or (prev_source and new_source and new_source != prev_source)
            )
            # ── R2 G6 会话级抑制：章节一旦确定（曾有上下文快照 / 曾定过章节），
            # 不再重发章节选择卡、不再渲染章节胶囊云 —— 缺章节时 prose 明说 +
            # 明确改选确认动作（decision_card 少量按钮，非胶囊云）。唯一例外是
            # 「章节曾定过但当前缺教材」的死局恢复：decision_card 因无 source 必为
            # None，教师无路可走 → 改发教材选择卡（真按钮，见下方 else 分支）。
            section_ever_set = bool(prev_section) or srow.context_snapshot_id is not None
            completed_rows: list[ConversationMessageRow] = []
            # R2 G5 + 宪法 §20：选择卡 / 恢复路径的 prose 一律经 compose_reply
            # （LLM 优先，teacher_prep_reply；失败回落确定性兜底），兜底文案是老师的
            # 话、不带「还差X才能继续备课」这类系统机制话术，且承诺的卡真的会给出。
            reply_state = await self._selection_reply_state(
                session, selection, cards["missing"], book_changed=book_changed
            )
            if not section_ever_set:
                # 全会话唯一一次选择卡（G6 ≤1）：prose 先回应老师这一句，再给选择结构。
                card_kind = str(cards["card"].get("kind") or "")
                duplicate = await self._selection_card_is_duplicate(
                    session, srow.id, selection, card_kind
                )
                if duplicate:
                    # 重复点选防抖：本轮选择与会话上一张选择卡完全同款 → 不重发卡
                    # （上一张仍在会话里），只回 prose 确认，避免无效点选刷屏。
                    prose_text = await compose_reply(
                        situation="selection_repeat",
                        teacher_message=user_msg.text,
                        state={"title": srow.title or ""},
                    )
                    assistant = self._make_message(
                        srow.id,
                        seq_hint=msg_seq,
                        role="assistant",
                        kind="text",
                        component_type="assistant_text",
                        component_props=validate_component(
                            "assistant_text", {"text": prose_text}
                        ),
                        text=prose_text,
                    )
                    event_payload: dict[str, Any] = {"missing": cards["missing"], "duplicate": True}
                else:
                    prose_text = await compose_reply(
                        situation="selection_pending",
                        teacher_message=user_msg.text,
                        state=reply_state,
                    )
                    assistant = self._make_message(
                        srow.id,
                        seq_hint=msg_seq,
                        role="assistant",
                        kind="selection",
                        component_type="selection_card",
                        component_props=cards["card"],
                        text=prose_text,
                    )
                    event_payload = {"missing": cards["missing"]}
                session.add(assistant)
                events.append(("conversation.selection.requested", event_payload))
                completed_rows.append(assistant)
            else:
                prose_text = await compose_reply(
                    situation="selection_pending",
                    teacher_message=user_msg.text,
                    state=reply_state,
                )
                if cards["card"].get("kind") == "textbook":
                    # 死局恢复：章节曾定过但当前缺教材（换册未重选 / 脏数据清空）→
                    # 原 decision_card 因无 source 返回 None、prose 承诺的按钮不渲染。
                    # 改发教材选择卡（选项 100% 来自已授权目录），prose 承诺与卡一致；
                    # 与上一张选择卡同款时同样防抖（只回 prose，不重发卡）。
                    duplicate = await self._selection_card_is_duplicate(
                        session, srow.id, selection, "textbook"
                    )
                    if duplicate:
                        prose_text = await compose_reply(
                            situation="selection_repeat",
                            teacher_message=user_msg.text,
                            state={"title": srow.title or ""},
                        )
                        assistant = self._make_message(
                            srow.id,
                            seq_hint=msg_seq,
                            role="assistant",
                            kind="text",
                            component_type="assistant_text",
                            component_props=validate_component(
                                "assistant_text", {"text": prose_text}
                            ),
                            text=prose_text,
                        )
                        event_payload = {"missing": cards["missing"], "duplicate": True}
                    else:
                        assistant = self._make_message(
                            srow.id,
                            seq_hint=msg_seq,
                            role="assistant",
                            kind="selection",
                            component_type="selection_card",
                            component_props=cards["card"],
                            text=prose_text,
                        )
                        event_payload = {"missing": cards["missing"], "recovery": True}
                    session.add(assistant)
                    completed_rows.append(assistant)
                    events.append(("conversation.selection.requested", event_payload))
                else:
                    prose = self._make_message(
                        srow.id,
                        seq_hint=msg_seq,
                        role="assistant",
                        kind="text",
                        component_type="assistant_text",
                        component_props=validate_component(
                            "assistant_text", {"text": prose_text}
                        ),
                        text=prose_text,
                    )
                    session.add(prose)
                    completed_rows.append(prose)
                    decision = await self._section_decision_card(
                        session,
                        srow.id,
                        seq_hint=msg_seq + 1,
                        selection=selection,
                        title="改选章节",
                    )
                    if decision is not None:
                        session.add(decision)
                        completed_rows.append(decision)
                    events.append(("conversation.selection.requested", {"missing": cards["missing"], "suppressed": True}))
            cmd_id = await self._log_command(
                session, srow=srow, command_type="conversation.send_message",
                actor_id=actor_id, actor_role=member_role,
                idempotency_key=idempotency_key, request_hash=request_hash,
                result_status="applied",
                payload={"intent_hash": parsed.snapshot_hash, "selection": "incomplete"},
            )
            await session.flush()
            events += [
                ("assistant.message.completed", _assistant_completed_payload(completed_rows)),
                ("conversation.updated", _conversation_updated_payload(srow)),
            ]
            await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
            return await self._assemble_workspace(session, srow)

        # 上下文确定性构建（无 LLM；越权/不可见 → fail closed，显式错误卡）
        ctx = await self._build_context(session, selection, srow)
        if ctx is None:
            srow.revision += 1
            # R2 G5：错误卡也必须先有针对老师这一句的 prose（LLM 优先，确定性兜底）
            from .reply import compose_reply

            err_prose = await compose_reply(
                situation="basic_mode",
                teacher_message=user_msg.text,
                state={"title": srow.title or ""},
            )
            assistant = self._make_message(
                srow.id,
                seq_hint=msg_seq,
                role="assistant",
                kind="error",
                component_type="error_card",
                component_props={
                    "error_label": "section_unavailable",
                    "error_code": ErrorCode.NOT_FOUND_RESOURCE,
                    "retryable": False,
                    "copy_keys": {"dismiss": "common.dismiss"},
                },
                text=err_prose,
            )
            session.add(assistant)
            events.append(("conversation.context.unavailable", {"reason": "section_unavailable"}))
            cmd_id = await self._log_command(
                session, srow=srow, command_type="conversation.send_message",
                actor_id=actor_id, actor_role=member_role,
                idempotency_key=idempotency_key, request_hash=request_hash,
                result_status="applied",
                payload={"intent_hash": parsed.snapshot_hash, "context": "unavailable"},
            )
            await session.flush()
            events += [
                ("assistant.message.completed", _assistant_completed_payload([assistant])),
                ("conversation.updated", _conversation_updated_payload(srow)),
            ]
            await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
            return await self._assemble_workspace(session, srow)

        # 章节存在但授权切片无可用内容（no_units / rule_extraction_fallback）：
        # 显式告知并按"章节"重给候选，绝不进入投影（LLM 无上下文会产出空提案，教师会误以为"选择无效"）。
        # R2 G6：章节已确定的会话**不重发选择卡**——prose 明说缺授权 + 少量「改选为 X」
        # 确认按钮（decision_card，非胶囊云）。
        if not any((ctx.objectives, ctx.prior_knowledge, ctx.concepts, ctx.theorems, ctx.examples, ctx.inquiries)):
            from .reply import quote_of

            srow.revision += 1
            section_name = (ctx.section.title or "本章节") if ctx.section else "本章节"
            prose_text = (
                f"关于「{quote_of(user_msg.text)}」：{section_name}的授权切片里暂时没有可备课的内容，"
                "我不会硬编一份方案。可以回复「改选：章节名」换一节，或点下面的按钮改选相邻章节。"
            )
            assistant = self._make_message(
                srow.id,
                seq_hint=msg_seq,
                role="assistant",
                kind="error",
                component_type="error_card",
                component_props={
                    "error_label": "section_no_authorized_content",
                    "error_code": ErrorCode.NOT_FOUND_RESOURCE,
                    "retryable": False,
                    "copy_keys": {"dismiss": "common.dismiss"},
                },
                text=prose_text,
            )
            session.add(assistant)
            completed_rows = [assistant]
            decision = await self._section_decision_card(
                session,
                srow.id,
                seq_hint=msg_seq + 1,
                selection=selection,
                title="改选其他章节",
                exclude_section_id=str(selection.get("section_id") or "") or None,
            )
            if decision is not None:
                session.add(decision)
                completed_rows.append(decision)
            events.append(("conversation.context.empty", {"reason": "no_authorized_content"}))
            cmd_id = await self._log_command(
                session, srow=srow, command_type="conversation.send_message",
                actor_id=actor_id, actor_role=member_role,
                idempotency_key=idempotency_key, request_hash=request_hash,
                result_status="applied",
                payload={"intent_hash": parsed.snapshot_hash, "context": "empty"},
            )
            await session.flush()
            events += [
                ("assistant.message.completed", _assistant_completed_payload(completed_rows)),
                ("conversation.updated", _conversation_updated_payload(srow)),
            ]
            await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
            return await self._assemble_workspace(session, srow)

        snap = await self._persist_context(session, srow, ctx)
        srow.context_snapshot_id = snap.id
        srow.revision += 1
        user_msg.context_snapshot_id = snap.id  # worker 经 message → snapshot 加载投影上下文

        run_row = MessageRunRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            message_id=user_msg.id,
            run_type="blueprint",
            status="PENDING",
            attempt=1,
        )
        session.add(run_row)
        await session.flush()
        user_msg.run_id = run_row.id

        ctx_props = self._context_card_props(snap, ctx)
        from .reply import quote_of

        ctx_text = (
            f"先定位一下「{quote_of(user_msg.text)}」对应的教材与学情："
            f"{ctx_props.get('source_title') or '教材'} · {ctx_props.get('section_title') or '章节'} 已记录，"
            "我正在按这个方向拟教学方案。"
        )
        assistant = self._make_message(
            srow.id,
            seq_hint=msg_seq,
            role="assistant",
            kind="context",
            component_type="context_card",
            component_props=ctx_props,
            context_snapshot_id=snap.id,
            run_id=run_row.id,
            text=ctx_text,
        )
        session.add(assistant)

        job = await JobEngine.create(
            session,
            owner_service=OWNER_SERVICE,
            job_type=JOB_TYPE_RUN,
            payload={
                "session_id": str(srow.id),
                "message_id": str(user_msg.id),
                "run_id": str(run_row.id),
                "class_id": _class_ref(srow),
                "verified_role": member_role,
                "verified_actor": actor_id,
            },
            dedupe_key=str(user_msg.id),
            timeout_s=300,
        )
        events += [
            ("conversation.intent.parsed", {"intent_hash": parsed.snapshot_hash}),
            ("conversation.context.updated", {"snapshot_id": str(snap.id), "snapshot_hash": snap.snapshot_hash}),
            ("conversation.run.enqueued", {"run_id": str(run_row.id), "job_id": str(job.id)}),
        ]
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.send_message",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"intent_hash": parsed.snapshot_hash, "job_id": str(job.id)},
        )
        await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
        return await self._assemble_workspace(session, srow)

    # ── casual_chat（闲聊/问候 → 温暖自然回复，不走蓝图投影）───────────────────

    # 闲聊回复模板（确定性兜底；有 LLM 时会覆盖为更自然的回复）
    _CASUAL_REPLIES: dict[str, list[str]] = {
        "greeting": [
            "你好呀！我是备课助手。想备什么课？告诉我课题、课时和教材就行 😊",
            "您好！今天准备备哪节课呢？比如「椭圆第一课时，45 分钟，人教A版」。",
            "嗨！随时可以开始备课，说一下你今天想准备什么内容吧。",
        ],
        "ack": [
            "好的，随时告诉我需要什么帮助～",
            "收到！准备好了就说一下课题，我们一起把它备出来。",
            "没问题，等你说课题我们就开始。",
        ],
        "short_greeting": [
            "你好！想备什么课？",
            "嗨，准备好就告诉我课题哦。",
        ],
    }

    async def _handle_casual_chat(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        actor_id: str,
        member_role: str,
        user_msg: ConversationMessageRow,
        intent: Any,
        msg_seq: int,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
        opened: bool,
        reuse_accepted: bool = False,
    ) -> WorkspaceSnapshot:
        """处理闲聊/问候：生成自然语言回复，不触发任何备课工作流。

        有 LLM → 生成更自然的回复；无 LLM → 用模板。
        回复作为普通 assistant_text 消息落库（白色气泡，非卡片）。
        """
        # 从 intent.note 取闲聊类型（detect_casual_chat 写入的 "casual:greeting"）
        note = intent.note or ""
        casual_type = note.split(":", 1)[-1] if note else "greeting"

        reply_text = None

        # 优先：用 LLM 生成温暖回复（如果可用）
        if self._llm is not None:
            try:
                casual_prompt = (
                    "你是高中数学教师的备课助手。教师刚跟你打招呼或闲聊了一句。"
                    "请用一句温暖、自然、口语化的中文回复（不超过 30 字），"
                    "并友好地引导他说一下想备什么课。\n\n"
                    "教师的话：" + (user_msg.text or "")
                )
                result = self._llm(casual_prompt)
                if inspect.isawaitable(result):
                    result = await result
                text = str(result).strip()
                # 截断过长回复
                if len(text) > 80:
                    text = text[:80]
                # 基本安全过滤：不含代码块/HTML
                if text and "```" not in text and "<" not in text[:10]:
                    reply_text = text
            except Exception:  # noqa: BLE001 —— LLM 失败 → 模板兜底
                logger.warning("casual chat LLM failed, using template", exc_info=True)
                reply_text = None

        # 兜底：确定性模板
        if reply_text is None:
            import random as _random
            templates = self._CASUAL_REPLIES.get(casual_type) or self._CASUAL_REPLIES["greeting"]
            reply_text = _random.choice(templates)

        # 落库：纯文本 assistant 消息（非卡片，让前端渲染为 AI 气泡）
        from .catalog import validate_component
        assistant = self._make_message(
            srow.id,
            seq_hint=msg_seq,
            role="assistant",
            kind="text",
            component_type="assistant_text",
            component_props=validate_component("assistant_text", {"text": reply_text}),
            text=reply_text,
        )
        session.add(assistant)

        srow.revision += 1
        srow.last_command_at = now
        events: list[tuple[str, dict[str, Any]]] = []
        if not reuse_accepted:
            events.append(("conversation.message.created", {"seq": msg_seq}))
        if opened:
            srow.opened_at = now
            events.append(("session.opened", {"actor_id": actor_id}))
        events += [
            ("conversation.intent.parsed", {"intent_hash": intent_snapshot_hash(intent), "casual": True}),
            ("assistant.message.completed", _assistant_completed_payload([assistant])),
            ("conversation.updated", _conversation_updated_payload(srow)),
        ]
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.send_message",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"intent_hash": intent_snapshot_hash(intent), "casual": True},
        )
        await session.flush()
        await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
        return await self._assemble_workspace(session, srow)

    # ── R2 对话去表单机：提问 / 质疑 / 确认 / 改选确认（prose 优先，卡只承载决策）──

    async def _handle_dialog_reply(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        actor_id: str,
        member_role: str,
        user_msg: ConversationMessageRow,
        intent: Any,
        msg_seq: int,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
        opened: bool,
        reuse_accepted: bool = False,
        situation: str = "question",
    ) -> WorkspaceSnapshot:
        """提问/质疑 → **只回 prose**（针对老师这一句），不发卡、不重投影。

        G5：老师的自由文本必须得到针对性 prose 回应；问知识点不是备课参数变更，
        硬套 modify 会再生成一张一样的蓝图卡（表单机根因）。
        """
        from .reply import compose_reply

        state: dict[str, Any] = {}
        if srow.title:
            state["title"] = srow.title
        focus = ((srow.intent or {}).get("knowledge_points") or []) if isinstance(srow.intent, dict) else []
        if focus:
            state["focus"] = "、".join(str(k) for k in focus[:4])
        prose_text = await compose_reply(
            situation=situation, teacher_message=user_msg.text, state=state
        )
        assistant = self._make_message(
            srow.id,
            seq_hint=msg_seq,
            role="assistant",
            kind="text",
            component_type="assistant_text",
            component_props=validate_component("assistant_text", {"text": prose_text}),
            text=prose_text,
        )
        session.add(assistant)
        srow.revision += 1
        srow.last_command_at = now
        user_msg.intent = intent_dto(intent)
        user_msg.intent_hash = intent_snapshot_hash(intent)
        events: list[tuple[str, dict[str, Any]]] = []
        if not reuse_accepted:
            events.append(("conversation.message.created", {"seq": msg_seq}))
        if opened:
            srow.opened_at = now
            events.append(("session.opened", {"actor_id": actor_id}))
        events += [
            ("conversation.intent.parsed", {"intent_hash": intent_snapshot_hash(intent), "dialog": situation}),
            ("assistant.message.completed", _assistant_completed_payload([assistant])),
            ("conversation.updated", _conversation_updated_payload(srow)),
        ]
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.send_message",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"dialog": situation, "intent_hash": intent_snapshot_hash(intent)},
        )
        await session.flush()
        await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
        return await self._assemble_workspace(session, srow)

    async def _handle_confirm_chat(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        member_role: str,
        user_msg: ConversationMessageRow,
        intent: Any,
        msg_seq: int,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
        opened: bool,
        reuse_accepted: bool = False,
    ) -> WorkspaceSnapshot:
        """聊天确认（"按这个方向继续"）→ 真实 blueprint 状态迁移（G9），不再重投影。

        与 `POST .../actions` 的 confirm_proposal 同一状态机闸门：
        教学角色 + 执行瞬间 8000 重验 + BlueprintRevisionRow 留痕。
        """
        from .reply import compose_reply

        brow = (
            (
                await session.execute(
                    select(TeachingBlueprintRow)
                    .where(TeachingBlueprintRow.session_id == srow.id)
                    .order_by(TeachingBlueprintRow.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        user_msg.intent = intent_dto(intent)
        user_msg.intent_hash = intent_snapshot_hash(intent)
        if brow is None or brow.status != sm.BLUEPRINT_PROPOSAL:
            return await self._handle_dialog_reply(
                session,
                srow=srow, actor_id=actor_id, member_role=member_role, user_msg=user_msg,
                intent=intent, msg_seq=msg_seq, idempotency_key=idempotency_key,
                request_hash=request_hash, now=now, opened=opened,
                reuse_accepted=reuse_accepted, situation="confirm_noop",
            )

        member_role = await self._verify_membership(
            adapter, token=token, class_id=_class_ref(srow)
        )
        sm.assert_confirm_actor(member_role)
        sm.assert_transition("blueprint", brow.status, sm.BLUEPRINT_CONFIRMED)
        prev_no = brow.revision
        brow.status = sm.BLUEPRINT_CONFIRMED
        brow.revision += 1
        brow.confirmed_at, brow.confirmed_by = _now(), uuid.UUID(actor_id)
        brow.rejected_at, brow.rejected_by, brow.rejected_reason = None, None, None
        session.add(
            BlueprintRevisionRow(
                blueprint_id=brow.id,
                revision_no=brow.revision,
                body=_jsonable(brow.body),
                verdict=sm.BLUEPRINT_CONFIRMED,
                source="teacher",
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
                parent_revision_no=prev_no,
            )
        )

        prose_text = await compose_reply(
            situation="confirm",
            teacher_message=user_msg.text,
            state={"title": brow.title or ""},
        )
        assistant = self._make_message(
            srow.id,
            seq_hint=msg_seq,
            role="assistant",
            kind="text",
            component_type="assistant_text",
            component_props=validate_component("assistant_text", {"text": prose_text}),
            text=prose_text,
            blueprint_id=brow.id,
        )
        session.add(assistant)
        srow.revision += 1
        srow.last_command_at = now
        events: list[tuple[str, dict[str, Any]]] = []
        if not reuse_accepted:
            events.append(("conversation.message.created", {"seq": msg_seq}))
        if opened:
            srow.opened_at = now
            events.append(("session.opened", {"actor_id": actor_id}))
        events += [
            (
                "blueprint.confirmed",
                {"actor_id": actor_id, "blueprint_id": str(brow.id), "source": "chat_confirm"},
            ),
            ("assistant.message.completed", _assistant_completed_payload([assistant])),
            ("conversation.updated", _conversation_updated_payload(srow)),
        ]
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.confirm_proposal",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"blueprint_id": str(brow.id), "source": "chat_confirm"},
        )
        await session.flush()
        await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
        return await self._assemble_workspace(session, srow)

    async def _handle_section_change_confirm(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        actor_id: str,
        member_role: str,
        user_msg: ConversationMessageRow,
        intent: Any,
        msg_seq: int,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
        opened: bool,
        reuse_accepted: bool = False,
        prev_section_id: str,
        new_section_id: str,
        textbook_id: str | None,
    ) -> WorkspaceSnapshot:
        """章节改选 → prose 明说 + 明确确认动作（G6：非章节胶囊云），不静默换章节。"""
        from .reply import quote_of

        prev_title, _ = await resolve_section_title(session, prev_section_id)
        new_title, _ = await resolve_section_title(session, new_section_id)
        prev_name = prev_title or "当前章节"
        new_name = new_title or "新章节"
        prose_text = (
            f"关于「{quote_of(user_msg.text)}」：这会把备课章节从「{prev_name}」改到「{new_name}」，"
            "替换当前的备课定位，所以先跟你确认一下。"
        )
        assistant = self._make_message(
            srow.id,
            seq_hint=msg_seq,
            role="assistant",
            kind="text",
            component_type="assistant_text",
            component_props=validate_component("assistant_text", {"text": prose_text}),
            text=prose_text,
        )
        session.add(assistant)
        decision = self._make_message(
            srow.id,
            seq_hint=msg_seq + 1,
            role="assistant",
            kind="decision",
            component_type="decision_card",
            component_props=validate_component(
                "decision_card",
                {
                    "title": "确认改选章节",
                    "options": [
                        {
                            "id": "confirm-change",
                            "label": f"确认改选为 {new_name}",
                            "action": "select_section",
                            "section_id": new_section_id,
                            "textbook_id": textbook_id,
                        },
                        {
                            "id": "keep-current",
                            "label": f"保持「{prev_name}」不变",
                            "action": "send_text",
                            "text": "保持当前章节不变",
                        },
                    ],
                    "copy_keys": {"pick": "prep.selection.pick", "cancel": "common.cancel"},
                },
            ),
        )
        session.add(decision)
        srow.revision += 1
        srow.last_command_at = now
        user_msg.intent = intent_dto(intent)
        user_msg.intent_hash = intent_snapshot_hash(intent)
        events: list[tuple[str, dict[str, Any]]] = []
        if not reuse_accepted:
            events.append(("conversation.message.created", {"seq": msg_seq}))
        if opened:
            srow.opened_at = now
            events.append(("session.opened", {"actor_id": actor_id}))
        events += [
            (
                "conversation.selection.requested",
                {"missing": ["section"], "change_confirm": True, "from": prev_section_id, "to": new_section_id},
            ),
            ("assistant.message.completed", _assistant_completed_payload([assistant, decision])),
            ("conversation.updated", _conversation_updated_payload(srow)),
        ]
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.send_message",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"section_change_confirm": True, "from": prev_section_id, "to": new_section_id},
        )
        await session.flush()
        await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
        return await self._assemble_workspace(session, srow)

    async def _section_decision_card(
        self,
        session: AsyncSession,
        srow_id: uuid.UUID,
        *,
        seq_hint: int,
        selection: dict[str, Any],
        title: str,
        exclude_section_id: str | None = None,
    ) -> ConversationMessageRow | None:
        """改选确认动作（≤3 按钮，非胶囊云）：授权章节选项 → select_section 结构化回传。

        选项 100% 来自已授权目录（list_section_options，知识点命中排前）；无候选 → 不发卡。
        """
        source_id = selection.get("source_id")
        if not source_id:
            return None
        options = await list_section_options(
            session,
            source_id=str(source_id),
            level=2,
            knowledge_points=selection.get("knowledge_points") or [],
        )
        picks = [o for o in (options or []) if str(o.get("id")) != exclude_section_id][:3]
        if not picks:
            return None
        card = {
            "title": title,
            "options": [
                {
                    "id": f"sec-{o.get('id')}",
                    "label": f"改选为 {o.get('label')}",
                    "action": "select_section",
                    "section_id": str(o.get("id")),
                    "textbook_id": str(source_id),
                }
                for o in picks
            ],
            "copy_keys": {"pick": "prep.selection.pick"},
        }
        return self._make_message(
            srow_id,
            seq_hint=seq_hint,
            role="assistant",
            kind="decision",
            component_type="decision_card",
            component_props=card,
        )

    # ── basic mode（AI 失败 → 带标签可编辑，不沉默/不编造/不假成功）────────────

    async def _handle_basic_mode(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        actor_id: str,
        member_role: str,
        user_msg: ConversationMessageRow,
        run_type: str,
        error_label: str,
        error_detail: str,
        seq_hint: int,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
        opened: bool,
        reuse_accepted: bool = False,
    ) -> WorkspaceSnapshot:
        current = await self._current_selection(session, str(srow.id))
        run_row = MessageRunRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            message_id=user_msg.id,
            run_type=run_type,
            status="BASIC_MODE",
            attempt=1,
            provider_error=error_label,
            started_at=now,
            completed_at=now,
        )
        session.add(run_row)
        await session.flush()
        user_msg.run_id = run_row.id
        user_msg.status = "basic_mode"

        from .reply import compose_reply

        basic_prose = await compose_reply(
            situation="basic_mode",
            teacher_message=user_msg.text,
            state={"title": srow.title or ""},
        )
        assistant = self._make_message(
            srow.id,
            seq_hint=seq_hint,
            role="assistant",
            kind="basic_mode",
            component_type="basic_mode_card",
            component_props={
                "error_label": error_label,
                "error_code": ErrorCode.DEPENDENCY_UNAVAILABLE,
                "retryable": True,
                "editable": {
                    "source_id": current.get("source_id"),
                    "section_id": current.get("section_id"),
                    "class_id": current.get("class_id"),
                    "knowledge_points": current.get("knowledge_points") or [],
                    "lesson_scope": current.get("lesson_scope"),
                    "lesson_hours": current.get("lesson_hours"),
                    "objectives": current.get("objectives") or [],
                },
                "copy_keys": {
                    "retry": "common.retry",
                    "edit": "prep.basic_mode.edit",
                    "submit": "prep.basic_mode.submit",
                },
            },
            run_id=run_row.id,
            editable=True,
            text=basic_prose,
        )
        session.add(assistant)
        srow.revision += 1
        srow.last_command_at = now

        events: list[tuple[str, dict[str, Any]]] = []
        if not reuse_accepted:
            events.append(("conversation.message.created", {"seq": seq_hint}))
        if opened:
            srow.opened_at = now
            events.append(("session.opened", {"actor_id": actor_id}))
        events += [
            ("conversation.run.failed", {"run_id": str(run_row.id), "error_label": error_label}),
            ("conversation.basic_mode.entered", {"run_id": str(run_row.id), "error_label": error_label}),
        ]
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.send_message",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"basic_mode": True, "error_label": error_label, "detail": error_detail[:300]},
        )
        # 终态事件（D4）：basic mode 卡即最终 assistant 消息 + 产品化 error（error_key 供 i18n）
        from .background import error_payload_of

        await session.flush()
        events += [
            ("assistant.message.completed", _assistant_completed_payload([assistant])),
            ("conversation.updated", _conversation_updated_payload(srow)),
            ("error", error_payload_of(error_label, message_id=str(user_msg.id), run_id=str(run_row.id))),
        ]
        await self._log_events(session, srow=srow, command_id=cmd_id, events=events)
        return await self._assemble_workspace(session, srow)

    # ── 动作（confirm/reject/retry/cancel/apply_edits/select_class/propose_revision）──

    ACTION_TYPES = (
        "confirm_proposal",
        "reject_proposal",
        "propose_revision",
        "retry_run",
        "cancel_run",
        "apply_edits",
        "select_class",
        "cancel_session",
        # R18 学前诊断（教师侧闭环）
        "propose_diagnostic",   # 已确认蓝图 → 生成诊断 proposal（worker 执行 LLM）
        "edit_diagnostic",      # 教师编辑草稿（改题/删题/调整预计时长）
        "publish_diagnostic",   # 高风险发布（持久 PendingAction + 8000 Adapter）
        "skip_diagnostic",      # 明确跳过（审计）
        # L-221-2：AI 修订提案 + 双 Spec 投影
        "propose_blueprint_revision",
        "project_specs",
    )

    async def run_action(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        session_id: str,
        action: str,
        target: str | None,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        payload: dict[str, Any] | None = None,
    ) -> WorkspaceSnapshot:
        _validate_idem(idempotency_key)
        if action not in self.ACTION_TYPES:
            raise ApiError(ErrorCode.VALIDATION, f"未知动作 {action!r}（allowlist 之外）")

        async def _exec() -> WorkspaceSnapshot:
            srow = await self._lock_session(session, session_id)
            if expected_revision is not None and expected_revision != srow.revision:
                raise RevisionConflictError("会话", session_id, expected_revision, srow.revision)
            if srow.status in (sm.SESSION_CANCELLED, sm.SESSION_ARCHIVED):
                raise InvalidActionError(action, f"会话已{srow.status}")

            # ── R18 学前诊断四动作 ────────────────────────────────────────────
            if action == "propose_diagnostic":
                return await self._propose_diagnostic(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    target=target,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "edit_diagnostic":
                return await self._edit_diagnostic(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "publish_diagnostic":
                return await self._publish_diagnostic(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "skip_diagnostic":
                return await self._skip_diagnostic(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )

            if action in ("confirm_proposal", "reject_proposal"):
                return await self._adjudicate_proposal(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    action=action, target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "retry_run":
                return await self._retry_run(
                    session, srow=srow, adapter=adapter, token=token,
                    target=target,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "cancel_run":
                return await self._cancel_run(
                    session, srow=srow, target=target,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "apply_edits":
                return await self._apply_edits(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "select_class":
                return await self._select_class(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "propose_revision":
                return await self._propose_revision(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "propose_blueprint_revision":
                return await self._propose_blueprint_revision_action(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            if action == "project_specs":
                return await self._project_specs_action(
                    session, srow=srow, adapter=adapter, token=token,
                    actor_id=actor_id, actor_role_hint=actor_role_hint,
                    target=target, payload=payload or {},
                    idempotency_key=idempotency_key, request_hash=request_hash,
                )
            # cancel_session → 编排层内联（复用 R14 状态机/审计/事件原语，单幂等作用域）
            if srow.status == sm.SESSION_ARCHIVED:
                raise InvalidActionError("cancel_session", "会话已归档")
            sm.assert_transition("session", srow.status, sm.SESSION_CANCELLED)
            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(srow)
            )
            srow.status = sm.SESSION_CANCELLED
            srow.revision += 1
            srow.closed_reason = (payload or {}).get("reason")
            srow.last_command_at = _now()
            cmd_id = await self._log_command(
                session, srow=srow, command_type="conversation.action.cancel_session",
                actor_id=actor_id, actor_role=member_role,
                idempotency_key=idempotency_key, request_hash=request_hash,
                result_status="applied", payload={"reason": srow.closed_reason},
            )
            await self._log_events(
                session, srow=srow, command_id=cmd_id,
                events=[("session.cancelled", {"actor_id": actor_id, "reason": srow.closed_reason})],
            )
            return await self._assemble_workspace(session, srow)

        result = await self._dispatch(
            session,
            operation=f"conversation.action.{action}",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )
        # R18 发布两阶段：阶段 1 已在幂等壳内提交（持久 PendingAction + run PENDING）；
        # 阶段 2/3（8000 幂等创建 + 落定）在壳外执行，避免事务内跨服务 HTTP。
        if isinstance(result, dict) and result.get("publish_prepared"):
            return await self._publish_execute(
                session, prepared=result, adapter=adapter, token=token, actor_id=actor_id
            )
        if isinstance(result, dict) and result.get("publish_noop"):
            factory = self._db_factory()
            async with factory() as s3:
                srow3 = await self._get_session(s3, str(result["session_id"]))
                return await self._assemble_workspace(s3, srow3)
        return result

    async def _adjudicate_proposal(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        action: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """确认/驳回：R14 闸门原样复用（前置教学角色 + 执行瞬间 8000 重验 + revision）。"""
        if not target:
            raise InvalidActionError(action, "缺少 blueprint_id（target）")
        sm.assert_confirm_actor(actor_role_hint)  # 前置硬闸门：LLM/系统角色无权落定
        brow = await self._lock_blueprint(session, target)
        if brow.session_id != srow.id:
            raise MembershipDeniedError("蓝图不属于该会话（跨会话访问拒绝）")
        member_role = await self._verify_membership(
            adapter, token=token, class_id=_class_ref(srow)
        )
        sm.assert_confirm_actor(member_role)  # 执行瞬间权威角色仍须为教学角色
        if action == "confirm_proposal":
            target_state = sm.BLUEPRINT_CONFIRMED
            event, detail = "blueprint.confirmed", {"note": payload.get("note")}
        else:
            target_state = sm.BLUEPRINT_REJECTED
            event, detail = "blueprint.rejected", {"reason": payload.get("reason")}
        sm.assert_transition("blueprint", brow.status, target_state)

        prev_no = brow.revision
        brow.status = target_state
        brow.revision += 1
        if action == "confirm_proposal":
            brow.confirmed_at, brow.confirmed_by = _now(), uuid.UUID(actor_id)
            brow.rejected_at, brow.rejected_by, brow.rejected_reason = None, None, None
            # R6-n1fix（N6 治本）：蓝图确认 = 章节权威落定 → 会话标题/章节主题与
            # **会话绑定章节**同源同步（同一 resolve_section_title；与 service.py
            # 蓝图确认入口双口径一致）。s4 画布页眉（DeckPane 优先读 session.title）、
            # 首页/导航读 intent.knowledge_points——NL 原始知识点易过期
            # （实测会话「椭圆的标准方程」残留），确认时点收敛到绑定章节。
            from .options import resolve_section_title

            _section_id = str(((srow.intent or {}).get("section_id")) or "")
            if _section_id:
                _section_title, _ = await resolve_section_title(session, _section_id)
                if _section_title:
                    srow.title = _section_title
                    _intent = dict(srow.intent or {})
                    _topic = re.sub(r"[0-9]+(?:\.[0-9]+)+", " ", _section_title)
                    _topic = re.sub(r"第[一二三四五六七八九十百\d]+课时", " ", _topic)
                    _topic = re.sub(r"\s+", " ", _topic).strip(" -·、（）()")
                    if _topic:
                        _intent["knowledge_points"] = [_topic]
                        srow.intent = _intent
        else:
            brow.rejected_at, brow.rejected_by = _now(), uuid.UUID(actor_id)
            brow.rejected_reason = payload.get("reason")
            brow.confirmed_at, brow.confirmed_by = None, None
        session.add(
            BlueprintRevisionRow(
                blueprint_id=brow.id,
                revision_no=brow.revision,
                body=_jsonable(brow.body),
                verdict=target_state,
                source="teacher",
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
                parent_revision_no=prev_no,
            )
        )
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type=f"conversation.action.{action}",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied", payload=_jsonable(detail),
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[(event, {"actor_id": actor_id, **_jsonable(detail)})],
        )
        return await self._assemble_workspace(session, srow)

    async def _retry_run(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        target: str | None,
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """局部重试：仅重跑 blueprint 阶段（intent/context 已提交不可变；成员关系现场重验）。"""
        msg = await self._get_message(session, target or "")
        if msg.session_id != srow.id:
            raise MembershipDeniedError("消息不属于该会话")
        if msg.context_snapshot_id is None:
            raise InvalidActionError("retry_run", "消息无上下文快照，无法重跑（请重新发送消息）")
        prev_runs = (
            (
                await session.execute(
                    select(MessageRunRow)
                    .where(MessageRunRow.message_id == msg.id)
                    .order_by(MessageRunRow.attempt.desc())
                )
            )
            .scalars()
            .all()
        )
        attempt = (prev_runs[0].attempt if prev_runs else 0) + 1
        if attempt > 3:
            raise InvalidActionError("retry_run", f"重试次数已达上限（{3}）")
        member_role = await self._verify_membership(
            adapter, token=token, class_id=_class_ref(srow)
        )

        run_row = MessageRunRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            message_id=msg.id,
            run_type="blueprint",
            status="PENDING",
            attempt=attempt,
        )
        session.add(run_row)
        await session.flush()
        job = await JobEngine.create(
            session,
            owner_service=OWNER_SERVICE,
            job_type=JOB_TYPE_RUN,
            payload={
                "session_id": str(srow.id),
                "message_id": str(msg.id),
                "run_id": str(run_row.id),
                "class_id": _class_ref(srow),
                "verified_role": member_role,
                "verified_actor": "00000000-0000-0000-0000-000000000000",
            },
            dedupe_key=f"{msg.id}:retry:{attempt}",
            timeout_s=300,
        )
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.retry_run",
            actor_id="00000000-0000-0000-0000-000000000000", actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied", payload={"message_id": str(msg.id), "attempt": attempt, "job_id": str(job.id)},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[("conversation.run.enqueued", {"run_id": str(run_row.id), "job_id": str(job.id), "retry": True})],
        )
        return await self._assemble_workspace(session, srow)

    async def _cancel_run(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        target: str | None,
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        run = await self._get_run(session, target or "")
        if run.session_id != srow.id:
            raise MembershipDeniedError("run 不属于该会话")
        if run.status in ("COMPLETED", "FAILED", "BASIC_MODE", "CANCELLED"):
            raise InvalidActionError("cancel_run", f"run 已处于终态（{run.status}）")
        run.cancel_requested = True
        run.status = "CANCELLED"
        run.completed_at = _now()
        # 关联 PENDING/CLAIMED 作业 → 标记取消（JobEngine 幂等；DB 无关筛选）
        jobs = (
            (
                await session.execute(
                    select(ReliabilityJob)
                    .where(
                        ReliabilityJob.owner_service == OWNER_SERVICE,
                        ReliabilityJob.job_type == JOB_TYPE_RUN,
                        ReliabilityJob.status.in_(("PENDING", "CLAIMED")),
                    )
                    .order_by(ReliabilityJob.created_at)
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            if (job.payload or {}).get("run_id") == str(run.id):
                await JobEngine.cancel(session, job.id, expected_revision=job.revision)
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.cancel_run",
            actor_id="00000000-0000-0000-0000-000000000000", actor_role="system",
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied", payload={"run_id": str(run.id)},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[("conversation.run.cancelled", {"run_id": str(run.id)})],
        )
        return await self._assemble_workspace(session, srow)

    async def _apply_edits(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """basic mode 可编辑路径：教师手工编辑 → 确定性上下文 + teacher 提案（无 LLM）。"""
        from .schema import PrepIntent

        fields = payload.get("fields") or {}
        try:
            intent = PrepIntent(
                textbook_id=fields.get("source_id") or fields.get("textbook_id"),
                section_id=fields.get("section_id"),
                class_id=fields.get("class_id"),
                knowledge_points=fields.get("knowledge_points"),
                lesson_scope=fields.get("lesson_scope"),
                lesson_hours=fields.get("lesson_hours"),
                objectives=(
                    [
                        {"text": o["text"], "source_ref": o.get("source_ref")}
                        for o in (fields.get("objectives") or [])
                    ]
                    or None
                ),
                intent_type="modify",
            )
        except Exception as exc:  # noqa: BLE001 —— pydantic ValidationError
            raise ApiError(ErrorCode.VALIDATION, f"编辑字段校验失败：{exc}") from exc

        selection = apply_intent(await self._current_selection(session, str(srow.id)), intent)
        if not selection_complete(selection):
            raise InvalidActionError("apply_edits", "编辑后仍缺教材/章节（班级可选，未绑定不影响备课）")
        member_role = await self._verify_membership(
            adapter, token=token, class_id=selection.get("class_id") or _class_ref(srow)
        )
        if intent.class_id is not None and str(intent.class_id) != _class_ref(srow):
            srow.class_id = uuid.UUID(intent.class_id)

        ctx = await self._build_context(session, selection, srow)
        if ctx is None:
            raise InvalidActionError("apply_edits", "所选教材/章节不可用（fail closed）")
        snap = await self._persist_context(session, srow, ctx)
        srow.context_snapshot_id = snap.id

        # teacher 提案（source='teacher'，无 LLM，直接落 proposal 快照）
        body = {
            "schema_version": "0.1.0",
            "context_snapshot_hash": snap.snapshot_hash,
            "title": selection.get("lesson_scope") or f"备课 {selection.get('section_id')}",
            "objectives": [
                {"text": o.get("text"), "source_ref": o.get("source_ref")}
                for o in (selection.get("objectives") or [])
            ],
            "prior_knowledge": [],
            "concepts": [],
            "theorems": [],
            "examples": [],
            "inquiries": [],
            "design_intent": [],
            "key_points": [
                {"text": kp, "source_ref": None} for kp in (selection.get("knowledge_points") or [])
            ],
            "difficult_points": [],
            "lesson_flow": [],
            "warnings_ack": [],
        }
        brow = TeachingBlueprintRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            status=sm.BLUEPRINT_PROPOSAL,
            revision=1,
            title=body["title"],
            body=_jsonable(body),
        )
        session.add(brow)
        # 先落 blueprint 行，再写 revision（避免 autoflush 把 revision 插在 blueprint 之前导致 FK 失败）
        await session.flush()
        session.add(
            BlueprintRevisionRow(
                blueprint_id=brow.id,
                revision_no=1,
                body=_jsonable(body),
                verdict=sm.BLUEPRINT_PROPOSAL,
                source="teacher",
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
            )
        )
        msg_seq = await self._next_message_seq(session, srow.id)
        user_msg = ConversationMessageRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            seq=msg_seq,
            role="user",
            kind="text",
            component_type="user_text",
            component_props=validate_component(
                "user_text", {"text": (payload.get("summary") or "教师编辑提交")[:400]}
            ),
            text=payload.get("summary") or "教师编辑提交",
            intent=intent_dto(intent),
            intent_hash=selection.get("intent_hash"),
            status="applied",
        )
        session.add(user_msg)
        await session.flush()
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.apply_edits",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"intent_hash": selection.get("intent_hash"), "snapshot_hash": snap.snapshot_hash},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[
                ("conversation.message.created", {"seq": msg_seq}),
                ("conversation.context.updated", {"snapshot_id": str(snap.id), "snapshot_hash": snap.snapshot_hash}),
                ("blueprint.proposed", {"source": "teacher", "edits": True}),
            ],
        )
        return await self._assemble_workspace(session, srow)

    async def _select_class(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        class_id = payload.get("class_id")
        if not class_id:
            raise ApiError(ErrorCode.VALIDATION, "缺少 class_id")
        member_role = await self._verify_membership(adapter, token=token, class_id=class_id)
        old = _class_ref(srow)
        srow.class_id = uuid.UUID(class_id)
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.select_class",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied", payload={"from": old, "to": class_id},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[("conversation.class.updated", {"from": old, "to": class_id})],
        )
        return await self._assemble_workspace(session, srow)

    async def _propose_revision(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """对已确认蓝图发起教师修订提案（revision proposal；无 LLM，教师编辑即提案）。"""
        if not target:
            raise InvalidActionError("propose_revision", "缺少 blueprint_id")
        brow = await self._lock_blueprint(session, target)
        if brow.session_id != srow.id:
            raise MembershipDeniedError("蓝图不属于该会话")
        fields = payload.get("fields") or {}
        body = dict(brow.body or {})
        if fields.get("objectives") is not None:
            body["objectives"] = [
                {"text": o["text"], "source_ref": o.get("source_ref")} for o in fields["objectives"]
            ]
        for key in ("key_points", "difficult_points", "lesson_flow"):
            if fields.get(key) is not None:
                body[key] = fields[key]
        member_role = await self._verify_membership(
            adapter, token=token, class_id=_class_ref(srow)
        )
        sm.assert_transition("blueprint", brow.status, sm.BLUEPRINT_PROPOSAL)
        prev_no = brow.revision
        brow.status = sm.BLUEPRINT_PROPOSAL
        brow.revision += 1
        brow.body = _jsonable(body)
        brow.confirmed_at, brow.confirmed_by = None, None
        brow.rejected_at, brow.rejected_by, brow.rejected_reason = None, None, None
        session.add(
            BlueprintRevisionRow(
                blueprint_id=brow.id,
                revision_no=brow.revision,
                body=_jsonable(body),
                verdict=sm.BLUEPRINT_PROPOSAL,
                source="teacher",
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
                parent_revision_no=prev_no,
                note=(payload or {}).get("note"),
            )
        )
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.propose_revision",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied", payload={"blueprint_id": str(brow.id), "revision_no": brow.revision},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[("blueprint.proposed", {"actor_id": actor_id, "source": "teacher", "revision_no": brow.revision})],
        )
        return await self._assemble_workspace(session, srow)

    async def _load_context_from_snapshot(
        self, session: AsyncSession, srow: TeachingSessionRow
    ) -> TextbookTeachingContext | None:
        if not srow.context_snapshot_id:
            return None
        snap = (
            await session.execute(
                select(ContextSnapshotRow).where(ContextSnapshotRow.id == srow.context_snapshot_id)
            )
        ).scalar_one_or_none()
        if snap is None or not snap.snapshot:
            return None
        try:
            return TextbookTeachingContext.model_validate(snap.snapshot)
        except Exception:
            return None

    async def _load_published_profile_ref(
        self, session: AsyncSession, srow: TeachingSessionRow
    ) -> tuple[dict[str, Any] | None, Any]:
        """返回 (profile_ref, profile_row)；无 published 画像时 (None, None)。"""
        from app.teacher_prep.profile.models import ClassLearningProfileRow

        row = (
            await session.execute(
                select(ClassLearningProfileRow)
                .where(ClassLearningProfileRow.session_id == srow.id)
                .order_by(ClassLearningProfileRow.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None or row.status != "published":
            return None, None
        ref = {
            "profile_id": str(row.id),
            "profile_revision": row.revision,
            "status": row.status,
            "coverage": row.coverage,
            "warnings": row.warnings or [],
        }
        return ref, row

    async def _propose_blueprint_revision_action(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """L-221-2：AI 修订提案（confirmed blueprint + published profile → service）。"""
        from app.teacher_prep.profile.schema import VersionedClassLearningProfile
        from app.teacher_prep.service import TeacherPrepService

        if not target:
            raise InvalidActionError("propose_blueprint_revision", "缺少 blueprint_id（target）")
        _require_teaching_role_or_403(actor_role_hint)
        ctx = await self._load_context_from_snapshot(session, srow)
        if ctx is None:
            raise InvalidActionError(
                "propose_blueprint_revision", "会话无教学上下文快照（请先完成备课选择）"
            )
        profile_ref, profile_row = await self._load_published_profile_ref(session, srow)
        if profile_row is None:
            raise InvalidActionError(
                "propose_blueprint_revision",
                "需要 published 学情画像才能生成 AI 修订提案（当前无已发布画像）",
            )
        profile = VersionedClassLearningProfile.model_validate(profile_row.body)
        brow = await self._lock_blueprint(session, target)
        if brow.session_id != srow.id:
            raise MembershipDeniedError("蓝图不属于该会话")
        member_role = await self._verify_membership(
            adapter, token=token, class_id=_class_ref(srow)
        )
        _require_teaching_role_or_403(member_role)

        svc = TeacherPrepService()
        llm = self._llm
        if llm is None:
            raise InvalidActionError("propose_blueprint_revision", "AI 生成器未配置")

        async def _llm_async(prompt: str) -> str:
            out = llm(prompt)
            if hasattr(out, "__await__"):
                return await out  # type: ignore[misc]
            return out

        result = await svc.propose_blueprint_revision(
            session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            session_id=str(srow.id),
            blueprint_id=str(brow.id),
            expected_revision=brow.revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            context=ctx,
            profile=profile,
            llm_generate=_llm_async,
        )
        del result, profile_ref
        return await self._assemble_workspace(session, srow)

    async def _project_specs_action(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """L-221-2：confirmed blueprint + context → LessonPlanSpec（+可选 deck）。"""
        from app.teacher_prep.service import TeacherPrepService
        from app.teacher_prep.specs.projector import ProjectionRunLineage

        if not target:
            raise InvalidActionError("project_specs", "缺少 blueprint_id（target）")
        _require_teaching_role_or_403(actor_role_hint)
        ctx = await self._load_context_from_snapshot(session, srow)
        if ctx is None:
            raise InvalidActionError("project_specs", "会话无教学上下文快照（请先完成备课选择）")
        profile_ref, _ = await self._load_published_profile_ref(session, srow)
        brow = await self._lock_blueprint(session, target)
        if brow.session_id != srow.id:
            raise MembershipDeniedError("蓝图不属于该会话")
        if brow.status != "confirmed":
            raise InvalidActionError("project_specs", f"蓝图未确认（{brow.status}）")
        member_role = await self._verify_membership(
            adapter, token=token, class_id=_class_ref(srow)
        )
        _require_teaching_role_or_403(member_role)

        kinds = tuple(payload.get("kinds") or ("lesson_plan",))
        lineage = ProjectionRunLineage(
            prompt_release_id=srow.prompt_release_id or "conversation-default",
            ai_run_id=srow.ai_run_id or f"action:{idempotency_key[:16]}",
            model="teacher-prep-specs-v1",
        )
        svc = TeacherPrepService()
        await svc.project_specs(
            session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            session_id=str(srow.id),
            blueprint_id=str(brow.id),
            expected_revision=brow.revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            context=ctx,
            profile_ref=profile_ref or {"status": "none"},
            lineage=lineage,
            kinds=kinds,
            manage_idempotency=False,
        )
        return await self._assemble_workspace(session, srow)

    # ── R18 学前诊断（教师侧闭环）─────────────────────────────────────────────

    async def _propose_diagnostic(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        target: str | None,
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """已确认蓝图 → 生成学前诊断 proposal（LLM 在 worker 投影；本命令只入队）。

        范围闸门：蓝图未确认 / 无上下文快照 / 该蓝图已有活跃诊断 → 显式 409；
        在途诊断 run → 幂等 no-op（不重建作业）。
        """
        if not target:
            raise InvalidActionError("propose_diagnostic", "缺少 blueprint_id（target）")
        brow = await self._lock_blueprint(session, target)
        if brow.session_id != srow.id:
            raise MembershipDeniedError("蓝图不属于该会话（跨会话访问拒绝）")
        if brow.status != sm.BLUEPRINT_CONFIRMED:
            raise InvalidActionError(
                "propose_diagnostic", f"蓝图未确认（{brow.status}）——仅已确认蓝图可生成学前诊断建议"
            )
        member_role = await self._verify_membership(adapter, token=token, class_id=_class_ref(srow))
        _require_teaching_role_or_403(actor_role_hint)
        _require_teaching_role_or_403(member_role)
        if srow.context_snapshot_id is None:
            raise InvalidActionError("propose_diagnostic", "会话无教学上下文快照（请先完成备课选择）")
        existing = (
            (
                await session.execute(
                    select(DiagnosticTaskRow)
                    .where(DiagnosticTaskRow.blueprint_id == brow.id)
                    .order_by(DiagnosticTaskRow.created_at.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if existing is not None and existing.status != sm.DIAGNOSTIC_SKIPPED:
            raise InvalidActionError(
                "propose_diagnostic",
                f"该蓝图已有诊断任务（{existing.status}）——请编辑/发布/跳过后再操作",
            )
        inflight = (
            (
                await session.execute(
                    select(MessageRunRow)
                    .where(
                        MessageRunRow.session_id == srow.id,
                        MessageRunRow.run_type == "diagnostic",
                        MessageRunRow.status.in_(("PENDING", "RUNNING")),
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if inflight is not None:
            return await self._assemble_workspace(session, srow)  # 幂等 no-op

        run_row = MessageRunRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            message_id=None,
            run_type="diagnostic",
            status="PENDING",
            attempt=1,
            result_ref=str(brow.id),
        )
        session.add(run_row)
        await session.flush()
        job = await JobEngine.create(
            session,
            owner_service=OWNER_SERVICE,
            job_type=JOB_TYPE_RUN,
            payload={
                "run_kind": "diagnostic",
                "session_id": str(srow.id),
                "run_id": str(run_row.id),
                "blueprint_id": str(brow.id),
                "class_id": _class_ref(srow),
                "verified_role": member_role,
                "verified_actor": actor_id,
            },
            dedupe_key=f"diagnostic:{run_row.id}",
            timeout_s=300,
        )
        msg_seq = await self._next_message_seq(session, srow.id)
        session.add(
            self._make_message(
                srow.id,
                seq_hint=msg_seq,
                role="assistant",
                kind="job",
                component_type="job_status_card",
                component_props={
                    "job_id": str(job.id),
                    "job_type": JOB_TYPE_RUN,
                    "status": "PENDING",
                    "attempt": 1,
                    "max_attempts": 5,
                    "copy_keys": {"title": "prep.diagnostic.generating"},
                },
                run_id=run_row.id,
            )
        )
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.propose_diagnostic",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"blueprint_id": str(brow.id), "run_id": str(run_row.id), "job_id": str(job.id)},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[
                ("conversation.message.created", {"seq": msg_seq}),
                ("conversation.diagnostic.enqueued", {"run_id": str(run_row.id), "job_id": str(job.id)}),
            ],
        )
        return await self._assemble_workspace(session, srow)

    async def _edit_diagnostic(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """教师编辑草稿（改题/删题/调整预计时长共用一条编辑命令）。

        - 仅 proposal/draft 可编辑（终态 409）；角色须为教学角色（执行瞬间复查）；
        - 载荷经 DiagnosticDraft schema 校验（时长 5–10、题量 1–8、item_no 唯一、禁 HTML）；
        - 范围铁律同样约束教师编辑：kp 超出允许范围 → fail closed（不得偷偷扩围）。
        """
        if not target:
            raise InvalidActionError("edit_diagnostic", "缺少 diagnostic_id（target）")
        drow = await self._lock_diagnostic(session, target)
        if drow.session_id != srow.id:
            raise MembershipDeniedError("诊断任务不属于该会话")
        member_role = await self._verify_membership(adapter, token=token, class_id=_class_ref(srow))
        _require_teaching_role_or_403(actor_role_hint)
        _require_teaching_role_or_403(member_role)
        sm.assert_transition("diagnostic", drow.status, sm.DIAGNOSTIC_DRAFT)
        raw = payload.get("draft")
        if not isinstance(raw, dict):
            raise ApiError(ErrorCode.VALIDATION, "缺少 draft 载荷（title/description/estimated_duration_minutes/items）")
        try:
            draft = DiagnosticDraft.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 —— pydantic ValidationError → 422 语义
            raise ApiError(
                ErrorCode.VALIDATION,
                f"诊断草稿校验失败（时长 5–10 分钟、题量 1–8、item_no 唯一、禁 HTML）：{exc}",
            ) from exc
        allowed = await self._diagnostic_allowed_scope(session, drow)
        violations = scope_violations(
            kps=[*draft.knowledge_points, *(it.kp for it in draft.items)], allowed=allowed
        )
        if violations:
            raise ApiError(
                ErrorCode.VALIDATION,
                f"知识点超出本课允许范围（不得偷偷扩大知识点范围）：{'、'.join(violations[:5])}",
            )

        body = draft.model_dump(mode="json")
        prev_no = drow.revision
        drow.status = sm.DIAGNOSTIC_DRAFT
        drow.revision += 1
        drow.title = draft.title
        drow.description = draft.description
        drow.estimated_duration_minutes = draft.estimated_duration_minutes
        drow.body = _jsonable(body)
        session.add(
            DiagnosticRevisionRow(
                diagnostic_id=drow.id,
                revision_no=drow.revision,
                body=_jsonable(body),
                verdict=sm.DIAGNOSTIC_DRAFT,
                source="teacher",
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
                parent_revision_no=prev_no,
                note=payload.get("note"),
            )
        )
        msg_seq = await self._next_message_seq(session, srow.id)
        session.add(
            self._make_message(
                srow.id,
                seq_hint=msg_seq,
                role="assistant",
                kind="diagnostic",
                component_type="diagnostic_card",
                component_props=self._diagnostic_card_props(drow),
            )
        )
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.edit_diagnostic",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"diagnostic_id": str(drow.id), "revision_no": drow.revision,
                     "items": len(draft.items), "duration": draft.estimated_duration_minutes},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[
                ("conversation.message.created", {"seq": msg_seq}),
                ("diagnostic.edited", {"diagnostic_id": str(drow.id), "revision_no": drow.revision}),
            ],
        )
        return await self._assemble_workspace(session, srow)

    async def _publish_diagnostic(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        """发布阶段 1（幂等壳事务内）：校验 + 持久 PendingAction + 发布 run PENDING。

        返回 prepared dict → run_action 在幂等壳提交后调用 `_publish_execute`
        （阶段 2/3 在事务外：执行瞬间复查 + 8000 幂等创建 + 短事务落定）。
        """
        if not target:
            raise InvalidActionError("publish_diagnostic", "缺少 diagnostic_id（target）")
        drow = await self._lock_diagnostic(session, target)
        if drow.session_id != srow.id:
            raise MembershipDeniedError("诊断任务不属于该会话")
        _require_class_id("publish_diagnostic", srow)
        member_role = await self._verify_membership(adapter, token=token, class_id=_class_ref(srow))
        _require_teaching_role_or_403(actor_role_hint)
        _require_teaching_role_or_403(member_role)
        if drow.status == sm.DIAGNOSTIC_PUBLISHED:
            # 幂等 no-op：重复确认绝不二次创建 Assignment
            return {"publish_noop": True, "diagnostic_id": str(drow.id), "session_id": str(srow.id)}
        sm.assert_transition("diagnostic", drow.status, sm.DIAGNOSTIC_PUBLISHED)
        inflight = (
            (
                await session.execute(
                    select(MessageRunRow)
                    .where(
                        MessageRunRow.session_id == srow.id,
                        MessageRunRow.run_type == "diagnostic_publish",
                        MessageRunRow.status.in_(("PENDING", "RUNNING")),
                        MessageRunRow.result_ref == str(drow.id),
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if inflight is not None:
            return {"publish_noop": True, "diagnostic_id": str(drow.id), "session_id": str(srow.id), "pending": True}

        due_at = default_due_at()
        raw_due = payload.get("due_at")
        if raw_due:
            from datetime import datetime as _dt

            try:
                due_at = _dt.fromisoformat(str(raw_due).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ApiError(ErrorCode.VALIDATION, "due_at 必须是 ISO 8601 时间") from exc

        from .schema import canonical_json

        fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "diagnostic_id": str(drow.id),
                    "revision": drow.revision,
                    "body": drow.body,
                    "due_at": due_at.isoformat(),
                    "class_id": _class_ref(srow),
                }
            ).encode("utf-8")
        ).hexdigest()

        # 持久 PendingAction（迁移表 pending_actions； DECISIONS §5）
        store = self._pending_store()
        action_id = f"diag-publish:{drow.id}:{fingerprint[:12]}"
        existing_action = await store.get(action_id)
        if existing_action is not None and existing_action.fingerprint != fingerprint:
            raise ApiError(ErrorCode.CONFLICT, "发布审批指纹不匹配（内容已变化）——请重新发起发布")
        if existing_action is None:
            from app.agent.pending_actions import DEFAULT_TTL_SECONDS, PendingAction

            await store.create(
                PendingAction(
                    action_id=action_id,
                    session_id=str(srow.id),
                    teacher_id=actor_id,
                    tool="publish_diagnostic",
                    card={
                        "diagnostic_id": str(drow.id),
                        "title": drow.title or (drow.body or {}).get("title") or "",
                        "revision": drow.revision,
                        "estimated_duration_minutes": drow.estimated_duration_minutes,
                        "due_at": due_at.isoformat(),
                        "class_id": _class_ref(srow),
                    },
                    fingerprint=fingerprint,
                    expires_at=_now().timestamp() + DEFAULT_TTL_SECONDS,
                )
            )

        run_row = MessageRunRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            message_id=None,
            run_type="diagnostic_publish",
            status="PENDING",
            attempt=1,
            result_ref=str(drow.id),
        )
        session.add(run_row)
        await session.flush()
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.publish_diagnostic",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"diagnostic_id": str(drow.id), "pending_action_id": action_id,
                     "due_at": due_at.isoformat()},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[
                ("conversation.diagnostic.publish_requested",
                 {"diagnostic_id": str(drow.id), "run_id": str(run_row.id), "pending_action_id": action_id}),
            ],
        )
        return {
            "publish_prepared": True,
            "session_id": str(srow.id),
            "diagnostic_id": str(drow.id),
            "run_id": str(run_row.id),
            "pending_action_id": action_id,
            "fingerprint": fingerprint,
            "revision": drow.revision,
            "class_id": _class_ref(srow),
            "due_at": due_at.isoformat(),
            "actor_id": actor_id,
            "idempotency_key": f"diag-publish:{drow.id}",
        }

    async def _publish_execute(
        self,
        session: AsyncSession,  # noqa: ARG002 —— 阶段 2/3 使用独立短事务，不复用请求事务
        *,
        prepared: dict[str, Any],
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
    ) -> WorkspaceSnapshot:
        """发布阶段 2/3（幂等壳外）：8000 幂等创建 + 独立短事务落定。

        - 已发布 → 直接返回工作区（重复确认绝不二次创建 Assignment）；
        - 执行瞬间复查：membership（8000 现场）+ 角色（8000 裁决）+ fingerprint/revision（落定前）；
        - 8000/网络失败 → run FAILED + `diagnostic.publish.failed`，UI 不误报成功；
          再次发布（新 key）走同一稳定映射键安全重试，绝不重建 Assignment。
        """
        factory = self._db_factory()
        diagnostic_id = prepared["diagnostic_id"]
        run_id = prepared["run_id"]
        due_at = datetime.fromisoformat(prepared["due_at"])

        # 快捷幂等：已发布 → 返回工作区
        async with factory() as s0:
            d0 = (
                await s0.execute(
                    select(DiagnosticTaskRow).where(DiagnosticTaskRow.id == uuid.UUID(diagnostic_id))
                )
            ).scalar_one_or_none()
            if d0 is not None and d0.status == sm.DIAGNOSTIC_PUBLISHED:
                srow0 = await self._get_session(s0, str(d0.session_id))
                return await self._assemble_workspace(s0, srow0)

        # 阶段 2A：落库前 fingerprint/revision 复查（教师并发编辑 → 放弃本次执行）
        async with factory() as s1:
            drow = (
                await s1.execute(
                    select(DiagnosticTaskRow).where(DiagnosticTaskRow.id == uuid.UUID(diagnostic_id))
                )
            ).scalar_one_or_none()
            if drow is None:
                raise PrepSessionGone(diagnostic_id)
            from .schema import canonical_json

            fp_now = hashlib.sha256(
                canonical_json(
                    {
                        "diagnostic_id": str(drow.id),
                        "revision": drow.revision,
                        "body": drow.body,
                        "due_at": prepared["due_at"],
                        "class_id": prepared["class_id"],
                    }
                ).encode("utf-8")
            ).hexdigest()
            if fp_now != prepared["fingerprint"] or drow.revision != prepared["revision"]:
                await self._publish_fail(
                    factory, run_id=run_id, actor_id=actor_id,
                    label="publish_fingerprint_conflict",
                    detail="发布内容在审批后发生变化（revision/fingerprint 不一致）",
                )
                srow1 = await self._get_session(s1, str(drow.session_id))
                return await self._assemble_workspace(s1, srow1)
            if drow.status not in (sm.DIAGNOSTIC_PROPOSAL, sm.DIAGNOSTIC_DRAFT):
                srow1 = await self._get_session(s1, str(drow.session_id))
                return await self._assemble_workspace(s1, srow1)
            run1 = (
                await s1.execute(select(MessageRunRow).where(MessageRunRow.id == uuid.UUID(run_id)))
            ).scalar_one_or_none()
            if run1 is not None and run1.status in ("FAILED", "CANCELLED"):
                # 同 key 重放首失败结果：重试须以新幂等键发起（新发布 run）
                srow1 = await self._get_session(s1, str(drow.session_id))
                return await self._assemble_workspace(s1, srow1)

        # 阶段 2B：执行瞬间成员/角色重验（8000 现场裁决）+ 8000 幂等创建（事务外）
        try:
            member_role = await self._verify_membership(adapter, token=token, class_id=prepared["class_id"])
            _require_teaching_role_or_403(member_role)
            async with factory() as s1b:
                drow_b = (
                    await s1b.execute(
                        select(DiagnosticTaskRow).where(DiagnosticTaskRow.id == uuid.UUID(diagnostic_id))
                    )
                ).scalar_one_or_none()
                if drow_b is None:
                    raise PrepSessionGone(diagnostic_id)
                assignment = build_task_assignment(
                    drow_b,
                    tenant_id=prepared["class_id"],
                    class_id=prepared["class_id"],
                    session_id=prepared["session_id"],
                    owner_id=prepared["actor_id"],
                    due_at=due_at,
                    idempotency_key=prepared["idempotency_key"],
                )
                # tel F1：学前诊断经内联题组直发（8000 CreateAssignmentRequest.quiz_items，
                # 与 artifact_id 二选一）——诊断内容物化为真实 Quiz+QuizItems，学生可作答；
                # 不再空 artifact_id（必 422）。
                quiz_items = diagnostic_items_to_quiz_items(drow_b.body or {})
            ref = await adapter.create_assignment(
                token=token,
                assignment=assignment,
                creator_id=prepared["actor_id"],
                title=(drow_b.title or (drow_b.body or {}).get("title") or None),
                quiz_items=quiz_items,
            )
            # tel T3：8000 create_assignment 恒建 draft（assessment.create_assignment，
            # 学生侧 /api/student/assignments 仅见 published）——创建成功后必须补显式
            # publish，否则学生永远看不到该任务。publish 失败走同一 _publish_fail
            # （run FAILED 不误报成功）；重试经同一稳定映射键幂等回放 create 后重发 publish。
            await adapter.publish_assignment(
                token=token,
                assignment_id=ref.external_id,
                client_request_id=prepared["idempotency_key"],
                idempotency_key=f"diag-publish:{diagnostic_id}:publish",
            )
        except ContractMembershipDenied as exc:
            await self._publish_fail(
                factory, run_id=run_id, actor_id=actor_id,
                label="membership_denied", detail=str(exc)[:300],
            )
            return await self._publish_workspace(factory, diagnostic_id)
        except (ContractAuthorityUnreachable, ContractIdempotencyConflict) as exc:
            await self._publish_fail(
                factory, run_id=run_id, actor_id=actor_id,
                label="authority_unreachable" if isinstance(exc, ContractAuthorityUnreachable) else "idempotency_conflict",
                detail=str(exc)[:300],
            )
            return await self._publish_workspace(factory, diagnostic_id)
        except MembershipDeniedError as exc:
            await self._publish_fail(
                factory, run_id=run_id, actor_id=actor_id,
                label="membership_denied", detail=str(exc)[:300],
            )
            return await self._publish_workspace(factory, diagnostic_id)
        except AuthorityUnreachableError as exc:
            await self._publish_fail(
                factory, run_id=run_id, actor_id=actor_id,
                label="authority_unreachable", detail=str(exc)[:300],
            )
            return await self._publish_workspace(factory, diagnostic_id)
        except Exception as exc:  # noqa: BLE001 —— 兜底：任何执行异常一律落 FAILED，绝不误报成功
            await self._publish_fail(
                factory, run_id=run_id, actor_id=actor_id,
                label="publish_error", detail=str(exc)[:300],
            )
            return await self._publish_workspace(factory, diagnostic_id)

        # 阶段 3：短事务落定（published + provision + 版本快照 + PendingAction APPROVED）
        store = self._pending_store()
        async with factory() as s2:
            drow2 = (
                (
                    await s2.execute(
                        select(DiagnosticTaskRow)
                        .where(DiagnosticTaskRow.id == uuid.UUID(diagnostic_id))
                        .with_for_update()
                    )
                )
                .scalar_one_or_none()
            )
            run2 = (
                await s2.execute(select(MessageRunRow).where(MessageRunRow.id == uuid.UUID(run_id)))
            ).scalar_one_or_none()
            if drow2 is None or run2 is None:
                raise PrepSessionGone(diagnostic_id)
            if drow2.status == sm.DIAGNOSTIC_PUBLISHED and run2.status == "COMPLETED":
                srow2 = await self._get_session(s2, str(drow2.session_id))
                return await self._assemble_workspace(s2, srow2)  # 并发落定 → 幂等
            srow2 = await self._lock_session(s2, str(drow2.session_id))
            prev_no = drow2.revision
            drow2.status = sm.DIAGNOSTIC_PUBLISHED
            drow2.revision += 1
            drow2.published_at = _now()
            drow2.published_by = uuid.UUID(prepared["actor_id"])
            drow2.provision = {
                "authority_service": "api8000",
                "assignment_id": ref.external_id,
                "mapping_key": ref.key[:64],
                "due_at": prepared["due_at"],
                "adapter": "coursework",
            }
            pa = await store.confirm(
                prepared["pending_action_id"],
                approved=True,
                params={"assignment_id": ref.external_id},
                by=prepared["actor_id"],
            )
            if pa is None:
                pa = await store.get(prepared["pending_action_id"])
            if pa is not None:
                try:
                    drow2.pending_action_id = uuid.UUID(pa.id)
                except ValueError:
                    drow2.pending_action_id = None
            s2.add(
                DiagnosticRevisionRow(
                    diagnostic_id=drow2.id,
                    revision_no=drow2.revision,
                    body=_jsonable(drow2.body),
                    verdict=sm.DIAGNOSTIC_PUBLISHED,
                    source="teacher",
                    actor_id=uuid.UUID(prepared["actor_id"]),
                    actor_role=member_role,
                    parent_revision_no=prev_no,
                )
            )
            run2.status = "COMPLETED"
            run2.completed_at = _now()
            run2.provider_error = None
            run2.result_ref = str(drow2.id)
            msg_seq = await self._next_message_seq(s2, srow2.id)
            s2.add(
                self._make_message(
                    srow2.id,
                    seq_hint=msg_seq,
                    role="assistant",
                    kind="diagnostic",
                    component_type="diagnostic_card",
                    component_props=self._diagnostic_card_props(drow2),
                )
            )
            srow2.revision += 1
            srow2.last_command_at = _now()
            cmd2 = SessionCommandRow(
                session_id=srow2.id,
                command_type="conversation.action.publish_diagnostic.execute",
                actor_id=uuid.UUID(prepared["actor_id"]),
                actor_role=member_role,
                class_id=srow2.class_id,
                idempotency_key=prepared["pending_action_id"][:128],
                request_hash=prepared["fingerprint"],
                expected_revision=srow2.revision,
                result_status="applied",
                payload=_jsonable({"diagnostic_id": str(drow2.id), "assignment_id": ref.external_id}),
            )
            s2.add(cmd2)
            await s2.flush()
            parent: uuid.UUID | None = None
            seq = srow2.last_event_seq
            for event_type, ev_payload in [
                ("command.applied", {}),
                ("diagnostic.published",
                 {"diagnostic_id": str(drow2.id), "assignment_id": ref.external_id,
                  "actor_id": prepared["actor_id"]}),
                ("conversation.message.created", {"seq": msg_seq}),
            ]:
                seq += 1
                ev_id = uuid.uuid4()
                s2.add(
                    SessionEventRow(
                        id=ev_id,
                        session_id=srow2.id,
                        command_id=cmd2.id,
                        seq=seq,
                        event_type=event_type,
                        payload=_jsonable(ev_payload),
                        parent_event_id=parent,
                        revision=srow2.revision,
                    )
                )
                if parent is None:
                    parent = ev_id
            srow2.last_event_seq = seq
            srow2.updated_at = _now()
            await s2.commit()
        return await self._publish_workspace(factory, diagnostic_id)

    async def _publish_fail(
        self,
        factory: Any,
        *,
        run_id: str,
        actor_id: str,
        label: str,
        detail: str,
    ) -> None:
        """发布失败落定（独立短事务）：run FAILED + publish.failed 事件；诊断保持原状态。"""
        import app.teacher_prep.conversation.handler as _handler

        async with factory() as s:
            run = (
                await s.execute(select(MessageRunRow).where(MessageRunRow.id == uuid.UUID(run_id)))
            ).scalar_one_or_none()
            if run is None or run.status in ("COMPLETED", "FAILED"):
                await s.commit()
                return
            srow = await _handler._lock_session(s, str(run.session_id))
            srow.revision += 1
            srow.last_command_at = _now()
            run.status = "FAILED"
            run.completed_at = _now()
            run.provider_error = label
            # tp_session_commands.result_status 受 CHECK 约束（applied/replayed/conflict/
            # denied/invalid_transition/authority）→ 按失败类别归一到合法口径
            result_status = {
                "membership_denied": "denied",
                "authority_unreachable": "authority",
                "publish_fingerprint_conflict": "conflict",
                "idempotency_conflict": "conflict",
            }.get(label, "conflict")
            cmd = SessionCommandRow(
                session_id=srow.id,
                command_type="conversation.action.publish_diagnostic.execute",
                actor_id=uuid.UUID(actor_id) if actor_id else uuid.UUID(int=0),
                actor_role="teacher",
                class_id=srow.class_id,
                idempotency_key=f"publish-fail:{run_id}",
                request_hash=f"publish-fail:{run_id}",
                expected_revision=srow.revision,
                result_status=result_status,
                payload=_jsonable({"diagnostic_run_id": run_id, "label": label, "detail": detail}),
            )
            s.add(cmd)
            await s.flush()
            parent: uuid.UUID | None = None
            seq = srow.last_event_seq
            for event_type, ev_payload in [
                ("command.applied", {}),
                ("diagnostic.publish.failed",
                 {"run_id": run_id, "error_label": label, "retryable": True}),
            ]:
                seq += 1
                ev_id = uuid.uuid4()
                s.add(
                    SessionEventRow(
                        id=ev_id,
                        session_id=srow.id,
                        command_id=cmd.id,
                        seq=seq,
                        event_type=event_type,
                        payload=_jsonable(ev_payload),
                        parent_event_id=parent,
                        revision=srow.revision,
                    )
                )
                if parent is None:
                    parent = ev_id
            srow.last_event_seq = seq
            srow.updated_at = _now()
            await s.commit()

    async def _publish_workspace(self, factory: Any, diagnostic_id: str) -> WorkspaceSnapshot:
        async with factory() as s:
            drow = (
                await s.execute(
                    select(DiagnosticTaskRow).where(DiagnosticTaskRow.id == uuid.UUID(diagnostic_id))
                )
            ).scalar_one_or_none()
            if drow is None:
                raise PrepSessionGone(diagnostic_id)
            srow = await self._get_session(s, str(drow.session_id))
            return await self._assemble_workspace(s, srow)

    async def _skip_diagnostic(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        target: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> WorkspaceSnapshot:
        """明确跳过（终态 + 审计）：skipped_reason 落库 + 版本快照 + 在途审批 REJECTED。"""
        if not target:
            raise InvalidActionError("skip_diagnostic", "缺少 diagnostic_id（target）")
        drow = await self._lock_diagnostic(session, target)
        if drow.session_id != srow.id:
            raise MembershipDeniedError("诊断任务不属于该会话")
        member_role = await self._verify_membership(adapter, token=token, class_id=_class_ref(srow))
        _require_teaching_role_or_403(actor_role_hint)
        _require_teaching_role_or_403(member_role)
        sm.assert_transition("diagnostic", drow.status, sm.DIAGNOSTIC_SKIPPED)
        reason = (payload.get("reason") or "")[:500] or None
        prev_no = drow.revision
        drow.status = sm.DIAGNOSTIC_SKIPPED
        drow.revision += 1
        drow.skipped_at = _now()
        drow.skipped_by = uuid.UUID(actor_id)
        drow.skipped_reason = reason
        session.add(
            DiagnosticRevisionRow(
                diagnostic_id=drow.id,
                revision_no=drow.revision,
                body=_jsonable(drow.body),
                verdict=sm.DIAGNOSTIC_SKIPPED,
                source="teacher",
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
                parent_revision_no=prev_no,
                note=reason,
            )
        )
        msg_seq = await self._next_message_seq(session, srow.id)
        session.add(
            self._make_message(
                srow.id,
                seq_hint=msg_seq,
                role="assistant",
                kind="diagnostic",
                component_type="diagnostic_card",
                component_props=self._diagnostic_card_props(drow),
            )
        )
        srow.revision += 1
        srow.last_command_at = _now()
        cmd_id = await self._log_command(
            session, srow=srow, command_type="conversation.action.skip_diagnostic",
            actor_id=actor_id, actor_role=member_role,
            idempotency_key=idempotency_key, request_hash=request_hash,
            result_status="applied",
            payload={"diagnostic_id": str(drow.id), "reason": reason},
        )
        await self._log_events(
            session, srow=srow, command_id=cmd_id,
            events=[
                ("conversation.message.created", {"seq": msg_seq}),
                ("diagnostic.skipped", {"diagnostic_id": str(drow.id), "reason": reason, "actor_id": actor_id}),
            ],
        )
        # 审计联动：该诊断在途发布审批 → REJECTED（教师改跳过，审批作废留痕）
        store = self._pending_store()
        try:
            for pa in await store.list_by_teacher(actor_id, include_handled=False):
                if pa.tool == "publish_diagnostic" and (pa.card or {}).get("diagnostic_id") == str(drow.id):
                    await store.confirm(pa.action_id, approved=False, params={"skipped": True}, by=actor_id)
        except Exception:  # noqa: BLE001 —— 审批联动失败不阻塞跳过（过期清扫兜底）
            pass
        return await self._assemble_workspace(session, srow)

    async def _diagnostic_allowed_scope(self, session: AsyncSession, drow: DiagnosticTaskRow) -> set[str]:
        """允许知识点范围：诊断来源蓝图 body + 会话当前上下文 selection 声明。"""
        brow = (
            await session.execute(
                select(TeachingBlueprintRow).where(TeachingBlueprintRow.id == drow.blueprint_id)
            )
        ).scalar_one_or_none()
        blueprint = (brow.body if brow is not None else None) or {}
        selection_kps: list[str] = []
        snap = (
            (
                await session.execute(
                    select(ContextSnapshotRow)
                    .where(ContextSnapshotRow.session_id == drow.session_id)
                    .order_by(ContextSnapshotRow.created_at.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if snap is not None:
            selection_kps = ((snap.snapshot or {}).get("selection") or {}).get("knowledge_points") or []
        return allowed_scope_of(blueprint, selection_kps)

    @staticmethod
    def _diagnostic_card_props(drow: DiagnosticTaskRow) -> dict[str, Any]:
        """diagnostic_card 视图（单一实现见 diagnostic/mapping.diagnostic_card_props）。"""
        from app.teacher_prep.diagnostic.mapping import diagnostic_card_props

        return diagnostic_card_props(drow)

    async def _lock_diagnostic(self, session: AsyncSession, diagnostic_id: str) -> DiagnosticTaskRow:
        try:
            did = uuid.UUID(diagnostic_id)
        except ValueError as exc:
            raise ApiError(ErrorCode.NOT_FOUND, f"诊断任务不存在（diagnostic_id={diagnostic_id}）") from exc
        row = (
            (
                await session.execute(
                    select(DiagnosticTaskRow)
                    .where(DiagnosticTaskRow.id == did)
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise ApiError(ErrorCode.NOT_FOUND, f"诊断任务不存在（diagnostic_id={diagnostic_id}）")
        return row

    def _db_factory(self) -> Any:
        if self._session_factory is None:
            from app.db.session import session_factory as default_factory

            self._session_factory = default_factory()
        return self._session_factory

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    async def _current_selection(self, session: AsyncSession, session_id: str) -> dict[str, Any]:
        """当前选择：快照优先，班级回落到会话创建时的权威 class_id。"""
        srow = (
            (
                await session.execute(
                    select(TeachingSessionRow).where(
                        TeachingSessionRow.id == uuid.UUID(session_id)
                    )
                )
            )
            .scalar_one_or_none()
        )
        session_class_id = _class_ref(srow) if srow is not None else None
        snap = (
            (
                await session.execute(
                    select(ContextSnapshotRow)
                    .where(ContextSnapshotRow.session_id == uuid.UUID(session_id))
                    .order_by(ContextSnapshotRow.created_at.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if snap is None:
            # 无上下文快照（选择尚未完整）→ 回退到会话上持久化的合并选择。
            # 否则部分选择每轮丢失，教师永远补不齐 source+section+class。
            stored = (srow.intent if srow is not None else None) or {}
            return {
                "source_id": stored.get("source_id"),
                "section_id": stored.get("section_id"),
                "class_id": stored.get("class_id") or session_class_id,
                "knowledge_points": stored.get("knowledge_points") or [],
                "lesson_scope": stored.get("lesson_scope"),
                "lesson_hours": stored.get("lesson_hours"),
                "objectives": [
                    {"text": o.get("text"), "source_ref": o.get("source_ref")}
                    for o in (stored.get("objectives") or [])
                    if isinstance(o, dict)
                ],
            }
        sel = (snap.snapshot or {}).get("selection") or {}
        return {
            "source_id": sel.get("source_id"),
            "section_id": sel.get("section_id"),
            "class_id": sel.get("class_id") or session_class_id,
            "knowledge_points": sel.get("knowledge_points") or [],
            "lesson_scope": sel.get("lesson_scope"),
            "lesson_hours": sel.get("lesson_hours"),
            "objectives": [
                {"text": f.get("text"), "source_ref": f.get("source_ref")}
                for f in (snap.snapshot or {}).get("objectives") or []
            ],
        }

    async def _build_context(
        self,
        session: AsyncSession,
        selection: dict[str, Any],
        srow: TeachingSessionRow,
    ) -> TextbookTeachingContext | None:
        from app.teacher_prep.context.builder import build_teaching_context

        ctx_selection = ContextSelection(
            source_id=selection["source_id"],
            section_id=selection["section_id"],
            class_id=selection["class_id"],
            knowledge_points=selection.get("knowledge_points") or [],
            lesson_scope=selection.get("lesson_scope"),
        )
        return await build_teaching_context(session, selection=ctx_selection)

    @staticmethod
    async def _persist_context(
        session: AsyncSession,
        srow: TeachingSessionRow,
        ctx: TextbookTeachingContext,
    ) -> ContextSnapshotRow:
        snap = ContextSnapshotRow(
            id=uuid.uuid4(),
            session_id=srow.id,
            snapshot=ctx.model_dump(mode="json"),
            snapshot_hash=ctx.snapshot_hash,
            source_id=uuid.UUID(ctx.selection.source_id),
            section_id=uuid.UUID(ctx.selection.section_id),
            class_id=uuid.UUID(ctx.selection.class_id) if ctx.selection.class_id else None,
        )
        session.add(snap)
        await session.flush()
        return snap

    async def _selection_card_is_duplicate(
        self,
        session: AsyncSession,
        srow_id: uuid.UUID,
        selection: dict[str, Any],
        card_kind: str,
    ) -> bool:
        """重复点选防抖：本轮将发的选择卡与会话上一张选择卡是否完全同款。

        「同款」= kind 相同 + 已选教材相同（不完整选择的卡身份由这两者唯一决定：
        kind=textbook ⟺ 缺教材；kind=section ⟺ 缺章节且已定教材）。上一张卡仍在
        会话里，重复发送只会让教师以为"点了没反应"。
        """
        last = (
            (
                await session.execute(
                    select(ConversationMessageRow)
                    .where(
                        ConversationMessageRow.session_id == srow_id,
                        ConversationMessageRow.component_type == "selection_card",
                    )
                    .order_by(ConversationMessageRow.seq.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if last is None:
            return False
        props = last.component_props or {}
        return (
            str(props.get("kind") or "") == card_kind
            and (props.get("source_id") or None) == (selection.get("source_id") or None)
        )

    async def _selection_reply_state(
        self,
        session: AsyncSession,
        selection: dict[str, Any],
        missing: list[str],
        *,
        book_changed: bool = False,
    ) -> dict[str, Any]:
        """compose_reply 的选择状态上下文：缺失项 + 当前已选教材名 + 换册标记。"""
        state: dict[str, Any] = {
            "missing": "textbook" if "textbook" in missing else "section"
        }
        source_id = selection.get("source_id")
        if source_id:
            source_title = await resolve_source_title(session, str(source_id))
            if source_title:
                state["source_label"] = source_title
        if book_changed:
            state["book_changed"] = True
        return state

    async def _selection_cards(self, session: AsyncSession, selection: dict[str, Any]) -> dict[str, Any]:
        """选择不完整 → 授权选项（教材来自已授权切片；找不到 → 空 options 不编造）。"""
        missing: list[str] = []
        options: list[dict[str, str]] = []
        if not selection.get("source_id"):
            missing.append("textbook")
            options = await list_textbook_options(session, limit=100)
        elif not selection.get("section_id"):
            missing.append("section")
            # LP-01：把教师口中的知识点透传给章节选项 → 命中的章节排在最前并带 recommended
            options = await list_section_options(
                session,
                source_id=selection["source_id"],
                level=2,
                knowledge_points=selection.get("knowledge_points") or [],
            )
        # E-3 可选班级：班级不再进入 missing——未绑定班级可继续备课（画像/诊断发布降级）
        return {
            "missing": missing,
            "card": {
                "kind": "section" if "section" in missing else "textbook" if "textbook" in missing else "class",
                # 前端契约（2026-10-04 对话闭环修复）：当前已选教材（TextbookSource.id，
                # 可空）——教材卡缺教材时为 null；前端据此展示已选态并在改选时显式
                # 回传 textbook_id（不再误传成 section_id）。
                "source_id": selection.get("source_id") or None,
                "options": options,
                "copy_keys": {"pick": "prep.selection.pick", "cancel": "common.cancel"},
            },
        }

    @staticmethod
    def _context_card_props(snap: ContextSnapshotRow, ctx: TextbookTeachingContext) -> dict[str, Any]:
        return {
            "source_id": ctx.selection.source_id,
            "source_title": label_of(ctx.source.publisher, ctx.source.edition, ctx.source.grade, ctx.source.volume),
            "section_id": ctx.selection.section_id,
            "section_title": ctx.section.title,
            "section_path": ctx.section.section_path,
            "class_id": ctx.selection.class_id,
            "lesson_scope": ctx.selection.lesson_scope,
            "knowledge_points": ctx.selection.knowledge_points,
            "objectives": [f.text for f in ctx.objectives][:12],
            "snapshot_hash": ctx.snapshot_hash,
            "warnings": [w.code for w in ctx.warnings],
        }

    @staticmethod
    def _make_message(
        session_id: uuid.UUID,
        *,
        seq_hint: int,
        role: str,
        kind: str,
        component_type: str | None,
        component_props: dict[str, Any] | None,
        run_id: uuid.UUID | None = None,
        context_snapshot_id: uuid.UUID | None = None,
        blueprint_id: uuid.UUID | None = None,
        text: str | None = None,
        editable: bool = False,
    ) -> ConversationMessageRow:
        props = validate_component(component_type, component_props)
        return ConversationMessageRow(
            id=uuid.uuid4(),
            session_id=session_id,
            seq=seq_hint + 1,
            role=role,
            kind=kind,
            component_type=component_type,
            component_props=props,
            text=text,
            run_id=run_id,
            context_snapshot_id=context_snapshot_id,
            blueprint_id=blueprint_id,
            status="basic_mode" if editable else "applied",
            editable=editable,
        )

    @staticmethod
    async def _next_message_seq(session: AsyncSession, session_id: uuid.UUID) -> int:
        row = (
            await session.execute(
                select(ConversationMessageRow.seq)
                .where(ConversationMessageRow.session_id == session_id)
                .order_by(ConversationMessageRow.seq.desc())
                .limit(1)
            )
        ).first()
        return (row[0] if row else 0) + 1

    async def _assemble_workspace(
        self,
        session: AsyncSession,
        srow: TeachingSessionRow,
    ) -> WorkspaceSnapshot:
        messages = (
            (
                await session.execute(
                    select(ConversationMessageRow)
                    .where(ConversationMessageRow.session_id == srow.id)
                    .order_by(ConversationMessageRow.seq)
                )
            )
            .scalars()
            .all()
        )
        runs = (
            (
                await session.execute(
                    select(MessageRunRow)
                    .where(MessageRunRow.session_id == srow.id)
                    .order_by(MessageRunRow.created_at)
                )
            )
            .scalars()
            .all()
        )
        blueprint = (
            (
                await session.execute(
                    select(TeachingBlueprintRow)
                    .where(TeachingBlueprintRow.session_id == srow.id)
                    .order_by(TeachingBlueprintRow.created_at.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        diagnostic = (
            (
                await session.execute(
                    select(DiagnosticTaskRow)
                    .where(DiagnosticTaskRow.session_id == srow.id)
                    .order_by(DiagnosticTaskRow.created_at.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        snap = None
        if srow.context_snapshot_id:
            snap = (
                await session.execute(
                    select(ContextSnapshotRow).where(ContextSnapshotRow.id == srow.context_snapshot_id)
                )
            ).scalar_one_or_none()
        return await build_workspace(
            session,
            srow=srow,
            message_rows=list(messages),
            run_rows=list(runs),
            blueprint=blueprint,
            context_snap=snap,
            diagnostic=diagnostic,
        )

    # ── 幂等分发壳（单作用域；失败审计 + 释放）────────────────────────────────

    async def _dispatch(
        self,
        session: AsyncSession,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        principal: str,
        session_id_hint: uuid.UUID | None,
        run: Callable[[], Awaitable[Any]],
    ) -> Any:
        self._emitted.clear()
        reserved, existing = await ReliabilityIdempotency.reserve(
            session,
            owner_service=OWNER_SERVICE,
            operation=operation,
            principal=principal,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not reserved:
            if existing is not None and existing.status == "COMPLETED":
                await session.commit()
                return existing.response
            raise IdempotencyInFlightError(
                f"幂等键在途（{operation} key={idempotency_key}）——并发请求或首者未落定"
            )
        try:
            async with session.begin_nested():
                response = await run()
        except (ApiError, ValueError) as exc:
            self._emitted.clear()  # 事件随 savepoint 回滚，live 镜像一并作废
            await ReliabilityIdempotency.fail(
                session,
                owner_service=OWNER_SERVICE,
                operation=operation,
                principal=principal,
                idempotency_key=idempotency_key,
            )
            await session.commit()
            if isinstance(exc, ApiError):
                raise exc
            raise ApiError(ErrorCode.CONFLICT, str(exc)) from exc
        await ReliabilityIdempotency.complete(
            session,
            owner_service=OWNER_SERVICE,
            operation=operation,
            principal=principal,
            idempotency_key=idempotency_key,
            response=_jsonable(
                response.model_dump(mode="json") if isinstance(response, WorkspaceSnapshot) else response
            ),
        )
        await session.commit()
        # D4：durable 事件已提交 → 镜像 live（live:prep:{session_id}；test 环境内部跳过）
        if self._emitted and session_id_hint is not None:
            from .background import publish_events_live

            dtos, self._emitted = self._emitted, []
            await publish_events_live(str(session_id_hint), dtos)
        return response

    # ── 成员裁决 / 审计 / 事件 ────────────────────────────────────────────────

    @staticmethod
    async def _verify_membership(
        adapter: Api8000Adapter, *, token: str, class_id: str | None
    ) -> str:
        """执行瞬间重验 8000 成员关系；class_id=None（未绑定班级）→ 仅教师身份裁决。

        E-3 可选班级：无班级会话不产生班级域授权（画像/诊断发布由 _require_class_id
        显式拒绝）；一旦绑定班级，所有路径恢复现场 8000 成员裁决（fail closed）。
        """
        if not class_id:
            return "teacher"
        try:
            row = await adapter.require_class_membership(token=token, class_id=class_id)
        except ContractMembershipDenied as exc:
            raise MembershipDeniedError(str(exc)) from exc
        except ContractAuthorityUnreachable as exc:
            raise AuthorityUnreachableError(str(exc)) from exc
        return str(row.member_role)

    @staticmethod
    async def _get_session(session: AsyncSession, session_id: str) -> TeachingSessionRow:
        try:
            sid = uuid.UUID(session_id)
        except ValueError as exc:
            raise PrepSessionGone(session_id) from exc
        row = (
            (
                await session.execute(
                    select(TeachingSessionRow).where(TeachingSessionRow.id == sid)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise PrepSessionGone(session_id)
        return row

    @staticmethod
    async def _lock_session(session: AsyncSession, session_id: str) -> TeachingSessionRow:
        try:
            sid = uuid.UUID(session_id)
        except ValueError as exc:
            raise PrepSessionGone(session_id) from exc
        row = (
            (
                await session.execute(
                    select(TeachingSessionRow)
                    .where(TeachingSessionRow.id == sid)
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise PrepSessionGone(session_id)
        return row

    @staticmethod
    async def _lock_blueprint(session: AsyncSession, blueprint_id: str) -> TeachingBlueprintRow:
        try:
            bid = uuid.UUID(blueprint_id)
        except ValueError as exc:
            from ..service import BlueprintNotFoundError

            raise BlueprintNotFoundError(blueprint_id) from exc
        row = (
            (
                await session.execute(
                    select(TeachingBlueprintRow)
                    .where(TeachingBlueprintRow.id == bid)
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            from ..service import BlueprintNotFoundError

            raise BlueprintNotFoundError(blueprint_id)
        return row

    @staticmethod
    async def _get_message(session: AsyncSession, message_id: str) -> ConversationMessageRow:
        try:
            mid = uuid.UUID(message_id)
        except ValueError as exc:
            raise ConversationMessageNotFound(message_id) from exc
        row = (
            (
                await session.execute(
                    select(ConversationMessageRow).where(ConversationMessageRow.id == mid)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise ConversationMessageNotFound(message_id)
        return row

    @staticmethod
    async def _get_run(session: AsyncSession, run_id: str) -> MessageRunRow:
        try:
            rid = uuid.UUID(run_id)
        except ValueError as exc:
            raise RunNotFound(run_id) from exc
        row = (
            (
                await session.execute(
                    select(MessageRunRow).where(MessageRunRow.id == rid)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise RunNotFound(run_id)
        return row

    @staticmethod
    async def _log_command(
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        command_type: str,
        actor_id: str,
        actor_role: str,
        idempotency_key: str,
        request_hash: str,
        result_status: str,
        payload: dict[str, Any],
    ) -> uuid.UUID:
        trace = get_current()
        cmd = SessionCommandRow(
            session_id=srow.id,
            command_type=command_type,
            actor_id=uuid.UUID(actor_id),
            actor_role=actor_role,
            class_id=srow.class_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expected_revision=srow.revision,
            result_status=result_status,
            payload=_jsonable(payload),
            trace_id=trace.trace_id if trace else None,
            correlation_id=trace.correlation_id if trace else None,
        )
        session.add(cmd)
        await session.flush()
        return cmd.id

    async def _log_events(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        command_id: uuid.UUID,
        events: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """durable 事件落库（会话行锁内分配 seq，UNIQUE 兜底）；返回 event DTO 列表
        供 _dispatch 提交后镜像 live（test 环境自动跳过）。"""
        from app.teacher_prep.service import event_dto

        parent: uuid.UUID | None = None
        seq = srow.last_event_seq
        rows: list[SessionEventRow] = []
        for event_type, payload in [("command.applied", {})] + events:
            seq += 1
            ev_id = uuid.uuid4()
            ev = SessionEventRow(
                id=ev_id,
                session_id=srow.id,
                command_id=command_id,
                seq=seq,
                event_type=event_type,
                payload=_jsonable(payload),
                parent_event_id=parent,
                revision=srow.revision,
            )
            session.add(ev)
            rows.append(ev)
            if parent is None:
                parent = ev_id
        srow.last_event_seq = seq
        srow.updated_at = _now()  # 显式赋值：避免 onupdate 服务端生成列过期 → DTO 读取触发懒加载
        await session.flush()
        dtos = [event_dto(row) for row in rows]
        self._emitted.extend(dtos)
        return dtos


__all__ = [
    "ConversationService",
    "PrepSessionGone",
    "ConversationMessageNotFound",
    "RunNotFound",
    "InvalidActionError",
    "AuthorityUnreachableError",
    "IdempotencyInFlightError",
    "OWNER_SERVICE",
    "JOB_TYPE_RUN",
    "request_hash_of",
]
