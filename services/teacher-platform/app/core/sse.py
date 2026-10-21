"""SSE 基建（02-ARCHITECTURE §7.3，B3 §3.4）：双通道 + Last-Event-ID 恢复内建。

传输契约（R11.5-C 冻结，头/帧/重连三件套）：
  - 响应头：`Cache-Control: no-cache`、`X-Accel-Buffering: no`、`Connection: keep-alive`
    （任何 SSE 端点必须经 `sse_response()` 构造，禁止裸 EventSourceResponse 产生头漂移）；
  - 帧协议：首行 `retry:3000` → `snapshot`（当前权威状态）→ `after(Last-Event-ID)` durable
    重放 → 续接 live（Pub/Sub 扇出）；`wrapped` 帧统一
    `event: event` + data={seq,event_id,event_type,payload,created_at}，且**每条 live 帧携带
    id=seq**（浏览器 Last-Event-ID 随帧推进，断线重连从断点续）；
  - 重连：客户端带 `Last-Event-ID`（或 `lastEventId` query）→ 服务端 `after(seq)` 幂等补发；
    15s 心跳（`: ping` 注释行，浏览器忽略）；断连由 `request.is_disconnected()` 检测。

流程：连接 → `retry:3000` → snapshot（当前权威状态）→ `after(Last-Event-ID)` durable 重放
→ 续接 live（Pub/Sub 扇出）；15s 心跳（`: ping` 注释行，浏览器忽略）；
断连由 `request.is_disconnected()` 检测，清理由调用方 `CancelScope(shield=True)` 保护
（图执行不中断，由 checkpoint 续，sse-starlette 参考）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from .config import get_settings

logger = logging.getLogger("teacher.sse")

SnapshotFn = Callable[[], Awaitable[dict[str, Any]]]
ReplayFn = Callable[[int], Awaitable[list[dict[str, Any]]]]

# R11.5-C 冻结传输头：任何 SSE 端点统一（Nginx 不缓冲 + 不缓存 + 长连接）
FROZEN_SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def sse_response(generator_factory: Any, **kwargs: Any) -> EventSourceResponse:
    """统一 SSE 构造入口：注入冻结头（冻结头优先于调用方传入，防 header 漂移）。

    生成器若为异步生成器函数则直接传 EventSourceResponse；不动其它参数语义。
    """
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(FROZEN_SSE_HEADERS)  # 冻结头不可被调用方覆盖（Cache-Control 等契约钉死）
    return EventSourceResponse(generator_factory, headers=headers, **kwargs)


def _dumps(data: Any) -> str:
    """sse-starlette 3.4.11 对 data 直接 str()，不自动 JSON——契约要求 data 是合法 JSON。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def _wrapped_live_seq_id(msg: dict[str, Any]) -> str:
    """wrapped live 帧的 Last-Event-ID 派生：优先取广播包装 payload.seq（课堂契约）。

    返回空串 = 无 seq 可派生（named 帧 / 旧形状），此时浏览器沿用最后收到的 id。
    """
    payload = msg.get("payload")
    if isinstance(payload, dict):
        seq = payload.get("seq", "")
        if seq is not None and str(seq) not in ("", "None"):
            return str(seq)
    seq = msg.get("seq")
    return str(seq) if seq is not None and str(seq) not in ("", "None") else ""


def _parse_last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id", "") or request.query_params.get("lastEventId", "")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _pubsub_listener(r: Redis, channel: str, queue: asyncio.Queue) -> asyncio.Task:
    """后台消费 Pub/Sub，把消息投递到队列；SSE 生成器统一从队列取。"""

    async def _collect() -> None:
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    await queue.put(msg["data"])
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return asyncio.create_task(_collect())


async def sse_channel(
    request: Request,
    *,
    snapshot_fn: SnapshotFn | None = None,
    replay_fn: ReplayFn | None = None,
    live_channel: str | None = None,
    heartbeat: float | None = None,
    redis: Redis | None = None,
    frame: str = "named",
) -> EventSourceResponse:
    """构造双通道 EventSourceResponse。

    - snapshot_fn：连接时发 `snapshot`（权威状态投影，课堂/任务通用）。
    - replay_fn：`after(Last-Event-ID)` 回放 durable 缺失事件（PG 权威源）。
    - live_channel：重放后续接 Pub/Sub 实时广播。
    - frame：`named`＝事件名即 event_type、data 为裸 payload（demo 任务流）；
      `wrapped`＝课堂契约（前端 classroomReducer 权威）：durable/live 一律
      `event: event` + data={seq,event_id,event_type,payload,created_at} 包装器。
    """
    settings = get_settings()
    hb = heartbeat or float(settings.sse_heartbeat_seconds)
    wrapped = frame == "wrapped"

    async def event_gen():
        r = redis or Redis.from_url(settings.valkey_url, decode_responses=True)
        collector: asyncio.Task | None = None
        try:
            # mock 契约：首行 retry: 3000（前端 fetchEventSource 解析）
            yield {"retry": 3000}
            after_seq = _parse_last_event_id(request)
            logger.info("sse connect", extra={"channel": live_channel, "after_seq": after_seq})

            if snapshot_fn is not None:
                yield {"event": "snapshot", "data": _dumps(await snapshot_fn())}

            # durable 重放：after(Last-Event-ID) 补齐缺失事件后续接 live（红线 7）
            if replay_fn is not None:
                for ev in await replay_fn(after_seq):
                    if wrapped:
                        yield {"event": "event", "data": _dumps(ev), "id": str(ev.get("seq", ""))}
                    else:
                        yield {"event": ev["event_type"], "data": _dumps(ev["payload"]), "id": str(ev["seq"])}

            if live_channel:
                queue: asyncio.Queue = asyncio.Queue(maxsize=256)
                collector = _pubsub_listener(r, f"live:{live_channel}", queue)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        raw = await asyncio.wait_for(queue.get(), timeout=hb)
                    except TimeoutError:
                        yield {"comment": "ping"}  # 心跳（15s）
                        continue
                    msg = json.loads(raw)
                    # R11.5-C：wrapped live 帧必须带 id=seq（Last-Event-ID 断点续传）
                    if wrapped:
                        seq_id = _wrapped_live_seq_id(msg)
                        yield {"event": "event", "data": _dumps(msg.get("payload")), "id": seq_id}
                    else:
                        yield {"event": msg["event_type"], "data": _dumps(msg["payload"])}
        finally:
            if collector is not None:
                collector.cancel()
                try:
                    await collector
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if redis is None:
                await r.aclose()
            logger.info("sse closed", extra={"channel": live_channel})

    return sse_response(
        event_gen(),
        ping=None,  # 由生成器内自定义心跳控制
    )
