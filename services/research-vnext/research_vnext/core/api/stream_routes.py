"""RV16 · token 流 SSE 通道：`GET /sessions/{session_id}/stream`。

与既有 domain-event SSE（`/sessions/{id}/events`）**职责分离、互不改写**：

| 通道 | 语义 | 生命周期 | `id:` 语义 |
|---|---|---|---|
| `/events` | domain event（durable） | 会话级 | `domain_event.id` |
| `/stream` | LLM token 输出（transient） | 每轮 turn | turn 内单调 `seq` |

URL 分域 ⇒ `Last-Event-ID` 天然分域，不会互相毒化水位（对齐 `routes.py` FIX-A2）。

**X7 附加约束**：
① token 流**不含任何 mutation**（写入一律走 REST）——本端点只读 + 写自身 StreamBuffer；
② 复用既有 SSE 基础设施（心跳 / `Last-Event-ID` / seq 连续性 / gap 重对账）；
③ 帧落库 `StreamBuffer` 支持断线续传；
④ 双前缀均可达（由 `app.py` 循环挂载）。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from ..config import sse_heartbeat_seconds
from ..db.engine import session_scope
from ..db.models import ResearchMessageRow
from ..domain.errors import DomainError
from ..llm import streaming
from ..stream.buffer import StreamBuffer
from .deps import READ_CAPABILITY, AccessContext, authorize_session, load_access_context, trace_from

router = APIRouter(tags=["research-stream"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _sse_frame(seq: int | None, event: str | None, data: dict) -> str:
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append("data: " + json.dumps(data, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


def _latest_user_text(db, session_id: uuid.UUID) -> str:
    row = db.execute(
        select(ResearchMessageRow)
        .where(ResearchMessageRow.session_id == session_id, ResearchMessageRow.role == "user")
        .order_by(ResearchMessageRow.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (row.text if row is not None else "") or ""


def _turn_id_from_latest(db, session_id: uuid.UUID) -> uuid.UUID:
    row = db.execute(
        select(ResearchMessageRow)
        .where(ResearchMessageRow.session_id == session_id, ResearchMessageRow.role == "user")
        .order_by(ResearchMessageRow.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.id if row is not None else uuid.uuid4()


@router.get("/sessions/{session_id}/stream")
def sse_token_stream(request: Request, session_id: uuid.UUID, turn: uuid.UUID | None = None):
    """token 流 SSE。

    帧类型（命名冻结）：`snapshot / token / thinking / tool / artifact / finding / done / error`。
    心跳为 `: ping` 注释帧（**无 event 名、无 `id:`**）。
    """
    ctx = _ctx(request)
    trace = trace_from(request, ctx)
    last_event_id = request.headers.get("Last-Event-ID")

    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            turn_id = turn or _turn_id_from_latest(db, session_id)
            prompt_text = _latest_user_text(db, session_id)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, DomainError):
                return JSONResponse(status_code=e.http_status, content={"error": e.to_dict()})
            raise

    try:
        after_seq = int(last_event_id) if last_event_id else 0
    except (TypeError, ValueError):
        after_seq = 0

    heartbeat = max(1, sse_heartbeat_seconds())

    def gen():
        buffer = StreamBuffer(turn_id=turn_id, session_id=session_id, tenant_id=ctx.tenant_id)
        # 首帧：snapshot（不写 id，避免毒化客户端水位）
        yield _sse_frame(None, "snapshot", {"turn_id": str(turn_id), "session_id": str(session_id)})

        # 1) 续传：重放已落库帧（晚订阅者补齐 / 断线续传）
        with session_scope() as db:
            replay = buffer.replay(db, after_seq=after_seq)
            terminal = buffer.is_terminal(db)
            replay_frames = [(r.seq, r.frame_type, r.payload) for r in replay]
        for seq, ftype, payload in replay_frames:
            yield _sse_frame(seq, ftype, payload)

        if terminal:
            yield ": stream-closed\n\n"
            return

        # 2) 驱动本轮 LLM token 流（transient：只写 StreamBuffer，不改领域状态）
        messages = [
            {"role": "system", "content": "你是数学科研助手，输出严谨、可溯源。"},
            {"role": "user", "content": prompt_text or "（本会话暂无用户输入）"},
        ]

        last_hb = time.time()
        done = False
        for ev in _drive(streaming.astream_completion(messages, task_class="long")):
            if time.time() - last_hb >= heartbeat:
                yield f": ping {datetime.now(timezone.utc).isoformat()}\n\n"
                last_hb = time.time()

            etype = ev.get("type")
            if etype == "token":
                payload = {"turn_id": str(turn_id), "delta": ev.get("delta", ""), "model": ev.get("model", "")}
                with session_scope() as db:
                    seq = buffer.append(db, frame_type="token", payload=payload)
                yield _sse_frame(seq, "token", {**payload, "seq": seq})
            elif etype == "done":
                payload = {
                    "turn_id": str(turn_id),
                    "finish_reason": ev.get("finish_reason", "stop"),
                    "model": ev.get("model", ""),
                    "degraded": bool(ev.get("degraded")),
                    "degraded_reason": ev.get("degraded_reason"),
                }
                with session_scope() as db:
                    seq = buffer.append(db, frame_type="done", payload=payload)
                yield _sse_frame(seq, "done", {**payload, "seq": seq})
                done = True
                break
            elif etype == "error":
                payload = {
                    "turn_id": str(turn_id),
                    "code": ev.get("code", "LLM_UNAVAILABLE"),
                    "message": ev.get("message", ""),
                    "retryable": bool(ev.get("retryable", True)),
                    "degraded": bool(ev.get("degraded")),
                }
                with session_scope() as db:
                    seq = buffer.append(db, frame_type="error", payload=payload)
                yield _sse_frame(seq, "error", {**payload, "seq": seq})
                done = True
                break

        if not done:
            yield f": ping {datetime.now(timezone.utc).isoformat()}\n\n"
        yield ": stream-closed\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={**_SSE_HEADERS, "X-Trace-Id": trace.trace_id})


def _drive(agen):
    """把 async generator 桥接为同步迭代（Starlette 的 StreamingResponse 用 sync gen）。

    在**单事件循环**里用 `asyncio.new_event_loop` 逐个拉取是不可行的；这里改用
    `anyio.from_thread` 风格的最简实现：直接以同步方式消费 —— 由调用方保证在
    FastAPI 的线程池执行（`def` 端点）。为稳妥起见，本函数用独立事件循环驱动。
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                yield loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                break
    finally:
        try:
            loop.run_until_complete(agen.aclose())
        finally:
            loop.close()
