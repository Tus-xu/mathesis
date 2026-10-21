"""正式 Job 引擎：状态机 + 原子 claim/fencing + events + 同事务 outbox（R11.5-C P0）。

状态机：PENDING → CLAIMED → COMPLETED
                     ├─ CLAIMED → PENDING（超时/重试，attempt+1，指数退避）
                     │         → FAILED（不可恢复业务失败）
                     ├─ PENDING → CANCELLED（操作者显式取消）
                     └─ attempt >= max_attempts → DLQ

不变式：
- 任何状态修改经「expected_revision 校验 + FOR UPDATE 行锁」；worker 用 claim_token fencing——
  租约过期（claim_expires_at < now）后被 reconciler 回收重投，旧 token 立即失效（重复投递被拒）；
- 事件（job_events）与 outbox 消息与状态修改**同一事务**（调用方 session）；
- 入队去重：UNIQUE(owner_service, job_type, dedupe_key) —— 同键重复入队返回既有行；
- 幂等执行路线：JobDispatcher claim → fenced_run（入口校验 claim_token/状态）→ handler
  （handler 内部用 InboxDedupe 与业务键双重防重）→ complete（同事务）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import JOB_TERMINAL, ReliabilityJob, ReliabilityJobEvent, ReliabilityOutbox
from .trace import get_current

logger = logging.getLogger("reliability.jobs")

PENDING, CLAIMED, COMPLETED, FAILED, CANCELLED, DLQ = (
    "PENDING",
    "CLAIMED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "DLQ",
)
DEFAULT_CLAIM_LEASE_S = 120.0


class FenceError(RuntimeError):
    """claim_token 不匹配 / 状态不允许（重复投递或过期回收后的旧 worker 报此错）。"""


class RevisionConflict(RuntimeError):
    """expected_revision 与库行 revision 不一致（并发修改冲突）。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    """JsonB/JSON 列安全：把 UUID/其它非 JSON 原生类型递归转 str（防序列化失败）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dict,)):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def backoff_seconds(attempt: int) -> float:
    return min(60.0, 1.0 * (2 ** max(0, attempt - 1)))


class JobHandler(Protocol):
    async def __call__(self, *, session: AsyncSession, job: ReliabilityJob) -> dict[str, Any] | None: ...


class JobEngine:
    """作业事实的全部状态转换（调用方提供 session —— 事务边界归调用方，保证同事务原子）。"""

    # ── 创建 / 入队 ────────────────────────────────────────────────────────────

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        owner_service: str,
        job_type: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        max_attempts: int = 5,
        scheduled_at: datetime | None = None,
        timeout_s: int = 600,
        parent_job_id: uuid.UUID | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ReliabilityJob:
        """在调用方事务内创建作业（幂等：同 dedupe_key 已存在则返回既有行）。

        返回 (job, created: bool)。
        """
        ctx = get_current()
        deadline = None
        if timeout_s and timeout_s > 0:
            deadline = _now() + timedelta(seconds=timeout_s)
        row = ReliabilityJob(
            owner_service=owner_service,
            job_type=job_type,
            payload=payload,
            dedupe_key=dedupe_key,
            max_attempts=max_attempts,
            scheduled_at=scheduled_at,
            available_at=scheduled_at or _now(),
            timeout_s=max(60, timeout_s),
            deadline_at=deadline,
            parent_job_id=parent_job_id,
            trace_id=trace_id or (ctx.trace_id if ctx else None),
            correlation_id=correlation_id or (ctx.correlation_id if ctx else None),
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            if dedupe_key:
                existing = (
                    await session.execute(
                        select(ReliabilityJob).where(
                            ReliabilityJob.owner_service == owner_service,
                            ReliabilityJob.job_type == job_type,
                            ReliabilityJob.dedupe_key == dedupe_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing
            raise
        await JobEngine._append_event(session, row, "job.created", {"job_type": job_type})
        return row

    # ── claim / fencing ─────────────────────────────────────────────────────────

    @staticmethod
    async def claim(
        session: AsyncSession,
        *,
        owner_service: str,
        worker_name: str,
        limit: int = 10,
        claim_lease_s: float = DEFAULT_CLAIM_LEASE_S,
    ) -> list[ReliabilityJob]:
        """原子认领到期 PENDING：FOR UPDATE SKIP LOCKED，写入 claim_token 租约。

        并发 worker 之间互抢（skip_locked）；被取消的作业在 claim 时直接终态化。
        """
        stmt = (
            select(ReliabilityJob)
            .where(
                ReliabilityJob.owner_service == owner_service,
                ReliabilityJob.status == PENDING,
                ReliabilityJob.available_at <= func.now(),
                ReliabilityJob.cancel_requested.is_(False),
            )
            .order_by(ReliabilityJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        token = uuid.uuid4().hex
        for job in rows:
            job.status = CLAIMED
            job.claim_token = f"{worker_name}:{token}"
            job.claim_expires_at = _now() + timedelta(seconds=claim_lease_s)
            job.attempt += 1
            job.started_at = _now()
            job.revision += 1
            await JobEngine._append_event(
                session, job, "job.claimed",
                {"worker": worker_name, "attempt": job.attempt},
            )
        return rows

    @staticmethod
    async def renew(
        session: AsyncSession,
        job_id: uuid.UUID,
        claim_token: str,
        *,
        lease_s: float = DEFAULT_CLAIM_LEASE_S,
    ) -> bool:
        """心跳续租：仅持有有效 claim_token 的 worker 可延长租约。返回 False = 已被回收/终态。"""
        res = await session.execute(
            update(ReliabilityJob)
            .where(
                ReliabilityJob.id == job_id,
                ReliabilityJob.status == CLAIMED,
                ReliabilityJob.claim_token == claim_token,
                ReliabilityJob.cancel_requested.is_(False),
            )
            .values(claim_expires_at=_now() + timedelta(seconds=lease_s))
        )
        return (res.rowcount or 0) > 0

    # ── 终态 ────────────────────────────────────────────────────────────────────

    @staticmethod
    async def complete(
        session: AsyncSession,
        job: ReliabilityJob,
        claim_token: str,
        *,
        result: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> None:
        """成功终态（业务副作用已完成）：状态/result/revision 在同事务落库。

        fencing：claim_token 必须与库行一致；expected_revision 必须等于当前 revision。
        """
        live = await JobEngine._lock(session, job.id)
        if live is None or live.claim_token != claim_token:
            raise FenceError(f"job {job.id} 已被其他 worker 接管或已终态（claim 失窃）")
        JobEngine._assert_revision(live, expected_revision)
        live.status = COMPLETED
        live.result = _jsonable(result)
        live.claim_token = None
        live.claim_expires_at = None
        live.completed_at = _now()
        live.revision += 1
        await JobEngine._append_event(session, live, "job.completed", {"result": _jsonable(result)})

    @staticmethod
    async def fail(
        session: AsyncSession,
        job: ReliabilityJob,
        claim_token: str,
        *,
        error: str,
        expected_revision: int | None = None,
    ) -> None:
        """失败：attempt 未达上限 → 退回 PENDING + 指数退避（可重试）；达上限 → FAILED。

        每次失败都将错误写入 last_error 供对账（脱敏由调用方负责）。
        """
        live = await JobEngine._lock(session, job.id)
        if live is None or live.claim_token != claim_token:
            raise FenceError(f"job {job.id} claim 失窃（fail 被拒绝）")
        JobEngine._assert_revision(live, expected_revision)
        live.last_error = error[:2000]
        if live.attempt >= live.max_attempts:
            live.status = FAILED
            live.claim_token = None
            live.claim_expires_at = None
            live.completed_at = _now()
            live.revision += 1
            await JobEngine._append_event(session, live, "job.failed", {"error": error})
        else:
            live.status = PENDING
            live.claim_token = None
            live.claim_expires_at = None
            live.available_at = _now() + timedelta(seconds=backoff_seconds(live.attempt))
            live.revision += 1
            await JobEngine._append_event(session, live, "job.retry", {"attempt": live.attempt})

    @staticmethod
    async def cancel(
        session: AsyncSession,
        job_id: uuid.UUID,
        *,
        expected_revision: int | None = None,
    ) -> bool:
        """显式取消（操作者触发）：CLAIMED 中 → 标记 cancel_requested + 事件；
        PENDING → 直接 CANCELLED；终态不可取消。返回是否执行了取消。"""
        live = await JobEngine._lock(session, job_id)
        if live is None or live.status in JOB_TERMINAL:
            return False
        JobEngine._assert_revision(live, expected_revision)
        if live.status == CLAIMED:
            live.cancel_requested = True
            live.revision += 1
            await JobEngine._append_event(session, live, "job.cancel_requested", {})
            return True
        live.status = CANCELLED
        live.claim_token = None
        live.claim_expires_at = None
        live.completed_at = _now()
        live.revision += 1
        await JobEngine._append_event(session, live, "job.cancelled", {})
        return True

    # ── reconciler 用：回收过期租约 / 终态化取消 / DLQ ──────────────────────────

    @staticmethod
    async def requeue_stale(
        session: AsyncSession,
        *,
        owner_service: str,
        claim_lease_s: float = DEFAULT_CLAIM_LEASE_S,
        limit: int = 50,
    ) -> list[uuid.UUID]:
        """回收 CLAIMED 但租约过期的作业：退回 PENDING（attempt 已计）或 FAILED/DLQ。

        返回被回收的 job id 列表（供审计）。旧 claim_token 失效 → 重复投递被 fencing 拒绝。
        """
        threshold = _now() - timedelta(seconds=claim_lease_s)
        stmt = (
            select(ReliabilityJob)
            .where(
                ReliabilityJob.owner_service == owner_service,
                ReliabilityJob.status == CLAIMED,
                ReliabilityJob.claim_expires_at < threshold,
            )
            .order_by(ReliabilityJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        reclaimed: list[uuid.UUID] = []
        for job in rows:
            if job.cancel_requested:
                job.status = CANCELLED
                job.claim_token = None
                job.claim_expires_at = None
                job.completed_at = _now()
                job.revision += 1
                await JobEngine._append_event(session, job, "job.cancelled", {"via": "reconciler"})
                reclaimed.append(job.id)
                continue
            if job.attempt >= job.max_attempts:
                job.status = DLQ
                job.claim_token = None
                job.claim_expires_at = None
                job.completed_at = _now()
                job.revision += 1
                await JobEngine._append_event(
                    session, job, "job.dlq", {"reason": f"stale_claim x{job.attempt}"}
                )
            else:
                job.status = PENDING
                job.claim_token = None
                job.claim_expires_at = None
                job.available_at = _now()
                job.revision += 1
                await JobEngine._append_event(session, job, "job.reclaimed", {})
            reclaimed.append(job.id)
        return reclaimed

    @staticmethod
    async def deadline_raise(
        session: AsyncSession,
        *,
        owner_service: str,
        limit: int = 50,
    ) -> list[uuid.UUID]:
        """CLAIMED 但超过绝对 deadline_at 的作业 → 直接判 DLQ（防永远占用租约的死循环）。"""
        stmt = (
            select(ReliabilityJob)
            .where(
                ReliabilityJob.owner_service == owner_service,
                ReliabilityJob.status == CLAIMED,
                ReliabilityJob.deadline_at.is_not(None),
                ReliabilityJob.deadline_at < func.now(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        out: list[uuid.UUID] = []
        for job in rows:
            job.status = DLQ
            job.claim_token = None
            job.claim_expires_at = None
            job.completed_at = _now()
            job.revision += 1
            await JobEngine._append_event(session, job, "job.dlq", {"reason": "deadline_exceeded"})
            out.append(job.id)
        return out

    # ── 事件 + outbox 同事务 ────────────────────────────────────────────────────

    @staticmethod
    async def _append_event(
        session: AsyncSession, job: ReliabilityJob, event_type: str, payload: dict[str, Any]
    ) -> None:
        last = (
            await session.execute(
                select(func.max(ReliabilityJobEvent.seq)).where(
                    ReliabilityJobEvent.job_id == job.id
                )
            )
        ).scalar() or 0
        ctx = get_current()
        session.add(
            ReliabilityJobEvent(
                job_id=job.id,
                seq=int(last) + 1,
                event_type=event_type,
                payload=payload,
                trace_id=ctx.trace_id if ctx else (job.trace_id or None),
            )
        )
        await session.flush()

    @staticmethod
    async def enqueue_outbox(
        session: AsyncSession,
        job: ReliabilityJob,
        *,
        topic: str,
        event_type: str,
    ) -> ReliabilityOutbox:
        """作业里程碑 → outbox（同事务）：消费侧按 job_id 去重，重放/重复投递单副作用。"""
        from .outbox import OutboxService

        return await OutboxService.enqueue(
            session,
            topic=topic,
            payload={
                "job_id": str(job.id),
                "owner_service": job.owner_service,
                "job_type": job.job_type,
                "event_type": event_type,
                "status": job.status,
                "attempt": job.attempt,
                "revision": job.revision,
            },
            dedupe_key=str(job.id),  # 同一 job 各里程碑同键 → 消费侧天然去重
            trace_id=job.trace_id,
            correlation_id=job.correlation_id,
        )

    # ── helpers ─────────────────────────────────────────────────────────────────

    @staticmethod
    async def _lock(session: AsyncSession, job_id: uuid.UUID) -> ReliabilityJob | None:
        return (
            await session.execute(
                select(ReliabilityJob).where(ReliabilityJob.id == job_id).with_for_update()
            )
        ).scalar_one_or_none()

    @staticmethod
    def _assert_revision(job: ReliabilityJob, expected_revision: int | None) -> None:
        if expected_revision is not None and job.revision != expected_revision:
            raise RevisionConflict(
                f"job {job.id} revision={job.revision} != expected={expected_revision}"
            )


async def fenced_run(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    claim_token: str,
    handler: JobHandler,
) -> str:
    """worker 幂等执行入口：重复投递 / ACK 前崩溃 / 过期回收场景下的失败导向。

    返回结果码：'completed' | 'fenced_out' | 'failed_retry' | 'failed_terminal'。
    - 入口校验：仅 CLAIMED + claim_token 一致才执行；否则 fenced_out（重复投递跳过）；
    - handler 负责业务副作用（用 InboxDedupe 预占 job_id 去重键 → 崩溃重放不重复）；
    - handler 抛异常 → fail（可重试退避 / 终态 FAILED）；handler 完成 → complete；
    - 同一事务 commit：副作用 + inbox 键 + 作业状态 + 事件一并落库（崩溃 = 全回滚）。
    """
    live = (
        await session.execute(
            select(ReliabilityJob).where(ReliabilityJob.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if live is None:
        return "fenced_out"
    if live.status != CLAIMED or live.claim_token != claim_token:
        return "fenced_out"
    if live.cancel_requested:
        live.status = CANCELLED
        live.claim_token = None
        live.completed_at = _now()
        live.revision += 1
        await JobEngine._append_event(session, live, "job.cancelled", {"via": "fenced_run"})
        await session.commit()
        return "fenced_out"

    # handler 内部用自己的业务事务（可能有独立 DB 交互），此处仅管理作业终态
    try:
        result = await handler(session=session, job=live)
    except Exception as exc:  # noqa: BLE001 —— 业务失败 → 作业失败语义统一
        safe = repr(exc)[:2000]
        try:
            await JobEngine.fail(session, live, claim_token, error=safe)
            await session.commit()
        except FenceError:
            await session.rollback()
            return "fenced_out"
        return "failed_terminal" if live.attempt >= live.max_attempts else "failed_retry"
    else:
        await JobEngine.complete(session, live, claim_token, result=result)
        await session.commit()
        return "completed"


class JobDispatcher:
    """worker 内轮询调度：claim → fenced_run（handler 幂等）。背压 = 并发上限信号量。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        owner_service: str,
        handlers: dict[str, JobHandler],
        *,
        worker_name: str = "worker-default",
        claim_lease_s: float = DEFAULT_CLAIM_LEASE_S,
        poll_interval_s: float = 0.5,
        batch: int = 10,
        max_concurrency: int = 4,
    ) -> None:
        self._factory = session_factory
        self._owner_service = owner_service
        self._handlers = handlers
        self._worker_name = worker_name
        self._lease = claim_lease_s
        self._poll = poll_interval_s
        self._batch = batch
        self._sem = asyncio.Semaphore(max_concurrency)

    async def run_once(self) -> int:
        """一次轮询：claim 一批并执行。返回已认领数（含被 fencing 拒的重复投递）。"""
        async with self._factory() as session:
            jobs = await JobEngine.claim(
                session,
                owner_service=self._owner_service,
                worker_name=self._worker_name,
                limit=self._batch,
                claim_lease_s=self._lease,
            )
            await session.commit()
        for job in jobs:
            handler = self._handlers.get(job.job_type)
            if handler is None:
                async with self._factory() as session:
                    live = (
                        await session.execute(
                            select(ReliabilityJob).where(ReliabilityJob.id == job.id).with_for_update()
                        )
                    ).scalar_one_or_none()
                    await JobEngine.fail(session, live, job.claim_token or "",
                                         error=f"未知 job_type: {job.job_type}")
                    await session.commit()
                continue
            await self._run_one(job)
        return len(jobs)

    async def _run_one(self, job: ReliabilityJob) -> None:
        await self._run_with(job, self._handlers[job.job_type])

    async def _run_with(self, job: ReliabilityJob, handler: JobHandler) -> None:
        async def _do() -> None:
            async with self._factory() as session:
                await fenced_run(
                    session,
                    job_id=job.id,
                    claim_token=job.claim_token or "",
                    handler=handler,
                )

        async with self._sem:
            await _do()

    async def run(self) -> None:
        logger.info("job dispatcher started", extra={"owner": self._owner_service})
        while True:
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 —— 轮询循环必须存活
                logger.exception("job dispatcher run_once crashed; retry in poll interval")
            await asyncio.sleep(self._poll)


class JobReconciler:
    """对账循环：回收过期租约、DLQ 过期 deadline、输出诊断。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, owner_service: str):
        self._factory = session_factory
        self._owner_service = owner_service

    async def run_once(self) -> dict[str, int]:
        async with self._factory() as session:
            reclaimed = await JobEngine.requeue_stale(
                session, owner_service=self._owner_service
            )
            deadlined = await JobEngine.deadline_raise(session, owner_service=self._owner_service)
            await session.commit()
        return {"reclaimed": len(reclaimed), "deadlined": len(deadlined)}
