# coding: utf-8
"""butler 域路由（AI 管家：chat SSE / voice-formula SSE / tools / action 确认）。

关键接缝（rescue 整合 2026-09-07）：B1 的 butler 图通过 B1 ToolRegistry 调工具，
B4 的 15 个工具实现在 app/domains/tools.py 的独立注册表——此前无人桥接。
本文件提供 _build_registry()：B4 工具 → B1 ToolSpec（LooseParams pydantic 壳）+ DB 会话适配器。
别名：图内 quiz_import_node 调用名 'quiz_import_question' ↔ B4 'quiz.create'（IFC 漂移，桥接归一）。

已知债务（诚实边界）：
- butler_sessions/messages/actions 三表未迁移（MIGRATION_REQUEST-002），会话历史与
  确认卡 pending 态暂存进程内存——API 重启丢失，不冒充持久化。
- web_search / FunASR L2+ / embedding 通道未接通，对应意图走 B1 图内的诚实边界文案。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from ...core.deps import get_current_user
from ...core.errors import ApiError, ErrorCode
from ...core.response import ok
from ...core.security import UserToken
from ...core.sse import sse_response
from .service import ButlerMessage, ButlerService

router = APIRouter()

_service: ButlerService | None = None
_checkpoint_saver = None
# R11.5-B：PendingAction 持久化取代进程内 dict（DECISIONS §5；表由迁移 d17b2c3e4f5a 所有）
_pending_store: Any = None


async def _get_pending_store():
    """懒建持久化 PendingAction store（PG；生产 fail-closed，缺表抛）。"""
    global _pending_store
    if _pending_store is None:
        from ...agent.pending_actions import PostgresPendingActionStore
        from ...db.session import session_factory

        _pending_store = PostgresPendingActionStore(session_factory())
    return _pending_store


async def _get_saver():
    """ManagedCheckpointer 惰性启动（AsyncPostgresSaver，thread_id=session_id）。"""
    global _checkpoint_saver
    if _checkpoint_saver is None:
        from ...agent.checkpoint import ManagedCheckpointer
        from ...core.config import get_settings

        _checkpoint_saver = await ManagedCheckpointer(get_settings().database_url).start()
    return _checkpoint_saver


async def get_butler_service() -> ButlerService:
    global _service
    if _service is None:
        gateway = None
        prompt_renderer = None
        search_web = None
        memories_provider = None
        try:
            from ...db.session import session_factory
            from ...llm.ai_runs import PostgresAiRunsSink
            from ...llm.gateway import LlmGateway
            from ...prompts.registry import default_registry
            from ...rag.websearch import search_web as _real_search_web
            from .memory import load_butler_memories

            # Spark 主通道（.env）；ai_runs 台账经 PG sink 落账（G6）；系统提示词 B2 butler/system.j2。
            # 无 key 时 LlmGateway 内部不建 router → chat_node 走诚实降级文案。
            gateway = LlmGateway(PostgresAiRunsSink(session_factory()))
            prompt_renderer = default_registry()
            search_web = _real_search_web  # 博查联网搜索（.env BOCHA_API_KEY）

            async def _memories(teacher_id: str, session_id: str) -> list[str]:
                # 跨会话记忆：取该教师最近 user 消息（排除当前会话），注入 system prompt
                try:
                    async with session_factory()() as ss:
                        return await load_butler_memories(ss, teacher_id=teacher_id, exclude_session_id=session_id)
                except Exception:
                    return []

            memories_provider = _memories
        except Exception:
            pass
        _service = ButlerService(
            checkpointer=await _get_saver(), registry=_build_registry(),
            gateway=gateway, prompt_renderer=prompt_renderer, search_web=search_web,
            memories_provider=memories_provider,
        )
    return _service


class _LooseParams(BaseModel):
    """B4 JSON-schema dict 参数 → B1 要求的 pydantic v2 模型类壳（透传，不再校验）。"""

    model_config = ConfigDict(extra="allow")


def _build_registry():
    """B4 工具注册表 → B1 ToolRegistry 桥（rescue 整合接缝）。"""
    from ...agent.tools.protocol import ToolRegistry
    from ...agent.tools.protocol import ToolSpec as B1ToolSpec
    from ...db.session import session_factory
    from ...domains import tools as b4_tools

    b4_tools.register_tools()
    from ...domains.tools import TeacherCtx as B4TeacherCtx

    reg = ToolRegistry()

    def adapt(spec: dict, handler):
        b1_spec = B1ToolSpec(
            # B4 工具名含点号（quiz.create 等）违反 B1 命名正则 → 桥接层统一转下划线
            name=spec["name"].replace(".", "_"),
            label=spec.get("label", spec["name"]),
            kind=spec["kind"],
            description=spec.get("description", ""),
            params_schema=_LooseParams,
            confirm_required=bool(spec.get("confirm_required", False)),
            domain=spec.get("domain", ""),
        )

        async def _handler(parsed: BaseModel, ctx) -> dict:
            from uuid import UUID as _UUID

            async with session_factory()() as ss:
                teacher_raw = ctx.get("teacher_id") if isinstance(ctx, dict) else None
                try:
                    user_id = _UUID(str(teacher_raw)) if teacher_raw else uuid.uuid4()
                except (ValueError, TypeError):
                    user_id = uuid.uuid4()
                result = await handler(B4TeacherCtx(session=ss, user_id=user_id), dict(parsed.model_dump(exclude_none=True)))
                await ss.commit()
                return result

        return b1_spec, _handler

    for name, (spec, handler) in list(b4_tools.registry_items().items()):
        b1_spec, adapted = adapt(spec, handler)
        reg.register(b1_spec, adapted)
        if name == "quiz.create":
            # 图内 quiz_import_node 的调用名（IFC 漂移别名，归一在桥接层）
            alias_spec = b1_spec.__class__(
                name="quiz_import_question",
                label=b1_spec.label,
                kind=b1_spec.kind,
                description=b1_spec.description,
                params_schema=_LooseParams,
                confirm_required=True,
                domain=b1_spec.domain,
            )
            reg.register(alias_spec, adapted)
    return reg


def _to_sse(agent_events, *, session_id: str | None = None, teacher_id: str | None = None):
    """AgentEvent → SSE 帧；拦截 action 事件登记待确认动作（确认端点据此恢复 interrupt）。"""
    async def gen():
        async for ev in agent_events:
            if ev.name == "action" and session_id:
                action_id = str(ev.payload.get("id") or f"act-{uuid.uuid4().hex[:10]}")
                await register_pending_action(action_id, {
                    "session_id": session_id,
                    "teacher_id": teacher_id,
                    "tool": ev.payload.get("tool"),
                    "card": ev.payload,
                })
                ev.payload = {**ev.payload, "id": action_id}
            yield {"event": ev.name, "data": _json(ev.payload)}
    return gen()


@router.post("/butler/chat")
async def butler_chat(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> EventSourceResponse:
    """主对话（SSE）：meta → thinking → token* → (tool_call → tool_result)* → card* → action? → citation? → done"""
    message = (body.get("message") or "").strip()
    if not message:
        raise ApiError(ErrorCode.VALIDATION, "message 必填")
    session_id = body.get("session_id") or f"bs-{uuid.uuid4().hex[:12]}"
    svc = await get_butler_service()
    # 跨会话记忆：落 user 消息（内部静默失败，不拖垮对话）
    try:
        from .memory import record_butler_message
        from ...db.session import session_factory as _sf

        async with _sf()() as _ss:
            await record_butler_message(_ss, teacher_id=user.id, session_id=session_id, role="user", text_=message)
    except Exception:
        pass
    stream = svc.stream_butler(ButlerMessage(
        session_id=session_id,
        teacher_id=user.id,
        message=message,
        has_images=bool(body.get("images")),
        force_web_search=bool(body.get("force_web_search")),
        context=body.get("context") or {},
    ))
    return sse_response(_to_sse(stream, session_id=session_id, teacher_id=user.id))


@router.post("/butler/voice-formula")
async def voice_formula(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> EventSourceResponse:
    """语音公式（SSE）：asr_final → card → done。

    诚实边界：FunASR sidecar 未部署（8104 占位），P0 用前端 text 直传（mock 契约同款）。
    L1 术语规则引擎为真实实现（76 条词典）；L2 LLM 解析 / L3 KaTeX 校验未接——
    card 带 confidence 与来源标注，不冒充全链路。
    """
    from ...agent.graphs.voice_rules import rule_confidence, voice_to_latex

    text = (body.get("text") or "").strip()
    if not text:
        raise ApiError(ErrorCode.VALIDATION, "text 必填（P0 用文本模拟语音；ASR sidecar 未部署）")
    latex = voice_to_latex(text)
    events: list[dict] = []
    if not latex:
        events.append({"event": "asr_final", "data": _json({"text": text})})
        events.append({"event": "card", "data": _json({
            "type": "formula", "latex": "", "confidence": 0.0,
            "note": "未能解析该口述公式（L1 规则引擎未命中，LLM 解析通道未接入）", "alternatives": [],
        })})
        events.append({"event": "done", "data": _json({"finish_reason": "no_rule_match"})})
        return sse_response(_static(events))

    conf = rule_confidence(text, latex)
    events.append({"event": "asr_final", "data": _json({"text": text})})
    events.append({"event": "card", "data": _json({
        "type": "formula", "latex": latex, "confidence": round(conf, 2),
        "source": "rule-l1", "alternatives": [],
    })})
    events.append({"event": "done", "data": _json({"finish_reason": "stop"})})
    return sse_response(_static(events))


@router.get("/butler/tools")
async def butler_tools(request: Request, user: UserToken = Depends(get_current_user)) -> dict:
    """工具目录（read/write/frontend 三档；write 必经确认卡）。"""
    from ...domains import tools as b4_tools

    # tools.py 惰性注册访问器（register_tools() 已在 _build_registry() 注册过，此处不再重复触发）
    specs = b4_tools.specs()
    return ok({"tools": [
        {
            "name": s["name"], "label": s["label"], "kind": s["kind"],
            "confirm_required": bool(s.get("confirm_required", False)),
            "description": s.get("description", ""),
        }
        for s in specs
    ]}, request_id=request.state.request_id)


@router.post("/butler/actions/{action_id}/confirm")
async def confirm_action(
    request: Request,
    action_id: str,
    body: dict,
    user: UserToken = Depends(get_current_user),
) -> EventSourceResponse:
    """写操作确认执行（教师点「执行」才真正调工具，红线 1）。确认后流式回 tool_result。

    R11.5-B：pending 持久化（重启不丢），执行瞬再次鉴权（当前教师身份/角色，
    必须仍为 teacher 且 ownership 归属本人），过期自动失效。
    """
    store = await _get_pending_store()
    pending = await store.get(action_id)
    approved = bool(body.get("approved", True)) if "approved" in body else True
    if pending is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "确认卡不存在、已处理或已过期")
    if pending.status == "EXPIRED":
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "确认卡已过期，请重新发起操作")
    # 执行瞬再次鉴权（审计 #9 + DECISIONS §5）：必须当前 teacher 本人；角色由 Depends 已校验
    if pending.teacher_id != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "该确认卡属于另一位教师，无法执行")
    # 写入前重新鉴权：核对工具仍为 write 且确认流未旁路（协议层再次把关）
    svc = await get_butler_service()
    confirmed = await store.confirm(
        action_id, approved=approved, params=body.get("params") or {}, by=user.id
    )
    if confirmed is None:
        raise ApiError(ErrorCode.CONFLICT, "确认卡状态已变化（可能已被处理/过期），请重新发起")
    stream = svc.stream_butler(ButlerMessage(
        session_id=pending.session_id,
        teacher_id=user.id,
        message="",
        resume={"approved": approved, "params": confirmed.params_overrides, "action_id": action_id},
    ))
    return sse_response(_to_sse(stream))


@router.get("/butler/actions")
async def list_pending_actions(request: Request, user: UserToken = Depends(get_current_user)) -> dict:
    """服务重启后可查看：当前教师未处理确认卡列表（含状态/过期时间）。"""
    store = await _get_pending_store()
    pending = await store.list_by_teacher(user.id)
    return ok({
        "actions": [
            {
                "action_id": a.action_id,
                "session_id": a.session_id,
                "tool": a.tool,
                "status": a.status,
                "expires_at": a.expires_at,
                "card": a.card,
            }
            for a in pending
        ]
    }, request_id=request.state.request_id)


@router.post("/butler/actions/{action_id}/reject")
async def reject_action(
    request: Request,
    action_id: str,
    user: UserToken = Depends(get_current_user),
) -> dict:
    """明确拒绝 pending 操作（持久化 REJECTED，重启后可查）。"""
    store = await _get_pending_store()
    pending = await store.get(action_id)
    if pending is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "确认卡不存在、已处理或已过期")
    if pending.teacher_id != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "该确认卡属于另一位教师，无法操作")
    if await store.confirm(action_id, approved=False, params=None, by=user.id) is None:
        raise ApiError(ErrorCode.CONFLICT, "确认卡状态已变化，无法拒绝")
    return ok({"action_id": action_id, "status": "REJECTED"}, request_id=request.state.request_id)


@router.post("/butler/actions/{action_id}/expire")
async def expire_action(
    request: Request,
    action_id: str,
    user: UserToken = Depends(get_current_user),
) -> dict:
    """强制过期 pending 操作（不执行工具，仅状态置 EXPIRED）。"""
    store = await _get_pending_store()
    pending = await store.get(action_id)
    if pending is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "确认卡不存在、已处理或已过期")
    if pending.teacher_id != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "该确认卡属于另一位教师，无法操作")
    await store.mark_expired(action_id)
    return ok({"action_id": action_id, "status": "EXPIRED"}, request_id=request.state.request_id)


async def register_pending_action(action_id: str, payload: dict) -> None:
    """持久化 pending action（表 pending_actions；重启可查）。失败 fail-closed（不允许偷偷丢）。"""
    from ...agent.pending_actions import DEFAULT_TTL_SECONDS, PendingAction
    import time as _time

    store = await _get_pending_store()
    teacher_id = str(payload.get("teacher_id") or "") or ""
    await store.create(PendingAction(
        action_id=action_id,
        session_id=str(payload.get("session_id") or ""),
        teacher_id=teacher_id if teacher_id else str(uuid.uuid4()),
        tool=str(payload.get("tool") or ""),
        card=payload.get("card") or {},
        expires_at=_time.time() + DEFAULT_TTL_SECONDS,
    ))


def _json(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def _static(events: list[dict]):
    async def gen():
        for e in events:
            yield e
    return gen()
