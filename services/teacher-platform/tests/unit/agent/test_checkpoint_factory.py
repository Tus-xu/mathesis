"""Checkpointer 工厂与 DSN 转换单测（无 PG 路径）。"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.agent.checkpoint import ManagedCheckpointer, sqlalchemy_to_psycopg_dsn


def test_dsn_conversion() -> None:
    assert (
        sqlalchemy_to_psycopg_dsn("postgresql+asyncpg://teacher:pw@localhost:5433/teacher_platform")
        == "postgresql://teacher:pw@localhost:5433/teacher_platform"
    )
    assert sqlalchemy_to_psycopg_dsn("postgresql://x") == "postgresql://x"


async def test_no_dsn_falls_back_to_memory_saver() -> None:
    handle = ManagedCheckpointer(None)
    saver = await handle.start()
    assert isinstance(saver, MemorySaver)
    await handle.close()
