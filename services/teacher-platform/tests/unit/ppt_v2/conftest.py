"""T24 单测共享基建：SQLite async 库（含 T24 新表）+ 会话工厂。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401 —— 注册全部 ORM 模型（含 T24 新表）到 Base.metadata
from app.db.base import Base

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest_asyncio.fixture
async def engine():
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
def session_factory_of(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory_of) -> AsyncSession:
    async with session_factory_of() as s:
        yield s


def golden_spec() -> dict:
    return json.loads((_FIXTURES / "deck_golden_a.json").read_text(encoding="utf-8"))
