"""T25 P0-1/P0-2 回归锁：父子行 UoW 顺序在外键强约束下必须正确。

T25 在 PG 运行态实测：TeachingSessionRow↔SessionCommandRow、TeachingBlueprintRow↔
BlueprintRevisionRow 之间没有 ORM relationship，UoW 无法自行排序，把子表 INSERT 排在
父表之前 → create_session / propose_blueprint / 新建 conversation 全部 500（FK violation）。
单测共享 conftest 的 SQLite 未开外键 pragma，遮蔽了该缺陷；本模块用
PRAGMA foreign_keys=ON 的私有引擎锁住修复（_log_command 前置 flush +
propose_blueprint 新建路径显式先落父行）。
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401 —— 注册全部 ORM 模型到 Base.metadata
from app.db.base import Base
from app.reliability.idempotency import request_hash_of
from app.teacher_prep.models import BlueprintRevisionRow, SessionCommandRow, TeachingSessionRow
from app.teacher_prep.service import TeacherPrepService

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
TOKEN = "jwt-teacher-token"

RH = lambda body: request_hash_of("POST", "/p", body)  # noqa: E731


@pytest_asyncio.fixture
async def fk_db():
    """外键强约束的内存 SQLite：每个连接开 PRAGMA foreign_keys=ON。"""
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from sqlalchemy import event

    @event.listens_for(eng.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s:
        yield s
    await eng.dispose()


@pytest.fixture
def service() -> TeacherPrepService:
    return TeacherPrepService()


async def test_fk_pragma_is_actually_enforced(fk_db) -> None:
    """守卫测试：本模块的引擎必须真的在拦外键（否则其余测试形同虚设）。"""
    assert (await fk_db.execute(text("PRAGMA foreign_keys"))).scalar() == 1
    orphan = SessionCommandRow(
        session_id=uuid.uuid4(),
        command_type="create_session",
        actor_id=uuid.UUID(TEACHER),
        actor_role="teacher",
        class_id=uuid.UUID(CLASS_A),
        idempotency_key="orphan-key",
        request_hash="rh",
        result_status="applied",
    )
    fk_db.add(orphan)
    with pytest.raises(IntegrityError):
        await fk_db.flush()


async def test_create_session_persists_parent_before_command_fk_enforced(
    fk_db, service, adapter, members
) -> None:
    """P0-1 回归：新建 TeachingSession 同事务落父子行，外键强约束下不得乱序。"""
    members.set(CLASS_A, user_id=TEACHER)
    result = await service.create_session(
        fk_db,
        adapter=adapter,
        token=TOKEN,
        actor_id=TEACHER,
        class_id=CLASS_A,
        idempotency_key="fk-order-sess-1",
        request_hash=RH({"title": "备课"}),
        title="备课",
    )
    await fk_db.commit()

    sid = uuid.UUID(result["session_id"])
    assert (await fk_db.execute(select(TeachingSessionRow).where(TeachingSessionRow.id == sid))).scalar_one()
    cmds = (
        (await fk_db.execute(select(SessionCommandRow).where(SessionCommandRow.session_id == sid)))
        .scalars()
        .all()
    )
    assert len(cmds) == 1 and cmds[0].result_status == "applied"


async def test_propose_new_blueprint_persists_parent_before_revision_fk_enforced(
    fk_db, service, adapter, members
) -> None:
    """P0-2 回归：新建蓝图同事务落 tp_blueprints + tp_blueprint_revisions，不得乱序。"""
    members.set(CLASS_A, user_id=TEACHER)
    created = await service.create_session(
        fk_db,
        adapter=adapter,
        token=TOKEN,
        actor_id=TEACHER,
        class_id=CLASS_A,
        idempotency_key="fk-order-bp-0",
        request_hash=RH({"title": "备课"}),
        title="备课",
    )
    proposed = await service.propose_blueprint(
        fk_db,
        adapter=adapter,
        token=TOKEN,
        actor_id=TEACHER,
        session_id=created["session_id"],
        idempotency_key="fk-order-bp-1",
        request_hash=RH({"body": {"objective": "o"}}),
        body={"objective": "理解二次函数最值"},
        title="蓝图",
        source="ai_suggestion",
    )
    await fk_db.commit()

    bid = uuid.UUID(proposed["blueprint_id"])
    assert (await fk_db.execute(select(TeachingSessionRow).where(TeachingSessionRow.id == uuid.UUID(created["session_id"])))).scalar_one()
    assert (await fk_db.execute(select(BlueprintRevisionRow).where(BlueprintRevisionRow.blueprint_id == bid))).scalar_one()
