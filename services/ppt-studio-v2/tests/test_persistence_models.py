"""迁移/模型证据：6 张 8200 独有表、关键列、约束、message_id。"""
from __future__ import annotations

from sqlalchemy import inspect

from app.persistence.models import (
    PptV2AssetRecord,
    PptV2DeckSpecRecord,
    PptV2Inbox,
    PptV2Job,
    PptV2JobEvent,
    PptV2Outbox,
)

EXPECTED_TABLES = {
    PptV2DeckSpecRecord.__tablename__,
    PptV2AssetRecord.__tablename__,
    PptV2Job.__tablename__,
    PptV2JobEvent.__tablename__,
    PptV2Outbox.__tablename__,
    PptV2Inbox.__tablename__,
}


async def test_all_tables_present(engine) -> None:  # noqa: ANN001
    async def _check(sync_conn) -> None:
        names = set(inspect(sync_conn).get_table_names())
        missing = EXPECTED_TABLES - names
        assert not missing, f"缺表: {missing}"
        assert "reliability_jobs" not in names  # 不得与 8100 共享表名
        assert "tp_sessions" not in names

    async with engine.connect() as conn:
        await conn.run_sync(_check)


async def test_job_key_columns(engine) -> None:  # noqa: ANN001
    async def _check(sync_conn) -> None:
        cols = {c["name"] for c in inspect(sync_conn).get_columns("ppt_v2_jobs")}
        for need in ("claim_token", "claim_expires_at", "deadline_at", "cancel_requested", "available_at", "revision"):
            assert need in cols

    async with engine.connect() as conn:
        await conn.run_sync(_check)


async def test_outbox_has_message_id(engine) -> None:  # noqa: ANN001
    async def _check(sync_conn) -> None:
        cols = {c["name"] for c in inspect(sync_conn).get_columns("ppt_v2_outbox")}
        assert "message_id" in cols

    async with engine.connect() as conn:
        await conn.run_sync(_check)


def test_single_migration_root() -> None:
    """独立链：有且仅有一个 down_revision=None 的版本文件（单一 head 的静态依据）。"""
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    files = sorted(versions.glob("*.py"))
    roots = [f for f in files if "down_revision = None" in f.read_text(encoding="utf-8")]
    assert len(roots) == 1, f"迁移根不唯一: {roots}"
