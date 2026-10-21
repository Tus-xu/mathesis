"""research-vnext REST + SSE 路由（8300）。

SSE 只承载 domain event / heartbeat / 续传，**不承载**高风险 command 与
正式状态 mutation（那走 REST）—— 总手册 §13.1。

**前缀不属于本模块**：`APIRouter` 刻意不声明 `prefix=`，前缀由
`app.py` 在 `include_router(router, prefix=p)` 时注入，从而同一组路由可以
同时挂载在多个前缀下（`/api/research-v1` 与 `/api/research-vnext`）。
路由函数名与相对路径保持不变。

**RV-XF 论文工作台工作流代理挂载点**：讯飞星辰线上工作流（论文工作台功能内核）
的端点在 `core/api/xf_routes.py`，于本文件**末尾**以 `router.include_router`
挂载 —— 因此它们同样随本 router 在双前缀下可达，且 `xf_router` 自身不写 prefix。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import sse_heartbeat_seconds
from ..contracts.command import IncomingCommand
from ..db.engine import session_scope
from ..domain.errors import DomainError
from ..observability.redaction import redact_mapping
from ..observability.trace import TraceContext
from ..application import job_service
from ..application import message_service
from ..application import retry_service
from ..application import session_service as svc
from .deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    AccessContext,
    authorize_create,
    authorize_session,
    load_access_context,
    trace_from,
)

# 前缀由 include_router(prefix=...) 注入（见模块 docstring），此处**不要**再写 prefix。
router = APIRouter(tags=["research-vnext"])


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _backpressure_headers(e: Exception) -> dict[str, str]:
    """背压响应头。定义在这里（模块顶部）以便 `_err` 使用。"""
    ra = getattr(e, "retry_after", None)
    return {"Retry-After": str(ra)} if ra else {}


def _trace(request: Request, ctx: AccessContext) -> TraceContext:
    return trace_from(request, ctx)


def _json(body: dict, status: int = 200, idem: str | None = None, trace: TraceContext | None = None,
          extra_headers: dict[str, str] | None = None) -> JSONResponse:
    headers = dict(extra_headers or {})
    if idem:
        headers["X-Idempotency-Status"] = idem
    if trace:
        headers["X-Trace-Id"] = trace.trace_id
    return JSONResponse(content=body, status_code=status, headers=headers)


def _err(e: Exception) -> JSONResponse:
    if isinstance(e, DomainError):
        # 背压必须带 `Retry-After`：只给 429 而不说等多久，客户端只能瞎猜/狂重试。
        headers = _backpressure_headers(e) if e.http_status in (429, 503) else None
        return JSONResponse(
            status_code=e.http_status, content={"error": e.to_dict()}, headers=headers
        )
    # LLM 供应商容量闸：**单账号单点**下这是唯一的保护层，必须变成 429 + Retry-After，
    # 而不是 500。它的错误体形状与 DomainError 一致，便于前端统一处理。
    from ..llm.capacity import LlmCapacityExceeded

    if isinstance(e, LlmCapacityExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "BACKPRESSURE",
                    "message": str(e),
                    "reason": e.reason,
                    "scope": e.scope,
                    "retry_after": e.retry_after,
                }
            },
            headers={"Retry-After": str(e.retry_after)},
        )
    raise e


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------


@router.post("/sessions:create", status_code=201)
def create_session(
    request: Request,
    command: IncomingCommand,
) -> JSONResponse:
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    tenant_id = uuid.UUID(command.tenant_id) if command.tenant_id else ctx.tenant_id

    with session_scope() as db:
        try:
            authorize_create(db, ctx, tenant_id)
            body, idem = svc.create_session(
                db, cmd=command, payload=command.domain_payload, trace=trace, tenant_id=tenant_id
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 201, idem, trace)


@router.post("/sessions/{session_id}:transition")
def transition(
    request: Request,
    session_id: uuid.UUID,
    command: IncomingCommand,
) -> JSONResponse:
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            body, idem = svc.transition(
                db,
                session_id=session_id,
                cmd=command,
                payload=command.domain_payload,
                trace=trace,
                tenant_id=ctx.tenant_id,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, idem, trace)


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------


@router.get("/sessions/{session_id}")
def get_session(request: Request, session_id: uuid.UUID) -> JSONResponse:
    """返回完整 `ResearchSnapshot`（ADR-RV-003 §3.2）。

    **加法式变更**：既有内部字段（`revision` / `summary_ref` / `trace_hint` /
    `session`）全部保留，只在其上新增前端契约要求的
    `timeline` / `evidence` / `capabilities` / `artifactFocus` / `problem`。
    """
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            state, reason = message_service.default_message_state()
            body = message_service.get_message_snapshot(
                db, session_id, message_state=state, message_reason=reason
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.post("/sessions/{session_id}/messages")
async def post_message(
    request: Request,
    session_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """CR-6a：消息即最常用的复合命令（ADR-RV-003 §3.1）。

    - 401：无/伪造 AccessContext；
    - 403：**对象级**鉴权失败（tenant / project / owner / capability）；
    - 409：`expected_revision` 不匹配（body 含 `current` + `snapshot_ref`），
           或同一 `client_message_id` 用于不同内容；
    - 200 + `X-Idempotency-Status: REPLAYED`：同 id + 同内容重放；
    - 201 + `X-Idempotency-Status: EXECUTED`：新写入。
    """
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    raw = body_in or {}

    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    try:
        body, idem, status = await message_service.send_message(
            session_id=session_id,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            message=raw.get("message", ""),
            expected_revision=raw.get("expected_revision"),
            client_message_id=raw.get("client_message_id"),
            attachments=raw.get("attachments"),
            trace=trace,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
    # RV16 加法式：为 token 流（`GET /sessions/{id}/stream`）提供本轮 turn 标识。
    # **纯新增字段**，既有消费者忽略即可；turn_id = 本轮 user message id（稳定、可续传）。
    body["stream_turn_id"] = _latest_turn_id(session_id)
    return _json(body, status, idem, trace)


def _latest_turn_id(session_id: uuid.UUID) -> str:
    """取会话最新 user message id 作为本轮 turn 标识（无则回退随机 UUID）。"""
    from sqlalchemy import select

    from ..db.models import ResearchMessageRow

    try:
        with session_scope() as db:
            row = db.execute(
                select(ResearchMessageRow.id)
                .where(
                    ResearchMessageRow.session_id == session_id,
                    ResearchMessageRow.role == "user",
                )
                .order_by(ResearchMessageRow.seq.desc())
                .limit(1)
            ).scalar_one_or_none()
        return str(row) if row is not None else str(uuid.uuid4())
    except Exception:  # noqa: BLE001 - 标识生成失败不得影响主响应
        return str(uuid.uuid4())


@router.post("/sessions/{session_id}:resume")
def resume(
    request: Request,
    session_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    last = (body_in or {}).get("last_event_id") or request.headers.get("Last-Event-ID")
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            body = svc.resume(db, session_id, last)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.get("/sessions/{session_id}/summary")
def summary(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            body = svc.get_summary(db, session_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.get("/sessions/{session_id}/summary/history")
def summary_history(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            body = {"items": svc.summary_history(db, session_id)}
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body)


@router.get("/sessions/{session_id}/pending-actions")
def pending_actions(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            body = {"items": svc.list_pending(db, session_id)}
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body)


def _pending_resolve(
    request: Request, session_id: uuid.UUID, pending_id: uuid.UUID, kind: str,
    body_in: dict[str, Any] | None,
) -> JSONResponse:
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    raw = body_in or {}
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            if kind == "accept":
                body = svc.accept_pending(
                    db, session_id=session_id, pending_id=pending_id,
                    ctx_capabilities=ctx.capabilities, actor_id=ctx.actor_id,
                    trace=trace, expected_revision=raw.get("expected_revision"),
                )
            elif kind == "reject":
                body = svc.reject_pending(
                    db, session_id=session_id, pending_id=pending_id,
                    ctx_capabilities=ctx.capabilities, actor_id=ctx.actor_id,
                    trace=trace, reason=raw.get("reason", ""),
                )
            else:
                body = svc.cancel_pending(
                    db, session_id=session_id, pending_id=pending_id,
                    ctx_capabilities=ctx.capabilities, actor_id=ctx.actor_id,
                    trace=trace, reason=raw.get("reason", ""),
                )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.post("/sessions/{session_id}/pending-actions/{pending_id}:accept")
def accept(
    request: Request, session_id: uuid.UUID, pending_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    return _pending_resolve(request, session_id, pending_id, "accept", body_in)


@router.post("/sessions/{session_id}/pending-actions/{pending_id}:reject")
def reject(
    request: Request, session_id: uuid.UUID, pending_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    return _pending_resolve(request, session_id, pending_id, "reject", body_in)


@router.post("/sessions/{session_id}/pending-actions/{pending_id}:cancel")
def cancel(
    request: Request, session_id: uuid.UUID, pending_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    return _pending_resolve(request, session_id, pending_id, "cancel", body_in)


# --------------------------------------------------------------------------
# CR-6 · 「重试这一轮」（单一供应商下失败必须可恢复）
# --------------------------------------------------------------------------


@router.get("/sessions/{session_id}/messages/{message_id}")
def turn_state(request: Request, session_id: uuid.UUID, message_id: uuid.UUID) -> JSONResponse:
    """某一轮的可重试状态（供前端决定是否显示"重试"按钮）。"""
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            body = retry_service.turn_state(db, session_id, message_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.post("/sessions/{session_id}/messages/{message_id}:retry")
async def retry_turn(request: Request, session_id: uuid.UUID, message_id: uuid.UUID) -> JSONResponse:
    """重跑该轮的 assistant 侧。

    存在的理由：本平台只有**一家** LLM 供应商、**一个**账号（用户有意统一），
    没有供应商级容灾。因此"模型暂时不可用"是预期会发生的状态，而研究者
    不应为了重试而重复输入 —— 那会产生第二条用户消息并污染实验记录。
    重试**不**新增 user 消息。
    """
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    try:
        body, status = await retry_service.retry_turn(
            session_id=session_id,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            message_id=message_id,
            trace=trace,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return _json(body, status, None, trace)


# --------------------------------------------------------------------------
# RV03 · 作业（对外状态一律经 public_status 折叠）
# --------------------------------------------------------------------------


@router.get("/sessions/{session_id}/jobs")
def list_jobs(request: Request, session_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            body = {"items": job_service.list_jobs(db, session_id)}
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            job = job_service.get_job(db, job_id)
            authorize_session(db, ctx, job.session_id, capability=READ_CAPABILITY)
            body = job_service.public_view(job)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


@router.post("/jobs/{job_id}:cancel")
def cancel_job(
    request: Request, job_id: uuid.UUID, body_in: dict[str, Any] | None = Body(default=None)
) -> JSONResponse:
    """请求取消：写持久化 `cancel_requested_at`；running 作业由 worker 在安全点退出。"""
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    raw = body_in or {}
    with session_scope() as db:
        try:
            job = job_service.get_job(db, job_id)
            authorize_session(db, ctx, job.session_id, capability=WRITE_CAPABILITY)
            job_service.request_cancel(
                db, job_id=job_id, reason=raw.get("reason", ""), trace_id=trace.trace_id
            )
            body = job_service.public_view(job_service.get_job(db, job_id))
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------


def _sse(event_id: str | int | None, data: dict, event: str | None = None) -> str:
    """序列化一帧 SSE。

    `event_id is None` 时**不写 `id:` 行** —— 这一点是有意的（FIX-A2）：
    按 SSE 规范，客户端会把最后见到的 `id:` 作为 `Last-Event-ID` 回传。
    若给"没有真实事件可指向"的帧（如无新事件的 cursor 帧）写一个合成 id
    （修复前是 `cursor-<millis>`），客户端就会回传一个服务端无法续传的值，
    导致**每次自动重连都退化成全量快照**。宁可不发 id，也不发一个不可续传的 id。
    """
    lines = []
    if event:
        lines.append(f"event: {event}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps(data, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


def _snapshot_frame(db, session_id: uuid.UUID, *, reason: str = "") -> str:
    """`event: snapshot` 帧（CR-6b）。

    前端 `onFrame` 只在 `f.event === 'snapshot' && e.snapshot` 时走恢复路径；
    `snapshot_required` 会落到 `onGap` ⇒ **静默无操作**。因此这里固定用具名
    `snapshot`，游标过期只体现在 `reason` 字段（`cursor_expired`）里。

    该帧**不带 `id:`**：快照不是增量，回传一个快照 id 毫无续传意义，
    反而会把客户端水位毒化成一个服务端认不出的值。
    """
    state, state_reason = message_service.default_message_state()
    snapshot = message_service.get_message_snapshot(
        db, session_id, message_state=state, message_reason=state_reason
    )
    payload: dict[str, Any] = {"snapshot": snapshot}
    if reason:
        payload["reason"] = reason
    return _sse(None, payload, event="snapshot")


@router.get("/sessions/{session_id}/events")
def sse_events(request: Request, session_id: uuid.UUID):
    """SSE：持久 domain event + heartbeat + Last-Event-ID 续传。

    线上契约（CR-6b，**只允许**这三类帧）：

    | 场景 | 帧 | data |
    |---|---|---|
    | 连接建立 / 全量对齐 | `event: snapshot` | `{"snapshot": <ResearchSnapshot>}` |
    | 领域事件增量 | `event: event` | domain event（`id:` = 真实数值 `domain_event.id`） |
    | 游标超出保留窗口 | `event: snapshot` | `{"snapshot": ..., "reason": "cursor_expired"}` |
    | 心跳 | `: ping` 注释帧（**无 event 名、无 `id:`**） | — |

    不再发 `event: cursor`（前端会当 gap），不再发 `event: snapshot_required`
    （前端会当 gap ⇒ 静默失败），不再发**无名帧**承载领域事件。
    心跳**不带 `id:`** —— 否则浏览器会把心跳 id 当作 `Last-Event-ID` 回传。
    """
    ctx = _ctx(request)
    trace = _trace(request, ctx)
    last_event_id = request.headers.get("Last-Event-ID")

    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            start = svc.resume(db, session_id, last_event_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    heartbeat = max(1, sse_heartbeat_seconds())
    max_ticks = 24
    expired = start["fallback"] == "SNAPSHOT_REQUIRED"

    def gen():
        # 首帧：**全量对齐快照**（具名 `snapshot`）。永不写 `id:`。
        with session_scope() as db0:
            yield _snapshot_frame(
                db0, session_id, reason="cursor_expired" if expired else ""
            )

        if expired:
            # 保留窗口之外的游标：已诚实全量重同步，不再伪造增量。
            # 仍发心跳，让客户端知道连接是活的。
            for _ in range(max_ticks):
                yield f": ping {datetime.now(timezone.utc).isoformat()}\n\n"
                time.sleep(0.5)
            yield ": stream-closed\n\n"
            return

        cursor: int = start.get("cursor") or 0
        for ev in start["events"]:
            # 领域事件**必须**具名 `event`（无名帧会被前端当 gap 丢弃）。
            yield _sse(ev["id"], redact_mapping(ev), event="event")
            cursor = max(cursor, int(ev["id"]))

        last_hb = time.time()
        for _ in range(max_ticks):
            if time.time() - last_hb >= heartbeat:
                # 心跳注释帧：**不带 id**，不改变客户端水位
                yield f": ping {datetime.now(timezone.utc).isoformat()}\n\n"
                last_hb = time.time()
            time.sleep(0.5)
            with session_scope() as db2:
                # 真实增量：由数据库按数值水位过滤（修复前恒为 after_id=0）
                fresh = svc.events_after(db2, session_id, after_id=cursor, limit=500)
            for ev in fresh:
                yield _sse(ev["id"], redact_mapping(ev), event="event")
                cursor = max(cursor, int(ev["id"]))
        yield ": stream-closed\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Trace-Id": trace.trace_id,
        },
    )


# RV-XF 挂载点：import 置于末尾，避免与 xf_routes 的模块级循环导入。
from .xf_routes import router as xf_router  # noqa: E402

router.include_router(xf_router)

# RV-WB 挂载点（第 2 阶段）：论文工作台「自研内核」端点（LLM 网关 + 文献内核）。
# 同样置于末尾以避免模块级循环导入；`wb_router` 自身不写 prefix。
from .wb_routes import router as wb_router  # noqa: E402

router.include_router(wb_router)
