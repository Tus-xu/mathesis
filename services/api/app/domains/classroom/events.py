"""课堂生成事件总线（对齐 OpenMAIC 生成进度可见化：outline/slide/status 实时推送）。

进程内 pub/sub：生成管线在关键节点 publish，SSE 端点 subscribe 并以
text/event-stream 推给前端。带重放缓冲：用户刷新/断线重连时先补发历史事件，
再继续直播，保证「刷新页面进度不丢」。

事件契约（type → data）：
- status   → {status: generating|ready|failed, stage?: outline|content|practice, error?}
- title    → {title}
- outlines → {outlines: [...], slide_count}
- slide    → {index, order, slide}（单页完成即推，不等前缀）
- practice → {practice}（ready 之后补推，不阻塞进课堂）
- done     → {}（终止哨兵，SSE 关闭）
"""

from __future__ import annotations

import asyncio
import uuid

_MAX_LOG_EVENTS = 400

_subscribers: dict[str, set[asyncio.Queue]] = {}
_event_log: dict[str, list[dict]] = {}


def publish_session_event(session_id: str | uuid.UUID, event_type: str, data: dict | None = None) -> None:
    """记录并广播一条生成事件；无订阅者时仅入重放缓冲（开销 O(1)，可放心多发）。"""
    sid = str(session_id)
    event = {"type": event_type, "data": data or {}}
    log = _event_log.setdefault(sid, [])
    log.append(event)
    if len(log) > _MAX_LOG_EVENTS:
        del log[: len(log) - _MAX_LOG_EVENTS]
    for q in list(_subscribers.get(sid, ())):
        try:
            q.put_nowait(event)
        except RuntimeError:  # 队列绑定的事件循环已关闭（极端情况），丢弃该订阅者
            _subscribers[sid].discard(q)


def subscribe_session_events(session_id: str | uuid.UUID) -> tuple[list[dict], asyncio.Queue]:
    """返回 (历史事件快照, 直播队列)；调用方先回放快照再消费队列。"""
    sid = str(session_id)
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(sid, set()).add(q)
    return list(_event_log.get(sid, ())), q


def unsubscribe_session_events(session_id: str | uuid.UUID, q: asyncio.Queue) -> None:
    sid = str(session_id)
    subs = _subscribers.get(sid)
    if subs is not None:
        subs.discard(q)
        if not subs:
            _subscribers.pop(sid, None)


def clear_session_events(session_id: str | uuid.UUID) -> None:
    """会话删除时清理重放缓冲；活跃订阅者会收到 done 哨兵后自然关闭。"""
    sid = str(session_id)
    _event_log.pop(sid, None)
    subs = _subscribers.pop(sid, None)
    if subs:
        done = {"type": "done", "data": {}}
        for q in subs:
            try:
                q.put_nowait(done)
            except RuntimeError:
                continue
