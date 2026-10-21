"""迭代15 B6：学情行动清单 GET /api/student/mastery/today-actions 测试

覆盖：
1. 未登录 401/403
2. 空数据用户：code=0 + actions 为列表（可为空）+ due_today=0
3. 到期错题 → 首个行动卡 type=review 且含 record_id/items；未到期不出现
4. 薄弱 kp（mastery<0.7）→ weak 卡带 kp_code/reason
5. 防伪勤奋：最薄弱 kp ≥0.9 → 无 weak 卡、notice.type=move_on

需要 PostgreSQL + Redis 运行中（与 test_student_linkage 同环境同模式）。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.coursework import ErrorRecord, MasteryRecord
from app.models.database import get_db
from app.models.knowledge_point import KnowledgePoint

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


@pytest_asyncio.fixture(autouse=True)
async def _cleanup():
    """自清洁：TST 前缀 KP 及关联 mastery/error 行（与 test_student_linkage 同口径）"""
    yield
    async with _test_session_factory() as s:
        kp_ids = select(KnowledgePoint.id).where(KnowledgePoint.code.like("TST_ta%"))
        await s.execute(delete(MasteryRecord).where(MasteryRecord.kp_id.in_(kp_ids)))
        await s.execute(delete(ErrorRecord).where(ErrorRecord.kp_code.like("TST_ta%")))
        await s.execute(delete(KnowledgePoint).where(KnowledgePoint.code.like("TST_ta%")))
        await s.commit()


async def _auth(client):
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_kp_with_mastery(user_id: str, mastery: float, code: str | None = None):
    """种一个 TST 前缀 KP + 掌握度记录，返回 kp_code"""
    code = code or f"TST_ta_{uuid.uuid4().hex[:8]}"
    async with _test_session_factory() as s:
        kp = KnowledgePoint(code=code, name=f"行动清单知识点{code[-4:]}")
        s.add(kp)
        await s.flush()
        s.add(
            MasteryRecord(
                user_id=uuid.UUID(user_id), kp_id=kp.id, mastery=mastery, practice_count=3
            )
        )
        await s.commit()
    return code


async def _make_due_error(client, token, kp_code: str, due: bool = True) -> str:
    """走 API 收录错题（默认排期 +1 天），再把排期改到期/未到期，返回 record_id"""
    resp = await client.post(
        "/api/student/error-records",
        json={
            "question_text": f"行动清单测试错题{uuid.uuid4().hex[:6]}",
            "source_channel": "manual_photo",
            "error_type": "concept",
            "kp_code": kp_code,
        },
        headers=_headers(token),
    )
    assert resp.json()["code"] == 0, resp.json()
    record_id = resp.json()["data"]["record_id"]
    async with _test_session_factory() as s:
        rec = await s.get(ErrorRecord, uuid.UUID(record_id))
        rec.next_review_at = datetime.now(UTC) + timedelta(hours=-1 if due else 24 * 7)
        await s.commit()
    return record_id


class TestTodayActions:
    async def test_unauthorized(self, client):
        resp = await client.get("/api/student/mastery/today-actions")
        assert resp.status_code in (401, 403)

    async def test_empty_user_shape(self, client):
        token, _ = await _auth(client)
        resp = await client.get("/api/student/mastery/today-actions", headers=_headers(token))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data["actions"], list)
        assert data["due_today"] == 0
        assert data["notice"] is None
        assert len(data["actions"]) <= 3

    async def test_due_error_yields_review_action(self, client):
        token, user_id = await _auth(client)
        kp_code = await _make_kp_with_mastery(user_id, 0.4)
        record_id = await _make_due_error(client, token, kp_code, due=True)

        data = (
            await client.get("/api/student/mastery/today-actions", headers=_headers(token))
        ).json()["data"]
        assert data["due_today"] >= 1
        review = next((a for a in data["actions"] if a["type"] == "review"), None)
        assert review is not None, f"到期错题应产生 review 行动卡: {data}"
        assert review["duration_min"] > 0 and review["reason"]
        ids = [it["record_id"] for it in review["items"]]
        assert record_id in ids
        assert review["items"][0]["question_text"]

    async def test_future_review_not_due(self, client):
        token, user_id = await _auth(client)
        kp_code = await _make_kp_with_mastery(user_id, 0.4)
        await _make_due_error(client, token, kp_code, due=False)

        data = (
            await client.get("/api/student/mastery/today-actions", headers=_headers(token))
        ).json()["data"]
        assert data["due_today"] == 0
        assert all(a["type"] != "review" for a in data["actions"])

    async def test_weak_kp_action(self, client):
        token, user_id = await _auth(client)
        kp_code = await _make_kp_with_mastery(user_id, 0.3)

        data = (
            await client.get("/api/student/mastery/today-actions", headers=_headers(token))
        ).json()["data"]
        weak = next((a for a in data["actions"] if a["type"] == "weak"), None)
        assert weak is not None, f"掌握度 0.3 应产生 weak 卡: {data}"
        assert weak["kp_code"] == kp_code
        assert "30%" in weak["reason"]
        assert data["notice"] is None

    async def test_anti_fake_diligence_notice(self, client):
        token, user_id = await _auth(client)
        await _make_kp_with_mastery(user_id, 0.95)

        data = (
            await client.get("/api/student/mastery/today-actions", headers=_headers(token))
        ).json()["data"]
        assert all(a["type"] != "weak" for a in data["actions"]), "≥0.9 不应再推专练卡"
        assert data["notice"] is not None and data["notice"]["type"] == "move_on"
        assert "95%" in data["notice"]["text"]
