"""Classroom RealtimeHub 传输层（架构 §8 签名，B3 拥有；业务规则/状态机在 B4）。

- `join`：课堂作用域接入（M1 骨架：token 存在性校验；会话/参与者业务校验由 B4 提供）。
- `broadcast_live`：Pub/Sub 实时扇出（在线人数/作答统计刷新等允许丢失的广播）。
- `durable_replay`：Streams/PG 双通道 `after(seq)` 回放（课堂活动生命周期等必须可 replay 的事件）。
- `snapshot`：权威状态投影——内容由 B4 的 snapshot provider 注入（服务端权威，前端仅 projection）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable

from .events import EventPublisher, get_publisher

logger = logging.getLogger("teacher.hub")

SnapshotProvider = Callable[[str], Awaitable[dict[str, Any]]]


class RealtimeHub:
    def __init__(self, publisher: EventPublisher | None = None):
        self._publisher = publisher or get_publisher()
        self._snapshot_provider: SnapshotProvider | None = None

    def set_snapshot_provider(self, provider: SnapshotProvider) -> None:
        """B4 在应用启动时注入权威快照实现（M1 缺省返回传输层骨架状态）。"""
        self._snapshot_provider = provider

    async def join(self, session_id: str, token: str | None) -> None:
        """课堂作用域接入（M1 骨架）。token 语义（绑定 session+participant）由 B4 落业务校验。"""
        if not token:
            raise ValueError("missing classroom token")
        # 骨架期：仅记录接入；B4 在此注入会话/参与者状态校验
        logger.info("hub join", extra={"session_id": session_id, "channel": f"session:{session_id}"})

    async def broadcast_live(self, session_id: str | uuid.UUID, event: str, data: dict[str, Any]) -> None:
        await self._publisher.publish_live(f"session:{session_id}", event, data)

    async def durable_replay(self, session_id: str | uuid.UUID, after_seq: int = 0) -> list[dict[str, Any]]:
        return await self._publisher.replay(scope_kind="session", scope_id=session_id, after_seq=after_seq)

    async def snapshot(self, session_id: str | uuid.UUID) -> dict[str, Any]:
        if self._snapshot_provider is not None:
            return await self._snapshot_provider(str(session_id))
        return {
            "session_id": str(session_id),
            "status": "created",
            "note": "transport-ready (snapshot provider 由 B4 注入)",
        }


_hub: RealtimeHub | None = None


def get_hub() -> RealtimeHub:
    global _hub
    if _hub is None:
        _hub = RealtimeHub()
    return _hub
