"""Job worker（RV03）—— 可运行、可重启、可恢复。

运行方式：
    python -m research_vnext.core.worker.entrypoint --once          # 跑一轮就退出（CI/测试）
    python -m research_vnext.core.worker.entrypoint                 # 常驻

**恢复语义**（这是"durable"的实际含义）：
- worker 崩溃 ⇒ 租约到期 ⇒ 别的 worker 用**更大的 fencing token** 接管；
- 崩溃前写的进度/阶段是持久的（在 `job_run`），因此接管者从**已记录的阶段**
  继续，而不是从零重来；
- 旧 worker 若"复活"并上报结果，`fencing_token` 不匹配 ⇒ 被拒
  （见 `job_service.StaleFencingToken`）。**没有这一条，durable 只是口号。**
- 事件来源有两路：Valkey 队列（快）与 PostgreSQL 扫描（**Valkey 全丢也不丢活**）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from sqlalchemy.orm import Session

from ..config import (
    consumer_name,
    job_heartbeat_seconds,
    job_stage_timeout_seconds,
    worker_poll_block_ms,
)
from ..db.engine import session_scope
from ..queue.streams import QueueUnavailable, get_queue
from ..application import job_service as js

logger = logging.getLogger(__name__)

#: handler 收到的上下文（只暴露它需要的东西，避免 handler 直接改状态）
@dataclass
class HandlerContext:
    job_id: uuid.UUID
    fencing_token: int
    attempt: int
    session_id: uuid.UUID
    tenant_id: uuid.UUID
    input_payload: dict[str, Any]
    job_type: str
    resumed_from_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)

    def stopped(self) -> bool:
        """安全点检查：被要求取消 / 围栏失效 ⇒ True。

        **同步**是刻意的：安全点检查必须在 handler 的每一步都能**立刻**问一次，
        不应因为"要不要 await"而让 handler 作者偷懒跳过。
        """
        with session_scope() as db:
            return js.should_stop(db, job_id=self.job_id, fencing_token=self.fencing_token)

    async def report(self, *, stage: str = "", progress: int | None = None) -> None:
        with session_scope() as db:
            js.report_progress(
                db, job_id=self.job_id, fencing_token=self.fencing_token, stage=stage, progress=progress
            )

    async def heartbeat(self) -> None:
        with session_scope() as db:
            js.heartbeat(db, job_id=self.job_id, fencing_token=self.fencing_token, worker=consumer_name())


@dataclass
class HandlerOutcome:
    """handler 的返回值。`succeeded_stages` / `failed_stages` 决定 SUCCEEDED 还是 PARTIAL。"""

    result: dict[str, Any] = field(default_factory=dict)
    succeeded_stages: list[str] = field(default_factory=list)
    failed_stages: list[str] = field(default_factory=list)
    error_code: str = ""
    error_message: str = ""


Handler = Callable[[HandlerContext], Coroutine[Any, Any, HandlerOutcome]]

_HANDLERS: dict[str, Handler] = {}


def register(job_type: str, handler: Handler) -> None:
    _HANDLERS[job_type] = handler


def known_job_types() -> list[str]:
    return sorted(_HANDLERS)


def get_handler(job_type: str) -> Handler | None:
    return _HANDLERS.get(job_type)


# --------------------------------------------------------------------------
# 内置 handler
# --------------------------------------------------------------------------


async def _noop_handler(ctx: HandlerContext) -> HandlerOutcome:
    """探针作业：用于验证运行时的租约/围栏/重试/取消语义。"""
    steps = int((ctx.input_payload or {}).get("steps", 2))
    fail_at = (ctx.input_payload or {}).get("fail_at")
    error_code = (ctx.input_payload or {}).get("error_code", "PROVIDER_ERROR")
    done: list[str] = []
    for i in range(steps):
        if ctx.stopped():
            return HandlerOutcome(
                succeeded_stages=done,
                failed_stages=[f"step{i}"],
                error_code="CANCELLED",
                error_message="取消请求在安全点生效",
                result={"partial": done},
            )
        await ctx.report(stage=f"step{i}", progress=int((i + 1) / steps * 100))
        if fail_at == i:
            return HandlerOutcome(
                succeeded_stages=done,
                failed_stages=[f"step{i}"],
                error_code=error_code,
                error_message=f"模拟失败于 step{i}",
            )
        done.append(f"step{i}")
        await asyncio.sleep(0)
    return HandlerOutcome(result={"steps": done}, succeeded_stages=done)


async def _experiment_handler(ctx: HandlerContext) -> HandlerOutcome:
    """RV11：真实实验执行走隔离沙箱（SandboxExecutor）。

    **诚实边界**：
    - 用户/模型代码只在 Linux 容器里执行（container backend）；worker 进程
      只发起 `docker run` 并读取结果，**从不**在本进程执行用户代码；
    - 执行材料（代码/输入）按 run 的 dispatch 记录回读不可变 plan 行，
      并校验 sha256 —— 队列 payload 里没有代码，也不能有；
    - `exit 0` 只记录执行事实；`claim_holds` 永远保持 None（RV12 验证域）；
    - 超时/超内存/OOM/守卫拦截 → 真实 FAILED/CANCELLED，绝不返回绿色预览。
    """
    import uuid as _uuid

    from ..application import experiment_run_service as run_svc
    from ..execution.executor import ExecutionRequest, ExecutorNotConfigured, get_executor

    payload = ctx.input_payload or {}
    run_id_raw = str(payload.get("run_id") or "")
    if not run_id_raw:
        return HandlerOutcome(
            failed_stages=["execute"],
            error_code="INVALID_INPUT",
            error_message="experiment.run payload 缺少 run_id",
        )
    try:
        run_uuid = _uuid.UUID(run_id_raw)
    except ValueError:
        return HandlerOutcome(
            failed_stages=["execute"], error_code="INVALID_INPUT", error_message="run_id 不是 UUID"
        )

    # 材料回读（DB 唯一事实权威）+ 完整性校验
    with session_scope() as db:
        material = run_svc.execution_material(db, run_id=run_uuid)
    if not material["integrity_ok"]:
        return HandlerOutcome(
            failed_stages=["execute"],
            error_code="INVALID_INPUT",
            error_message="代码完整性校验失败：sha256(plan.code) != dispatch 记录的 codeSha256",
        )
    if not material["input_hashes_ok"]:
        return HandlerOutcome(
            failed_stages=["execute"],
            error_code="INVALID_INPUT",
            error_message="输入完整性校验失败：inputHashes 与 dispatch 记录不一致",
        )

    if ctx.stopped():
        return HandlerOutcome(
            succeeded_stages=[],
            failed_stages=["execute"],
            error_code="CANCELLED",
            error_message="取消请求在安全点生效（执行前）",
        )

    executor = get_executor()
    if not executor.configured:
        return HandlerOutcome(
            failed_stages=["execute"],
            error_code="EXECUTOR_NOT_CONFIGURED",
            error_message="未配置沙箱执行器（RESEARCH_EXECUTOR=sandbox 或 RESEARCH_EXECUTOR_ENDPOINT）",
        )

    await ctx.report(stage="sandbox", progress=5)
    request = ExecutionRequest(
        job_id=str(ctx.job_id),
        session_id=str(ctx.session_id),
        plan_id=material["plan_id"],
        code_ref=material["code_ref"],
        dataset_ref=material["dataset_ref"],
        environment=material["environment"],
        params=material["params"],
        seed=material["seed"],
        code=material["code"],
        inputs=material["inputs"],
        limits=material["limits"],
        attempt=int(ctx.attempt),
        reproduce_of=payload.get("reproduce_of"),
    )
    try:
        res = await executor.run(request)
    except ExecutorNotConfigured as e:
        return HandlerOutcome(
            failed_stages=["execute"], error_code="EXECUTOR_NOT_CONFIGURED", error_message=str(e)
        )
    except Exception as e:  # noqa: BLE001 - adapter 崩溃是基础设施错误（可重试）
        return HandlerOutcome(
            failed_stages=["execute"],
            error_code="PROVIDER_ERROR",
            error_message=f"sandbox adapter 崩溃: {type(e).__name__}: {e}"[:500],
        )

    cancelled = ctx.stopped()
    summary = run_svc.complete_run(run_id=run_uuid, result=res, cancelled=cancelled)

    if summary["status"] == "CANCELLED":
        return HandlerOutcome(
            succeeded_stages=[],
            failed_stages=["execute"],
            error_code="CANCELLED",
            error_message="取消请求在安全点生效",
            result={"runId": run_id_raw, **{k: summary[k] for k in ("exit_code",)}},
        )

    repro_summary = {
        "outputHash": (res.repro or {}).get("outputHash", ""),
        "imageDigest": (res.repro or {}).get("imageDigest", ""),
        "execStatus": (res.repro or {}).get("execStatus", ""),
    }
    result_payload = {
        "runId": run_id_raw,
        "exitCode": summary["exit_code"],
        "claimHolds": None,  # exit 0 ≠ 结论成立
        "reproSummary": repro_summary,
    }
    if summary["status"] == "SUCCEEDED":
        return HandlerOutcome(
            result=result_payload, succeeded_stages=["execute"], failed_stages=[]
        )
    return HandlerOutcome(
        result=result_payload,
        succeeded_stages=[],
        failed_stages=["execute"],
        error_code=summary["error_code"] or "SANDBOX_FAILED",
        error_message=f"沙箱执行未成功（status={summary['status']}, exit={summary['exit_code']}）",
    )


register("probe.noop", _noop_handler)
register("experiment.run", _experiment_handler)
register("job.noop", _noop_handler)

# RV04 · literature ingest（与其它 handler 同一 JobContract 形状）
from .literature_handlers import literature_ingest_handler  # noqa: E402

register("literature.ingest", literature_ingest_handler)

# RV05 · document.parse（隔离 Docling/pypdf）
from .parser_handlers import document_parse_handler, register_parser_jobs  # noqa: E402

register_parser_jobs()

# RV12 · verification.verify（claim 验证；与同步路径共用同一 service）
from .verification_handlers import register_verification_jobs  # noqa: E402

register_verification_jobs()

# RV14 · paper.export（Research 导出；跑在独立 compile pool）
from .export_handlers import register_export_jobs  # noqa: E402

register_export_jobs()


# --------------------------------------------------------------------------
# 单次执行
# --------------------------------------------------------------------------


async def run_job(job_id: uuid.UUID) -> dict[str, Any]:
    """领取并执行一个作业（含心跳、超时、分类、DLQ）。返回结果摘要。"""
    worker = consumer_name()
    with session_scope() as db:
        try:
            job = js.claim_job(db, job_id=job_id, worker=worker)
        except js.JobNotClaimable:
            # RV14：已排队作业被 request_cancel 直接收尾（QUEUED → CANCELLED）
            # 时，claim 因"终态不可领取"失败 —— 读取真实状态如实返回，
            # 而不是把取消当成 worker 竞争冲突抛给调用方。
            with session_scope() as db2:
                job2 = js.get_job(db2, job_id)
                if job2 is not None and job2.status == "CANCELLED":
                    return {"job_id": str(job_id), "status": "CANCELLED", "note": "cancelled_before_claim"}
            raise
        if job.status == "CANCELLED":
            return {"job_id": str(job_id), "status": "CANCELLED", "note": "pre_claim"}
        fence = int(job.fencing_token)
    return await _execute_claimed(job_id, fence, worker)

async def _execute_claimed(job_id: uuid.UUID, fence: int, worker: str) -> dict[str, Any]:
    """执行一个**已经领取**的作业。

    与 `run_job` 分开是必要的：`drain_once` 已经从数据库/队列领到了作业，
    再调一次 `claim_job` 会因为"租约仍有效"被判为不可领取 —— 那会把
    "已领取"误当成"领取失败"，让 worker 什么都不干。
    """
    with session_scope() as db:
        job = js.get_job(db, job_id)
        handle = HandlerContext(
            job_id=job.id,
            fencing_token=fence,
            attempt=int(job.attempts),
            session_id=job.session_id,
            tenant_id=job.tenant_id,
            input_payload=dict(job.input_payload or {}),
            job_type=job.job_type,
            resumed_from_stage=job.stage or "",
            completed_stages=list((job.partial_detail or {}).get("succeeded_stages", []) or []),
        )

    handler = get_handler(handle.job_type)
    if handler is None:
        return _finalize_failure(
            job_id, fence, "UNSUPPORTED_JOB_TYPE", f"未注册的 job_type: {handle.job_type!r}"
        )

    # 心跳协程：**与 progress 分开**，只续租
    stop_hb = asyncio.Event()

    async def _hb() -> None:
        interval = max(1, job_heartbeat_seconds())
        while not stop_hb.is_set():
            try:
                await asyncio.wait_for(stop_hb.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                with session_scope() as hdb:
                    js.heartbeat(hdb, job_id=job_id, fencing_token=fence, worker=worker)
            except js.StaleFencingToken:
                return
            except Exception as e:  # noqa: BLE001 - 心跳失败不应让作业崩掉
                logger.warning("job.heartbeat_failed job_id=%s error=%s", job_id, e)

    hb_task = asyncio.create_task(_hb())
    stage_timeout = job_stage_timeout_seconds()
    try:
        outcome = await asyncio.wait_for(handler(handle), timeout=stage_timeout)
    except asyncio.TimeoutError:
        outcome = HandlerOutcome(
            failed_stages=[handle.resumed_from_stage or "stage"],
            error_code="STAGE_TIMEOUT",
            error_message=f"stage 超时（{stage_timeout}s）",
        )
    except js.StaleFencingToken:
        stop_hb.set()
        hb_task.cancel()
        return {"job_id": str(job_id), "status": "FENCED_OUT", "note": "租约已被接管，结果丢弃"}
    except Exception as e:  # noqa: BLE001 - handler 的任何异常都必须变成可分类的失败
        outcome = HandlerOutcome(
            failed_stages=["unknown"],
            error_code=f"UNEXPECTED_{type(e).__name__}",
            error_message=str(e)[:500],
        )
    finally:
        stop_hb.set()
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    return _finalize(job_id, fence, outcome)


def _finalize(job_id: uuid.UUID, fence: int, outcome: HandlerOutcome) -> dict[str, Any]:
    with session_scope() as db:
        if outcome.error_code == "CANCELLED":
            js.cancel_job(
                db,
                job_id=job_id,
                fencing_token=fence,
                reason=outcome.error_message or "cancelled",
                partial_manifest={
                    "succeeded_stages": outcome.succeeded_stages,
                    "failed_stages": outcome.failed_stages,
                    "result": outcome.result,
                },
            )
            return {"job_id": str(job_id), "status": "CANCELLED"}

        if outcome.failed_stages and outcome.succeeded_stages:
            js.complete_partial(
                db,
                job_id=job_id,
                fencing_token=fence,
                succeeded_stages=outcome.succeeded_stages,
                failed_stages=outcome.failed_stages,
                result=outcome.result,
                error_code=outcome.error_code or "STAGE_FAILED",
            )
            return {"job_id": str(job_id), "status": "PARTIAL"}

        if outcome.error_code:
            js.fail_job(
                db,
                job_id=job_id,
                fencing_token=fence,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
            job = js.get_job(db, job_id)
            return {"job_id": str(job_id), "status": js.public_status(job.status)}

        js.complete_job(db, job_id=job_id, fencing_token=fence, result=outcome.result)
        return {"job_id": str(job_id), "status": "SUCCEEDED"}


def _finalize_failure(job_id: uuid.UUID, fence: int, code: str, msg: str) -> dict[str, Any]:
    with session_scope() as db:
        js.fail_job(db, job_id=job_id, fencing_token=fence, error_code=code, error_message=msg)
        job = js.get_job(db, job_id)
        return {"job_id": str(job_id), "status": js.public_status(job.status)}


# --------------------------------------------------------------------------
# 循环
# --------------------------------------------------------------------------


def _job_id_from_message(payload: dict[str, Any]) -> uuid.UUID | None:
    raw = payload.get("job_id") or (payload.get("payload") or {}).get("job_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _event_id_from_message(payload: dict[str, Any]) -> tuple[str, int]:
    eid = str(payload.get("event_id") or "")
    payload_inner = payload.get("payload") or {}
    return eid, int(payload_inner.get("attempt", 0) or 0)


async def drain_once(
    *, limit: int = 10, from_queue: bool = True, pool: str = "main"
) -> dict[str, Any]:
    """跑一轮：先从队列读，再从数据库兜底领活。

    返回 `{"processed": [...], "source": {...}}`。`from_queue=False` 强制走数据库
    路径 —— 用于证明"Valkey 丢了也不丢活"。

    RV14：`pool="compile"` 时使用独立 compile stream/group/dedupe，
    且 DB 兜底**只**领 COMPILE_JOB_TYPES；主 pool（默认）则**排除**它们 ——
    两个 pool 的作业空间互不重叠（队列隔离，任务书 4）。
    """
    processed_out: list[dict[str, Any]] = []
    source = {"pool": pool, "queue": 0, "db": 0, "deduped": 0, "queue_error": ""}

    queue_for_pool = None
    job_types = None
    exclude_job_types = None
    if pool == "compile":
        from ..config import (
            COMPILE_CONSUMER_GROUP,
            COMPILE_DEDUPE_SCOPE,
            COMPILE_JOB_TYPES,
            COMPILE_STREAM_KEY,
        )
        from ..queue.streams import ValkeyStreamQueue

        queue_for_pool = ValkeyStreamQueue(
            stream=COMPILE_STREAM_KEY,
            group=COMPILE_CONSUMER_GROUP,
            dedupe_scope=COMPILE_DEDUPE_SCOPE,
        )
        job_types = COMPILE_JOB_TYPES
    else:
        from ..config import COMPILE_JOB_TYPES

        exclude_job_types = COMPILE_JOB_TYPES

    if from_queue:
        q = queue_for_pool or get_queue()
        try:
            messages = q.read(consumer=consumer_name(), count=limit, block_ms=worker_poll_block_ms())
        except QueueUnavailable as e:
            messages = []
            source["queue_error"] = str(e)
            logger.warning("worker.queue_unavailable error=%s", e)

        for _stream_id, payload in messages:
            job_id = _job_id_from_message(payload)
            if job_id is None:
                continue
            event_id, attempt = _event_id_from_message(payload)
            with session_scope() as db:
                if event_id and not js.try_accept(
                    db, event_id=event_id, job_id=job_id, attempt=attempt, consumer=consumer_name()
                ):
                    source["deduped"] += 1
                    continue
            try:
                processed_out.append(await run_job(job_id))
                source["queue"] += 1
            except js.JobNotClaimable:
                # 已终态/已被别人领走：正常且幂等
                continue
            except js.JobNotFound:
                continue

    # 数据库兜底：**Valkey 全丢也能继续干活**（并按 pool 过滤作业类型）
    with session_scope() as db:
        due = js.claim_due_jobs(
            db,
            worker=consumer_name(),
            limit=limit,
            job_types=job_types,
            exclude_job_types=exclude_job_types,
        )
        due_claimed = [(j.id, int(j.fencing_token)) for j in due]

    for jid, fence in due_claimed:
        try:
            processed_out.append(await _execute_claimed(jid, fence, consumer_name()))
            source["db"] += 1
        except js.JobNotClaimable:
            continue
        except js.JobNotFound:
            continue

    return {"processed": processed_out, "source": source}


def _install_signal_handlers(state: dict[str, bool]) -> None:
    def _handler(signum, frame):  # noqa: ANN001,ARG001
        state["stop"] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # pragma: no cover - 非主线程/Windows 限制
            pass


async def run_forever(*, limit: int = 10, idle_sleep: float = 1.0, pool: str = "main") -> None:
    """常驻循环。SIGINT/SIGTERM 触发**优雅退出**（在安全点停止领取新活）。"""
    state = {"stop": False}
    _install_signal_handlers(state)
    logger.info("worker.started pool=%s consumer=%s handlers=%s", pool, consumer_name(), known_job_types())
    while not state["stop"]:
        try:
            out = await drain_once(limit=limit, pool=pool)
        except Exception as e:  # noqa: BLE001 - 常驻进程不得因单轮异常退出
            logger.error("worker.round_failed error=%s", e)
            out = {"processed": [], "source": {}}
        if not out["processed"]:
            await asyncio.sleep(idle_sleep)
    logger.info("worker.stopped consumer=%s", consumer_name())


def recovered_state(job_id: uuid.UUID) -> dict[str, Any]:
    """读回一个作业的**持久**恢复信息（用于测试"崩溃后从哪继续"）。"""
    with session_scope() as db:
        job = js.get_job(db, job_id)
        return {
            "status": job.status,
            "public_status": js.public_status(job.status),
            "attempts": job.attempts,
            "fencing_token": job.fencing_token,
            "stage": job.stage,
            "progress": job.progress,
            "partial_detail": job.partial_detail or {},
            "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _unused(_db: Session) -> None:  # pragma: no cover - 保留类型引用
    return None
