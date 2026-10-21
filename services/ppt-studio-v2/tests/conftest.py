"""8200 测试公共夹具：SQLite 内存库 + session factory（含表结构与依赖就绪）。"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("PRESENTON_SERVICE_TOKEN", "test-presenton-token")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.persistence import models as _models  # noqa: F401  注册全部模型


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine) -> async_sessionmaker:  # noqa: ANN001
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:  # noqa: ANN001
    async with session_factory() as s:
        yield s


class FakePresenton:
    """可开关的 8201 fake：alive=False → probe 抛 PresentonUnavailable（transient）。"""

    def __init__(self, ok: bool = True):
        self.alive = ok
        self.calls = 0

    async def probe(self) -> None:
        self.calls += 1
        if not self.alive:
            from app.reliability.presenton_client import PresentonUnavailable

            raise PresentonUnavailable("fake presenton(8201) down")
