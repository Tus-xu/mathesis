"""错题去重测试（任务4）。

覆盖：
1. 手工收录同题两次 → 单条记录、created=false、wrong_count 累加；
2. 学习事件总线跨日重复答错 → 不建新行（全时段去重，比原"同日去重"更强）；
3. 练习自动收录 _auto_record_error 重复调用 → 单条记录；
4. 去重脚本：预置重复行 → 合并（保留 wrong_count 最大者、聚合值正确、其余软删）。

需要 PostgreSQL 运行中（与 test_student_pipeline 同环境）。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.coursework import ErrorRecord
from app.models.database import get_db
from app.models.role_binding import RoleBinding
from app.models.user import User

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_user(session) -> User:
    user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="去重测试")
    session.add(user)
    await session.flush()
    session.add(RoleBinding(user_id=user.id, role="student", verified=True))
    await session.commit()  # API 请求走独立会话，用户必须先落库（FK）
    return user


def _auth_headers(user: User) -> dict:
    from app.gateway.jwt import create_token_with_role

    token = create_token_with_role(
        user_id=str(user.id), role="student", roles=["student"], verified=True
    )
    return {"Authorization": f"Bearer {token}"}


async def _active_records(session, user: User) -> list[ErrorRecord]:
    rs = await session.execute(
        select(ErrorRecord).where(
            ErrorRecord.user_id == user.id, ErrorRecord.deleted_at.is_(None)
        )
    )
    return list(rs.scalars().all())


class TestManualDedup:
    async def test_same_question_twice_single_record(self, client):
        async with _test_session_factory() as session:
            user = await _make_user(session)
            headers = _auth_headers(user)
            body = {
                "question_text": "已知 f(x)=x^2-2x，求 f(3)",
                "answer_text": "9",
                "source_channel": "manual_photo",
                "error_type": "calculation",
            }
            r1 = await client.post("/api/student/error-records", json=body, headers=headers)
            r2 = await client.post("/api/student/error-records", json=body, headers=headers)
            assert r1.status_code == 200 and r2.status_code == 200
            d1, d2 = r1.json()["data"], r2.json()["data"]
            assert d1["created"] is True
            assert d2["created"] is False
            assert d2["record_id"] == d1["record_id"]
            records = await _active_records(session, user)
            assert len(records) == 1
            assert records[0].wrong_count == 2  # 1 默认 + 1 累加

    async def test_whitespace_variant_same_record(self, client):
        async with _test_session_factory() as session:
            user = await _make_user(session)
            headers = _auth_headers(user)
            b1 = {**{"question_text": " 解方程 x^2=4 ", "answer_text": "x=±2", "source_channel": "chat_command"}}
            b2 = {**{"question_text": "解方程 x^2=4", "answer_text": "", "source_channel": "chat_command"}}
            await client.post("/api/student/error-records", json=b1, headers=headers)
            r2 = await client.post("/api/student/error-records", json=b2, headers=headers)
            assert r2.json()["data"]["created"] is False
            records = await _active_records(session, user)
            assert len(records) == 1


class TestLearningEventDedup:
    async def test_cross_day_duplicate_no_new_row(self, client):
        async with _test_session_factory() as session:
            user = await _make_user(session)
            # 预置一条"昨天"的错题（旧去重逻辑只防同日，跨日会重复建行）
            yesterday = datetime.now(UTC) - timedelta(days=1)
            old = ErrorRecord(
                user_id=user.id,
                question_text="求 sin(pi/6) 的值",
                answer_text="1/2",
                source_channel="auto_judge",
                created_at=yesterday,
            )
            session.add(old)
            await session.commit()

            headers = _auth_headers(user)
            resp = await client.post(
                "/api/student/learning-events",
                json={
                    "kind": "quiz_judge",
                    "question_text": "求 sin(pi/6) 的值",
                    "answer": "1/2",
                    "chosen": "0",
                    "correct": False,
                    "source": "chat_quiz",
                },
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["error_recorded"] is False  # 命中去重，不新建
            assert data["record_id"] == str(old.id)
            records = await _active_records(session, user)
            assert len(records) == 1
            await session.refresh(old)
            assert old.wrong_count == 2


class TestAutoRecordDedup:
    async def test_auto_record_error_twice_single_row(self, client):
        from app.gateway.student_router import _auto_record_error

        async with _test_session_factory() as session:
            user = await _make_user(session)
            item = {
                "question_text": "已知 a=2,b=3，求 a+b",
                "answer_text": "4",
                "image": [],
            }
            await _auto_record_error(session, user.id, item, None)
            await _auto_record_error(session, user.id, item, None)
            await session.commit()
            records = await _active_records(session, user)
            assert len(records) == 1
            assert records[0].wrong_count == 2


class TestDedupScript:
    async def test_script_merges_and_soft_deletes(self, client):
        """预置 3 条同题干重复 → 脚本合并：保留 wrong_count 最大者、聚合值正确、其余软删"""
        from scripts.dedup_error_records import main as dedup_main

        async with _test_session_factory() as session:
            user = await _make_user(session)
            base = {
                "user_id": user.id,
                "question_text": "合并测试题：求 1+1",
                "source_channel": "auto_judge",
            }
            r1 = ErrorRecord(**base, wrong_count=5, review_count=1, answer_text="2")
            r2 = ErrorRecord(**base, wrong_count=2, review_count=3, error_type="calculation", answer_text="3")
            r3 = ErrorRecord(**base, wrong_count=1, review_count=0, kp_code="NUM-01", note="易错")
            session.add_all([r1, r2, r3])
            await session.flush()
            keeper_id = r1.id  # wrong_count 最大者
            await session.commit()

            await dedup_main(["--dry-run"])
            await dedup_main([])  # 实际执行

            async with _test_session_factory() as s2:
                rs = await s2.execute(
                    select(ErrorRecord).where(ErrorRecord.user_id == user.id)
                )
                rows = list(rs.scalars().all())
                assert len(rows) == 3
                active = [r for r in rows if r.deleted_at is None]
                assert len(active) == 1
                keeper = active[0]
                assert keeper.id == keeper_id
                assert keeper.wrong_count == 8  # 5+2+1
                assert keeper.review_count == 4  # 1+3+0
                assert keeper.error_type == "calculation"  # 最新非空回填
                assert keeper.kp_code == "NUM-01"
                assert keeper.note == "易错"
