"""PendingAction 持久化 store（取代进程内 `_pending_actions`，DECISIONS §5 / 迁移 d17b2c3e4f5a）。

- 表 `pending_actions` 由正式 Alembic 迁移所有；PG store 不建表、不静默降级（fail-closed）。
- 语义：PENDING → (APPROVED|REJECTED) | EXPIRED；确认/拒绝/过期均落 handled_at。
- 过期策略：expires_at 写入时设定（默认 15 分钟）；读取时对过期项顺手置 EXPIRED（惰性清扫）。
- 执行瞬再次鉴权由调用方保证（router 在 confirm 时校验当前教师身份/角色）。
- InMemoryPendingStore 仅供 local/test。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text

from app.db.ownership import RuntimeOwnershipError

DEFAULT_TTL_SECONDS = 15 * 60

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"


@dataclass
class PendingAction:
    action_id: str
    session_id: str
    teacher_id: str
    tool: str
    card: dict[str, Any]
    status: str = PENDING
    approved: bool | None = None
    params_overrides: dict[str, Any] | None = None
    fingerprint: str | None = None
    expires_at: float | None = None  # unix ts
    created_at: float = field(default_factory=time.time)
    handled_at: float | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "teacher_id": self.teacher_id,
            "tool": self.tool,
            "card": self.card,
            "status": self.status,
            "approved": self.approved,
            "params_overrides": self.params_overrides,
            "fingerprint": self.fingerprint,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "handled_at": self.handled_at,
        }


class PendingActionStore(Protocol):
    async def create(self, action: PendingAction) -> None: ...
    async def get(self, action_id: str) -> PendingAction | None: ...
    async def sweep_expired(self) -> None: ...
    async def list_by_teacher(self, teacher_id: str, *, include_handled: bool = False) -> list[PendingAction]: ...
    async def confirm(self, action_id: str, *, approved: bool, params: dict[str, Any] | None, by: str) -> PendingAction | None: ...
    async def mark_expired(self, action_id: str) -> PendingAction | None: ...


class InMemoryPendingStore:
    """local/test：行留进程内；过期仍按时间判定（便于测试重启前状态）。"""

    def __init__(self) -> None:
        self._rows: dict[str, PendingAction] = {}

    async def create(self, action: PendingAction) -> None:
        self._rows[action.action_id] = action

    async def get(self, action_id: str) -> PendingAction | None:
        action = self._rows.get(action_id)
        if action is not None and action.is_expired and action.status == PENDING:
            await self.mark_expired(action_id)
        return self._rows.get(action_id)

    async def sweep_expired(self) -> None:
        for a in list(self._rows.values()):
            if a.status == PENDING and a.is_expired:
                await self.mark_expired(a.action_id)

    async def list_by_teacher(self, teacher_id: str, *, include_handled: bool = False) -> list[PendingAction]:
        await self.sweep_expired()
        return [
            a for a in self._rows.values()
            if a.teacher_id == teacher_id and (include_handled or a.status == PENDING)
        ]

    async def confirm(self, action_id: str, *, approved: bool, params: dict[str, Any] | None, by: str) -> PendingAction | None:
        action = self._rows.get(action_id)
        if action is None or action.status != PENDING:
            return None
        if action.is_expired:
            await self.mark_expired(action_id)
            return None
        action.status = APPROVED if approved else REJECTED
        action.approved = approved
        action.params_overrides = params
        action.handled_at = time.time()
        return action

    async def mark_expired(self, action_id: str) -> PendingAction | None:
        action = self._rows.get(action_id)
        if action is None:
            return None
        if action.status == PENDING:
            action.status = EXPIRED
            action.handled_at = time.time()
            return action
        return None  # 已处理项不允许再次变更，与 PG 实现 RETURNING 语义一致


class PostgresPendingActionStore:
    """PG 实现：来自正式迁移表 pending_actions；不执行 DDL（fail-closed）。"""

    def __init__(self, session_factory) -> None:  # async_sessionmaker[AsyncSession]
        self._session_factory = session_factory

    async def _ensure_owned(self, session) -> None:
        row = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'pending_actions'"
            )
        )
        if row.scalar() is None:
            raise RuntimeOwnershipError(
                "pending_actions 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a）"
            )

    async def create(self, action: PendingAction) -> None:
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            await session.execute(
                text(
                    "INSERT INTO pending_actions (id, action_id, session_id, teacher_id, tool, card, status,"
                    " approved, params_overrides, fingerprint, expires_at, created_at, handled_at)"
                    "VALUES (:id, :action_id, :session_id, :teacher_id, :tool, :card, :status,"
                    " :approved, :params, :fingerprint, to_timestamp(:expires), to_timestamp(:created), to_timestamp(:handled))"
                ),
                {
                    "id": uuid.UUID(action.id),
                    "action_id": action.action_id,
                    "session_id": action.session_id,
                    "teacher_id": uuid.UUID(action.teacher_id),
                    "tool": action.tool,
                    "card": json.dumps(action.card, ensure_ascii=False),
                    "status": action.status,
                    "approved": action.approved,
                    "params": json.dumps(action.params_overrides, ensure_ascii=False) if action.params_overrides else None,
                    "fingerprint": action.fingerprint,
                    "expires": action.expires_at,
                    "created": action.created_at,
                    "handled": action.handled_at,
                },
            )
            await session.commit()

    @staticmethod
    def _row_to_action(data: Any) -> PendingAction:
        return PendingAction(
            id=str(data["id"]),
            action_id=data["action_id"],
            session_id=data["session_id"],
            teacher_id=str(data["teacher_id"]),
            tool=data["tool"],
            card=data["card"] if isinstance(data["card"], dict) else json.loads(data["card"] or "{}"),
            status=data["status"],
            approved=data["approved"],
            params_overrides=data["params_overrides"] if isinstance(data["params_overrides"], dict) else (
                json.loads(data["params_overrides"]) if data["params_overrides"] else None
            ),
            fingerprint=data["fingerprint"],
            expires_at=time.mktime(data["expires_at"].timetuple()) if data["expires_at"] else None,
            created_at=data["created_at"].timestamp() if data["created_at"] else time.time(),
            handled_at=time.mktime(data["handled_at"].timetuple()) if data["handled_at"] else None,
        )

    async def _fetch(self, action_id: str):
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM pending_actions WHERE action_id = :a ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"a": action_id},
                )
            )
            return row.mappings().first()

    async def get(self, action_id: str) -> PendingAction | None:
        data = await self._fetch(action_id)
        if data is None:
            return None
        action = self._row_to_action(data)
        if action.is_expired and action.status == PENDING:
            await self.mark_expired(action_id)
        return await self._reget(action_id)

    async def _reget(self, action_id: str) -> PendingAction | None:
        data = await self._fetch(action_id)
        return self._row_to_action(data) if data else None

    async def sweep_expired(self) -> None:
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            await session.execute(
                text(
                    "UPDATE pending_actions SET status = 'EXPIRED', handled_at = now()"
                    " WHERE status = 'PENDING' AND expires_at IS NOT NULL AND expires_at < now()"
                )
            )
            await session.commit()

    async def list_by_teacher(self, teacher_id: str, *, include_handled: bool = False) -> list[PendingAction]:
        await self.sweep_expired()
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM pending_actions WHERE teacher_id = :t"
                        " AND (:inc IS TRUE OR status = 'PENDING') ORDER BY created_at DESC"
                    ),
                    {"t": uuid.UUID(teacher_id), "inc": include_handled},
                )
            )
            return [self._row_to_action(r) for r in rows.mappings().all()]

    async def confirm(self, action_id: str, *, approved: bool, params: dict[str, Any] | None, by: str) -> PendingAction | None:
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            result = await session.execute(
                text(
                    "UPDATE pending_actions SET status = CASE WHEN :approved THEN 'APPROVED' ELSE 'REJECTED' END,"
                    " approved = :approved, params_overrides = :params, handled_at = now()"
                    " WHERE action_id = :a AND status = 'PENDING'"
                    " AND (expires_at IS NULL OR expires_at >= now()) RETURNING *"
                ),
                {
                    "approved": approved,
                    "params": json.dumps(params, ensure_ascii=False) if params else None,
                    "a": action_id,
                },
            )
            row = result.mappings().first()
            await session.commit()
        return self._row_to_action(row) if row else None

    async def mark_expired(self, action_id: str) -> PendingAction | None:
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            result = await session.execute(
                text(
                    "UPDATE pending_actions SET status = 'EXPIRED', handled_at = now()"
                    " WHERE action_id = :a AND status = 'PENDING' RETURNING *"
                ),
                {"a": action_id},
            )
            row = result.mappings().first()
            await session.commit()
        return self._row_to_action(row) if row else None