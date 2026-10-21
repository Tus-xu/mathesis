"""学生端 AI 管家 v2 对话（POST /api/butler/chat）合同测试。

- mock Planner 的 ModelRouter（仿 test_butler_model_adapter.py / 教师端 chat 测试范式）
  返回固定 plan JSON → student.practice.generate；
- 注册 fake "practice.generate" 任务 handler（仿 test_task_center.demo_handler），
  避免真出题；工具只建任务（queued+spawn），完成通知由任务中心发；
- 覆盖：a) v2 开启走 v2 并创建 tasks 行；b) 同 client_request_id 幂等回放
  （同 run_id、tasks 只一行）；c) v2 关闭走 v1 兜底不报错。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.agent_run import AgentRun
from app.models.database import get_db
from app.models.task import Task
from app.services import task_runner
from tests._m3_helpers import make_user, token


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


@pytest.fixture
def fake_practice_handler():
    """fake practice.generate handler：记录执行次数，不触真出题内核。"""
    calls: list[str] = []
    prev = task_runner._HANDLERS.get("practice.generate")

    async def handler(task: Task, db: AsyncSession, progress) -> dict:
        calls.append(str(task.id))
        await progress("处理中", 50)
        return {"artifact_type": "practice", "quiz_id": str(uuid.uuid4()), "jump": "/tasks"}

    task_runner._HANDLERS["practice.generate"] = handler
    yield handler, calls
    if prev is None:
        task_runner._HANDLERS.pop("practice.generate", None)
    else:
        task_runner._HANDLERS["practice.generate"] = prev


class _PlannerRouter:
    """模拟 ModelRouter.chat：固定返回 plan JSON（Planner 的 LLM mock）。"""

    def __init__(self, plan: dict):
        self.plan = plan

    async def chat(self, *args, **kwargs):
        return {"content": json.dumps(self.plan, ensure_ascii=False)}


def _practice_plan() -> dict:
    return {
        "intent": "practice_request",
        "goal": "为你生成专属练习",
        "actions": [
            {
                "tool_name": "student.practice.generate",
                "arguments": {"kp_code": "函数", "count": 5},
                "reason": "学生要练函数题",
            }
        ],
        "response_mode": "direct",
        "needs_web_search": False,
    }


def _auth(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


async def _wait_terminal(task_id: uuid.UUID, timeout_s: float = 8.0) -> Task:
    """轮询等待任务到终态（runner 在事件循环内异步执行）。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        async with _test_session_factory() as db:
            task = await db.get(Task, task_id)
            if task is not None and task.status in ("succeeded", "failed", "cancelled"):
                return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout_s}s 内到达终态")


# ==================== a) v2 开启：走 v2 并创建 tasks 行 ====================


@pytest.mark.asyncio
async def test_butler_chat_v2_creates_task_row(client, fake_practice_handler, monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", True)
    async with _test_session_factory() as db:
        student_id = await make_user(db)
        await db.commit()
    crid = f"student-chat-{uuid.uuid4().hex}"
    with patch(
        "app.providers.router.get_model_router_for_user",
        new=AsyncMock(return_value=_PlannerRouter(_practice_plan())),
    ):
        response = await client.post(
            "/api/butler/chat",
            json={"message": "帮我出5道函数的练习题", "client_request_id": crid},
            headers=_auth(token(student_id, "student")),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    envelope = body["data"]["envelope"]
    assert envelope["run_id"]
    assert envelope["replies"][0]["kind"] == "text"
    assert "为你生成专属练习" in envelope["replies"][0]["text"]
    assert envelope["actions"][0]["type"] == "task_created"
    assert envelope["actions"][0]["label"] == "练习生成"
    assert envelope["actions"][0]["jump"] == "/tasks"
    task_id = uuid.UUID(envelope["actions"][0]["task_id"])

    # tasks 行：按幂等键规则下单，只此一行
    async with _test_session_factory() as db:
        rows = (
            await db.execute(
                select(Task).where(Task.idempotency_key == f"butler:{crid}:practice")
            )
        ).scalars().all()
        run = (
            await db.execute(select(AgentRun).where(AgentRun.client_request_id == crid))
        ).scalar_one()
    assert len(rows) == 1
    assert rows[0].id == task_id
    assert rows[0].kind == "practice.generate"
    assert rows[0].user_id == student_id
    # kp 名称解析：mock 传「函数」→ 工具解析为真实知识点编码后下单
    assert rows[0].payload["kp_code"].startswith("MATH-")
    assert rows[0].payload["kp_code"] != "函数"
    assert rows[0].payload["count"] == 5
    assert run.user_id == student_id
    assert run.role == "student"
    assert run.scene == "student.chat"
    assert run.status == "succeeded"
    assert run.tool_call_count == 1

    # fake handler 真正被 spawn 执行并到终态
    task = await _wait_terminal(task_id)
    assert task.status == "succeeded"
    assert fake_practice_handler[1] == [str(task_id)]


# ==================== b) 幂等：同 client_request_id 两次 ====================


@pytest.mark.asyncio
async def test_butler_chat_duplicate_client_request_id_replays(client, fake_practice_handler, monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", True)
    async with _test_session_factory() as db:
        student_id = await make_user(db)
        await db.commit()
    crid = f"student-chat-idem-{uuid.uuid4().hex}"
    payload = {"message": "帮我出5道函数的练习题", "client_request_id": crid}
    headers = _auth(token(student_id, "student"))
    with patch(
        "app.providers.router.get_model_router_for_user",
        new=AsyncMock(return_value=_PlannerRouter(_practice_plan())),
    ):
        first = await client.post("/api/butler/chat", json=payload, headers=headers)
        second = await client.post("/api/butler/chat", json=payload, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    env1 = first.json()["data"]["envelope"]
    env2 = second.json()["data"]["envelope"]
    assert env1["run_id"] == env2["run_id"]  # 幂等回放：非随机新 run
    assert env2["replies"][0]["text"] == "重复请求已忽略"
    assert env2["actions"] == []

    async with _test_session_factory() as db:
        runs = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.user_id == student_id,
                    AgentRun.client_request_id == crid,
                )
            )
        ).scalars().all()
        tasks = (
            await db.execute(
                select(Task).where(Task.idempotency_key == f"butler:{crid}:practice")
            )
        ).scalars().all()
    assert len(runs) == 1  # 账本唯一约束：不产生重复运行
    assert len(tasks) == 1  # tasks 只一行
    if env1["actions"]:
        assert uuid.UUID(env1["actions"][0]["task_id"]) == tasks[0].id


# ==================== c) v2 关闭：走旧 v1 兜底不报错 ====================


@pytest.mark.asyncio
async def test_butler_chat_v2_disabled_falls_back_to_v1(client, monkeypatch):
    monkeypatch.setattr(settings, "butler_v2_enabled", False)
    async with _test_session_factory() as db:
        student_id = await make_user(db)
        await db.commit()
    crid = f"student-chat-v1-{uuid.uuid4().hex}"
    with patch(
        "app.providers.router.get_model_router_for_user",
        new=AsyncMock(return_value=_PlannerRouter(_practice_plan())),
    ):
        response = await client.post(
            "/api/butler/chat",
            json={"message": "帮我出5道函数的练习题", "client_request_id": crid},
            headers=_auth(token(student_id, "student")),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    envelope = body["data"]["envelope"]
    assert envelope["run_id"] is None
    assert envelope["actions"] == []
    assert envelope["replies"][0]["kind"] == "text"

    # 不进 v2：无运行账本、无任务行
    async with _test_session_factory() as db:
        runs = (
            await db.execute(select(AgentRun).where(AgentRun.client_request_id == crid))
        ).scalars().all()
        tasks = (
            await db.execute(
                select(Task).where(Task.idempotency_key.like(f"butler:{crid}:%"))
            )
        ).scalars().all()
    assert runs == []
    assert tasks == []
