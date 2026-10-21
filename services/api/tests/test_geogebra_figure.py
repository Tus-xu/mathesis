"""GeoGebra 交互图形端点测试（M2 新增）

覆盖：
1. POST /api/figures/ggb — 通用生成（question_text + interactive → ggb payload）
2. POST /api/student/error-records/{id}/figure — 错题本动态图形：生成并持久化进 image 列
3. 幂等：已有 ggb 未 force 直接复用；force 重新生成
4. 详情接口透传 ggb 条目（image 列含 {"type":"ggb",...}）
5. 越权/不存在 40400
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway import figures_router
from app.gateway import student_router as sr
from app.main import app
from app.models.coursework import ErrorRecord
from app.models.database import get_db

_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def _override_get_db():
    async with _factory() as session:
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


async def _auth(client):
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_FAKE_GGB = {
    "commands": [
        "# perspective: 3d",
        "# view3d: -3 -3 -3 3 3 3",
        "A=(0,0,0)",
        "B=(2,0,0)",
        "C=(2,2,0)",
        "D=(0,2,0)",
        "A1=(0,0,2)",
        "cu=Cube(A,B)",
        "SetColor(cu,40,60,120)",
    ],
    "view": "3d",
}


async def _make_record(client, token, **overrides) -> str:
    body = {
        "question_text": "在长方体 ABCD-A1B1C1D1 中，AB=2，求对角线 A1C 的长",
        "source_channel": "manual_photo",
        "error_type": "concept",
    }
    body.update(overrides)
    resp = await client.post("/api/student/error-records", json=body, headers=_headers(token))
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]["record_id"]


async def _cleanup(db, user_id):
    rows = await db.execute(
        select(ErrorRecord).where(ErrorRecord.user_id == user_id)
    )
    for r in rows.scalars().all():
        await db.delete(r)
    await db.commit()


@pytest.mark.asyncio
async def test_ggb_generate_generic(client):
    token, user_id = await _auth(client)
    with patch.object(figures_router, "generate_ggb", new=AsyncMock(return_value=dict(_FAKE_GGB))) as m:
        resp = await client.post(
            "/api/figures/ggb",
            json={"question_text": "函数 f(x)=x^2 的图像", "interactive": True},
            headers=_headers(token),
        )
    body = resp.json()
    assert body["code"] == 0, body
    ggb = body["data"]["ggb"]
    assert ggb["type"] == "ggb"
    assert ggb["view"] == "3d"
    assert ggb["commands"] == _FAKE_GGB["commands"]
    m.assert_awaited_once()
    async with _factory() as db:
        await _cleanup(db, uuid.UUID(user_id))


@pytest.mark.asyncio
async def test_ggb_generate_requires_input(client):
    token, _ = await _auth(client)
    resp = await client.post("/api/figures/ggb", json={}, headers=_headers(token))
    assert resp.json()["code"] == 40001


@pytest.mark.asyncio
async def test_error_record_figure_generate_and_persist(client):
    token, user_id = await _auth(client)
    record_id = await _make_record(client, token)
    with patch.object(sr, "generate_ggb", new=AsyncMock(return_value=dict(_FAKE_GGB))):
        resp = await client.post(
            f"/api/student/error-records/{record_id}/figure", headers=_headers(token)
        )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["generated"] is True
    assert body["data"]["ggb"]["type"] == "ggb"

    # 详情接口透传 ggb 条目
    detail = await client.get(
        f"/api/student/error-records/{record_id}/detail", headers=_headers(token)
    )
    images = detail.json()["data"]["image"]
    ggb_entries = [e for e in images if isinstance(e, dict) and e.get("type") == "ggb"]
    assert len(ggb_entries) == 1

    async with _factory() as db:
        row = (
            await db.execute(select(ErrorRecord).where(ErrorRecord.id == uuid.UUID(record_id)))
        ).scalar_one()
        stored = [e for e in row.image if isinstance(e, dict) and e.get("type") == "ggb"]
        assert len(stored) == 1
        await _cleanup(db, uuid.UUID(user_id))


@pytest.mark.asyncio
async def test_error_record_figure_idempotent_and_force(client):
    token, user_id = await _auth(client)
    record_id = await _make_record(client, token)
    with patch.object(sr, "generate_ggb", new=AsyncMock(return_value=dict(_FAKE_GGB))) as m:
        r1 = await client.post(
            f"/api/student/error-records/{record_id}/figure", headers=_headers(token)
        )
        assert r1.json()["data"]["generated"] is True
        # 幂等：第二次不重新生成
        r2 = await client.post(
            f"/api/student/error-records/{record_id}/figure", headers=_headers(token)
        )
        assert r2.json()["data"]["generated"] is False
        assert m.await_count == 1
        # force 强制重生成
        r3 = await client.post(
            f"/api/student/error-records/{record_id}/figure?force=true",
            headers=_headers(token),
        )
        assert r3.json()["data"]["generated"] is True
        assert m.await_count == 2
    async with _factory() as db:
        await _cleanup(db, uuid.UUID(user_id))


@pytest.mark.asyncio
async def test_error_record_figure_not_found(client):
    token, _ = await _auth(client)
    resp = await client.post(
        f"/api/student/error-records/{uuid.uuid4()}/figure", headers=_headers(token)
    )
    assert resp.json()["code"] == 40400


# ==================== 视觉读图链路（MathMover 内核） ====================


@pytest.mark.asyncio
async def test_generate_ggb_byok_vision_direct(monkeypatch):
    """配置了视觉模型时，图片直接走 BYOK 看图生成（不调用文本模型）。"""
    from app.services import geogebra_figure as gf

    vision_cmd = ["# perspective: 3d", "A=(0,0,0)", "B=(2,0,0)", "C=(2,2,0)", "D=(0,2,0)"]
    async def fake_vision(image_data_uri, question_text, figure_hint, profile, interactive):
        assert image_data_uri.startswith("data:image")
        return "\n".join(vision_cmd)

    monkeypatch.setattr(gf, "_call_vision_byok", fake_vision)
    called = {"llm": False}

    async def fake_llm(system, user, *, user_id, db):
        called["llm"] = True
        return {"commands": [], "view": "2d"}

    monkeypatch.setattr(gf, "_run_llm_generate", fake_llm)
    result = await gf.generate_ggb(
        "在长方体 ABCD-A1B1C1D1 中…", image_data_uri="data:image/jpeg;base64,xxxx", interactive=True
    )
    assert result is not None
    assert result["view"] == "3d"
    assert result["commands"] == vision_cmd
    assert called["llm"] is False  # 未走文本模型


@pytest.mark.asyncio
async def test_generate_ggb_vision_fallback_reads_figure(monkeypatch):
    """未配置视觉模型但有原图时：先尝试 BYOK（None），再 wf_doc_understand 读图并入 prompt。"""
    from app.services import geogebra_figure as gf

    monkeypatch.setattr(gf, "_call_vision_byok", AsyncMock(return_value=None))

    async def fake_read(image_data_uri, user_id, db):
        return "\n题图视觉识别：长方体 ABCD-A1B1C1D1，AB=2 BC=1 AA1=√3，E 为 CC1 中点"

    monkeypatch.setattr(gf, "_read_figure_vision", fake_read)
    captured = {}

    async def fake_llm(system, user, *, user_id, db):
        captured["user"] = user
        captured["system"] = system
        return {"commands": ["# perspective: 3d", "A=(0,0,0)"], "view": "3d"}

    monkeypatch.setattr(gf, "_run_llm_generate", fake_llm)
    result = await gf.generate_ggb(
        "在长方体 ABCD-A1B1C1D1 中…", image_data_uri="data:image/jpeg;base64,xxxx", interactive=False
    )
    assert result is not None
    assert "题图视觉识别" in captured["user"]  # 视觉读图结果已并入 prompt
    assert "长方体" in captured["user"]
    assert "立体几何 3D" in captured["system"]  # solid_geometry 专业规则已注入 system prompt
