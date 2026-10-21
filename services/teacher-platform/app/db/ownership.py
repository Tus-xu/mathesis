"""运行时表所有权清单 + preflight / 行数/hash 对账（R11.5-B 接管）。

R11.5-B 后这些表全部由正式 Alembic 迁移所有（head d17b2c3e4f5a）；
运行时各 sink/store 禁止任何隐式 DDL（生产 fail-closed）。

- OWNED_TABLES：所有权清单（含 checkpoint 4 表与新增正式表），供 preflight 与对账使用。
- preflight_owned_tables：启动/工具用——检测缺失表，`fail_closed=True` 时抛
  RuntimeOwnershipError（生产启动失败门；差异在 missing/mismatched）。
- reconcile_row_hashes：无损对账——对每张表输出 {rows, sha256(全行规范序列)}，
  迁移前后行数与 hash 相等即证明数据无损（验收用）。
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# 所有权清单：正式迁移 d17b2c3e4f5a 接管/新增的表（表名 -> 关键列，供 preflight 校验）
# 注：conversation_messages 未单独建表——Message 由 butler_messages 承载（既有运行时表移交所有权）。
OWNED_TABLES: dict[str, set[str]] = {
    "teacher_session_contexts": {"session_id", "teacher_id", "retrieval_scope_ids", "sticky", "updated_at"},
    "teacher_memories": {"id", "teacher_id", "layer", "content", "content_md5", "source_refs"},
    "teacher_memory_history": {"id", "memory_id", "action", "payload"},
    "butler_messages": {"id", "teacher_id", "session_id", "role", "text"},
    "ai_runs": {"id", "feature", "model", "input_tokens", "output_tokens", "cost", "latency_ms", "created_at"},
    "conversation_threads": {"id", "session_id", "teacher_id", "status"},
    "conversation_summaries": {"id", "session_id", "teacher_id", "summary"},
    "prompt_releases": {"id", "prompt_name", "version", "content_hash", "status"},
    "tool_call_records": {"id", "tool_name", "params_hash", "status", "created_at"},
    "pending_actions": {"id", "action_id", "session_id", "teacher_id", "tool", "status"},
    # LangGraph checkpoint（langgraph-checkpoint-postgres 3.1.2 所有权收编）
    "checkpoint_migrations": {"v"},
    "checkpoints": {"thread_id", "checkpoint_ns", "checkpoint_id", "checkpoint"},
    "checkpoint_blobs": {"thread_id", "checkpoint_ns", "channel", "version"},
    "checkpoint_writes": {"thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"},
    # R11.5-C 可靠性闸门表（迁移 e26a6b7c8d9e 所有）
    "reliability_jobs": {"id", "owner_service", "job_type", "status", "attempt", "revision", "claim_token"},
    "reliability_job_events": {"id", "job_id", "seq", "event_type", "payload"},
    "reliability_outbox": {"id", "topic", "dedupe_key", "payload", "status", "attempt"},
    "reliability_inbox": {"id", "owner_service", "dedupe_key", "payload_hash", "status"},
    "reliability_idempotency": {"id", "owner_service", "operation", "principal", "idempotency_key", "request_hash", "status"},
    # R14 Teacher Prep 会话/蓝图骨架（迁移 16a2b3c4d5e6 所有）
    "tp_sessions": {"id", "teacher_id", "class_id", "status", "revision", "last_event_seq"},
    "tp_blueprints": {"id", "session_id", "status", "revision"},
    "tp_blueprint_revisions": {"id", "blueprint_id", "revision_no", "verdict", "source"},
    "tp_session_commands": {"id", "session_id", "command_type", "idempotency_key", "request_hash", "result_status"},
    "tp_session_events": {"id", "session_id", "seq", "event_type"},
    # T24 编辑/修订/逐页 QA 与产物投递（迁移 r22 所有）
    "ppt_v2_spec_revisions": {"id", "deck_id", "revision", "kind", "spec_sha256"},
    "ppt_v2_edit_proposals": {"id", "deck_id", "status", "expected_revision", "operations"},
    "ppt_v2_page_qa": {"id", "deck_id", "spec_revision", "page_no", "status", "defects"},
    "tp_lesson_plan_revisions": {"id", "spec_id", "revision", "kind", "body_sha256"},
    "tp_lesson_plan_edit_proposals": {"id", "spec_id", "status", "expected_revision", "operations"},
    "teacher_artifacts": {"id", "spec_kind", "spec_id", "kind", "object_key", "content_sha256"},
    "teacher_artifact_tickets": {"id", "ticket", "artifact_id", "actor_id", "expires_at"},
    "teacher_artifact_download_audit": {"id", "ticket", "artifact_id", "decision", "at"},
}

# 清单掩码：重算对账 hash 时排除易变列（如 created_at/updated_at），保证无损语义稳定
_HASH_EXCLUDE: dict[str, set[str]] = {
    "teacher_session_contexts": {"updated_at"},
    "butler_messages": {"created_at"},
    "ai_runs": {"created_at"},
    "teacher_memories": {"created_at"},
    "teacher_memory_history": {"created_at"},
    "conversation_threads": {"created_at", "updated_at", "last_message_at"},
    "conversation_summaries": {"created_at"},
    "prompt_releases": {"created_at"},
    "tool_call_records": {"created_at"},
    "pending_actions": {"created_at", "handled_at"},
    "tp_sessions": {"created_at", "updated_at", "opened_at", "last_command_at"},
    "tp_blueprints": {"created_at", "updated_at", "confirmed_at", "rejected_at"},
    "tp_blueprint_revisions": {"created_at"},
    "tp_session_commands": {"created_at"},
    "tp_session_events": {"created_at"},
}


class RuntimeOwnershipError(RuntimeError):
    """生产启动/运行时发现正式迁移未接管（表缺失）→ fail-closed。"""


class AsyncSessionFactory(Protocol):
    def __call__(self) -> async_sessionmaker[AsyncSession]: ...


async def _table_exists(session: AsyncSession, table: str) -> bool:
    row = await session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :t"
        ),
        {"t": table},
    )
    return row.scalar() is not None


async def _columns_of(session: AsyncSession, table: str) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :t"
        ),
        {"t": table},
    )
    return {r[0] for r in rows}


async def preflight_owned_tables(
    get_session_factory: Any,
    *,
    tables: dict[str, set[str]] | None = None,
    fail_closed: bool = True,
) -> list[dict[str, Any]]:
    """校验所有权清单内表存在且关键列齐备。

    返回逐表报告：[{table, exists, missing_columns, ok}]。
    fail_closed=True（生产）→ 任一缺失抛 RuntimeOwnershipError，启动失败。
    """
    expected = tables or OWNED_TABLES
    report: list[dict[str, Any]] = []
    factory = get_session_factory() if callable(get_session_factory) else get_session_factory
    async with factory() as session:
        for table, required in expected.items():
            exists = await _table_exists(session, table)
            missing_cols: set[str] = set()
            if exists:
                cols = await _columns_of(session, table)
                missing_cols = required - cols
            ok = exists and not missing_cols
            report.append({"table": table, "exists": exists, "missing_columns": sorted(missing_cols), "ok": ok})
    if fail_closed:
        bad = [r for r in report if not r["ok"]]
        if bad:
            detail = "; ".join(
                f"{r['table']}(missing={r['missing_columns'] or 'table'})" for r in bad
            )
            raise RuntimeOwnershipError(f"运行时表未由正式迁移接管（缺失/缺列）: {detail}")
    return report


def row_hash(rows: list[tuple], *, exclude_idx: set[int] | None = None) -> str:
    """行集合规范 hash：逐行 repr 规范序列化（剔除易变列下标）后 sha256。"""
    h = hashlib.sha256()
    for row in rows:
        parts = []
        for i, value in enumerate(row):
            if exclude_idx and i in exclude_idx:
                continue
            parts.append(repr(value))
        h.update("\x1f".join(parts).encode("utf-8"))
    return h.hexdigest()


async def reconcile_row_hashes(
    get_session_factory: Any,
    *,
    tables: list[str] | None = None,
) -> dict[str, dict[str, int | str]]:
    """对账：每张表输出行数与规范 hash（排除易变列），迁移前后相等 → 无损证明。

    返回 {table: {rows: int, sha256: str}}。
    """
    expected = tables or list(OWNED_TABLES)
    out: dict[str, dict[str, int | str]] = {}
    factory = get_session_factory() if callable(get_session_factory) else get_session_factory
    async with factory() as session:
        for table in expected:
            if not await _table_exists(session, table):
                out[table] = {"rows": 0, "sha256": "", "exists": False}
                continue
            cols_rows = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :t ORDER BY ordinal_position"
                ),
                {"t": table},
            )
            col_names = [r[0] for r in cols_rows]
            exclude = _HASH_EXCLUDE.get(table, set())
            exclude_idx = {i for i, c in enumerate(col_names) if c in exclude}
            rows = (await session.execute(text(f'SELECT * FROM "{table}"'))).all()
            out[table] = {
                "rows": len(rows),
                "sha256": row_hash(rows, exclude_idx=exclude_idx),
                "exists": True,
            }
    return out


async def run_preflight_cli(get_session_factory: Any) -> int:
    """CLI 工具：preflight 报告 + 对账 hash 输出（供验收脚本比对前后差异）。"""
    report = await preflight_owned_tables(get_session_factory, fail_closed=False)
    print("=== PREFLIGHT ===")
    for r in report:
        print(f'  {r["table"]}: ok={r["ok"]} exists={r["exists"]} missing={r["missing_columns"]}')
    counts = await reconcile_row_hashes(get_session_factory)
    print("=== RECONCILE (rows + sha256) ===")
    for t, info in counts.items():
        print(f'  {t}: {info.get("rows", 0)} rows sha256={info.get("sha256", "")}')
    missing = [r["table"] for r in report if not r["ok"]]
    return 1 if missing else 0


async def _async_main(get_session_factory: Any) -> None:
    code = await run_preflight_cli(get_session_factory)
    raise SystemExit(code)


def main_cli() -> None:
    """入口：uv run python -m app.db.ownership（需 DATABASE_URL 可用）。"""
    from .session import session_factory as sf

    asyncio.run(_async_main(sf))
