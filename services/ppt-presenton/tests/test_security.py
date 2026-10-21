"""8201 security：loopback-only + 服务令牌 fail-closed。"""
from __future__ import annotations

import httpx
import pytest

from presenton_adapter.app import create_app
from presenton_adapter.config import Settings, get_settings

APP = create_app()
BASE = "http://presenton.test"


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url=BASE)


async def test_healthz_is_public() -> None:
    async with await _client() as client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_health_requires_token() -> None:
    async with await _client() as client:
        r = await client.get("/api/v1/health")
    assert r.status_code == 401
    assert r.json()["error_key"] == "unauthorized"


async def test_wrong_token_rejected() -> None:
    async with await _client() as client:
        r = await client.get("/api/v1/health", headers={"X-Presenton-Service-Token": "wrong"})
    assert r.status_code == 401


async def test_correct_token_accepted() -> None:
    token = get_settings().service_token
    async with await _client() as client:
        r = await client.get("/api/v1/health", headers={"X-Presenton-Service-Token": token})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_render_page_real_endpoint_with_token() -> None:
    """R21：stub 已替换为真实引擎端点；render=False（跳过 COM）验证契约与鉴权。"""
    token = get_settings().service_token
    async with await _client() as client:
        r = await client.post(
            "/api/v1/deck/pages/render",
            headers={"X-Presenton-Service-Token": token},
            json={
                "deck_id": "d-1",
                "deck_title": "合成",
                "page_no": 1,
                "total_pages": 8,
                "slide": {"id": "s1", "type": "title", "title": "合成标题",
                          "content": "副标题", "layout_hint": "title", "assets": []},
                "render": False,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["engine"].startswith("r21")  # r21-engine-*（R21）/ r21.1-engine-*（R21.1 起）


def test_default_binding_is_loopback() -> None:
    s = get_settings()
    assert s.port == 8201
    assert s.host == "127.0.0.1"


def test_non_loopback_binding_rejected() -> None:
    s = Settings(_env_file=None, host="0.0.0.0")
    with pytest.raises(ValueError, match="禁止绑定非回环地址"):
        s.validate_binding()
