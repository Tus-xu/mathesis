"""R11.5-C reliability 单测共享基建：SQLite async 引擎 + session_factory + 文件库（重启语义）。"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

import app.db.models  # noqa: F401 —— 注册全部 ORM 模型（含 reliability_*）到 Base.metadata
from app.db.base import Base


async def _create_all(url: str) -> None:
    eng = create_async_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def engine():
    """内存 SQLite：单连接（StaticPool）保共享；每测试全新库（隔离）。"""
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncSession:
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def file_db(tmp_path):
    """文件 SQLite：多 session_factory（模拟 API/worker 重启共享同一持久化文件）。

    用法：`factory = file_db()` 每次返回全新 session_factory（等价"重启后新进程连接"）。
    """
    path = str(tmp_path / "reliability-test.db").replace("\\", "/")  # Windows URL 兼容
    url = f"sqlite+aiosqlite:///{path}"
    await _create_all(url)

    def _factory() -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(
            create_async_engine(url, poolclass=NullPool),
            expire_on_commit=False,
        )

    return _factory
