"""JobWorker：流触发（push）+ DB 轮询（pull，崩溃安全的后备/重试路径）。

- process_stream：XREADGROUP strm:ps8200:ppt.job → inbox 去重 → claim → step → complete/fail；
- process_pending：DB 轮询 PENDING(available_at<=now) → claim → step → complete/fail（重试唯一重触发路径）；
- 取消：claim 阶段若 cancel_requested → CANCELLED；租约过期由 reconciler 统一回收；
- 超时：deadline 由 reconciler 转为 DLQ；claim 阶段发现已过期 → 直接 DLQ。

每次执行一个作业 = 单事务（持行锁跨 step，符合 lease 语义）；kill 进程 → 事务未提交 → 租约过期 → reconciler 回收重试。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.reliability.broker import RedisStreamBroker, StreamBroker
from app.reliability.inbox import InboxService
from app.reliability.jobs import JOB_TOPIC, JobEngine
from app.reliability.presenton_client import PresentonClient
from app.reliability.queue_policy import OWNER_SERVICE_8200, policy_for, stream_of
from app.reliability.steps import DeckCancelled, StepContext, StepFailure, run_step

logger = logging.getLogger("ps2.worker")


def _consumer_name() -> str:
    return f"ps2-{os.getpid()}"


class JobWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        engine: JobEngine,
        *,
        broker: StreamBroker | None = None,
        presenton: PresentonClient | None = None,
        batch: int = 8,
        poll_interval_s: float = 0.05,
    ):
        settings = get_settings()
        self.session_factory = session_factory
        self.engine = engine
        self.inbox = InboxService()
        self.broker = broker or RedisStreamBroker(settings.valkey_url)
        self.step_ctx = StepContext(
            presenton=presenton or PresentonClient(settings.presenton_base_url, settings.presenton_service_token),
            session_factory=session_factory,
            engine=engine,
            settings=settings,
        )
        self.batch = batch
        self.poll_interval_s = poll_interval_s
        self.stream_key = stream_of(OWNER_SERVICE_8200, JOB_TOPIC)
        self.group = policy_for(OWNER_SERVICE_8200).consumer_group
        self.consumer = _consumer_name()

    # ── 主循环 ────────────────────────────────────────────────────
    async def run(self, stop: asyncio.Event | None = None) -> None:
        if stop is None:
            stop = asyncio.Event()
        await self.broker.group_create(self.group, self.stream_key)
        while not stop.is_set():
            try:
                await self.process_stream()
            except Exception:  # noqa: BLE001
                logger.exception("worker stream tick failed")
            try:
                await self.process_pending()
            except Exception:  # noqa: BLE001
                logger.exception("worker pending poll failed")
            if not stop.is_set():
                await asyncio.sleep(self.poll_interval_s)

    # ── 流路径（低延迟推送；inbox 去重保证单副作用）────────────────
    async def process_stream(self) -> int:
        entries = await self.broker.xreadgroup(
            self.group, self.consumer, self.stream_key, count=self.batch, block_ms=50
        )
        for message_id, fields in entries:
            try:
                await self._handle_message(fields)
            except Exception:  # noqa: BLE001
                logger.exception("message handling failed", message_id=message_id)
            await self.broker.xack(self.group, self.stream_key, message_id)
        return len(entries)

    async def _handle_message(self, fields: dict[str, str]) -> None:
        payload = json.loads(fields.get("payload") or "{}")
        dedupe = fields.get("dedupe") or f"job:{payload.get('job_id')}"
        async with self.session_factory() as session:
            verdict = await self.inbox.dedupe(
                session, dedupe_key=dedupe, payload={"job_id": payload.get("job_id"), "topic": payload.get("msg_ctx") or ""}
            )
            if verdict in ("DUPLICATE", "CONFLICT"):
                if verdict == "CONFLICT":
                    logger.error("inbox conflict on message", dedupe=dedupe)
                await session.rollback()
                return
            job_id = UUID(str(payload["job_id"]))
            await self._execute(session, job_id)
            await session.commit()

    # ── DB 轮询路径（重试/恢复的后备触发）─────────────────────────
    async def process_pending(self) -> int:
        handled = 0
        async with self.session_factory() as session:
            jobs = await self.engine.pending_jobs(session, self.batch)
            for job in jobs:
                await self._execute(session, job.id)
                handled += 1
            await session.commit()
        return handled

    # ── 单作业执行 ────────────────────────────────────────────────
    # prepare（R15）：单事务语义保持；generate（R21）：step 内部做页级 checkpoint commit
    # + 每页续租，claim/complete/fail 仍由 CAS token fencing 控制。
    async def _execute(self, session: AsyncSession, job_id: UUID) -> None:
        token = uuid4().hex
        claimed = await self.engine.claim(session, job_id, token)
        if claimed is None:
            return  # 已终态 / 已被他人 claim / claim 阶段取消或超时已就地终态化
        ctx = self.step_ctx.bind(session, str(job_id), token, trace_id=claimed.get("trace_id"))
        try:
            result = await run_step(claimed["job_type"], claimed["payload"], ctx)
            await self.engine.complete(session, job_id, token, {"result": result})
        except DeckCancelled:
            await self.engine.cancel_claimed(session, job_id, token)
        except StepFailure as exc:
            await self.engine.fail(session, job_id, token, exc.message, retriable=exc.retriable)
        except Exception as exc:  # noqa: BLE001
            await self.engine.fail(session, job_id, token, f"{type(exc).__name__}: {exc}", retriable=True)

    async def close(self) -> None:
        await self.broker.close()
