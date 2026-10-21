# coding: utf-8
"""课堂实时扇出的业务语义（RealtimeHub 传输层归 B3，B4 提供业务侧协议与 fake）。

恢复协议（架构 §7.1/§12.4）：连接先 snapshot（权威投影）→ durable 重放
after(Last-Event-ID)（classroom_events 为 PG 权威回放源）→ 续 live 订阅。
InProcRealtimeBus 是测试期协议 fake（B4 规范 §8 口径，不复制 B3 实现）：
生产实现 = B3 RealtimeHub（Valkey Streams + Pub/Sub），接口不变。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.classroom import service as cs


class LiveBus(Protocol):
    """live 扇出（Pub/Sub 语义：允许丢失，durable 兜底）。"""

    async def publish(self, session_id: str, event: str, data: dict) -> None: ...
    def subscribe(self, session_id: str) -> AsyncIterator[tuple[str, dict]]: ...


@dataclass
class InProcRealtimeBus:
    """进程内 fake（asyncio.Queue 扇出；仅单测/单进程 dev 用，禁生产）。"""

    _subscribers: dict[str, list[asyncio.Queue]] = field(default_factory=dict)

    async def publish(self, session_id: str, event: str, data: dict) -> None:
        for q in self._subscribers.get(session_id, []):
            q.put_nowait((event, data))

    def subscribe(self, session_id: str) -> AsyncIterator[tuple[str, dict]]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(session_id, []).append(q)

        async def _gen() -> AsyncIterator[tuple[str, dict]]:
            try:
                while True:
                    yield await q.get()
            finally:
                self._subscribers[session_id].remove(q)

        return _gen()


@dataclass(frozen=True)
class StreamFrame:
    """SSE 帧语义：id=seq（Last-Event-ID 锚点）、event、data。"""

    seq: int | None
    event: str
    data: dict


async def teacher_stream(
    session: AsyncSession, session_id: str, *,
    bus: LiveBus, last_event_id: int = 0,
) -> AsyncIterator[StreamFrame]:
    """教师流：snapshot → durable 重放 → live 续接（心跳由 B3 SSE 层加）。"""
    snap = await cs.snapshot(session, _as_uuid(session_id))
    yield StreamFrame(seq=snap['last_seq'], event='snapshot', data=snap)
    for ev in await cs.replay_events(session, _as_uuid(session_id), after_seq=last_event_id):
        yield StreamFrame(seq=ev.seq, event=ev.event_type, data=ev.payload)
    async for event, data in bus.subscribe(session_id):
        yield StreamFrame(seq=None, event=event, data=data)
        await asyncio.sleep(0)  # 让出事件循环（压测 P8 友好）


async def student_stream(
    session: AsyncSession, session_id: str, *,
    bus: LiveBus, last_event_id: int = 0,
) -> AsyncIterator[StreamFrame]:
    """学生流：同构，载荷为学生视角投影（无他人身份/未公布分布）。"""
    snap = await cs.snapshot(session, _as_uuid(session_id), student=True)
    yield StreamFrame(seq=snap['last_seq'], event='snapshot', data=snap)
    for ev in await cs.replay_events(session, _as_uuid(session_id), after_seq=last_event_id):
        yield StreamFrame(seq=ev.seq, event=ev.event_type, data=cs._student_config(ev.payload))
    async for event, data in bus.subscribe(session_id):
        yield StreamFrame(seq=None, event=event, data=data)


def _as_uuid(session_id: str):
    from uuid import UUID

    return UUID(session_id) if isinstance(session_id, str) else session_id
