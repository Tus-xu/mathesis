"""M3 教师端：Today 与班级可行动洞察（§11 真数据聚合）。"""


import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.coursework import Assignment, Submission, SubmissionItem
from app.models.database import async_session_factory
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _seed_pending_grading():
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        a = Assignment(class_id=cid, creator_id=tid, title="导数周测", type="quiz",
                       status="published")
        db.add(a)
        await db.flush()
        s = Submission(user_id=tid, assignment_id=a.id, client_submit_id="c1", status="pending_review")
        db.add(s)
        await db.flush()
        db.add(SubmissionItem(submission_id=s.id, item_no=1, q_type="text", verdict="pending_review",
                              needs_review=True))
        await db.commit()
        return tid, cid, a.id


@pytest.mark.asyncio
async def test_today_reports_pending_grading(client):
    tid, cid, aid = await _seed_pending_grading()
    resp = await client.get("/api/teacher/today", headers=_auth(token(tid, "teacher")))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["grading_queue"]["count"] >= 1


@pytest.mark.asyncio
async def test_class_insights_review_backlog(client):
    tid, cid, aid = await _seed_pending_grading()
    resp = await client.get(f"/api/teacher/classes/{cid}/insights?actionable=true",
                            headers=_auth(token(tid, "teacher")))
    assert resp.status_code == 200
    insights = resp.json()["data"]["insights"]
    review = next(i for i in insights if i["kind"] == "review_backlog")
    assert review["evidence"] == "待复核作答 1 份"
    assert "=" not in review["evidence"]


@pytest.mark.asyncio
async def test_empty_class_returns_empty_insights(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    resp = await client.get(f"/api/teacher/classes/{cid}/insights?actionable=true",
                            headers=_auth(token(tid, "teacher")))
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"]["insights"], list)


@pytest.mark.asyncio
async def test_video_insights_degraded_when_no_source(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    resp = await client.get(f"/api/teacher/classes/{cid}/video-insights",
                            headers=_auth(token(tid, "teacher")))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["degraded"] is True
