"""M3 教师 Butler Chat HTTP 合同。"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.agent_run import AgentRun
from app.models.database import async_session_factory
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


def _auth(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


class _PlannerRouter:
    def __init__(self, plan: dict | None = None):
        self.plan = plan or {
            "intent": "teacher_help",
            "goal": "已读取教师请求",
            "actions": [],
            "response_mode": "direct",
            "needs_web_search": False,
        }

    async def chat(self, *args, **kwargs):
        return {"content": json.dumps(self.plan, ensure_ascii=False)}


@pytest.mark.asyncio
async def test_teacher_butler_chat_runs_kernel_and_writes_ledger(client):
    """路由缺失、绕过 Kernel 或未记教师账本时必须失败。"""
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        class_id = await make_class(db, teacher_id)
        await db.commit()
    client_request_id = f"teacher-chat-{uuid.uuid4().hex}"
    with patch(
        "app.providers.router.get_model_router_for_user",
        new=AsyncMock(return_value=_PlannerRouter()),
    ):
        response = await client.post(
            "/api/teacher/butler/chat",
            json={
                "scene": "teacher.prep",
                "classId": str(class_id),
                "userMessage": "帮我准备函数单调性的课堂导入",
                "clientRequestId": client_request_id,
            },
            headers=_auth(token(teacher_id, "teacher")),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["message"] == "已读取教师请求"
    assert body["data"]["degraded"] is False
    assert body["data"]["confirmation_required"] is False
    assert body["data"]["run_id"]

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.client_request_id == client_request_id)
            )
        ).scalar_one()
    assert run.user_id == teacher_id
    assert run.role == "teacher"
    assert run.scene == "teacher.prep"


@pytest.mark.asyncio
async def test_teacher_butler_chat_never_auto_executes_formal_write(client):
    """模型即使计划正式发布，聊天也只提示确认，不执行 WRITE handler。"""
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        class_id = await make_class(db, teacher_id)
        await db.commit()
    client_request_id = f"teacher-chat-write-{uuid.uuid4().hex}"
    plan = {
        "intent": "publish_assignment",
        "goal": "发布作业",
        "actions": [
            {
                "tool_name": "teacher.assignment.publish",
                "arguments": {
                    "class_id": str(class_id),
                    "assignment_id": str(uuid.uuid4()),
                },
                "reason": "教师要求发布",
            }
        ],
        "response_mode": "cards",
        "needs_web_search": False,
    }
    with patch(
        "app.providers.router.get_model_router_for_user",
        new=AsyncMock(return_value=_PlannerRouter(plan)),
    ):
        response = await client.post(
            "/api/teacher/butler/chat",
            json={
                "scene": "teacher.assessment",
                "classId": str(class_id),
                "userMessage": "发布这份作业",
                "clientRequestId": client_request_id,
            },
            headers=_auth(token(teacher_id, "teacher")),
        )
    data = response.json()["data"]
    assert data["confirmation_required"] is True
    assert data["degraded"] is True
    assert data["trace"]["error_code"] == "risk_denied"
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.client_request_id == client_request_id)
            )
        ).scalar_one()
    assert run.tool_call_count == 0
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_teacher_butler_chat_enforces_role_scene_scope_and_extra_fields(client):
    """学生、非法场景、他班 classId 或客户端注入工具字段均不得进入 Runtime。"""
    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        class_id = await make_class(db, owner)
        await db.commit()
    base = {
        "scene": "teacher.prep",
        "classId": str(class_id),
        "userMessage": "备课",
        "clientRequestId": f"teacher-chat-deny-{uuid.uuid4().hex}",
    }
    student = await client.post(
        "/api/teacher/butler/chat", json=base, headers=_auth(token(owner, "student"))
    )
    assert student.json()["code"] == 40301

    invalid_scene = await client.post(
        "/api/teacher/butler/chat",
        json={**base, "scene": "student.chat"},
        headers=_auth(token(owner, "teacher")),
    )
    assert invalid_scene.json()["code"] == 40001

    foreign = await client.post(
        "/api/teacher/butler/chat", json=base, headers=_auth(token(other, "teacher"))
    )
    assert foreign.json()["code"] == 40302

    injected = await client.post(
        "/api/teacher/butler/chat",
        json={**base, "toolName": "teacher.assignment.publish"},
        headers=_auth(token(owner, "teacher")),
    )
    assert injected.status_code == 422
