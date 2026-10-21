"""健康检查测试"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    """测试客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health_check(client):
    """测试健康检查端点"""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


async def test_model_health_check(client):
    """测试模型健康检查端点"""
    response = await client.get("/api/health/models")
    assert response.status_code == 200
    data = response.json()
    assert "spark" in data
    assert "deepseek" in data
    assert "embedding" in data
