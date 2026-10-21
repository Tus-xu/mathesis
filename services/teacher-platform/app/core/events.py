"""G4 双通道事件模型（02-ARCHITECTURE §7，B3 灵魂基建）。

- **Durable** `publish_durable`：Valkey Streams `XADD`（热通道，带 maxlen 裁剪）
  + PG `UNIQUE(scope, seq)` 双写（权威回放源，裁剪后仍可 after(seq) 回放）。
  seq 由 PG 事务内 `pg_advisory_xact_lock(scope)` 串行分配——幂等靠 DB 唯一约束兜底（红线 8）。
- **Live** `publish_live`：Valkey Pub/Sub 广播（允许丢失）。
- **回放** `replay`：读 PG（权威源）`after(seq)` 增量。

需要 replay 的事件禁止只走 Pub/Sub（架构红线 7）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from ..db.models import ClassroomEvents, TaskEvents
from ..db.session import session_factory

logger = logging.getLogger("teacher.events")

ScopeKind = Literal["task", "session"]

_PG_TABLE = {
    "task": TaskEvents,
    "session": ClassroomEvents,
}


class EventPublisher:
    def __init__(self, redis_url: str | None = None):
        settings = get_settings()
        self.redis_url = redis_url or settings.valkey_url
        self._redis: Redis | None = None
        self._maxlen = settings.sse_stream_maxlen

    async def redis(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    @staticmethod
    def stream_key(scope_kind: ScopeKind, scope_id: str) -> str:
        return f"events:{scope_kind}:{scope_id}"

    @staticmethod
    def live_channel(channel: str) -> str:
        return f"live:{channel}"

    async def publish_durable(
        self,
        *,
        scope_kind: ScopeKind,
        scope_id: str | uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        """XADD + PG 双写，返回分配的 seq。重发同 seq 由 DB 唯一约束拒绝。"""
        sid = str(scope_id)
        table = _PG_TABLE[scope_kind]

        async with session_factory()() as session:
            seq = await self._alloc_seq(session, table, scope_kind, sid, event_type, payload)
            # Valkey Streams 热通道：以 seq 为消息 ID（after(seq) 语义对齐）
            try:
                r = await self.redis()
                await r.xadd(self.stream_key(scope_kind, sid), {"seq": seq, "event_type": event_type, "payload": json.dumps(payload, ensure_ascii=False)}, id=f"{seq}-0", maxlen=self._maxlen)
            except Exception:  # noqa: BLE001 —— Valkey 失败不回滚 PG（PG 仍权威可回放）
                logger.exception("valkey xadd failed; pg remains authoritative", extra={"task_id": sid, "seq": seq})
        # 双通道：durable 事件同时镜像到 live（同 scope 通道）——实时订阅与断线重放共用同一事件源
        await self.publish_live(f"{scope_kind}:{sid}", event_type, payload)
        return seq

    async def _alloc_seq(
        self,
        session: AsyncSession,
        table: Any,
        scope_kind: ScopeKind,
        scope_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        scope_col = "task_id" if scope_kind == "task" else "session_id"
        # 事务级 advisory lock 串行化同一 scope 的 seq 分配（幂等由 UNIQUE 兜底）
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(scope_id)})
        last = (
            await session.execute(
                select(table).where(getattr(table, scope_col) == scope_id).order_by(getattr(table, "seq").desc()).limit(1)
            )
        ).scalar_one_or_none()
        seq = (last.seq if last else 0) + 1
        session.add(table(**{scope_col: scope_id, "seq": seq, "event_type": event_type, "payload": payload}))
        await session.commit()
        return seq

    async def publish_live(self, channel: str, event_type: str, payload: dict[str, Any]) -> None:
        """Pub/Sub 实时广播（允许丢失；丢失后由 durable 里程碑或 snapshot 兜底）。"""
        try:
            r = await self.redis()
            await r.publish(self.live_channel(channel), json.dumps({"event_type": event_type, "payload": payload}, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            logger.exception("valkey publish failed", extra={"channel": channel, "event_type": event_type})

    async def replay(
        self,
        *,
        scope_kind: ScopeKind,
        scope_id: str | uuid.UUID,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """权威回放：PG `after(seq)` 增量（Streams 裁剪后仍完整）。"""
        sid = str(scope_id)
        table = _PG_TABLE[scope_kind]
        scope_col = "task_id" if scope_kind == "task" else "session_id"
        async with session_factory()() as session:
            rows = (
                await session.execute(
                    select(table)
                    .where(and_(getattr(table, scope_col) == sid, getattr(table, "seq") > after_seq))
                    .order_by(getattr(table, "seq"))
                    .limit(limit)
                )
            ).scalars().all()
        return [
            {"seq": row.seq, "event_type": row.event_type, "payload": row.payload, "created_at": row.created_at.isoformat()}
            for row in rows
        ]

    async def latest_seq(self, *, scope_kind: ScopeKind, scope_id: str | uuid.UUID) -> int:
        sid = str(scope_id)
        table = _PG_TABLE[scope_kind]
        scope_col = "task_id" if scope_kind == "task" else "session_id"
        async with session_factory()() as session:
            row = (
                await session.execute(
                    select(getattr(table, "seq"))
                    .where(getattr(table, scope_col) == sid)
                    .order_by(getattr(table, "seq").desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return row or 0


def _lock_key(scope_id: str) -> int:
    return int(uuid.uuid5(uuid.NAMESPACE_URL, f"seq-alloc/{scope_id}").int % (2**63))


_publisher: EventPublisher | None = None


def get_publisher() -> EventPublisher:
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher
