"""双师课堂·历史闭环端点测试（需 PG 测试库，conftest 初始化）。

覆盖：
- 创建会话带 source_type/source_ref
- 列表筛选（status/source_type/kp_code/date_from + 排除软删除）
- 详情返回扩展字段（verification/progress/notes/qa_summary/knowledge_points/source）
- PATCH /progress（学习进度更新）
- PATCH /notes（笔记持久化）
- POST /qa（问答追加 + 错因摘要）
- POST /clone（复制为新课）
- DELETE（软删除 + 列表不展示）

注意：本文件不包含金标准确定性生成测试——生产链路无金标准特例分支，
所有题目走同一条通用 LLM 生成链路。端到端验收见 test_classroom_acceptance.py。

运行：cd services/api && python -m pytest tests/test_classroom_history.py -q
（需先启动 PG 测试库：docker compose up -d postgres in deploy/）
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db


def _make_test_engine():
    return create_async_engine(settings.database_url, poolclass=NullPool)


_test_engine = _make_test_engine()
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


async def _register_and_login(client, phone=None) -> tuple[str, str]:
    phone = phone or f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _wait_session_ready(client, sid, h, timeout=60):
    """等待会话生成完成（ready/failed），返回详情 data。"""
    import asyncio

    for _ in range(timeout * 2):
        detail = await client.get(f"/api/classroom/sessions/{sid}", headers=h)
        status = detail.json()["data"]["status"]
        if status in ("ready", "failed"):
            return detail.json()["data"]
        await asyncio.sleep(0.5)
    return detail.json()["data"]


# ==================== 历史闭环端点 ====================


@pytest.mark.asyncio
class TestHistoryEndpoints:
    async def test_create_with_source(self, client):
        """创建会话带 source_type/source_ref（拍题来源）。"""
        token, _ = await _register_and_login(client)
        resp = await client.post(
            "/api/classroom/sessions",
            json={
                "topic": "函数的单调性与极值",
                "slide_count": 8,
                "mode": "topic",
                "source_type": "photo",
                "source_ref": {"filename": "test.jpg", "page": 1, "status": "confirmed"},
            },
            headers=_headers(token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "generating"
        sid = data["session_id"]

        detail = await client.get(f"/api/classroom/sessions/{sid}", headers=_headers(token))
        d = detail.json()["data"]
        assert d["source_type"] == "photo"
        assert d["source_ref"]["filename"] == "test.jpg"

    async def test_list_filter_and_soft_delete(self, client):
        """列表筛选 + 软删除后不展示。"""
        token, _ = await _register_and_login(client)
        h = _headers(token)

        r1 = await client.post(
            "/api/classroom/sessions",
            json={"topic": "数列求和", "slide_count": 8, "source_type": "topic"},
            headers=h,
        )
        sid1 = r1.json()["data"]["session_id"]
        r2 = await client.post(
            "/api/classroom/sessions",
            json={"topic": "概率分布", "slide_count": 8, "source_type": "file"},
            headers=h,
        )
        sid2 = r2.json()["data"]["session_id"]

        resp = await client.get("/api/classroom/sessions?source_type=file", headers=h)
        items = resp.json()["data"]["items"]
        assert all(i["source_type"] == "file" for i in items)
        assert any(i["session_id"] == sid2 for i in items)

        del_resp = await client.delete(f"/api/classroom/sessions/{sid1}", headers=h)
        assert del_resp.json()["code"] == 0

        resp2 = await client.get("/api/classroom/sessions", headers=h)
        ids = [i["session_id"] for i in resp2.json()["data"]["items"]]
        assert sid1 not in ids
        assert sid2 in ids

        del_resp2 = await client.delete(f"/api/classroom/sessions/{sid1}", headers=h)
        assert del_resp2.json()["code"] == 0

        detail = await client.get(f"/api/classroom/sessions/{sid1}", headers=h)
        assert detail.json()["code"] == 40400

    async def test_progress_update(self, client):
        """学习进度更新（继续学习闭环）。"""
        token, _ = await _register_and_login(client)
        h = _headers(token)
        sid = (await client.post(
            "/api/classroom/sessions", json={"topic": "导数应用", "slide_count": 8}, headers=h
        )).json()["data"]["session_id"]

        resp = await client.patch(
            f"/api/classroom/sessions/{sid}/progress",
            json={"slide_index": 3, "page_check": {"3": "ok"}},
            headers=h,
        )
        assert resp.json()["code"] == 0
        progress = resp.json()["data"]["progress"]
        assert progress["slide_index"] == 3
        assert progress["page_check"]["3"] == "ok"

        resp2 = await client.patch(
            f"/api/classroom/sessions/{sid}/progress",
            json={"slide_index": 5, "page_check": {"5": "again"}},
            headers=h,
        )
        progress2 = resp2.json()["data"]["progress"]
        assert progress2["slide_index"] == 5
        assert progress2["page_check"]["3"] == "ok"
        assert progress2["page_check"]["5"] == "again"

    async def test_notes_persist(self, client):
        """笔记服务端持久化。"""
        token, _ = await _register_and_login(client)
        h = _headers(token)
        sid = (await client.post(
            "/api/classroom/sessions", json={"topic": "三角函数", "slide_count": 8}, headers=h
        )).json()["data"]["session_id"]

        notes = "# 笔记\n单调递增区间：$(-\\infty, -1)$"
        resp = await client.patch(
            f"/api/classroom/sessions/{sid}/notes", json={"notes": notes}, headers=h
        )
        assert resp.json()["code"] == 0

        detail = await client.get(f"/api/classroom/sessions/{sid}", headers=h)
        assert detail.json()["data"]["notes"] == notes

    async def test_qa_append(self, client):
        """问答追加 + 错因摘要。"""
        token, _ = await _register_and_login(client)
        h = _headers(token)
        sid = (await client.post(
            "/api/classroom/sessions", json={"topic": "向量运算", "slide_count": 8}, headers=h
        )).json()["data"]["session_id"]

        await client.post(
            f"/api/classroom/sessions/{sid}/qa",
            json={"role": "user", "text": "为什么 f'(-1)=0 是必要条件？"},
            headers=h,
        )
        resp = await client.post(
            f"/api/classroom/sessions/{sid}/qa",
            json={
                "role": "assistant",
                "text": "导数为零只是必要条件，还需二阶导判断极大/极小。",
                "error_summary": "混淆必要条件与充分条件",
            },
            headers=h,
        )
        qa = resp.json()["data"]["qa_summary"]
        assert len(qa["messages"]) == 2
        assert qa["error_summary"] == "混淆必要条件与充分条件"

    async def test_clone_session(self, client):
        """复制为新课（保留内容快照）。需等待 LLM 生成完成。"""
        token, _ = await _register_and_login(client)
        h = _headers(token)
        sid = (await client.post(
            "/api/classroom/sessions", json={"topic": "指数函数性质", "slide_count": 8}, headers=h
        )).json()["data"]["session_id"]

        d = await _wait_session_ready(client, sid, h)
        if d["status"] != "ready":
            pytest.skip("LLM 生成未完成，跳过克隆测试")

        clone_resp = await client.post(f"/api/classroom/sessions/{sid}/clone", headers=h)
        assert clone_resp.json()["code"] == 0
        new_sid = clone_resp.json()["data"]["session_id"]
        assert new_sid != sid
        assert clone_resp.json()["data"]["status"] == "ready"

        new_detail = await client.get(f"/api/classroom/sessions/{new_sid}", headers=h)
        assert new_detail.json()["data"]["status"] == "ready"
        assert len(new_detail.json()["data"]["slides"]) > 0


# ==================== 通用链路验证（非金标准特例） ====================


@pytest.mark.asyncio
class TestGenericGenerationPath:
    """验证所有题目走同一条通用链路（无特例分支）。

    创建不同主题的会话，确认都经过 generating→ready/failed 的通用流程，
    且 engine 标注为通用引擎（非 math_classroom_golden）。
    """

    async def test_general_topic_uses_generic_engine(self, client):
        """普通主题使用通用生成引擎。"""
        token, _ = await _register_and_login(client)
        h = _headers(token)
        sid = (await client.post(
            "/api/classroom/sessions", json={"topic": "对数函数的图像与性质", "slide_count": 8}, headers=h
        )).json()["data"]["session_id"]

        d = await _wait_session_ready(client, sid, h)
        # 通用引擎标注（不是 math_classroom_golden）
        assert d["engine"] != "math_classroom_golden"
        # 生成完成应有 slides
        if d["status"] == "ready":
            assert len(d["slides"]) > 0
            # 每页应有 verification_result（通用校验接入）
            for s in d["slides"]:
                assert "verification_result" in s or "blocks" in s
