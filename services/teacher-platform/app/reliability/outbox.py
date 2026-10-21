"""事务性发件箱（TransactionalOutbox）+ dispatcher/reconciler + 背压（R11.5-C P0）。

核心不变式：**业务状态 / job / outbox 处于同一 DB 事务**。
- 入队：`outbox.enqueue(调用方 session, ...)` —— 由调用方事务一并 commit；
- 发布：dispatcher 用 `FOR UPDATE SKIP LOCKED` 原子领一批 PENDING，逐个投递到流
  （默认 Valkey XADD，message_id = outbox 行 id → 幂等重发）；成功后单独事务置 PUBLISHED；
- 故障语义：
  · DB commit 后发布失败 → 行保 PENDING，dispatcher 重试（不丢事件，消费侧 dedupe 防重复副作用）；
  · 发布成功但 DB 标记崩溃 → 下次重发同 message_id（幂等 XADD）；消费侧 InboxDedupe 兜底；
  · Valkey 短暂不可用 → 发布抛错 → 退避 available_at；恢复后自动补发；
  · attempt >= max_attempts → DLQ（人工/对账可见，不静默丢）。
- 背压：批量上限 + 并发信号量 + 指数退避（available_at = now + backoff^attempt）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import ReliabilityOutbox
from .trace import get_current

logger = logging.getLogger("reliability.outbox")

BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 60.0


class StreamPublisher(Protocol):
    """出站流发布器：外部副作用（Valkey XADD / 注入 fake）。"""

    async def publish(self, *, topic: str, message_id: str, payload: dict[str, Any], headers: dict[str, str] | None) -> None: ...


class ValkeyStreamPublisher:
    """默认实现：Valkey Streams XADD，message_id 显式指定（重发同 id 幂等 no-op）。

    消息 ID = "<ms>-<seq>"：ms 为毫秒、seq 为进程内单调递增数字（Redis Streams 要求
    序列部分为 64 位数字）。同一 message_id 在进程内映射到稳定 ID → 重发同 id 幂等；
    不同 message_id 严格递增 → 满足 Streams ID 序约束。
    """

    def __init__(self, redis_url: str | None = None, maxlen: int = 2000):
        from ..core.config import get_settings

        settings = get_settings()
        self.redis_url = redis_url or settings.valkey_url
        self._redis = None
        self._maxlen = maxlen
        self._ids: dict[str, str] = {}
        self._seq: int = 0

    async def _client(self):
        if self._redis is None:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    def _stream_id(self, message_id: str) -> str:
        cached = self._ids.get(message_id)
        if cached is not None:
            return cached
        self._seq += 1
        sid = f"{int(time.time() * 1000)}-{self._seq:06d}"
        self._ids[message_id] = sid
        return sid

    async def publish(self, *, topic: str, message_id: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        import json

        r = await self._client()
        body = dict(payload)
        body.setdefault("_meta", {})
        body["_meta"] = dict(body["_meta"], message_id=message_id, ts=time.time(), headers=headers or {})
        encoded = json.dumps(body, ensure_ascii=False, default=str)
        await r.xadd(
            topic,
            {"payload": encoded},
            id=self._stream_id(message_id),
            maxlen=self._maxlen,
        )


def backoff_seconds(attempt: int) -> float:
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, attempt - 1)))


def _now() -> datetime:
    return datetime.now(UTC)


class OutboxService:
    """outbox 表的读写操作（session 由调用方传入，事务边界归调用方）。"""

    @staticmethod
    async def enqueue(
        session: AsyncSession,
        *,
        topic: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ReliabilityOutbox:
        """在调用方事务内入队（不 commit）。返回行（含新 id 供下游引用）。"""
        ctx = get_current()
        row = ReliabilityOutbox(
            topic=topic,
            payload=payload,
            dedupe_key=dedupe_key,
            trace_id=trace_id or (ctx.trace_id if ctx else None),
            correlation_id=correlation_id or (ctx.correlation_id if ctx else None),
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def claim_batch(
        session: AsyncSession,
        *,
        topics: list[str] | None = None,
        limit: int = 20,
    ) -> list[ReliabilityOutbox]:
        """原子领取一批到期 PENDING（FOR UPDATE SKIP LOCKED：多 dispatcher 互不打架）。"""
        stmt = (
            select(ReliabilityOutbox)
            .where(
                ReliabilityOutbox.status == "PENDING",
                ReliabilityOutbox.available_at <= func.now(),
            )
            .order_by(ReliabilityOutbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        if topics:
            stmt = stmt.where(ReliabilityOutbox.topic.in_(topics))
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def mark_published(session: AsyncSession, row: ReliabilityOutbox) -> None:
        await session.execute(
            update(ReliabilityOutbox)
            .where(ReliabilityOutbox.id == row.id, ReliabilityOutbox.status == "PENDING")
            .values(status="PUBLISHED", published_at=_now())
        )

    @staticmethod
    async def mark_failed(
        session: AsyncSession, row: ReliabilityOutbox, error: str, *, quarantine_to_dlq: bool = False
    ) -> None:
        next_status = "DLQ" if quarantine_to_dlq else "PENDING"  # DLQ 只由本轮判断决定
        now = _now()
        if next_status == "PENDING":
            await session.execute(
                update(ReliabilityOutbox)
                .where(ReliabilityOutbox.id == row.id)
                .values(
                    attempt=row.attempt,
                    available_at=now + timedelta(seconds=backoff_seconds(row.attempt)),
                    last_error=error[:2000],
                )
            )
        else:
            await session.execute(
                update(ReliabilityOutbox)
                .where(ReliabilityOutbox.id == row.id)
                .values(status="DLQ", last_error=error[:2000])
            )


class OutboxDispatcher:
    """轮询 dispatcher：claim → publish（外部副作用）→ mark_published（新事务）。

    - poll_once 幂等可并发（SKIP LOCKED）；
    - 发布失败：attempt+1（行未改状态，仍可被下次认领）→ 退避；attempt 超限 → DLQ；
    - 背压：max_inflight 信号量限制并发发布（单个 publish 是网络 IO，可并发）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: StreamPublisher,
        *,
        topics: list[str] | None = None,
        batch: int = 20,
        poll_interval_s: float = 0.5,
        max_inflight: int = 8,
        name: str = "outbox-dispatcher",
    ) -> None:
        self._factory = session_factory
        self._publisher = publisher
        self._topics = topics
        self._batch = batch
        self._poll_interval = poll_interval_s
        self._inflight = asyncio.Semaphore(max_inflight)
        self._name = name

    async def poll_once(self) -> int:
        """一次轮询：返回本次成功投递条数（0 = 无到期消息）。"""
        processed = 0
        async with self._factory() as session:
            rows = await OutboxService.claim_batch(session, topics=self._topics, limit=self._batch)
            await session.commit()  # 认领即提交（抢占语义独立于发布）
        for row in rows:
            try:
                async with self._inflight:
                    await self._publish(session_factory=self._factory, row=row)
                processed += 1
            except Exception:  # 单条失败不中断轮询；下轮再由退避时间接管
                continue
        return processed

    async def _publish(self, *, session_factory: async_sessionmaker[AsyncSession], row: ReliabilityOutbox) -> None:
        row_id = str(row.id)
        try:
            await self._publisher.publish(
                topic=row.topic,
                message_id=row_id,
                payload=row.payload,
                headers={"x-trace-id": row.trace_id or "", "x-correlation-id": row.correlation_id or ""},
            )
        except Exception as exc:
            logger.warning("outbox publish failed", extra={"row": row_id, "error": repr(exc)[:500]})
            async with session_factory() as session:
                live = (
                    await session.execute(
                        select(ReliabilityOutbox).where(ReliabilityOutbox.id == row.id).with_for_update()
                    )
                ).scalar_one_or_none()
                if live is None:
                    return
                live.attempt += 1
                dlq = live.attempt >= live.max_attempts
                await OutboxService.mark_failed(session, live, repr(exc), quarantine_to_dlq=dlq)
                await session.commit()
            return
        async with session_factory() as session:
            await OutboxService.mark_published(session, row)
            await session.commit()
        logger.info("outbox published", extra={"row": row_id, "topic": row.topic})

    async def run(self) -> None:
        logger.info("outbox dispatcher started", extra={"name": self._name})
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 —— 轮询循环必须存活
                logger.exception("outbox poll_once crashed; retry in poll interval")
            await asyncio.sleep(self._poll_interval)


class OutboxReconciler:
    """对账：把长时间滞留技术态修正，并输出 DLQ/缺口诊断。

    - stale PENDING（available_at 已过但很久未被认领——dispatcher 挂/停）→ 无操作；
      正常 poll 会接管；仅统计通报避免重复副作用（维护 at-least-once + dedupe 不变量）。
    - DLQ 行仅报告（人工/对账工具可见），不自动重投（防止副作用风暴）。
    - count_pending/count_dlq 供验收与监控断言。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._factory = session_factory

    async def report(self) -> dict[str, int]:
        async with self._factory() as session:
            pending = (
                await session.execute(
                    select(ReliabilityOutbox.id)
                    .where(ReliabilityOutbox.status == "PENDING")
                    .limit(10000)
                )
            ).scalars().all()
            dlq = (
                await session.execute(
                    select(ReliabilityOutbox.id)
                    .where(ReliabilityOutbox.status == "DLQ")
                    .limit(10000)
                )
            ).scalars().all()
            published = (
                await session.execute(
                    select(ReliabilityOutbox.id)
                    .where(ReliabilityOutbox.status == "PUBLISHED")
                    .limit(10000)
                )
            ).scalars().all()
        return {"pending": len(pending), "published": len(published), "dlq": len(dlq)}
