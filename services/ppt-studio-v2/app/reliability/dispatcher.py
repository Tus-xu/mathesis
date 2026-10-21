"""事务性发件箱 + 发布调度器（8200 自持）。

- enqueue：仅在业务事务内调用（同事务写 outbox 行，见 JobEngine.create）；
- OutboxDispatcher.tick_once：轮询 PENDING（FOR UPDATE SKIP LOCKED）→ XADD 流 → 同事务标记 PUBLISHED；
  DB commit 后发布失败不丢事件（行保 PENDING，下轮重试，inbox 去重防重复副作用）；
  发布失败超 max_attempts → DLQ。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.persistence.models import PptV2Outbox
from app.reliability.broker import RedisStreamBroker, StreamBroker
from app.reliability.queue_policy import OWNER_SERVICE_8200, stream_of

logger = logging.getLogger("ps2.dispatcher")

_GROUP = "ps8200-dispatch"


def _now() -> datetime:
    return datetime.now(UTC)


class OutboxDispatcher:
    def __init__(self, session_factory: async_sessionmaker, broker: StreamBroker | None = None, *, batch: int = 20):
        self.session_factory = session_factory
        self.broker = broker or RedisStreamBroker(get_settings().valkey_url)
        self.batch = batch

    async def tick_once(self) -> int:
        """发布一批 PENDING outbox → 流；返回成功发布行数。"""
        published = 0
        async with self.session_factory() as session:
            now = _now()
            rows = (
                (
                    await session.execute(
                        select(PptV2Outbox)
                        .where(PptV2Outbox.status == "PENDING")
                        .where(PptV2Outbox.available_at <= now)
                        .order_by(PptV2Outbox.created_at)
                        .limit(self.batch)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                stream_key = stream_of(OWNER_SERVICE_8200, row.topic)
                await self.broker.group_create(_GROUP, stream_key)
                if await self._publish_one(session, row, stream_key):
                    published += 1
            await session.commit()
        return published

    async def _publish_one(self, session, row: PptV2Outbox, stream_key: str) -> bool:  # noqa: ANN001
        # 幂等依赖消费侧 inbox 去重（dedupe_key='job:{job_id}'）：发布后崩溃未标记 PUBLISHED →
        # 重发将产生新 stream id，但消费侧命中 inbox → 跳过（单副作用）。
        # Stream ID 必须是 ms-seq 格式（UUID 非法），故用 "*" 让服务端生成。
        try:
            message_id = await self.broker.xadd(
                stream_key,
                {
                    "job_id": str(row.payload.get("job_id", "")),
                    "dedupe": row.dedupe_key or str(row.id),
                    "payload": json.dumps(row.payload, ensure_ascii=False),
                    "trace_id": row.trace_id or "",
                },
            )
        except Exception as exc:  # noqa: BLE001
            row.attempt += 1
            row.last_error = f"xadd failed: {type(exc).__name__}: {exc}"
            if row.attempt >= row.max_attempts:
                row.status = "DLQ"
            else:
                row.status = "PENDING"
                row.available_at = _now() + timedelta(seconds=min(2**row.attempt, 30))
            return False
        row.status = "PUBLISHED"
        row.published_at = _now()
        row.message_id = message_id
        return True

    async def run(self, stop: asyncio.Event | None = None, *, interval_s: float | None = None) -> None:
        settings = get_settings()
        interval = interval_s if interval_s is not None else settings.dispatch_interval_s
        if stop is None:
            stop = asyncio.Event()
        while not stop.is_set():
            try:
                await self.tick_once()
            except Exception:  # noqa: BLE001
                logger.exception("dispatcher tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def close(self) -> None:
        await self.broker.close()
