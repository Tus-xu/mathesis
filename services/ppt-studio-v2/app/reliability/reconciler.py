"""JobReconciler：租约回收 + 超时 DLQ（8200 自持）。

- reclaim_expired：CLAIMED 且租约过期 → 重试（PENDING+退避）或（attempt 达上限/取消）终态；
  fencing：回收后旧 claim_token 失效，迟到 worker 的 complete/fail 被 CAS 拒绝。
- dlq_expired_deadlines：PENDING（排队超时）/ 剩余 CLAIMED 超 deadline → DLQ。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.reliability.jobs import JobEngine

logger = logging.getLogger("ps2.reconciler")


def _now() -> datetime:
    return datetime.now(UTC)


class JobReconciler:
    def __init__(self, session_factory: async_sessionmaker, engine: JobEngine, *, interval_s: float = 5.0):
        self.session_factory = session_factory
        self.engine = engine
        self.interval_s = interval_s

    async def tick_once(self) -> dict[str, int]:
        reclaimed: list = []
        dlqed: list = []
        async with self.session_factory() as session:
            reclaimed = await self.engine.reclaim_expired(session, now=_now())
            dlqed = await self.engine.dlq_expired_deadlines(session, now=_now())
            await session.commit()
        return {"reclaimed": len(reclaimed), "dlqed": len(dlqed)}

    async def run(self, stop: asyncio.Event | None = None, *, interval_s: float | None = None) -> None:
        interval = interval_s or self.interval_s
        if stop is None:
            stop = asyncio.Event()
        while not stop.is_set():
            try:
                await self.tick_once()
            except Exception:  # noqa: BLE001
                logger.exception("reconciler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
