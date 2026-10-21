"""消费侧去重（InboxDedupe）：重复投递 / ACK 前崩溃 / 重放兜底 → 单副作用。

语义：
- `consume()` 在同一业务事务里先查后插（UNIQUE(owner_service, dedupe_key) 兜底并发）；
- 已存在且 payload_hash 一致 → `(already_seen=True, conflict=False)`：跳过副作用（幂等消费）；
- 已存在但 payload_hash 不同 → `(already_seen=True, conflict=True)`：显式冲突，绝不静默吞
  （调用方决定抛错/告警，防「同一去重键被不同消息复用」）。
- `dedupe_key` 约定：优先取消息/行级稳定键（如 job_id），保证重放指向同一副作用。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ReliabilityInbox

__all__ = ["InboxConsumeResult", "InboxDedupe", "InboxPayloadConflict"]


class InboxPayloadConflict(RuntimeError):
    """同 dedupe_key 复用了不同 payload（去重键冲突，非重复投递）。"""


class InboxConsumeResult:
    """consume() 判定结果。

    - duplicate=False → 本消息首次见到，调用方应执行副作用（并在同一事务中已预占去重键）；
    - duplicate=True, conflict=False → 重复投递/重放，副作用已存在，跳过；
    - duplicate=True, conflict=True → 去重键冲突，抛 InboxPayloadConflict 由上层处理。
    """

    __slots__ = ("duplicate", "conflict", "record_id")

    def __init__(self, *, duplicate: bool, conflict: bool, record_id: int | None = None):
        self.duplicate = duplicate
        self.conflict = conflict
        self.record_id = record_id


class InboxDedupe:
    @staticmethod
    async def consume(
        session: AsyncSession,
        *,
        owner_service: str,
        dedupe_key: str,
        payload_hash: str,
        message_id: str | None = None,
    ) -> InboxConsumeResult:
        """在调用方事务内预占去重键并返回判定。

        首次：INSERT + flush（不 commit，随业务事务一起提交——这正是「副作用后 ACK 前崩溃」的
        原子性载体：崩溃 → 事务回滚 → 键未占 → 重放可再次消费；提交成功 → 键已占 → 重放跳过）。
        已存在：读取比对 payload_hash。
        """
        existing = (
            await session.execute(
                select(ReliabilityInbox).where(
                    ReliabilityInbox.owner_service == owner_service,
                    ReliabilityInbox.dedupe_key == dedupe_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.payload_hash != payload_hash:
                return InboxConsumeResult(duplicate=True, conflict=True, record_id=existing.id)
            return InboxConsumeResult(duplicate=True, conflict=False, record_id=existing.id)

        row = ReliabilityInbox(
            owner_service=owner_service,
            dedupe_key=dedupe_key,
            message_id=message_id,
            payload_hash=payload_hash,
            status="PROCESSED",
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # 并发双投：唯一约束兜底 → 回读到胜者行再判定
            row = (
                await session.execute(
                    select(ReliabilityInbox).where(
                        ReliabilityInbox.owner_service == owner_service,
                        ReliabilityInbox.dedupe_key == dedupe_key,
                    )
                )
            ).scalar_one_or_none()
            if row is None:  # pragma: no cover —— 理论上不可达（约束存在则必有行）
                raise
            if row.payload_hash != payload_hash:
                return InboxConsumeResult(duplicate=True, conflict=True, record_id=row.id)
            return InboxConsumeResult(duplicate=True, conflict=False, record_id=row.id)
        return InboxConsumeResult(duplicate=False, conflict=False, record_id=row.id)

    @staticmethod
    def payload_hash_of(message: dict) -> str:
        """消息规范 hash：去重键行级一致的前提下比对内容一致性。"""
        import hashlib
        import json

        canonical = json.dumps(message, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    async def mark_failed(
        session: AsyncSession, *, owner_service: str, dedupe_key: str, error: str
    ) -> None:
        from sqlalchemy import update

        await session.execute(
            update(ReliabilityInbox)
            .where(
                ReliabilityInbox.owner_service == owner_service,
                ReliabilityInbox.dedupe_key == dedupe_key,
            )
            .values(error=error[:2000], status="FAILED", handled_at=datetime.now(UTC))
        )
