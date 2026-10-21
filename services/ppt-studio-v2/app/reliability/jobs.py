"""JobEngine：ppt_v2_jobs 专属确定性状态机（8200 自持）。

状态：PENDING → CLAIMED → COMPLETED
              ├─ transient 失败→(attempt 未达上限) PENDING(退避) → 重新 claim
              ├─ transient 失败→(attempt 达上限) → DLQ
              ├─ cancel_requested → CANCELLED
              └─ deadline 过期 → DLQ（reconciler 负责）

全部状态变更 = 携带 claim_token 的 CAS UPDATE（fencing：过期租约被收回后旧 token 失效）；
每次状态变更在同一事务内追加 durable event（job_id, seq）与（如需）outbox 触发行。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError, ErrorCode
from app.persistence.models import (
    JOB_TERMINAL,
    PptV2Job,
    PptV2JobEvent,
    PptV2Outbox,
)

JOB_TOPIC = "ppt.job"  # → stream_of("ppt_studio_v2", ...) = strm:ps8200:ppt.job


def job_view(job: PptV2Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": str(job.id),
        "owner_service": job.owner_service,
        "job_type": job.job_type,
        "status": job.status,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "revision": job.revision,
        "payload": job.payload,
        "cancel_requested": job.cancel_requested,
        "dedupe_key": job.dedupe_key,
        "last_error": job.last_error,
        "result": job.result,
        "trace_id": job.trace_id,
        "correlation_id": job.correlation_id,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
        "available_at": _iso(job.available_at),
        "deadline_at": _iso(job.deadline_at),
    }


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite 存 naive datetime，比较前统一按 UTC 归一化（PG 存 aware 不受影响）。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _jid(job_id: UUID | str) -> UUID:
    return job_id if isinstance(job_id, UUID) else UUID(str(job_id))


class JobEngine:
    def __init__(self, *, lease_seconds: int = 60, backoff_base_s: float = 1.0, backoff_max_s: float = 30.0):
        self.lease_seconds = lease_seconds
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s

    async def _append_event(
        self, session: AsyncSession, job_id: UUID, event_type: str, payload: dict[str, Any], trace_id: str | None
    ) -> None:
        # 先在父行上持锁（调用方已先做 CAS UPDATE 该行），再取 seq → SQLite/PG 均串行安全
        next_seq = (
            await session.execute(
                select(PptV2JobEvent.seq).where(PptV2JobEvent.job_id == job_id).order_by(PptV2JobEvent.seq.desc()).limit(1)
            )
        ).scalar()
        session.add(
            PptV2JobEvent(
                job_id=job_id,
                seq=(next_seq or 0) + 1,
                event_type=event_type,
                payload=payload,
                trace_id=trace_id,
            )
        )

    async def append_event(
        self, session: AsyncSession, job_id: UUID | str, event_type: str, payload: dict[str, Any], trace_id: str | None = None
    ) -> None:
        """公开事件追加（step 页级 checkpoint / 装配进度用）。"""
        await self._append_event(session, _jid(job_id), event_type, payload, trace_id)

    async def cancel_claimed(self, session: AsyncSession, job_id: UUID | str, token: str) -> dict[str, Any] | None:
        """运行中取消：CLAIMED + token CAS → CANCELLED（fencing：过期租约/token 失效 → None）。"""
        job_id = _jid(job_id)
        job = await session.get(PptV2Job, job_id)
        if job is None or job.status != "CLAIMED" or job.claim_token != token:
            return None
        return await self._mark_cancelled(session, job, via="worker-midrun")

    async def create(
        self,
        session: AsyncSession,
        *,
        job_type: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
        timeout_s: int = 300,
        dedupe_key: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        job = PptV2Job(
            owner_service="ppt_studio_v2",
            job_type=job_type,
            payload=payload,
            status="PENDING",
            attempt=0,
            max_attempts=max_attempts,
            revision=1,
            available_at=now,
            deadline_at=now + timedelta(seconds=timeout_s),
            timeout_s=timeout_s,
            dedupe_key=dedupe_key,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        session.add(job)
        try:
            await session.flush()
        except Exception as exc:  # noqa: BLE001  UNIQUE(job_type, dedupe_key)
            if dedupe_key and _is_dup(exc):
                raise ApiError(ErrorCode.CONFLICT, f"重复作业（dedupe_key={dedupe_key}）", error_key="duplicate_job") from exc
            raise
        # 同事务入 outbox（事务性发件箱：commit 即触发流）
        session.add(
            PptV2Outbox(
                topic=JOB_TOPIC,
                dedupe_key=f"job:{job.id}",
                payload={"job_id": str(job.id), "job_type": job_type, "revision_trigger": 1},
                status="PENDING",
                available_at=now,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
        )
        await self._append_event(session, job.id, "CREATED", {"job_type": job_type}, trace_id)
        await session.flush()
        return job_view(job)  # type: ignore[return-value]

    async def list_events(self, session: AsyncSession, job_id: UUID | str, limit: int = 200) -> list[dict[str, Any]]:
        job_id = _jid(job_id)
        rows = (
            (
                await session.execute(
                    select(PptV2JobEvent)
                    .where(PptV2JobEvent.job_id == job_id)
                    .order_by(PptV2JobEvent.seq.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "seq": r.seq,
                "event_type": r.event_type,
                "payload": r.payload,
                "trace_id": r.trace_id,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ]

    async def get(self, session: AsyncSession, job_id: UUID | str) -> dict[str, Any] | None:
        job = await session.get(PptV2Job, _jid(job_id))
        return job_view(job)

    async def list_dlq(self, session: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
        rows = (await session.execute(select(PptV2Job).where(PptV2Job.status == "DLQ").order_by(PptV2Job.updated_at.desc()).limit(limit))).scalars()
        return [job_view(j) for j in rows if job_view(j)]  # type: ignore[list-item]

    async def request_cancel(self, session: AsyncSession, job_id: UUID | str) -> dict[str, Any] | None:
        job = await session.get(PptV2Job, _jid(job_id))
        if job is None:
            return None
        if job.status in JOB_TERMINAL:
            return job_view(job)
        job.cancel_requested = True
        job.revision += 1
        await self._append_event(session, job.id, "CANCEL_REQUESTED", {}, job.trace_id)
        await session.flush()
        return job_view(job)

    async def claim(self, session: AsyncSession, job_id: UUID | str, token: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        """CAS：仅 PENDING → CLAIMED；返回 job 视图（含更新后的 attempt）。"""
        job_id = _jid(job_id)
        now = _aware(now or _now())  # type: ignore[arg-type]
        job = await session.get(PptV2Job, job_id)
        if job is None or job.status != "PENDING":
            return None
        if job.cancel_requested:
            return await self._mark_cancelled(session, job)
        deadline = _aware(job.deadline_at)
        if deadline is not None and deadline <= now:
            return await self._mark_dlq(session, job, "deadline_exceeded_at_claim")
        job.status = "CLAIMED"
        job.attempt += 1
        job.revision += 1
        job.claim_token = token
        job.claim_expires_at = now + timedelta(seconds=self.lease_seconds)
        if job.started_at is None:
            job.started_at = now
        await self._append_event(session, job.id, "CLAIMED", {"attempt": job.attempt}, job.trace_id)
        await session.flush()
        return job_view(job)

    async def renew(self, session: AsyncSession, job_id: UUID | str, token: str, *, now: datetime | None = None) -> bool:
        job_id = _jid(job_id)
        now = _aware(now or _now())  # type: ignore[arg-type]
        job = await session.get(PptV2Job, job_id)
        if job is None or job.status != "CLAIMED" or job.claim_token != token:
            return False
        job.claim_expires_at = now + timedelta(seconds=self.lease_seconds)
        job.revision += 1
        await session.flush()
        return True

    async def complete(self, session: AsyncSession, job_id: UUID | str, token: str, result: dict[str, Any], *, now: datetime | None = None) -> bool:
        job_id = _jid(job_id)
        now = _aware(now or _now())  # type: ignore[arg-type]
        job = await session.get(PptV2Job, job_id)
        if job is None or job.status != "CLAIMED" or job.claim_token != token:
            return False
        job.status = "COMPLETED"
        job.revision += 1
        job.claim_token = None
        job.claim_expires_at = None
        job.result = result
        job.completed_at = now
        await self._append_event(session, job.id, "COMPLETED", {"attempt": job.attempt}, job.trace_id)
        await session.flush()
        return True

    async def fail(
        self,
        session: AsyncSession,
        job_id: UUID | str,
        token: str,
        error: str,
        *,
        retriable: bool,
        now: datetime | None = None,
    ) -> str | None:
        """CLAIMED → FAILED(不可重试) / DLQ(重试达上限) / PENDING(退避等重跑)。返回新状态或 None。"""
        job_id = _jid(job_id)
        now = _aware(now or _now())  # type: ignore[arg-type]
        job = await session.get(PptV2Job, job_id)
        if job is None or job.status != "CLAIMED" or job.claim_token != token:
            return None
        job.last_error = error
        if not retriable:
            job.status = "FAILED"
            job.claim_token = None
            job.claim_expires_at = None
            job.completed_at = now
            await self._append_event(session, job.id, "FAILED", {"error": error, "attempt": job.attempt}, job.trace_id)
        elif job.attempt >= job.max_attempts:
            await self._mark_dlq(session, job, f"max_attempts({job.max_attempts}) exceeded: {error}")
        else:
            backoff = min(self.backoff_base_s * (2 ** (job.attempt - 1)), self.backoff_max_s)
            job.status = "PENDING"
            job.claim_token = None
            job.claim_expires_at = None
            job.available_at = now + timedelta(seconds=backoff)
            await self._append_event(session, job.id, "RETRY_SCHEDULED", {"error": error, "attempt": job.attempt, "backoff_s": backoff}, job.trace_id)
        job.revision += 1
        await session.flush()
        return job.status

    async def reclaim_expired(self, session: AsyncSession, *, now: datetime | None = None) -> list[UUID]:
        """reconciler：租约超期 → 重试或 DLQ（fencing：旧 token 失效）。"""
        now = _aware(now or _now())  # type: ignore[arg-type]
        rows = (
            await session.execute(select(PptV2Job).where(PptV2Job.status == "CLAIMED"))
        ).scalars().all()
        touched: list[UUID] = []
        for job in rows:
            expires = _aware(job.claim_expires_at)
            if expires is None or expires > now:
                continue
            if job.cancel_requested:
                await self._mark_cancelled(session, job, via="lease_expiry")
            elif _aware(job.deadline_at) is not None and _aware(job.deadline_at) <= now:  # type: ignore[operator]
                await self._mark_dlq(session, job, "deadline_exceeded")
            elif job.attempt >= job.max_attempts:
                await self._mark_dlq(session, job, f"max_attempts({job.max_attempts}) at reclaim")
            else:
                backoff = min(self.backoff_base_s * (2 ** (job.attempt - 1)), self.backoff_max_s)
                job.status = "PENDING"
                job.claim_token = None
                job.claim_expires_at = None
                job.available_at = now + timedelta(seconds=backoff)
                job.revision += 1
                await self._append_event(session, job.id, "LEASE_RECLAIMED", {"attempt": job.attempt, "backoff_s": backoff}, job.trace_id)
            touched.append(job.id)
        # 未到租约的 CLAIMED 行不动
        if touched:
            await session.flush()
        return touched

    async def dlq_expired_deadlines(self, session: AsyncSession, *, now: datetime | None = None) -> list[UUID]:
        """reconciler：任何 PENDING（排队超时）与剩余 CLAIMED 超过 deadline → DLQ。"""
        now = _aware(now or _now())  # type: ignore[arg-type]
        rows = (await session.execute(select(PptV2Job).where(PptV2Job.status.in_(("PENDING", "CLAIMED"))))).scalars().all()
        touched: list[UUID] = []
        for job in rows:
            deadline = _aware(job.deadline_at)
            if deadline is None or deadline > now:
                continue
            await self._mark_dlq(session, job, "deadline_exceeded")
            touched.append(job.id)
        if touched:
            await session.flush()
        return touched

    async def pending_jobs(self, session: AsyncSession, limit: int, *, now: datetime | None = None) -> list[PptV2Job]:
        """worker db-poll：可触发的 PENDING（available_at 已到；取消/超期由 claim 阶段裁决）。"""
        now = _aware(now or _now())  # type: ignore[arg-type]
        rows = (
            await session.execute(
                select(PptV2Job).where(PptV2Job.status == "PENDING").order_by(PptV2Job.available_at).limit(limit * 4)
            )
        ).scalars().all()
        ready: list[PptV2Job] = []
        for job in rows:
            if len(ready) >= limit:
                break
            avail = _aware(job.available_at)
            if avail is None or avail <= now:
                ready.append(job)
        return ready

    async def _mark_cancelled(self, session: AsyncSession, job: PptV2Job, via: str = "worker") -> dict[str, Any]:
        job.status = "CANCELLED"
        job.revision += 1
        job.claim_token = None
        job.claim_expires_at = None
        job.completed_at = _now()
        await self._append_event(session, job.id, "CANCELLED", {"via": via}, job.trace_id)
        await session.flush()
        return job_view(job)  # type: ignore[return-value]

    async def _mark_dlq(self, session: AsyncSession, job: PptV2Job, reason: str) -> dict[str, Any]:
        job.status = "DLQ"
        job.revision += 1
        job.claim_token = None
        job.claim_expires_at = None
        job.last_error = f"DLQ: {reason}"
        await self._append_event(session, job.id, "DLQ", {"reason": reason, "attempt": job.attempt}, job.trace_id)
        await session.flush()
        return job_view(job)  # type: ignore[return-value]


def _is_dup(exc: Exception) -> bool:
    """方言无关的 UNIQUE 冲突识别（PG:23505 / SQLite:IntegrityError 约束消息）。"""
    import sqlalchemy.exc

    if isinstance(exc, sqlalchemy.exc.IntegrityError):
        msg = str(exc.orig).lower()
        return "23505" in msg or "unique" in msg or "constraint" in msg
    return False
