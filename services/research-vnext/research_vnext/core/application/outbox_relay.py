"""Outbox relay（RV03）。

**问题**：RV02 的 outbox 只到 durable record —— 事件写进了表，但**没有任何东西
把它送出去**。于是"事务性 outbox"这个模式只完成了一半：可靠性有了，投递没有。

**本模块补齐后半段**，并守住四条性质：

1. **并发安全**：用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取行。两个 relay
   同时跑不会重复领取同一行（SKIP LOCKED 让第二个跳过被锁住的行）。
2. **重启安全**：领取 + 标记在同一事务提交。崩溃只可能发生在"提交前"
   （行仍未发布，下次会重新领取）或"提交后"（行已标记 published，但队列
   可能没收到）。后者由 4 兜住。
3. **不丢**：`attempts` 超限的行**不会**被静默丢弃 —— 留在表里并标记 `FAILED`
   供人工/DLQ 处置，因为"丢掉一个领域事件"比"表里有几行红字"严重得多。
4. **重复投递是允许的**：至少一次语义。去重由 `job_inbox` 唯一约束
   `(event_id, job_id, attempt)` 在**数据库层**完成 —— 队列去重只做优化，
   丢了也不会导致重复执行。

**Valkey 只是协调**：`relay_once()` 在 Valkey 不可用时抛 `QueueUnavailable`，
**不**回填 `published_at` —— 让事件留在未发布状态，Valkey 回来后自动补发。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import COMPILE_JOB_TYPES, outbox_max_attempts, outbox_relay_batch
from ..db.models import OutboxEventRow
from ..queue.streams import Queue, QueueUnavailable, get_queue

RELAY_ACTOR = "outbox-relay"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _envelope(row: OutboxEventRow) -> dict[str, Any]:
    """送到队列的消息体（自包含：worker 不需要回表即可决定做什么）。"""
    return {
        "event_id": row.event_id,
        "event_type": row.event_type,
        "schema_version": row.schema_version,
        "session_id": str(row.session_id),
        "trace_id": row.trace_id,
        "payload": row.payload or {},
        "occurred_at": (row.created_at or _now()).isoformat(),
        "outbox_id": int(row.id),
    }


def claim_batch(db: Session, *, limit: int | None = None) -> list[OutboxEventRow]:
    """领取一批未发布事件。

    `FOR UPDATE SKIP LOCKED`：并发 relay 会互相跳过对方正在处理的行，
    因此不需要分布式锁，也不会串行化成一个瓶颈。
    """
    n = limit or outbox_relay_batch()
    return (
        db.execute(
            select(OutboxEventRow)
            .where(
                OutboxEventRow.published_at.is_(None),
                OutboxEventRow.status.in_(("PENDING", "RETRY")),
            )
            .order_by(OutboxEventRow.id)
            .limit(n)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )


def relay_once(
    db: Session,
    *,
    queue: Queue | None = None,
    compile_queue: Queue | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """发布一批事件并回填 `published_at` / `attempts`。

    **必须**在调用方的事务里运行：领取行依赖该事务持有的行锁。
    返回统计（用于日志/指标）。

    RV14：`compile_queue` 非空时，`job_type ∈ COMPILE_JOB_TYPES` 的
    `job.queued` 事件路由到独立 compile stream（与主 pool 完全隔离）；
    compile_queue 为空（旧调用方）时保持原行为 —— 全部进主队列。
    """
    q = queue or get_queue()
    cq = compile_queue
    rows = claim_batch(db, limit=limit)
    published = 0
    failed = 0
    exhausted = 0
    now = _now()
    max_attempts = outbox_max_attempts()

    for row in rows:
        row.attempts = int(row.attempts or 0) + 1
        target = q
        if cq is not None:
            envelope = _envelope(row)
            jtype = str((envelope.get("payload") or {}).get("job_type") or "")
            if jtype in COMPILE_JOB_TYPES:
                target = cq
        try:
            target.publish(row.event_id, _envelope(row))
        except QueueUnavailable:
            # **关键**：不回填 published_at。事件留在表里等 Valkey 回来。
            # 把 attempts 回滚成"未尝试"会让退避失去意义，因此保留这次尝试计数。
            failed += 1
            if row.attempts >= max_attempts:
                row.status = "FAILED"
                exhausted += 1
            else:
                row.status = "RETRY"
            continue

        row.published_at = now
        row.status = "PUBLISHED"
        published += 1

    db.flush()
    return {
        "claimed": len(rows),
        "published": published,
        "failed": failed,
        "exhausted": exhausted,
    }


def backlog(db: Session) -> dict[str, Any]:
    """未发布积压（可观测性/背压判定用）。"""
    from sqlalchemy import func

    rows = db.execute(
        select(OutboxEventRow.status, func.count())
        .where(OutboxEventRow.published_at.is_(None))
        .group_by(OutboxEventRow.status)
    ).all()
    by_status = {str(s): int(c) for s, c in rows}
    oldest = db.execute(
        select(func.min(OutboxEventRow.created_at)).where(OutboxEventRow.published_at.is_(None))
    ).scalar()
    return {
        "pending": sum(by_status.values()),
        "by_status": by_status,
        "oldest_age_seconds": (
            int((_now() - oldest).total_seconds()) if oldest is not None else 0
        ),
    }


def requeue_failed(db: Session, *, max_age_hours: int = 24) -> int:
    """把 `FAILED` 行重新置为可领取（显式运维动作，不是自动无限循环）。"""
    cutoff = _now() - timedelta(hours=max_age_hours)
    rows = (
        db.query(OutboxEventRow)
        .filter(
            OutboxEventRow.status == "FAILED",
            OutboxEventRow.published_at.is_(None),
            OutboxEventRow.created_at >= cutoff,
        )
        .all()
    )
    for r in rows:
        r.status = "PENDING"
        r.attempts = 0
    db.flush()
    return len(rows)


def dlq_payload(row: OutboxEventRow) -> str:
    """把一行 outbox 序列化成可重放 JSON（DLQ/审计用）。"""
    return json.dumps(
        {
            "id": str(row.id),
            "event_id": row.event_id,
            "event_type": row.event_type,
            "session_id": str(row.session_id),
            "attempts": int(row.attempts or 0),
            "trace_id": row.trace_id,
            "payload": row.payload or {},
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def new_manual_event_id() -> str:
    return f"evt-manual-{uuid.uuid4().hex[:24]}"
