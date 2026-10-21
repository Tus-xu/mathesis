"""R17 Conversation-first 备课 API（无 Stepper/Wizard；服务端状态机唯一权威）。

挂载：`/api/teacher-v3/prep/conversations/...`。
契约要点（DECISIONS.md §6/§7/§8）：
- `GET /prep/conversations`：继续候选（昨天/历史备课入口）；
- `POST /prep/conversations`：新建备课（class_id + 自然语言首条消息）；
- `GET /prep/conversations/{session_id}`：workspace（timeline + artifact + evidence
  + actions + job summary + revision；无步骤条字段）；
- `POST .../messages`：发送消息（续备 / 自然语言修改）；幂等；409 revision 冲突；
- `POST .../actions`：confirm/reject/retry/cancel/apply_edits/select_class/propose_revision；
- `GET .../events`：SSE（snapshot → replay(after Last-Event-ID) → live Pub/Sub）；
- 鉴权：get_current_user（role=teacher，40301）；成员裁决每次高风险命令向 8000 重验。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_user, get_session
from app.core.errors import ApiError, ErrorCode
from app.core.response import ok
from app.core.security import UserToken
from app.core.sse import sse_channel
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, HttpxApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

from . import background, node_ops
from .handler import ensure_conversation_worker
from .orchestrator import ConversationService
from .schema import WorkspaceSnapshot

router = APIRouter()

CurrentUser = Annotated[UserToken, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]

_IDEM_MIN = 8
_IDEM_MAX = 128


def _adapter() -> Api8000Adapter:
    s = get_settings()
    return Api8000Adapter(HttpxApiTransport(s.api8000_base_url), InMemoryMappingRegistry())


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


def _live_channel(session_id: str) -> str | None:
    """live 通道：test 环境禁用（单测无 Valkey）；dev/prod 使用 Pub/Sub 实时推送。"""
    if get_settings().environment == "test":
        return None
    return f"prep:{session_id}"


def _service() -> ConversationService:
    from .service_bridge import get_conversation_service

    return get_conversation_service()


# ── 请求模型 ─────────────────────────────────────────────────────────────────

class _Idem(BaseModel):
    idempotency_key: str = Field(min_length=_IDEM_MIN, max_length=_IDEM_MAX)


class CreateConversationRequest(_Idem):
    # E-3 可选班级：未绑定班级的教师也可创建备课会话（班级域能力随后显式降级/守卫）
    class_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    message: str = Field(min_length=1, max_length=4000)
    title: str | None = Field(default=None, max_length=200)


class SelectionIds(BaseModel):
    """选择卡回传的结构化 id（按 id 直传时使用；三者皆可缺省）。

    选择卡选项本身带 id，教师点选即回传 id——这条路径不调用 LLM，
    避免让模型从中文散文里猜 UUID（不可靠且 reasoning 爆量撞超时）。
    """

    textbook_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    section_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    class_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")


class SendMessageRequest(_Idem):
    # session_id is already authoritative in the REST path; keep the body field
    # optional for older clients that still echo it.
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    expected_revision: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=4000)
    selection: SelectionIds | None = None
    # R3 AI 定向修改：lesson_flow 节点下标（十进制字符串；存在即走节点修订链路）
    target_node_id: str | None = Field(default=None, pattern=r"^\d{1,3}$")


class NodeItemIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    source_ref: str | None = Field(default=None, max_length=512)


class EditNodeRequest(_Idem):
    """R3 教师在线编辑：单节点 PATCH（缺省键=不修改；duration_minutes 显式 null=清除）。"""

    expected_revision: int = Field(ge=1)
    node_index: int = Field(ge=0, le=99)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    duration_minutes: int | None = Field(default=None, ge=0, le=120)
    teacher_questions: list[NodeItemIn] | None = Field(default=None, max_length=12)
    student_activities: list[NodeItemIn] | None = Field(default=None, max_length=12)
    board_notes: list[NodeItemIn] | None = Field(default=None, max_length=12)
    hints: list[NodeItemIn] | None = Field(default=None, max_length=12)


class ActionRequest(_Idem):
    # session_id is already authoritative in the REST path; keep the body field
    # optional for older clients that still echo it.
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    action: str = Field(...)
    target: str | None = None
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any] | None = None


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.get("/prep/conversations")
async def list_conversations(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    items = await _service().list_conversations(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        limit=limit,
    )
    return ok({"items": items}, request_id=request.state.request_id)


@router.post("/prep/conversations")
async def create_conversation(
    request: Request,
    body: CreateConversationRequest,
    user: CurrentUser,
    session: DbSession,
    wait: bool = Query(default=False, description="true=旧同步行为（等待 LLM 完成）；缺省=异步受理（202）"),
) -> Any:
    await ensure_conversation_worker()
    svc = _service()
    if wait:
        # 兼容开关：旧 FE / 回归 —— 同步等待 LLM（响应体形状不变）
        ws = await svc.create_conversation(
            session,
            adapter=_adapter(),
            token=_bearer_token(request),
            actor_id=user.id,
            actor_role_hint=user.role,
            class_id=body.class_id,
            message=body.message,
            title=body.title,
            idempotency_key=body.idempotency_key,
            request_hash=_request_hash(request, body.model_dump()),
        )
        return ok(_ws_payload(ws), request_id=request.state.request_id)

    # 默认（FE 新链路，D4）：受理制 —— 用户消息立即落库 → 后台处理 → 202
    adapter = _adapter()
    token = _bearer_token(request)
    receipt = await svc.create_conversation_accepted(
        session,
        adapter=adapter,
        token=token,
        actor_id=user.id,
        actor_role_hint=user.role,
        class_id=body.class_id,
        message=body.message,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        title=body.title,
    )
    _spawn_processing(
        svc,
        session_id=receipt["session_id"],
        message=body.message,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        receipt=receipt,
        actor_id=user.id,
        token=token,
        adapter=adapter,
    )
    return await _accepted_response(
        request, svc, session, adapter=adapter, token=token, teacher_id=user.id, receipt=receipt
    )


@router.get("/prep/conversations/{session_id}")
async def get_conversation_workspace(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    ws = await _service().get_workspace(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        session_id=session_id,
    )
    return ok(ws.model_dump(mode="json"), request_id=request.state.request_id)


@router.get("/prep/conversations/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    items = await _service().list_messages(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        session_id=session_id,
        limit=limit,
    )
    return ok(items, request_id=request.state.request_id)


@router.post("/prep/conversations/{session_id}/messages")
async def send_message(
    session_id: str,
    request: Request,
    body: SendMessageRequest,
    user: CurrentUser,
    session: DbSession,
    wait: bool = Query(default=False, description="true=旧同步行为（等待 LLM 完成）；缺省=异步受理（202）"),
) -> Any:
    await ensure_conversation_worker()
    svc = _service()
    request_hash = _request_hash(request, body.model_dump())

    # R3 AI 定向修改：body 带 target_node_id → 节点修订链路（LLM 只重写该节点）。
    # 必须在 wait 分支之前判定：该链路不走通用意图解析（POST 语义 = 「改这个环节」）。
    if body.target_node_id is not None:
        adapter = _adapter()
        token = _bearer_token(request)
        receipt = await svc.accept_message(
            session,
            adapter=adapter,
            token=token,
            actor_id=user.id,
            session_id=session_id,
            message=body.message,
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
            request_hash=request_hash,
        )
        if wait:
            ws = await node_ops.run_node_revision(
                service=svc,
                factory=svc._db_factory(),  # noqa: SLF001
                session_id=session_id,
                message=body.message,
                accepted_msg_id=receipt["message_id"],
                member_role=receipt["member_role"],
                actor_id=user.id,
                target_node_id=body.target_node_id,
                idempotency_key=body.idempotency_key,
                request_hash=request_hash,
            )
            if isinstance(ws, WorkspaceSnapshot):
                return ok(_ws_payload(ws), request_id=request.state.request_id)
            # 幂等重放（dict）/ 失败（None）：失败错误已由 error 事件发布，此处 409 收口
            if ws is None:
                raise ApiError(ErrorCode.CONFLICT, "AI 定向修改未完成，请重试")
            return ok(ws, request_id=request.state.request_id)
        background.spawn_message_processing(
            background.process_node_revision_message(
                service=svc,
                factory=svc._db_factory(),  # noqa: SLF001
                session_id=session_id,
                message=body.message,
                idempotency_key=body.idempotency_key,
                request_hash=request_hash,
                accepted_msg_id=receipt["message_id"],
                member_role=receipt["member_role"],
                actor_id=user.id,
                target_node_id=body.target_node_id,
            )
        )
        return await _accepted_response(
            request, svc, session, adapter=adapter, token=token, teacher_id=user.id, receipt=receipt
        )

    if wait:
        # 兼容开关：旧 FE / 回归 —— 同步等待 LLM（响应体形状不变）
        ws = await svc.send_message(
            session,
            adapter=_adapter(),
            token=_bearer_token(request),
            actor_id=user.id,
            actor_role_hint=user.role,
            session_id=session_id,
            message=body.message,
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
            request_hash=request_hash,
            selection_ids=body.selection.model_dump() if body.selection else None,
        )
        return ok(_ws_payload(ws), request_id=request.state.request_id)

    # 默认（FE 新链路，D4）：受理制 —— 用户消息立即落库 → 后台处理 → 202
    adapter = _adapter()
    token = _bearer_token(request)
    receipt = await svc.accept_message(
        session,
        adapter=adapter,
        token=token,
        actor_id=user.id,
        session_id=session_id,
        message=body.message,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=request_hash,
    )
    _spawn_processing(
        svc,
        session_id=session_id,
        message=body.message,
        idempotency_key=body.idempotency_key,
        request_hash=request_hash,
        receipt=receipt,
        actor_id=user.id,
        token=token,
        adapter=adapter,
        selection_ids=body.selection.model_dump() if body.selection else None,
    )
    return await _accepted_response(
        request, svc, session, adapter=adapter, token=token, teacher_id=user.id, receipt=receipt
    )


@router.post("/prep/conversations/{session_id}/actions")
async def run_action(
    session_id: str,
    request: Request,
    body: ActionRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    await ensure_conversation_worker()
    ws = await _service().run_action(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        actor_role_hint=user.role,
        session_id=session_id,
        action=body.action,
        target=body.target,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        payload=body.payload,
    )
    return ok(ws.model_dump(mode="json"), request_id=request.state.request_id)


# ── R3 课堂设计页：节点编辑 / 历史版本 / 教材查看 ─────────────────────────────


@router.patch("/prep/conversations/{session_id}/blueprint/node")
async def edit_blueprint_node(
    session_id: str,
    request: Request,
    body: EditNodeRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    """教师在线编辑单个教学环节（三组内容/标题/时长）→ 新 revision（source=teacher）。

    - 幂等；409 revision 冲突；时长 Σ>45min → 422 data.error_key=time_budget_exceeded；
    - edit origin：revision.source="teacher" + 节点体 edited_by/edited_at。
    """
    svc = _service()
    patch = body.model_dump(exclude={"idempotency_key"})
    patch["_provided"] = set(body.model_fields_set) - {"idempotency_key", "expected_revision", "node_index"}
    ws = await node_ops.edit_blueprint_node(
        svc,
        session,
        actor_id=user.id,
        actor_role_hint=user.role,
        session_id=session_id,
        node_index=body.node_index,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        patch=patch,
    )
    # 幂等重放路径返回已存储 dict（_ws_payload 兼容两种形态，不破坏既有契约）
    return ok(_ws_payload(ws), request_id=request.state.request_id)


@router.get("/prep/conversations/{session_id}/blueprint/revisions")
async def list_blueprint_revisions(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """蓝图历史版本（3.7）：真实 revision 快照列表（时间/来源/裁决/note/完整 body）。

    只读；回滚能力未上线（FE 标注「即将支持」，不得放假按钮）。
    对象级授权：先走 get_workspace 的成员重验。
    """
    svc = _service()
    await svc.get_workspace(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        session_id=session_id,
    )
    data = await node_ops.list_blueprint_revisions(session, session_id=session_id, limit=limit)
    return ok(data, request_id=request.state.request_id)


@router.get("/prep/conversations/{session_id}/textbook/view")
async def view_textbook_ref(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    ref: str = Query(min_length=8, max_length=512),
) -> dict:
    """教材查看（3.6）：SourceLocator → 授权切片原文（章节名/页码/含 LaTeX 的 chunk 原文）。

    权限语义与 textbook_v2.locator 一致（rights 可见 + 租户隔离）；解析不到 fail closed 404。
    """
    svc = _service()
    await svc.get_workspace(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        session_id=session_id,
    )
    data = await node_ops.resolve_textbook_ref(session, ref=ref)
    return ok(data, request_id=request.state.request_id)


@router.get("/prep/conversations/{session_id}/specs")
async def list_session_specs(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    """L-221-2：会话下 LessonPlanSpec 列表（对象级授权：get_workspace 已做成员重验）。"""
    import uuid as _uuid

    from sqlalchemy import select

    from ..specs.models import LessonPlanSpecRow

    svc = _service()
    await svc.get_workspace(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        session_id=session_id,
    )
    rows = (
        (
            await session.execute(
                select(LessonPlanSpecRow)
                .where(LessonPlanSpecRow.session_id == _uuid.UUID(session_id))
                .order_by(LessonPlanSpecRow.revision.desc())
            )
        )
        .scalars()
        .all()
    )
    items = []
    for row in rows:
        body = row.body or {}
        title = body.get("title") or body.get("topic") or "教案"
        items.append(
            {
                "spec_id": str(row.id),
                "blueprint_id": str(row.blueprint_id),
                "revision": row.revision,
                "title": title if isinstance(title, str) else "教案",
                "approved_blueprint_revision": row.approved_blueprint_revision,
                "context_snapshot_hash": row.context_snapshot_hash,
                "lineage": row.lineage,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return ok({"items": items, "total": len(items)}, request_id=request.state.request_id)


@router.get("/prep/conversations/{session_id}/events")
async def conversation_events(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> Any:
    """SSE 双通道：snapshot → replay(after Last-Event-ID) → live Pub/Sub。"""
    svc = _service()
    adapter = _adapter()
    token = _bearer_token(request)

    async def snapshot_fn() -> dict[str, Any]:
        ws = await svc.get_workspace(
            session,
            adapter=adapter,
            token=token,
            teacher_id=user.id,
            session_id=session_id,
        )
        return ws.model_dump(mode="json")

    async def replay_fn(after_seq: int) -> list[dict[str, Any]]:
        return await svc.list_events_after(
            session,
            adapter=adapter,
            token=token,
            teacher_id=user.id,
            session_id=session_id,
            after_seq=after_seq,
        )

    return await sse_channel(
        request,
        snapshot_fn=snapshot_fn,
        replay_fn=replay_fn,
        live_channel=_live_channel(session_id),
        frame="wrapped",
    )


def _request_hash(request: Request, body: dict | None) -> str:
    from app.teacher_prep.service import request_hash_of

    return request_hash_of(request.method, request.url.path, body)


def _ws_payload(ws: Any) -> dict[str, Any]:
    """workspace → JSON dict；幂等重放路径返回的是已存储 dict（不破坏既有字段）。"""
    if isinstance(ws, dict):
        return ws
    return ws.model_dump(mode="json")


async def _accepted_response(
    request: Request,
    svc: ConversationService,
    session: AsyncSession,
    *,
    adapter: Api8000Adapter,
    token: str,
    teacher_id: str,
    receipt: dict[str, Any],
) -> JSONResponse:
    """202 受理响应：code=0 信封 + 既有 workspace 字段 + 受理字段（不破坏既有契约）。"""
    ws = await svc.get_workspace(
        session, adapter=adapter, token=token, teacher_id=teacher_id, session_id=receipt["session_id"]
    )
    payload = {
        **_ws_payload(ws),
        "conversation_id": receipt["session_id"],
        "message_id": receipt["message_id"],
        "status": "processing",
        "revision": receipt["revision"],
    }
    return JSONResponse(
        status_code=202, content=ok(payload, request_id=request.state.request_id)
    )


def _spawn_processing(
    svc: ConversationService,
    *,
    session_id: str,
    message: str,
    idempotency_key: str,
    request_hash: str,
    receipt: dict[str, Any],
    actor_id: str,
    token: str,
    adapter: Api8000Adapter,
    selection_ids: dict[str, str | None] | None = None,
) -> None:
    """spawn 受理后台任务（进程内 asyncio；异常在任务体内捕获并发布 error 事件）。"""
    background.spawn_message_processing(
        background.process_accepted_message(
            service=svc,
            factory=svc._db_factory(),  # noqa: SLF001 —— 同包装配；短事务/意图解析用独立 session
            session_id=session_id,
            message=message,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            accepted_msg_id=receipt["message_id"],
            member_role=receipt["member_role"],
            actor_id=actor_id,
            token=token,
            adapter=adapter,
            selection_ids=selection_ids,
        )
    )


__all__ = ["router"]
