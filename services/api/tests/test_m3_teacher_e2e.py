"""M3 教师端：端到端（§19.6）。"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.database import async_session_factory
from app.models.question_bank import QuestionBank, stem_hash
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _seed():
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        tag = uuid.uuid4().hex[:8]
        for i in range(4):
            stem = f"{tag}e2e题干{i}"
            db.add(QuestionBank(stem=stem, q_type="solution", answer=f"ans{i}",
                                difficulty="medium", kp_codes=["MATH-002"], scope="student",
                                hash=stem_hash(stem)))
        await db.commit()
        return tid, cid


@pytest.mark.asyncio
async def test_full_backend_flow(client):
    tid, cid = await _seed()
    tok = token(tid, "teacher")
    auth = _auth(tok)

    # 1 备课草稿
    r = await client.post("/api/teacher/lessons/adapt",
                          json={"class_id": str(cid), "topic": "函数"}, headers=auth)
    lesson_id = r.json()["data"]["artifact_id"]
    assert r.json()["data"]["status"] == "draft"

    # 2 确认教案
    c = await client.post(f"/api/teacher/artifacts/{lesson_id}/confirm",
                          json={"client_request_id": "c", "idempotency_key": "k1"}, headers=auth)
    assert c.json()["data"]["artifact"]["status"] == "confirmed"

    # 3 出题 → 确认 → 建作业 → 发布
    g = await client.post("/api/teacher/quizzes/generate",
                          json={"class_id": str(cid), "knowledge_points": ["MATH-002"],
                                "count": 4, "question_types": {"choice": 0, "blank": 0, "text": 4}},
                          headers=auth)
    quiz_id = g.json()["data"]["artifact_id"]
    await client.post(f"/api/teacher/artifacts/{quiz_id}/confirm",
                      json={"client_request_id": "q", "idempotency_key": "k2"}, headers=auth)
    a = await client.post("/api/teacher/assignments",
                          json={"class_id": str(cid), "title": "单元测", "artifact_id": quiz_id,
                                "client_assignment_id": "e2e-ca", "deadline": None}, headers=auth)
    assignment_id = a.json()["data"]["assignment_id"]
    assert a.json()["data"]["status"] == "draft"
    p = await client.post(f"/api/teacher/assignments/{assignment_id}/publish",
                          json={"client_request_id": "p", "idempotency_key": f"pk-{assignment_id}"}, headers=auth)
    assert p.json()["data"]["status"] == "published"

    # 4 Today 非空（至少看到该作业）
    today = await client.get("/api/teacher/today", headers=auth)
    assert today.json()["code"] == 0


@pytest.mark.asyncio
async def test_cross_teacher_denied_e2e(client):
    tid, cid = await _seed()
    async with async_session_factory() as db:
        other = await make_user(db)
        await db.commit()
    # 其他教师不能读取该班、不能猜作业
    r = await client.get(f"/api/teacher/classes/{cid}/insights",
                         headers=_auth(token(other, "teacher")))
    assert r.json()["code"] == 40302
    r2 = await client.post("/api/teacher/lessons/adapt",
                           json={"class_id": str(cid), "topic": "函数"},
                           headers=_auth(token(other, "teacher")))
    assert r2.json()["code"] == 40302
