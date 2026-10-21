"""ToolCallRecord 持久化 store（R11.5-B：工具调用可审计台账，迁移 d17b2c3e4f5a）。

- 表 `tool_call_records` 由正式 Alembic 迁移所有；PG store 不建表、不静默降级（fail-closed）。
- 每次 invoke_tool 记录：tool/kind、params_hash（脱敏）、params_redacted（敏感字段打码）、
  status(ok/error/denied/param_invalid)、latency_ms、错误（redact_error 截断打码）。
- ONLY 记录跑法：inject 到 ToolRegistry（run_id 链到 ai_runs）。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text

from app.db.ownership import RuntimeOwnershipError


@dataclass
class ToolCallRecord:
    tool_name: str
    params_hash: str
    status: str
    tool_kind: str | None = None
    session_id: str | None = None
    teacher_id: str | None = None
    run_id: str | None = None
    params_redacted: dict[str, Any] | None = None
    latency_ms: int | None = None
    error: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)


class ToolCallSink(Protocol):
    async def write(self, record: ToolCallRecord) -> None: ...


class MemoryToolCallSink:
    """local/test 用。"""

    def __init__(self) -> None:
        self.records: list[ToolCallRecord] = []

    async def write(self, record: ToolCallRecord) -> None:
        self.records.append(record)


class PostgresToolCallSink:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def _ensure_owned(self, session) -> None:
        row = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'tool_call_records'"
            )
        )
        if row.scalar() is None:
            raise RuntimeOwnershipError(
                "tool_call_records 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a）"
            )

    async def write(self, record: ToolCallRecord) -> None:
        from app.llm.ai_runs import redact_error

        async with self._session_factory() as session:
            await self._ensure_owned(session)
            await session.execute(
                text(
                    "INSERT INTO tool_call_records (id, run_id, session_id, teacher_id, tool_name, tool_kind,"
                    " params_hash, params_redacted, status, latency_ms, error, created_at)"
                    " VALUES (:id, :run_id, :session_id, :teacher_id, :tool, :kind,"
                    " :ph, :pr, :status, :latency, :err, to_timestamp(:ts))"
                ),
                {
                    "id": uuid.UUID(record.id),
                    "run_id": uuid.UUID(record.run_id) if record.run_id else None,
                    "session_id": record.session_id,
                    "teacher_id": uuid.UUID(record.teacher_id) if record.teacher_id else None,
                    "tool": record.tool_name,
                    "kind": record.tool_kind,
                    "ph": record.params_hash,
                    "pr": json.dumps(record.params_redacted, ensure_ascii=False) if record.params_redacted else None,
                    "status": record.status,
                    "latency": record.latency_ms,
                    "err": redact_error(record.error),
                    "ts": record.created_at,
                },
            )
            await session.commit()