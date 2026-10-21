"""M2 路由面（Route Profile）护栏测试

目标（阶段 1 Task 1）：
1. M2 默认 profile 下，/api/research/*（F14 wf_verify_derivation）不在 OpenAPI 路由面。
2. M2 默认 profile 下，管理后台工作流列表不含 wf_verify_derivation，数量 = FLOW_REGISTRY - 1。

科研代码保留（不物理删除），仅通过 M2_ENABLE_RESEARCH（默认 False）feature profile 排除；
M4 科研端将 M2_ENABLE_RESEARCH=true 后自动恢复。
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.providers.xingchen import FLOW_REGISTRY

# 被 M2 profile 排除的科研工作流
F14_WORKFLOW = "wf_verify_derivation"
F14_PATH = "/api/research/derivations/verify"


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
    try:
        async with _test_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL 不可用，跳过 M2 route profile 测试")
        return
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register(client, role: str = "student") -> tuple[str, str]:
    """验证码登录；返回 (token, user_id)"""
    phone = f"138{uuid.uuid4().int % 100000000:08d}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    login = await client.post(
        "/api/auth/login", json={"phone": phone, "code": settings.dev_sms_code}
    )
    data = login.json()["data"]
    token = data["token"]
    user_id = data["user"]["id"]
    if role != "student":
        await client.post(
            "/api/auth/role/apply",
            json={"role": role},
            headers={"Authorization": f"Bearer {token}"},
        )
    return token, user_id


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_admin(client) -> tuple[str, str]:
    """ADMIN_PHONES 白名单手机号登录（真实引导路径），返回 (token, user_id)"""
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    with patch.object(settings, "admin_phones", phone):
        await client.post("/api/auth/sms-code", json={"phone": phone})
        resp = await client.post(
            "/api/auth/login", json={"phone": phone, "code": settings.dev_sms_code}
        )
    data = resp.json()["data"]
    # 统一认证下默认 active_role 为 student；admin 端点要求显式切换到 admin
    switch = await client.post(
        "/api/auth/role/switch",
        json={"role": "admin"},
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert switch.json()["code"] == 0, switch.text
    return switch.json()["data"]["token"], data["user"]["id"]


class TestM2OpenAPIExcludesF14:
    """OpenAPI 路由面（无需登录）"""

    async def test_no_research_paths_in_m2_openapi(self, client):
        paths = (await client.get("/openapi.json")).json()["paths"]
        research = [p for p in paths if p.startswith("/api/research")]
        # 迭代20 门禁同步：RV16/GATE80 科研网关（commit 69fe36d）按 AGENTS.md §13.9
        # 「科研端所有请求必须经 8000 网关 → 8300」以两条 {full_path} 通配透传挂载，
        # 属已批准的 M2 路由面。除这两条网关通配外的**任何具体科研域路由**仍一律禁止
        # （F14 旧科研域等），守卫范围不缩小。
        _gateway_passthroughs = {
            "/api/research-vnext/{full_path}",
            "/api/research-v1/{full_path}",
        }
        unexpected = [p for p in research if p not in _gateway_passthroughs]
        assert unexpected == [], f"M2 OpenAPI 不应包含科研域路由（网关通配除外）: {unexpected}"
        assert F14_PATH not in paths

    async def test_research_path_returns_404(self, client):
        """M2 profile 下 F14 端点不存在 → 404（路由未挂载）"""
        resp = await client.post(
            F14_PATH,
            json={"derivation_text": "1+1=2"},
            headers=_auth("x"),
        )
        assert resp.status_code == 404


class TestM2AdminWorkflowsExcludeF14:
    """管理后台工作流列表"""

    async def test_workflows_list_excludes_f14(self, client):
        token, _ = await _register_admin(client)
        with patch.object(settings, "m3_enable_teacher", False):
            resp = await client.get("/api/admin/workflows", headers=_auth(token))
        assert resp.status_code == 200
        items = resp.json()["data"]["workflows"]
        names = {i["name"] for i in items}
        assert F14_WORKFLOW not in names, f"M2 管理列表不应包含 {F14_WORKFLOW}"
        # M2 模式 = FLOW_REGISTRY(10) - F14(1) = 9
        assert len(items) == len(FLOW_REGISTRY) - 1 == 9

    async def test_put_f14_returns_404(self, client):
        token, _ = await _register_admin(client)
        resp = await client.put(
            f"/api/admin/workflows/{F14_WORKFLOW}",
            json={"flow_id": "f14"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 40400  # 未知工作流（M2 面不可见）
