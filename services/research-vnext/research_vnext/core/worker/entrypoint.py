"""RV03 worker 可执行入口。

    python -m research_vnext.core.worker.entrypoint            # 常驻
    python -m research_vnext.core.worker.entrypoint --once     # 跑一轮（CI/验收）
    python -m research_vnext.core.worker.entrypoint --once --no-queue
                                                              # 只走 PostgreSQL（证明 Valkey 丢了不丢活）
    python -m research_vnext.core.worker.entrypoint --replay-dlq <dlq_id>
                                                              # 显式重放（绝不自动）
    python -m research_vnext.core.worker.entrypoint --relay    # 只跑 outbox relay
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid

from ..db.engine import session_scope
from ..observability.logging_setup import configure_logging
from ..application import job_service as js
from ..application import outbox_relay as relay
from . import runtime

logger = logging.getLogger(__name__)


def _relay_once() -> dict:
    with session_scope() as db:
        return relay.relay_once(db)


def _replay(dlq_id: str) -> dict:
    with session_scope() as db:
        job = js.replay_dlq(db, dlq_id=uuid.UUID(dlq_id))
        return {"replayed_job_id": str(job.id), "status": js.public_status(job.status)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-vnext-worker", description="RV03 job worker")
    parser.add_argument("--once", action="store_true", help="跑一轮后退出")
    parser.add_argument("--no-queue", action="store_true", help="只从 PostgreSQL 领活（不读 Valkey）")
    parser.add_argument("--relay", action="store_true", help="只运行一次 outbox relay")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--pool", choices=["main", "compile"], default="main",
                        help="RV14：compile = 只消费独立编译队列（paper.export）")
    parser.add_argument("--replay-dlq", metavar="DLQ_ID", default="")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果（便于脚本断言）")
    parser.add_argument("--job", metavar="JOB_ID", default="", help="只执行指定作业")
    args = parser.parse_args(argv)

    configure_logging()

    if args.replay_dlq:
        out = _replay(args.replay_dlq)
    elif args.relay:
        out = _relay_once()
    elif args.job:
        out = asyncio.run(runtime.run_job(uuid.UUID(args.job)))
    elif args.once:
        out = asyncio.run(
            runtime.drain_once(limit=args.limit, from_queue=not args.no_queue, pool=args.pool)
        )
    else:
        try:
            asyncio.run(runtime.run_forever(limit=args.limit, pool=args.pool))
        except KeyboardInterrupt:  # pragma: no cover
            return 130
        out = {"stopped": True}

    if args.json:
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    else:
        logger.info("worker.exit result=%s", json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
