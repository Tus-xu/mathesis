"""M3 教师端：Artifact 状态机 / 版本 / 幂等（§9 / §19.2）。"""


import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.domains.teacher.artifacts import create_artifact
from app.main import app
from app.models.database import async_session_factory
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture(loop_scope="function")
async def seeded():
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        art = await create_artifact(db, owner_id=tid, artifact_type="lesson_plan",
                                    scene="teacher.prep", class_id=cid, payload={"topic": "函数"})
        db.add(art)
        await db.commit()
        yield {"teacher": tid, "class": cid, "artifact_id": art.id}


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def _get(client, aid, tok):
    return await client.get(f"/api/teacher/artifacts/{aid}", headers=_auth(tok))


@pytest.mark.asyncio
async def test_new_artifact_is_draft(client, seeded):
    aid = seeded["artifact_id"]
    r = await _get(client, aid, token(seeded["teacher"], "teacher"))
    assert r.json()["data"]["status"] == "draft"
    assert r.json()["data"]["version"] == 1


@pytest.mark.asyncio
async def test_unconfirmed_cannot_publish(client, seeded):
    aid = seeded["artifact_id"]
    r = await client.post(
        f"/api/teacher/artifacts/{aid}/publish",
        json={"client_request_id": "c1", "idempotency_key": "k-pub"},
        headers=_auth(token(seeded["teacher"], "teacher")),
    )
    assert r.json()["code"] == 42210  # confirmation_required


@pytest.mark.asyncio
async def test_confirm_then_publish(client, seeded):
    aid = seeded["artifact_id"]
    tok = token(seeded["teacher"], "teacher")
    r1 = await client.post(f"/api/teacher/artifacts/{aid}/confirm",
                           json={"client_request_id": "c1", "idempotency_key": "k-conf"}, headers=_auth(tok))
    assert r1.json()["code"] == 0
    assert r1.json()["data"]["artifact"]["status"] == "confirmed"
    r2 = await client.post(f"/api/teacher/artifacts/{aid}/publish",
                           json={"client_request_id": "c1", "idempotency_key": "k-pub"}, headers=_auth(tok))
    assert r2.json()["code"] == 0
    assert r2.json()["data"]["artifact"]["status"] == "published"
    overall = await _get(client, aid, tok)
    assert overall.json()["data"]["status"] == "published"


@pytest.mark.asyncio
async def test_confirmed_modification_creates_new_version(client, seeded):
    aid = seeded["artifact_id"]
    tok = token(seeded["teacher"], "teacher")
    await client.post(f"/api/teacher/artifacts/{aid}/confirm",
                      json={"client_request_id": "c1", "idempotency_key": "k-c"}, headers=_auth(tok))
    r = await client.put(
        f"/api/teacher/artifacts/{aid}",
        json={"version": 1, "content": {"topic": "函数单调性", "updated": True}},
        headers=_auth(tok),
    )
    data = r.json()["data"]
    assert data["created_new_version"] is True
    assert data["artifact"]["version"] == 2
    assert data["artifact"]["status"] == "draft"


@pytest.mark.asyncio
async def test_draft_stale_version_conflict(client, seeded):
    aid = seeded["artifact_id"]
    tok = token(seeded["teacher"], "teacher")
    # draft 版本为 1；提交 version=2 → 版本冲突
    r = await client.put(f"/api/teacher/artifacts/{aid}",
                         json={"version": 2, "content": {"topic": "v2"}}, headers=_auth(tok))
    assert r.json()["code"] == 40901  # version_conflict


@pytest.mark.asyncio
async def test_confirm_idempotent_replay(client, seeded):
    aid = seeded["artifact_id"]
    tok = token(seeded["teacher"], "teacher")
    r1 = await client.post(f"/api/teacher/artifacts/{aid}/confirm",
                           json={"client_request_id": "c1", "idempotency_key": "same-key"}, headers=_auth(tok))
    assert r1.json()["data"]["replayed"] is False
    r2 = await client.post(f"/api/teacher/artifacts/{aid}/confirm",
                           json={"client_request_id": "c1", "idempotency_key": "same-key"}, headers=_auth(tok))
    assert r2.json()["data"]["replayed"] is True


@pytest.mark.asyncio
async def test_archive_keeps_audit_and_readable(client, seeded):
    aid = seeded["artifact_id"]
    tok = token(seeded["teacher"], "teacher")
    r = await client.post(f"/api/teacher/artifacts/{aid}/archive",
                          json={"client_request_id": "c1", "idempotency_key": "k-arch"}, headers=_auth(tok))
    assert r.json()["data"]["artifact"]["status"] == "archived"
    overall = await _get(client, aid, tok)
    assert overall.json()["data"]["status"] == "archived"  # 归档后仍可见（非物理删除）
