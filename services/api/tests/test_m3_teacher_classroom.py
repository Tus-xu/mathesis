"""M3 教师端：课堂模式 + 视频洞察（§14）。"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.database import async_session_factory
from tests._m3_helpers import add_member, make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.asyncio
async def test_classroom_mode_enable_and_audit(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    tok = token(tid, "teacher")
    r = await client.post(f"/api/teacher/classes/{cid}/classroom-mode",
                          json={"enabled": True, "lesson_id": None, "duration_minutes": 45,
                                "client_request_id": "cm", "idempotency_key": "cmk"},
                          headers=_auth(tok))
    assert r.json()["code"] == 0
    assert r.json()["data"]["enabled"] is True
    # 审计写入
    async with async_session_factory() as db:
        from sqlalchemy import select

        from app.models.teacher import TeacherAction

        acts = (await db.execute(select(TeacherAction))).scalars().all()
    assert any(a.action_type == "classroom.mode.set" for a in acts)


@pytest.mark.asyncio
async def test_classroom_mode_denied_for_non_teacher_class(client):
    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, owner)
        await db.commit()
    r = await client.post(f"/api/teacher/classes/{cid}/classroom-mode",
                          json={"enabled": True, "lesson_id": None, "client_request_id": "x",
                                "idempotency_key": "k"},
                          headers=_auth(token(other, "teacher")))
    assert r.json()["code"] == 40302


@pytest.mark.asyncio
async def test_video_insights_degraded(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    r = await client.get(f"/api/teacher/classes/{cid}/video-insights",
                         headers=_auth(token(tid, "teacher")))
    assert r.json()["data"]["degraded"] is True


@pytest.mark.asyncio
async def test_confirmed_student_reads_classroom_mode_but_outsiders_cannot(client):
    """教师开启课堂模式后，仅已确认班级成员可读取同一状态。"""
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        student_id = await make_user(db)
        pending_id = await make_user(db)
        outsider_id = await make_user(db)
        class_id = await make_class(db, teacher_id)
        await add_member(db, class_id, student_id, confirmed=True)
        await add_member(db, class_id, pending_id, confirmed=False)
        await db.commit()

    enabled = await client.post(
        f"/api/teacher/classes/{class_id}/classroom-mode",
        json={
            "enabled": True,
            "lesson_id": None,
            "duration_minutes": 30,
            "client_request_id": "student-linkage",
            "idempotency_key": f"student-linkage-{class_id}",
        },
        headers=_auth(token(teacher_id, "teacher")),
    )
    assert enabled.json()["code"] == 0

    visible = await client.get(
        f"/api/classes/{class_id}/classroom-mode",
        headers=_auth(token(student_id, "student")),
    )
    assert visible.json()["code"] == 0
    assert visible.json()["data"]["enabled"] is True
    assert visible.json()["data"]["class_id"] == str(class_id)

    for denied_id in (pending_id, outsider_id):
        denied = await client.get(
            f"/api/classes/{class_id}/classroom-mode",
            headers=_auth(token(denied_id, "student")),
        )
        assert denied.json()["code"] == 40401


@pytest.mark.asyncio
async def test_classroom_session_launch_question_deterministic(client):
    """课堂会话（§14 调研版）：start → question → 分布确定性 → close 全链路。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await add_member(db, cid, await make_user(db), confirmed=True)
        await db.commit()
    tok = token(tid, "teacher")

    start = await client.post(
        f"/api/teacher/classes/{cid}/classroom-session/start",
        json={"topic": "导数与函数单调性", "room": "303",
              "client_request_id": "s1", "idempotency_key": "sk1"},
        headers=_auth(tok),
    )
    assert start.json()["code"] == 0
    assert start.json()["data"]["status"] == "active"

    q = await client.post(
        f"/api/teacher/classes/{cid}/classroom-session/question",
        json={"question_no": 0, "client_request_id": "q1", "idempotency_key": "qk1"},
        headers=_auth(tok),
    )
    body = q.json()["data"]
    assert body["submitted"] > 0
    assert len(body["distribution"]) == 4
    assert 0 <= body["correct_rate"] <= 100
    assert body["options"] and body["prompt"]
    # 再次发起同一题 → 确定性：同分布（无随机）
    q2 = await client.post(
        f"/api/teacher/classes/{cid}/classroom-session/question",
        json={"question_no": 3, "client_request_id": "q2", "idempotency_key": "qk2"},
        headers=_auth(tok),
    )
    assert q2.json()["data"]["distribution"] == body["distribution"]

    state = await client.get(f"/api/teacher/classes/{cid}/classroom-session", headers=_auth(tok))
    assert state.json()["data"]["last_question"]["prompt"] == body["prompt"]

    closed = await client.post(
        f"/api/teacher/classes/{cid}/classroom-session/close",
        json={"client_request_id": "c1", "idempotency_key": "ck1"},
        headers=_auth(tok),
    )
    assert closed.json()["data"]["status"] == "ended"


@pytest.mark.asyncio
async def test_classroom_session_idle_without_start(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    r = await client.get(f"/api/teacher/classes/{cid}/classroom-session",
                         headers=_auth(token(tid, "teacher")))
    assert r.json()["data"]["status"] == "idle"
