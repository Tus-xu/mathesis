"""PPT Studio V2 Worker（8200）常驻入口：dispatcher + reconciler + worker 编排。

用法（服务目录内）：
  uv run python worker.py            # 前台常驻
  环境变量：DATABASE_URL / VALKEY_URL / PPT_STUDIO_ENVIRONMENT 等（经 app.config）
"""
from __future__ import annotations

import asyncio
import signal

from app.config import get_settings
from app.db.session import close_engine, session_factory
from app.logging import setup_logging
from app.reliability.dispatcher import OutboxDispatcher
from app.reliability.jobs import JobEngine
from app.reliability.reconciler import JobReconciler
from app.reliability.worker import JobWorker


async def _amain() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = JobEngine(
        lease_seconds=settings.lease_seconds,
        backoff_base_s=settings.backoff_base_s,
        backoff_max_s=settings.backoff_max_s,
    )
    dispatch = OutboxDispatcher(session_factory(), batch=settings.outbox_batch)
    reconcile = JobReconciler(session_factory(), engine)
    worker = JobWorker(session_factory(), engine, batch=settings.worker_pool)

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    tasks = [
        asyncio.create_task(dispatch.run(stop), name="dispatcher"),
        asyncio.create_task(reconcile.run(stop), name="reconciler"),
        asyncio.create_task(worker.run(stop), name="worker"),
    ]
    try:
        await stop.wait()
    finally:
        await dispatch.close()
        await worker.close()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_engine()


def entry() -> None:
    """进程入口（worker.py 薄壳与 `python -m app.worker` 复用）。"""
    asyncio.run(_amain())


if __name__ == "__main__":
    entry()
