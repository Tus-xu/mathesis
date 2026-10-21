"""M3 教师端工作流配置管理契约测试。"""

from __future__ import annotations

import contextlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.gateway.jwt import create_token_with_role
from app.gateway.redis import get_redis
from app.main import app
from app.models.database import async_session_factory
from app.models.role_binding import RoleBinding
from app.models.system_config import SystemConfig
from app.models.user import User

TEACHER_WORKFLOWS = {
    "wf_lesson_plan",
    "wf_ai_ppt",
    "wf_explainer_script",
    "wf_smart_quiz",
    "wf_solution_pregrade",
    "wf_course_preprocess",
    "wf_doc_understand",
}


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def _role_headers(role: str) -> dict[str, str]:
    user_id = uuid.uuid4()
    async with async_session_factory() as session:
        user = User(
            id=user_id,
            nickname=f"workflow {role}",
            onboarding_status="completed",
        )
        session.add(user)
        await session.flush()
        session.add(RoleBinding(user_id=user_id, role=role, status="approved", verified=True))
        await session.commit()
    token = create_token_with_role(str(user_id), role)
    return {"Authorization": f"Bearer {token}"}


async def _cleanup() -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(SystemConfig).where(SystemConfig.key.in_(("workflows", "xingchen.global")))
        )
        await session.commit()
    redis = get_redis()
    for name in TEACHER_WORKFLOWS:
        with contextlib.suppress(Exception):
            await redis.delete(f"switch:xingchen:{name}")


@pytest.mark.asyncio
async def test_teacher_workflow_crud_masks_secret_and_tracks_availability(client):
    """缺失持久化、密钥泄露或启停/验证状态误算时必须失败。"""
    await _cleanup()
    headers = await _role_headers("admin")
    payload = {
        "workflow_name": "wf_lesson_plan",
        "capability": "adapt_lesson",
        "enabled": False,
        "base_url": "https://workflow.example/api",
        "api_key": "super-secret-key",
        "api_secret": "super-secret-value",
        "workflow_id": "flow-lesson-001",
        "input_mapping": {"topic": "subject"},
        "output_mapping": {"content": "data"},
        "timeout_seconds": 12,
        "retry_count": 1,
        "health_check_url": "https://workflow.example/health",
    }
    try:
        created = await client.post("/api/admin/workflows", json=payload, headers=headers)
        assert created.status_code == 200
        body = created.json()
        assert body["code"] == 0
        item = body["data"]
        assert item["workflow_name"] == "wf_lesson_plan"
        assert item["capability"] == "adapt_lesson"
        assert item["api_key_configured"] is True
        assert item["configured"] is True
        assert item["verified"] is False
        assert item["available"] is False
        assert "super-secret-key" not in created.text
        assert "super-secret-value" not in created.text

        async with async_session_factory() as session:
            stored = (
                await session.execute(select(SystemConfig).where(SystemConfig.key == "workflows"))
            ).scalar_one()
        entry = stored.value["wf_lesson_plan"]
        assert entry["api_key"] != "super-secret-key"
        assert entry["api_secret"] != "super-secret-value"

        fetched = await client.get("/api/admin/workflows/wf_lesson_plan", headers=headers)
        assert fetched.json()["data"]["workflow_id"] == "flow-lesson-001"
        assert fetched.json()["data"]["input_mapping"] == {"topic": "subject"}

        enabled = await client.post(
            "/api/admin/workflows/wf_lesson_plan/enable", headers=headers
        )
        assert enabled.json()["data"]["enabled"] is True
        assert enabled.json()["data"]["available"] is False

        master = await client.put(
            "/api/admin/system/xingchen", json={"enabled": True}, headers=headers
        )
        assert master.json()["code"] == 0

        workflow_output = {
            "code": 0,
            "message": "ok",
            "data": {"lesson_plan": "函数单调性教案"},
            "trace_id": "trace-workflow-test",
        }
        with patch(
            "app.gateway.admin_router.run_workflow",
            new=AsyncMock(return_value=workflow_output),
        ):
            tested = await client.post(
                "/api/admin/workflows/wf_lesson_plan/test", headers=headers
            )
        tested_data = tested.json()["data"]
        assert tested_data["ok"] is True
        assert tested_data["last_test_status"] == "success"
        assert tested_data["verified"] is True
        assert tested_data["available"] is True
        assert tested_data["last_tested_at"]

        changed = await client.put(
            "/api/admin/workflows/wf_lesson_plan",
            json={"workflow_id": "flow-lesson-002"},
            headers=headers,
        )
        changed_data = changed.json()["data"]
        assert changed_data["workflow_id"] == "flow-lesson-002"
        assert changed_data["verified"] is False
        assert changed_data["available"] is False

        disabled = await client.post(
            "/api/admin/workflows/wf_lesson_plan/disable", headers=headers
        )
        assert disabled.json()["data"]["enabled"] is False
        assert disabled.json()["data"]["available"] is False

        deleted = await client.delete("/api/admin/workflows/wf_lesson_plan", headers=headers)
        assert deleted.json()["code"] == 0
        missing = await client.get("/api/admin/workflows/wf_lesson_plan", headers=headers)
        assert missing.json()["code"] == 40400
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_teacher_workflow_list_and_admin_authorization(client):
    """M3 开启时列表必须覆盖七条标准工作流，教师角色不得修改管理配置。"""
    await _cleanup()
    try:
        response = await client.get(
            "/api/admin/workflows", headers=await _role_headers("admin")
        )
        assert response.json()["code"] == 0
        names = {item["workflow_name"] for item in response.json()["data"]["workflows"]}
        assert names >= TEACHER_WORKFLOWS

        denied = await client.post(
            "/api/admin/workflows",
            json={
                "workflow_name": "wf_lesson_plan",
                "capability": "adapt_lesson",
                "workflow_id": "forbidden",
            },
            headers=await _role_headers("teacher"),
        )
        assert denied.status_code == 403

        invalid = await client.post(
            "/api/admin/workflows",
            json={
                "workflow_name": "wf_lesson_plan",
                "capability": "create_quiz",
                "workflow_id": "wrong-capability",
            },
            headers=await _role_headers("admin"),
        )
        assert invalid.json()["code"] == 40001
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_workflow_database_config_survives_redis_outage(client):
    """Redis 只是运行时缓存；故障时不得阻断数据库配置写入。"""
    await _cleanup()
    failing_redis = MagicMock()
    failing_redis.set = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    try:
        with patch("app.gateway.admin_router.get_redis", return_value=failing_redis):
            response = await client.post(
                "/api/admin/workflows",
                json={
                    "workflow_name": "wf_lesson_plan",
                    "capability": "adapt_lesson",
                    "enabled": True,
                    "workflow_id": "flow-without-redis",
                },
                headers=await _role_headers("admin"),
            )
        assert response.status_code == 200
        assert response.json()["code"] == 0
        async with async_session_factory() as session:
            stored = (
                await session.execute(select(SystemConfig).where(SystemConfig.key == "workflows"))
            ).scalar_one()
        assert stored.value["wf_lesson_plan"]["workflow_id"] == "flow-without-redis"
    finally:
        await _cleanup()
