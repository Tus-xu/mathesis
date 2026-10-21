"""作业运行时（RV03）—— 状态、租约、围栏、重试、DLQ、取消、背压。

**事实权威在 PostgreSQL**：`job_run` 持有 status / attempts / lease / fencing_token /
cancel_requested_at。Valkey 只是把"有活干了"更快地送达 worker；即使 Valkey 全丢，
`claim_due_jobs()` 也能直接从数据库捞到该跑的作业。

五条必须同时成立的性质：

1. **租约（lease）**：`lease_owner` + `lease_expires_at`。worker 死了，租约到期，
   别人可以接管（crashed worker 不会永久占住一个作业）。
2. **单调围栏（fencing）**：每次领取 `fencing_token += 1`。上报结果必须回传
   **当前** token。过期 worker 的迟到结果 → `StaleFencingToken`，**被拒**。
   这是"旧 worker 不得覆盖新尝试"的唯一保证，比"检查 lease_owner 字符串"
   更强（字符串可以被同名重启覆盖）。
3. **心跳 ≠ 进度**：`heartbeat` 只更新 `last_heartbeat_at`（证明存活），
   `progress` 只更新 `stage`/`progress`。合并它们会让"没进度"被误判为"已死"。
4. **重试分类**：schema / 权限 / 非法输入 ⇒ **立即失败，不重试**（重试只是浪费）。
5. **对外状态规范**：`CLAIMED` 是**内部**状态，永不作为用户可见成功态出现。
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import (
    JOB_DEFAULT_MAX_ATTEMPTS,
    job_backoff_base_seconds,
    job_backoff_max_seconds,
    job_lease_seconds,
    job_total_timeout_seconds,
    max_jobs_per_project,
    max_jobs_per_provider,
    max_jobs_per_user,
    max_oldest_age_seconds,
    max_queue_depth,
    retry_after_seconds,
)
from ..db.models import (
    DomainEventRow,
    JOB_STATUS_CLAIMED,
    JOB_STATUSES,
    JobDlqRow,
    JobInboxRow,
    JobRow,
    OutboxEventRow,
)
from ..domain.errors import DomainError
from ..domain.events import DomainEvent
from ..observability.redaction import redact_mapping

#: 在队（等待被跑）的状态集合 —— 背压与队列深度都基于它。
IN_FLIGHT_STATUSES = ("QUEUED", "RETRYING", JOB_STATUS_CLAIMED, "RUNNING")

#: **永久**错误：重试不会变好，立即失败。
PERMANENT_ERROR_CODES = frozenset(
    {
        "SCHEMA_INVALID",
        "PERMISSION_DENIED",
        "INVALID_INPUT",
        "UNAUTHORIZED",
        "UNSUPPORTED_JOB_TYPE",
        "EXECUTOR_NOT_CONFIGURED",
        "POLICY_REJECTED",
        # RV14 · export 域永久错误（重试只会重复同一失败）
        "EXPORT_NOT_FOUND",
        "EXPORT_ASSET_MISSING",
        "EXPORT_FORBIDDEN_SEQUENCE",
        "EXPORT_DOC_TOO_LARGE",
        "EXPORT_EMPTY_DOCUMENT",
        "EXPORT_OUTPUT_LIMIT",
        "EXPORT_COMPILE_FAILED",
        "EXPORT_SERIALIZATION_FAILED",
    }
)

#: **可重试**错误：临时性，退避后重试有意义。
RETRYABLE_ERROR_CODES = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_ERROR",
        "NETWORK_ERROR",
        "LEASE_EXPIRED",
        "STAGE_TIMEOUT",
        "TRANSIENT_DB_ERROR",
        "QUEUE_UNAVAILABLE",
        # RV14 · 编译器瞬时过载（容器被 OOM/负载挤掉）
        "EXPORT_COMPILE_TIMEOUT",
    }
)


class JobError(DomainError):
    code = "JOB_ERROR"
    http_status = 400


class StaleFencingToken(JobError):
    """过期的 worker 试图上报 —— **必须**被拒，否则旧结果会覆盖新尝试。"""

    code = "STALE_FENCING_TOKEN"
    http_status = 409


class JobNotFound(JobError):
    code = "JOB_NOT_FOUND"
    http_status = 404


class JobNotClaimable(JobError):
    code = "JOB_NOT_CLAIMABLE"
    http_status = 409


class BackpressureError(DomainError):
    """队列/并发上限已满 —— 返回 429/503 + `Retry-After`，而不是无限排队。"""

    code = "BACKPRESSURE"
    http_status = 429

    def __init__(self, reason: str, *, retry_after: int | None = None, scope: str = "") -> None:
        super().__init__(
            f"背压拒绝：{reason}",
            reason=reason,
            retry_after=retry_after or retry_after_seconds(),
            scope=scope,
        )
        self.reason = reason
        self.retry_after = retry_after or retry_after_seconds()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def classify_error(code: str) -> bool:
    """返回 `retryable`。

    **默认不可重试**：一个未知错误码重复执行 3 次，通常只是把一个 bug
    变成三次副作用。只有显式登记为临时的错误码才重试。
    """
    if code in PERMANENT_ERROR_CODES:
        return False
    if code in RETRYABLE_ERROR_CODES:
        return True
    return False


def backoff_seconds(attempt: int) -> float:
    """指数退避 + **全抖动**（full jitter）。

    全抖动（而不是等量抖动）避免"同一批失败的作业在退避后同时涌回"的
    惊群效应 —— 这正是重试风暴的典型成因。
    """
    base = job_backoff_base_seconds()
    cap = job_backoff_max_seconds()
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0.0, ceiling)


def public_status(status: str) -> str:
    """内部状态 → **对外规范状态**。

    `CLAIMED` 只表示"已被 worker 领走"，不代表任何进展。对外一律折叠成
    `QUEUED`：让用户以为"已被领走"是一种进展，等于用内部状态撒谎。
    """
    if status == JOB_STATUS_CLAIMED:
        return "QUEUED"
    if status in JOB_STATUSES:
        return status
    return "QUEUED"


def public_view(job: JobRow) -> dict[str, Any]:
    """作业的对外投影。PARTIAL **必须**说清哪些 stage 成/败。"""
    out: dict[str, Any] = {
        "id": str(job.id),
        "session_id": str(job.session_id),
        "job_type": job.job_type,
        "status": public_status(job.status),
        "stage": job.stage or "",
        "progress": int(job.progress or 0),
        "attempts": int(job.attempts or 0),
        "max_attempts": int(job.max_attempts or 0),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "cancel_requested": job.cancel_requested_at is not None,
        "trace_id": job.trace_id,
    }
    if job.status == "PARTIAL":
        detail = job.partial_detail or {}
        out["partial"] = {
            "succeeded_stages": list(detail.get("succeeded_stages", []) or []),
            "failed_stages": list(detail.get("failed_stages", []) or []),
            "is_success": False,  # 显式：PARTIAL **不是**成功
        }
    if job.status == "FAILED":
        out["error"] = {"code": job.error_code, "message": job.error_message}
        out["retryable"] = bool(job.retryable)
    if job.status in ("SUCCEEDED", "PARTIAL"):
        out["result"] = job.result or {}
    return out


# --------------------------------------------------------------------------
# 事件
# --------------------------------------------------------------------------


def emit_job_event(
    db: Session,
    *,
    job: JobRow,
    event_type: str,
    payload: dict[str, Any],
    trace_id: str,
) -> DomainEventRow:
    """typed job 事件 ⇒ domain event + outbox（同事务）。

    前端因此**不必**按定时器猜进度：`job.stage_changed` / `job.progress` /
    `job.partial` 都是显式事件。
    """
    body = {"job_id": str(job.id), "status": public_status(job.status), **payload}
    safe = redact_mapping(body)
    ev = DomainEvent.create(
        event_type=event_type,
        session_id=job.session_id,
        revision=0,  # 作业不属于会话 revision 链
        payload=safe,
        trace_id=trace_id or job.trace_id or ("0" * 32),
        actor_id=job.user_id,
        occurred_at=_now(),
    )
    db.add(
        DomainEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            revision=ev.revision,
            occurred_at=ev.occurred_at,
            trace_id=ev.trace_id,
            payload=safe,
            actor_id=ev.actor_id,
            aggregate_type="job",
        )
    )
    db.add(
        OutboxEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            payload=safe,
            trace_id=ev.trace_id,
            status="PENDING",
            attempts=0,
            published_at=None,
            created_at=ev.occurred_at,
        )
    )
    return ev  # type: ignore[return-value]


# --------------------------------------------------------------------------
# 背压
# --------------------------------------------------------------------------


def check_backpressure(
    db: Session,
    *,
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    provider: str = "",
) -> None:
    """并发/深度/队龄三道闸。超限即抛 `BackpressureError`（429 + Retry-After）。

    说明（诚实边界）：这是**准入**控制，不是 provider 侧的令牌桶限速。
    本平台此前完全没有 provider 限流；这里给出可配置的上限与 429 语义。
    """
    depth = int(
        db.scalar(
            select(func.count()).select_from(JobRow).where(JobRow.status.in_(IN_FLIGHT_STATUSES))
        )
        or 0
    )
    if depth >= max_queue_depth():
        raise BackpressureError(
            f"队列深度 {depth} 已达上限 {max_queue_depth()}", scope="queue"
        )

    oldest = db.scalar(
        select(func.min(JobRow.created_at)).where(JobRow.status.in_(("QUEUED", "RETRYING")))
    )
    oldest_aware = _aware(oldest)
    if oldest_aware is not None:
        age = int((_now() - oldest_aware).total_seconds())
        if age > max_oldest_age_seconds():
            raise BackpressureError(
                f"队首作业已等待 {age}s，超过上限 {max_oldest_age_seconds()}s", scope="queue"
            )

    if user_id is not None:
        n = int(
            db.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(JobRow.user_id == user_id, JobRow.status.in_(IN_FLIGHT_STATUSES))
            )
            or 0
        )
        if n >= max_jobs_per_user():
            raise BackpressureError(
                f"该用户并发作业 {n} 已达上限 {max_jobs_per_user()}", scope="user"
            )

    if project_id is not None:
        n = int(
            db.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(JobRow.project_id == project_id, JobRow.status.in_(IN_FLIGHT_STATUSES))
            )
            or 0
        )
        if n >= max_jobs_per_project():
            raise BackpressureError(
                f"该项目并发作业 {n} 已达上限 {max_jobs_per_project()}", scope="project"
            )

    if provider:
        n = int(
            db.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(JobRow.provider == provider, JobRow.status.in_(IN_FLIGHT_STATUSES))
            )
            or 0
        )
        if n >= max_jobs_per_provider():
            raise BackpressureError(
                f"provider {provider} 并发作业 {n} 已达上限 {max_jobs_per_provider()}",
                scope="provider",
            )


# --------------------------------------------------------------------------
# 入队
# --------------------------------------------------------------------------


def enqueue(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    job_type: str,
    input_payload: dict[str, Any],
    input_ref: str = "",
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    provider: str = "",
    max_attempts: int | None = None,
    client_request_id: str = "",
    trace_id: str = "",
    check_limits: bool = True,
) -> JobRow:
    """创建 QUEUED 作业 + `job.queued` 事件（同事务）。"""
    if check_limits:
        check_backpressure(db, user_id=user_id, project_id=project_id, provider=provider)

    job = JobRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        project_id=project_id,
        provider=provider,
        user_id=user_id,
        job_type=job_type,
        status="QUEUED",
        policy_version="rv03.job.v1",
        input_ref=input_ref,
        input_payload=redact_mapping(input_payload or {}),
        result={},
        partial_detail={},
        stage="",
        progress=0,
        attempts=0,
        max_attempts=max_attempts or JOB_DEFAULT_MAX_ATTEMPTS,
        lease_owner="",
        lease_expires_at=None,
        fencing_token=0,
        cancel_requested_at=None,
        error_code="",
        error_message="",
        retryable=True,
        client_request_id=client_request_id,
        trace_id=trace_id,
        created_at=_now(),
        started_at=None,
        finished_at=None,
    )
    db.add(job)
    db.flush()
    emit_job_event(db, job=job, event_type="job.queued", payload={"job_type": job_type}, trace_id=trace_id)
    return job


# --------------------------------------------------------------------------
# 领取（租约 + 围栏）
# --------------------------------------------------------------------------


def _lease_expired(job: JobRow) -> bool:
    """租约是否已过期。

    `lease_expires_at IS NULL` 的语义取决于状态：
    - 作业从未被领取（QUEUED / RETRYING）⇒ NULL 表示"可以领"；
    - 作业**已被领取**（CLAIMED / RUNNING）但租约为 NULL：这是"正在被别人持有"，
      **不得**视为可抢占。
      原因很实在：`claim_job` 先置 CLAIMED 再写租约，任何一个读到中间态的并发领取
      者都会看到一个"CLAIMED 且租约 NULL"的行。如果把它当成空闲，四个并发 worker
      会全部领走同一个作业（实测发生过），fencing token 也被推高四次。
      崩溃恢复不依赖这条路径：真正的崩溃会留下**有值且已过期**的租约。
    """
    if job.status in (JOB_STATUS_CLAIMED, "RUNNING") and job.lease_expires_at is None:
        return False
    expires = _aware(job.lease_expires_at)
    return expires is None or expires <= _now()


def claim_job(db: Session, *, job_id: uuid.UUID, worker: str) -> JobRow:
    """领取一个指定作业：置 CLAIMED、续租、**递增围栏 token**。

    实现在**一条条件 UPDATE**里完成状态迁移，而不是"先 SELECT 判断再改"。
    理由是并发正确性：`db.get()` 不带行锁，两个 worker 可以同时读到 QUEUED
    然后各自认为自己可以领（实测四个并发线程全部领走了同一个作业）。
    条件 UPDATE 把"检查"和"迁移"合并成一个原子动作，只有第一个提交者能成功。

    **接管（takeover）**是被允许的，且必须允许：worker 崩溃后任务不能永久卡死。
    条件是"租约已过期"——此时旧 worker 的任何后续上报都会因为围栏 token
    不再匹配而被拒（见 `_require_fence`）。这是把"抢占"做成安全的唯一方式：
    抢占本身不危险，**没有围栏的抢占**才危险。
    """
    import uuid as _uuid_mod

    from sqlalchemy import update

    now = _now()
    new_expiry = now + timedelta(seconds=job_lease_seconds())

    stmt = (
        update(JobRow)
        .where(
            JobRow.id == job_id,
            JobRow.status.in_(("QUEUED", "RETRYING", JOB_STATUS_CLAIMED, "RUNNING")),
            # 可领 = 排队中，或（已被领走 **且租约已过期**）
            (JobRow.status.in_(("QUEUED", "RETRYING")))
            | (
                JobRow.status.in_((JOB_STATUS_CLAIMED, "RUNNING"))
                & JobRow.lease_expires_at.is_not(None)
                & (JobRow.lease_expires_at <= now)
            ),
        )
        .values(
            status=JOB_STATUS_CLAIMED,
            lease_owner=worker,
            lease_expires_at=new_expiry,
            fencing_token=JobRow.fencing_token + 1,
            attempts=JobRow.attempts + 1,
            last_heartbeat_at=now,
        )
        .returning(JobRow.id)
    )
    claimed_id = db.execute(stmt).scalar()
    if claimed_id is None:
        # 没能领到：区分"不存在"与"不可领"，让调用方能正确处理
        existing = db.get(JobRow, job_id)
        if existing is None:
            raise JobNotFound(f"作业不存在: {job_id}")
        if existing.cancel_requested_at is not None and existing.status in ("QUEUED", "RETRYING"):
            return cancel_job(db, job_id=job_id, reason="cancel_requested")
        raise JobNotClaimable(f"作业当前状态 {existing.status} 不可领取（租约仍有效或已终态）")

    db.flush()
    # 迁移成功后重新读取，拿到新的 token/attempts（`expire_on_commit=False`，
    # 因此必须显式 refresh 才不会被 identity map 里的旧值骗到）
    job = db.get(JobRow, job_id)
    db.refresh(job)
    if job.started_at is None:
        job.started_at = now
        db.flush()
    emit_job_event(
        db,
        job=job,
        event_type="job.claimed",
        payload={"attempt": job.attempts, "worker": worker, "fencing_token": job.fencing_token},
        trace_id=job.trace_id,
    )
    return job


def _looks_like_uuid(text: str) -> bool:
    try:
        import uuid as _u

        _u.UUID(str(text))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def claim_due_jobs(
    db: Session,
    *,
    worker: str,
    limit: int = 10,
    job_types: tuple[str, ...] | list[str] | None = None,
    exclude_job_types: tuple[str, ...] | list[str] | None = None,
) -> list[JobRow]:
    """从**数据库**捞可取作业（Valkey 全丢也能工作）。

    取两类：
    1. `QUEUED` / `RETRYING` 且 `next_attempt_at` 已到（或为空）；
    2. `CLAIMED` / `RUNNING` 且**租约已过期**（原 worker 已死）—— 接管。

    RV14：`job_types` / `exclude_job_types` 支持独立 compile pool ——
    主 pool 用 `exclude_job_types=COMPILE_JOB_TYPES` 绝不领编译作业；
    compile pool 用 `job_types=COMPILE_JOB_TYPES` 只领编译作业。

    真正决定能否领取的是 `claim_job()` 里的条件 UPDATE；这里只是**候选筛选**，
    因此即便筛选宽一点也不会导致重复领取。
    """
    now = _now()
    stmt = select(JobRow).where(
        JobRow.status.in_(("QUEUED", "RETRYING", JOB_STATUS_CLAIMED, "RUNNING")),
        (JobRow.next_attempt_at.is_(None)) | (JobRow.next_attempt_at <= now),
        (JobRow.lease_expires_at.is_(None)) | (JobRow.lease_expires_at <= now),
    )
    if job_types:
        stmt = stmt.where(JobRow.job_type.in_(tuple(job_types)))
    if exclude_job_types:
        stmt = stmt.where(JobRow.job_type.not_in(tuple(exclude_job_types)))
    jobs = (
        db.execute(stmt.order_by(JobRow.created_at).limit(limit).with_for_update(skip_locked=True))
        .scalars()
        .all()
    )
    claimed: list[JobRow] = []
    for job in jobs:
        try:
            claimed.append(claim_job(db, job_id=job.id, worker=worker))
        except JobNotClaimable:
            continue
    return claimed


def heartbeat(db: Session, *, job_id: uuid.UUID, fencing_token: int, worker: str) -> JobRow:
    """续租（**与 progress 分开**）。围栏 token 不匹配 ⇒ 拒绝。"""
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    _require_fence(job, fencing_token)
    now = _now()
    job.last_heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=job_lease_seconds())
    job.lease_owner = worker
    db.flush()
    emit_job_event(db, job=job, event_type="job.heartbeat", payload={"worker": worker}, trace_id=job.trace_id)
    return job


def report_progress(
    db: Session,
    *,
    job_id: uuid.UUID,
    fencing_token: int,
    stage: str = "",
    progress: int | None = None,
) -> JobRow:
    """报告**进度**（不是心跳）。"""
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    _require_fence(job, fencing_token)
    if job.status == JOB_STATUS_CLAIMED:
        job.status = "RUNNING"
        emit_job_event(db, job=job, event_type="job.started", payload={}, trace_id=job.trace_id)
    stage_changed = bool(stage) and stage != job.stage
    if stage:
        job.stage = stage
    if progress is not None:
        job.progress = max(0, min(100, int(progress)))
    db.flush()
    if stage_changed:
        emit_job_event(
            db, job=job, event_type="job.stage_changed", payload={"stage": job.stage}, trace_id=job.trace_id
        )
    emit_job_event(
        db,
        job=job,
        event_type="job.progress",
        payload={"stage": job.stage, "progress": job.progress},
        trace_id=job.trace_id,
    )
    return job


def _require_fence(job: JobRow, fencing_token: int) -> None:
    if int(job.fencing_token or 0) != int(fencing_token):
        raise StaleFencingToken(
            f"围栏 token 不匹配：收到 {fencing_token}，当前 {job.fencing_token}"
            "（该 worker 的租约已被接管，其结果必须丢弃）",
            received=int(fencing_token),
            current=int(job.fencing_token),
            job_id=str(job.id),
        )


# --------------------------------------------------------------------------
# 结束：成功 / 部分成功 / 失败 / 重试 / 取消
# --------------------------------------------------------------------------


def _finish(job: JobRow) -> None:
    job.finished_at = _now()
    job.lease_owner = ""
    job.lease_expires_at = None


def complete_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    fencing_token: int,
    result: dict[str, Any] | None = None,
    trace_id: str = "",
) -> JobRow:
    """成功结束。围栏过期 ⇒ `StaleFencingToken`（**旧结果不得覆盖新尝试**）。"""
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    _require_fence(job, fencing_token)

    if job.cancel_requested_at is not None:
        # 取消与完成竞态：取消优先。一个已被要求取消的作业不得报告 SUCCEEDED。
        return cancel_job(db, job_id=job_id, fencing_token=fencing_token, reason="cancel_requested")

    job.status = "SUCCEEDED"
    job.result = redact_mapping(result or {})
    job.progress = 100
    job.error_code = ""
    job.error_message = ""
    _finish(job)
    db.flush()
    emit_job_event(db, job=job, event_type="job.succeeded", payload={"result": job.result}, trace_id=trace_id or job.trace_id)
    return job


def complete_partial(
    db: Session,
    *,
    job_id: uuid.UUID,
    fencing_token: int,
    succeeded_stages: list[str],
    failed_stages: list[str],
    result: dict[str, Any] | None = None,
    error_code: str = "STAGE_FAILED",
    trace_id: str = "",
) -> JobRow:
    """部分成功。**必须**说清成/败 stage —— 绝不渲染成绿色成功。"""
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    _require_fence(job, fencing_token)

    job.status = "PARTIAL"
    job.partial_detail = {
        "succeeded_stages": list(succeeded_stages),
        "failed_stages": list(failed_stages),
        "is_success": False,
        # RV14：PARTIAL 的部分产物/结构化诊断对用户可见（绝不展示假 PDF 预览）。
        "result": redact_mapping(result or {}),
    }
    job.result = redact_mapping(result or {})
    job.error_code = error_code
    job.error_message = f"部分 stage 失败: {list(failed_stages)}"
    _finish(job)
    db.flush()
    emit_job_event(
        db,
        job=job,
        event_type="job.partial",
        payload={
            "succeeded_stages": list(succeeded_stages),
            "failed_stages": list(failed_stages),
            "is_success": False,
        },
        trace_id=trace_id or job.trace_id,
    )
    return job


def fail_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    fencing_token: int,
    error_code: str,
    error_message: str = "",
    trace_id: str = "",
) -> JobRow:
    """失败：按分类决定重试还是终结；重试耗尽 ⇒ DLQ。"""
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    _require_fence(job, fencing_token)

    retryable = classify_error(error_code)
    job.error_code = error_code
    # **DLQ 里不得出现密钥/PII**：错误文本可能原样带出上游返回体，
    # 因此入库前统一走与日志同一套脱敏规则。
    safe_message = str(redact_mapping((error_message or "")[:2000]))
    job.error_message = safe_message
    job.retryable = retryable

    if retryable and int(job.attempts or 0) < int(job.max_attempts or 0):
        delay = backoff_seconds(int(job.attempts or 1))
        job.status = "RETRYING"
        job.next_attempt_at = _now() + timedelta(seconds=delay)
        job.lease_owner = ""
        job.lease_expires_at = None
        job.last_heartbeat_at = None
        db.flush()
        emit_job_event(
            db,
            job=job,
            event_type="job.retrying",
            payload={"attempt": job.attempts, "delay_seconds": round(delay, 3), "error_code": error_code},
            trace_id=trace_id or job.trace_id,
        )
        return job

    job.status = "FAILED"
    _finish(job)
    db.flush()
    emit_job_event(
        db,
        job=job,
        event_type="job.failed",
        payload={"error_code": error_code, "retryable": retryable, "attempts": job.attempts},
        trace_id=trace_id or job.trace_id,
    )
    move_to_dlq(db, job=job, trace_id=trace_id or job.trace_id)
    return job


def cancel_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    fencing_token: int | None = None,
    reason: str = "",
    trace_id: str = "",
    partial_manifest: dict[str, Any] | None = None,
) -> JobRow:
    """取消：落 CANCELLED 并**保留有效 partial manifest**（不丢已完成的产出）。"""
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    if fencing_token is not None:
        _require_fence(job, fencing_token)

    job.status = "CANCELLED"
    job.partial_detail = {
        "succeeded_stages": list((partial_manifest or {}).get("succeeded_stages", []) or []),
        "failed_stages": list((partial_manifest or {}).get("failed_stages", []) or []),
        "cancelled": True,
        "is_success": False,
        "reason": reason,
    }
    if partial_manifest:
        job.result = redact_mapping(partial_manifest.get("result") or {})
    _finish(job)
    db.flush()
    emit_job_event(db, job=job, event_type="job.cancelled", payload={"reason": reason}, trace_id=trace_id or job.trace_id)
    return job


def request_cancel(db: Session, *, job_id: uuid.UUID, reason: str = "", trace_id: str = "") -> JobRow:
    """**请求**取消：写持久化的 `cancel_requested_at`。

    对 QUEUED/RETRYING 作业立即生效（worker 根本不必启动）；
    对正在跑的作业只落请求，worker 在**安全点**自行退出。
    这样比"直接改状态"安全：直接改会让 worker 的结果上报与状态变更打架。
    """
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    if job.status in ("SUCCEEDED", "FAILED", "CANCELLED", "PARTIAL"):
        raise JobNotClaimable(f"作业已处于终态 {job.status}，无法取消")

    job.cancel_requested_at = _now()
    db.flush()
    if job.status in ("QUEUED", "RETRYING"):
        return cancel_job(db, job_id=job_id, reason=reason or "cancel_requested", trace_id=trace_id)
    return job


def should_stop(db: Session, *, job_id: uuid.UUID, fencing_token: int) -> bool:
    """worker 的**安全点**检查：是否已被要求取消 / 围栏已失效。"""
    job = db.get(JobRow, job_id)
    if job is None:
        return True
    if int(job.fencing_token or 0) != int(fencing_token):
        return True
    return job.cancel_requested_at is not None


# --------------------------------------------------------------------------
# DLQ
# --------------------------------------------------------------------------


def move_to_dlq(db: Session, *, job: JobRow, trace_id: str = "") -> JobDlqRow:
    """写入 DLQ（可重放：保留 input ref + policy version + 脱敏 error + trace）。"""
    row = JobDlqRow(
        id=uuid.uuid4(),
        job_id=job.id,
        session_id=job.session_id,
        tenant_id=job.tenant_id,
        job_type=job.job_type,
        policy_version=job.policy_version,
        input_ref=job.input_ref,
        input_payload=job.input_payload or {},
        error_code=job.error_code,
        error_message=(job.error_message or "")[:2000],
        attempts=int(job.attempts or 0),
        trace_id=trace_id or job.trace_id,
        replayed_job_id=None,
        replayed_at=None,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    emit_job_event(
        db,
        job=job,
        event_type="job.dlq",
        payload={"dlq_id": str(row.id), "error_code": job.error_code},
        trace_id=trace_id or job.trace_id,
    )
    return row


def replay_dlq(
    db: Session,
    *,
    dlq_id: uuid.UUID,
    max_attempts: int | None = None,
    trace_id: str = "",
) -> JobRow:
    """**显式管理动作**：把 DLQ 条目重放成一个新作业。

    刻意不提供"自动重放"：自动重放会变成无限循环，把一个坏输入
    变成永不停止的资源消耗。
    """
    row = db.get(JobDlqRow, dlq_id)
    if row is None:
        raise JobNotFound(f"DLQ 条目不存在: {dlq_id}")
    if row.replayed_at is not None:
        raise JobNotClaimable(f"DLQ 条目已重放过: {row.replayed_at.isoformat()}")

    job = enqueue(
        db,
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        job_type=row.job_type,
        input_payload=row.input_payload or {},
        input_ref=row.input_ref,
        max_attempts=max_attempts or JOB_DEFAULT_MAX_ATTEMPTS,
        trace_id=trace_id or row.trace_id,
        check_limits=False,  # 重放是管理员动作，不受用户配额限制
    )
    row.replayed_job_id = job.id
    row.replayed_at = _now()
    db.flush()
    emit_job_event(
        db,
        job=job,
        event_type="job.replayed",
        payload={"dlq_id": str(row.id), "original_job_id": str(row.job_id)},
        trace_id=trace_id or row.trace_id,
    )
    return job


# --------------------------------------------------------------------------
# 收件箱（幂等消费者）
# --------------------------------------------------------------------------


def try_accept(
    db: Session, *, event_id: str, job_id: uuid.UUID, attempt: int, consumer: str
) -> bool:
    """尝试把 `(event_id, job_id, attempt)` 记入收件箱。

    返回 `True` = 首次见到（可以执行）；`False` = 重复投递（**必须**跳过）。

    实现要点（踩过一次坑，写下来免得再犯）：用 `ON CONFLICT DO NOTHING ...
    RETURNING id`，**不能**用 `CursorResult.rowcount` 判断是否插入。
    实测 psycopg3 + `ON CONFLICT DO NOTHING` 时 rowcount 会报 **1**（即使什么都没插入），
    于是去重会静默失效 —— 重复投递被全部放行。`RETURNING` 的结果集长度才是权威判据。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(JobInboxRow)
        .values(
            scope="rv8300",
            event_id=event_id,
            job_id=job_id,
            attempt=int(attempt),
            consumer=consumer,
            outcome="ACCEPTED",
            detail={},
            received_at=_now(),
        )
        .on_conflict_do_nothing(constraint="uq_job_inbox_dedupe")
        .returning(JobInboxRow.id)
    )
    return db.execute(stmt).first() is not None


def record_outcome(
    db: Session, *, event_id: str, job_id: uuid.UUID, attempt: int, outcome: str, detail: dict | None = None
) -> None:
    row = (
        db.query(JobInboxRow)
        .filter(
            JobInboxRow.event_id == event_id,
            JobInboxRow.job_id == job_id,
            JobInboxRow.attempt == int(attempt),
        )
        .one_or_none()
    )
    if row is not None:
        row.outcome = outcome
        row.detail = redact_mapping(detail or {})
        db.flush()


# --------------------------------------------------------------------------
# 只读
# --------------------------------------------------------------------------


def get_job(db: Session, job_id: uuid.UUID) -> JobRow:
    job = db.get(JobRow, job_id)
    if job is None:
        raise JobNotFound(f"作业不存在: {job_id}")
    return job


def list_jobs(db: Session, session_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        db.query(JobRow)
        .filter(JobRow.session_id == session_id)
        .order_by(JobRow.created_at.desc())
        .limit(limit)
        .all()
    )
    return [public_view(j) for j in rows]


def stats(db: Session) -> dict[str, Any]:
    rows = db.execute(select(JobRow.status, func.count()).group_by(JobRow.status)).all()
    return {"by_status": {str(s): int(c) for s, c in rows}}
