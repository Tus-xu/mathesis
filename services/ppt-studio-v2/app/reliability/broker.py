"""Stream 抽象：让 worker/dispatcher 与具体 Broker 解耦。

- RedisStreamBroker：真实 Valkey/Redis（XADD/XREADGROUP/XACK/XGROUP CREATE）；
- InMemoryBroker：单元测试用（内存流，行为语义一致）。
"""
from __future__ import annotations

import asyncio
from typing import Protocol


class BrokerError(Exception):
    pass


class StreamBroker(Protocol):
    async def ping(self) -> bool: ...
    async def group_create(self, group: str, stream: str) -> None: ...
    async def xadd(self, stream: str, fields: dict[str, str], message_id: str | None = None) -> str: ...
    async def xreadgroup(
        self, group: str, consumer: str, stream: str, count: int, block_ms: int, restore_entries: bool = True
    ) -> list[tuple[str, dict[str, str]]]: ...
    async def xack(self, group: str, stream: str, *message_ids: str) -> int: ...


class RedisStreamBroker:
    """真实 Valkey/Redis 实现（redis-py asyncio，uri 来自 config.valkey_url）。"""

    def __init__(self, url: str):
        from redis.asyncio import Redis

        self._redis: Redis = Redis.from_url(url, decode_responses=True)

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def group_create(self, group: str, stream: str) -> None:
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001  (BusyGroup → 幂等 OK)
            msg = str(exc)
            if "BUSYGROUP" not in msg and "already exists" not in msg:
                raise BrokerError(f"xgroup_create failed: {exc}") from exc

    async def xadd(self, stream: str, fields: dict[str, str], message_id: str | None = None) -> str:
        return await self._redis.xadd(stream, fields, id=message_id or "*")

    async def xreadgroup(
        self, group: str, consumer: str, stream: str, count: int, block_ms: int, restore_entries: bool = True
    ) -> list[tuple[str, dict[str, str]]]:
        resp = await self._redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        if not resp:
            return []
        entries: list[tuple[str, dict[str, str]]] = []
        for _stream, items in resp:
            for mid, fields in items:
                entries.append((mid, dict(fields)))
        return entries

    async def xack(self, group: str, stream: str, *message_ids: str) -> int:
        if not message_ids:
            return 0
        return await self._redis.xack(stream, group, *message_ids)

    async def close(self) -> None:
        await self._redis.aclose()


class InMemoryBroker:
    """单元测试 Broker：内存流 + 组游标；语义与 Redis Streams 对齐（未 ACK 保留在 PEL）。"""

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._seq = 0
        self._group_cursors: dict[tuple[str, str], int] = {}

    async def ping(self) -> bool:
        return True

    async def group_create(self, group: str, stream: str) -> None:
        self._streams.setdefault(stream, [])
        self._group_cursors.setdefault((group, stream), 0)

    async def xadd(self, stream: str, fields: dict[str, str], message_id: str | None = None) -> str:
        mid = message_id or f"m-{len(self._streams.setdefault(stream, [])):06d}"
        self._streams.setdefault(stream, []).append((mid, dict(fields)))
        return mid

    async def xreadgroup(
        self, group: str, consumer: str, stream: str, count: int, block_ms: int, restore_entries: bool = True
    ) -> list[tuple[str, dict[str, str]]]:
        await asyncio.sleep(0)  # 让事件循环让出；block 不做真实等待
        entries = self._streams.get(stream, [])
        cur = self._group_cursors.get((group, stream), 0)
        pending = entries[cur: cur + count]
        if not pending:
            return []
        self._group_cursors[(group, stream)] = cur + len(pending)
        return [(m, f) for m, f in pending]

    async def xack(self, group: str, stream: str, *message_ids: str) -> int:
        return len(message_ids)

    async def close(self) -> None:
        self._streams.clear()
