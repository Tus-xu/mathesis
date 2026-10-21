"""Butler Kernel v2 兼容接入（阶段 3C）

覆盖：
- BUTLER_V2_ENABLED=false 时旧内核行为不变（信封 code=0）；
- BUTLER_V2_SHADOW=true 时旧内核结果仍返回给用户（v2 仅影子运行）；
- BUTLER_V2_ENABLED=true 时未迁移场景回退旧内核（本阶段无真实工具 → 永不切流）。
"""

import uuid

from httpx import ASGITransport, AsyncClient
from pytest_asyncio import fixture as async_fixture

from app.config import settings
from app.main import app


@async_fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register(client) -> tuple[str, str]:
    """验证码登录，返回 (token, user_id)。"""
    phone = f"138{uuid.uuid4().int % 100000000:08d}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    login = await client.post(
        "/api/auth/login", json={"phone": phone, "code": settings.dev_sms_code}
    )
    data = login.json()["data"]
    return data["token"], data["user"]["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_v2_disabled_keeps_legacy_dashboard(client):
    token, _ = await _register(client)
    resp = await client.get("/api/butler/dashboard", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert isinstance(body["data"], dict)
    assert "greeting" in body["data"]  # 旧内核字段保持不变


async def test_v2_shadow_keeps_legacy_result(client, monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_shadow", True)
    token, _ = await _register(client)
    resp = await client.get("/api/butler/dashboard", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0  # 旧内核结果仍返回给用户
    assert "greeting" in body["data"]


async def test_v2_enabled_unmigrated_scene_falls_back(client, monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", True)
    token, _ = await _register(client)
    resp = await client.get("/api/butler/dashboard", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0  # 未迁移场景继续旧内核，不切断页面
    assert "greeting" in body["data"]


async def test_v2_enabled_defaults_are_false():
    assert settings.butler_v2_enabled is False
    assert settings.butler_v2_shadow is False
