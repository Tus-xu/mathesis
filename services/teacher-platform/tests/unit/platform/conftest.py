"""契约壳单测的 hermetic 环境：内存 SQLite + 依赖覆盖（不依赖 Docker/PG）。

- `get_session` 依赖 → SQLite 内存引擎（StaticPool 单连接共享）；
- 覆盖点即 `app.core.deps.get_session`（platform.py 从该处 import，同一函数对象）。
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Classes, Users
from app.db.session import get_session

_test_engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
_test_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
async def _setup_db():
    # 建表 + 张老师（与 seed 一致的 id）
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.core.security import SEED_TEACHER

    import uuid

    async with _test_factory() as session:
        session.add(
            Users(
                id=uuid.UUID(SEED_TEACHER["id"]),
                name=SEED_TEACHER["name"],
                role=SEED_TEACHER["role"],
                school_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            )
        )
        await session.commit()
    yield
    await _test_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _override_db():
    import main

    async def fake_get_session():
        async with _test_factory() as session:
            yield session

    main.app.dependency_overrides[get_session] = fake_get_session
    yield
    main.app.dependency_overrides.pop(get_session, None)
