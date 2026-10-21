"""体验测试留痕端点测试（trial_router）

覆盖：结构化反馈提交（评分钳制+事件埋点）、使用档案聚合数值正确性、
导出权限负例（student 403）、导出 markdown 含身份标注与脱敏手机号。
需要 PostgreSQL 运行（与 test_student_pipeline 同环境，真实库+函数级 rollback）。
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway.auth import get_current_user
from app.main import app
from app.models.conversation import Conversation
from app.models.database import get_db
from app.models.event import Event
from app.models.message import Message
from app.models.trial import TrialFeedback
from app.models.tutor_session import TutorSession
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


app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _identity(user_id: str, role: str = "student"):
    return {"sub": user_id, "active_role": role}


async def _make_user() -> User:
    async with _test_session_factory() as s:
        u = User(phone=f"138{uuid.uuid4().int % 100000000:08d}", nickname="试用同学")
        s.add(u)
        await s.commit()
        return u


async def _cleanup_user(uid) -> None:
    async with _test_session_factory() as s:
        conv_ids = (
            (await s.execute(select(Conversation.id).where(Conversation.user_id == uid)))
            .scalars()
            .all()
        )
        if conv_ids:
            await s.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
        await s.execute(delete(TutorSession).where(TutorSession.user_id == uid))
        await s.execute(delete(Conversation).where(Conversation.user_id == uid))
        await s.execute(delete(TrialFeedback).where(TrialFeedback.user_id == uid))
        await s.execute(delete(Event).where(Event.user_id == uid))
        await s.commit()


FB_BODY = {
    "name": "张小明",
    "identity_label": "XX中学 高二 学生",
    "school": "XX中学",
    "grade": "高二",
    "ratings": {"usability": 5, "usefulness": 4, "speed": 4, "accuracy": 5, "overall": 5},
    "nps": 9,
    "comment": "引导解题像老师带着走，错题自动进本很省心。",
    "duration_minutes": 42,
    "allow_showcase": True,
}


@pytest.mark.asyncio
async def test_feedback_submit_clamps_and_persists(client):
    u = await _make_user()
    try:
        app.dependency_overrides[get_current_user] = lambda: _identity(str(u.id))
        r = await client.post(
            "/api/trial/feedback",
            json={**FB_BODY, "ratings": {"overall": 99, "usability": 0, "speed": 4}},
        )
        assert r.status_code == 200
        async with _test_session_factory() as s:
            rows = (
                (await s.execute(select(TrialFeedback).where(TrialFeedback.user_id == u.id)))
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].ratings["overall"] == 5  # 99 钳到 5
            assert rows[0].ratings["usability"] == 1  # 0 钳到 1
            assert rows[0].ratings["speed"] == 4
            assert rows[0].identity_label == "XX中学 高二 学生"
            ev = (
                (
                    await s.execute(
                        select(Event).where(
                            Event.user_id == u.id, Event.event == "trial_feedback_submitted"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(ev) == 1
    finally:
        await _cleanup_user(u.id)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_metrics_aggregation_values(client):
    u = await _make_user()
    try:
        async with _test_session_factory() as s:
            conv = Conversation(user_id=u.id, active_role="student", title="t")
            s.add(conv)
            await s.flush()
            s.add(
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="q",
                    client_msg_id=uuid.uuid4().hex,
                )
            )
            s.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="a",
                    client_msg_id=uuid.uuid4().hex,
                )
            )
            s.add(
                TutorSession(
                    user_id=u.id,
                    conversation_id=conv.id,
                    question_text="1+1=?",
                    status="completed",
                    plan={"steps": [], "final_answer": "2", "verified": True},
                )
            )
            await s.commit()
        app.dependency_overrides[get_current_user] = lambda: _identity(str(u.id))
        r = await client.get("/api/trial/metrics")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["conversations"] == 1
        assert data["messages_total"] == 2
        assert data["messages_from_user"] == 1
        assert data["tutor_sessions_by_status"].get("completed") == 1
    finally:
        await _cleanup_user(u.id)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_export_forbidden_for_student(client):
    u = await _make_user()
    try:
        app.dependency_overrides[get_current_user] = lambda: _identity(str(u.id), "student")
        r = await client.get("/api/trial/export")
        assert r.status_code == 403
    finally:
        await _cleanup_user(u.id)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_export_markdown_has_identity_and_masked_phone(client):
    u = await _make_user()
    teacher = await _make_user()
    try:
        async with _test_session_factory() as s:
            s.add(TrialFeedback(user_id=u.id, **dict(FB_BODY.items())))
            await s.commit()
        app.dependency_overrides[get_current_user] = lambda: _identity(str(teacher.id), "teacher")
        r = await client.get("/api/trial/export")
        assert r.status_code == 200
        text = r.text
        assert "学生端体验测试记录" in text
        assert "XX中学 高二 学生" in text
        assert "张小明" in text
        assert u.phone[:3] in text and u.phone[-4:] in text and u.phone not in text  # 脱敏
    finally:
        await _cleanup_user(u.id)
        await _cleanup_user(teacher.id)
        app.dependency_overrides.pop(get_current_user, None)
