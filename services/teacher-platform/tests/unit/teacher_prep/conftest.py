"""R14 teacher_prep 单测共享基建：SQLite 引擎 + 文件库（重启语义）+ 8000 成员模拟。"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

import app.db.models  # noqa: F401 —— 注册全部 ORM 模型（含 tp_* 与 reliability_*）到 Base.metadata
from app.db.base import Base
from app.teacher_prep.service import TeacherPrepService
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry


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
    """文件 SQLite：多 session_factory（模拟 API/worker 重启共享同一持久化文件）。"""
    path = str(tmp_path / "r14-test.db").replace("\\", "/")  # Windows URL 兼容
    url = f"sqlite+aiosqlite:///{path}"
    await _create_all(url)

    def _factory() -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(
            create_async_engine(url, poolclass=NullPool),
            expire_on_commit=False,
        )

    return _factory


# ── 8000 成员关系模拟 ────────────────────────────────────────────────────────


class MembershipSim:
    """按 class_id 模拟 8000 ClassMember：默认 teacher 成员；可动态剔除/换角色/5xx。"""

    def __init__(self) -> None:
        self._members: dict[str, dict] = {}
        self._fail_5xx: set[str] = set()
        self.calls: list[str] = []

    def set(self, class_id: str, *, user_id: str = "t-1", role: str = "teacher", confirmed: bool = True) -> None:
        self._members[class_id] = {
            "class_id": class_id,
            "user_id": user_id,
            "member_role": role,
            "confirmed": confirmed,
        }

    def remove(self, class_id: str) -> None:
        self._members.pop(class_id, None)

    def set_5xx(self, class_id: str) -> None:
        self._fail_5xx.add(class_id)

    def handler(self, method: str, path: str, token: str, json_body: dict | None):
        if path.startswith("/api/classes/"):
            self.calls.append(path)
            cid = path.split("/")[-1]
            if cid in self._fail_5xx:
                return 500, {"detail": "boom"}
            member = self._members.get(cid)
            if member is None:
                return 404, {"detail": "member not found"}
            if member.get("confirmed") is False:
                return 403, {"detail": "unconfirmed"}
            return 200, member
        return 404, {"detail": "unknown path"}


@pytest_asyncio.fixture
def members() -> MembershipSim:
    return MembershipSim()


@pytest_asyncio.fixture
def adapter(members: MembershipSim) -> Api8000Adapter:
    transport = FakeApiTransport()
    transport.handler = members.handler
    return Api8000Adapter(transport, InMemoryMappingRegistry())


@pytest_asyncio.fixture
def service() -> TeacherPrepService:
    return TeacherPrepService()


# ── 常用常量 ─────────────────────────────────────────────────────────────────

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
CLASS_B = "33333333-3333-3333-3333-333333333333"
TOKEN = "jwt-teacher-token"


def idem(n: int) -> str:
    return f"r14-key-{n:04d}"
