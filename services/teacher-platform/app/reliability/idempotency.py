"""REST 幂等（formal 五元键 + request_hash；R11.5-C P0）。

规格（任务书）：
- 幂等范围：owner_service + operation + principal + idempotency_key + request_hash；
- 同 key + 同 request_hash → 窗口内重放首响应（不重复副作用）；
- 同 key + 不同 request_hash → 409 CONFLICT（绝不把 A 请求的响应重放给 B 请求）；
- 并发同 key：唯一约束 + IN_PROGRESS 语义 → 次者等待首者落定（≤ window）后重放；
  IN_PROGRESS 超时（首者崩溃）→ 接管执行（底层唯一约束仍防双写）。

使用方（R12 端点）：`reserve() → 执行业务（同 session）→ complete() 同一事务提交`。
测试注入时可用任意 session 绑定业务行，保证「响应缓存与业务状态同事务」。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.errors import ApiError, ErrorCode
from .models import ReliabilityIdempotency as IdemRow
from .trace import get_current

__all__ = ["IdempotencyConflict", "RESERVED", "COMPLETED", "ReliabilityIdempotency", "request_hash_of"]

RESERVED = "IN_PROGRESS"
COMPLETED = "COMPLETED"
_ROLLBACK_WINDOW_SLACK = 60  # reserve 后允许完成的缓冲（秒），与窗口语义解耦


class IdempotencyConflict(ApiError):
    """同 key 不同 payload：HTTP 409。"""

    def __init__(self, message: str = "幂等键被其他请求占用（request_hash 不一致）"):
        super().__init__(ErrorCode.CONFLICT, message)


def request_hash_of(method: str, path: str, body: dict | list | None) -> str:
    """规范请求指纹：method + path + 规范化 body。list 与 dict / None 均支持。"""
    import hashlib
    import json

    canonical = json.dumps(
        {"method": method, "path": path, "body": body}, ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReliabilityIdempotency:
    @staticmethod
    def _window_seconds() -> int:
        return max(60, int(get_settings().idempotency_window_seconds))

    @staticmethod
    async def reserve(
        session: AsyncSession,
        *,
        owner_service: str,
        operation: str,
        principal: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[bool, ReliabilityIdempotency | None]:
        """尝试预占幂等键。

        返回 (reserved=True, None) → 调用方拥有该键，应继续执行并 complete()；
        返回 (reserved=False, record) → 键已存在：
          - COMPLETED 且 hash 一致 → callers 直接重放 record.response；
          - COMPLETED 且 hash 不一致 → 抛 IdempotencyConflict（409）；
          - IN_PROGRESS → 并发在途：调用方应短轮询等待首者提交（≤ window），超限接管。
        """
        trace = get_current()
        row = IdemRow(
            owner_service=owner_service,
            operation=operation,
            principal=principal,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=RESERVED,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=ReliabilityIdempotency._window_seconds()),
            trace_id=trace.trace_id if trace else None,
        )
        # savepoint 包裹插入：IntegrityError 只回滚 savepoint，不破坏调用方事务内的既有写入
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            existing = await ReliabilityIdempotency._get(
                session, owner_service, operation, principal, idempotency_key
            )
            return False, ReliabilityIdempotency._judge(existing, request_hash)
        return True, None

    @staticmethod
    async def complete(
        session: AsyncSession,
        *,
        owner_service: str,
        operation: str,
        principal: str,
        idempotency_key: str,
        response: dict | None,
    ) -> None:
        """业务成功 → 在调用方事务内落响应并置 COMPLETED（与业务状态同事务提交）。"""
        row = await ReliabilityIdempotency._get(
            session, owner_service, operation, principal, idempotency_key
        )
        if row is None:  # 理论上仅当 reserve 后行被删（不放行）；防御性跳过
            return
        row.status = COMPLETED
        row.response = response
        row.updated_at = datetime.now(UTC)  # onupdate 兜底（SQLite 兼容显式赋值）

    @staticmethod
    async def fail(
        session: AsyncSession,
        *,
        owner_service: str,
        operation: str,
        principal: str,
        idempotency_key: str,
    ) -> None:
        """业务失败 → 释放键（下次同 key 可重试，而不是永远 409/重放错误）。"""
        row = await ReliabilityIdempotency._get(
            session, owner_service, operation, principal, idempotency_key
        )
        if row is not None and row.status == RESERVED:
            await session.delete(row)

    @staticmethod
    async def _get(
        session: AsyncSession,
        owner_service: str,
        operation: str,
        principal: str,
        idempotency_key: str,
    ) -> IdemRow | None:
        return (
            await session.execute(
                select(IdemRow).where(
                    IdemRow.owner_service == owner_service,
                    IdemRow.operation == operation,
                    IdemRow.principal == principal,
                    IdemRow.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _judge(
        existing: IdemRow | None,
        request_hash: str,
    ) -> IdemRow:
        if existing is None:  # pragma: no cover —— 唯一约束后必有行
            raise RuntimeError("reliability idempotency unique constraint invariant broken")
        if existing.request_hash != request_hash:
            raise IdempotencyConflict(
                f"幂等键冲突（{existing.owner_service}/{existing.operation} "
                f"principal={existing.principal} key={existing.idempotency_key}）: request_hash 不一致"
            )
        return existing
