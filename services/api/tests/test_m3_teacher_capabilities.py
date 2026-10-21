"""M3 教师端：Capability Gateway（§10.4 / §16）。"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import settings
from app.domains.teacher import capability_gateway, workflow_adapter
from app.main import app
from app.models.database import async_session_factory
from app.models.system_config import SystemConfig, upsert_system_config
from app.providers.crypto import encrypt_api_key
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "payload", "required_key"),
    [
        ("adapt_lesson", {"topic": "函数", "duration_minutes": 45}, "timeline"),
        ("create_slides", {"topic": "函数", "timeline": [{"phase": "导入", "minutes": 5}]}, "slides"),
        ("create_quiz", {"knowledge_points": ["MATH-002"], "count": 3}, "items"),
        ("suggest_grade", {"answer": "2x"}, "needs_review"),
        ("explain_problem", {"question": "如何求导？"}, "steps"),
        ("preprocess_course", {"resource_id": "local-resource", "text": "函数与导数"}, "slices"),
        ("understand_document", {"question": "本文要点", "text": "函数描述变量关系。"}, "summary"),
    ],
)
async def test_all_capabilities_have_usable_local_fallback(capability, payload, required_key):
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        with patch.object(
            capability_gateway.adapter,
            "run",
            new=AsyncMock(
                return_value={
                    "status": "degraded",
                    "content": {},
                    "warnings": ["workflow_disabled"],
                }
            ),
        ):
            result = await capability_gateway.run_capability(
                db,
                teacher_id,
                scene="teacher.prep",
                class_id=None,
                capability=capability,
                payload=payload,
            )
    assert result["engine"] == "local"
    assert result["degraded"] is True
    if capability == "create_quiz":
        assert result["payload"]["items"] == []
        assert result["payload"]["insufficient"] is True
        assert any("题库" in warning for warning in result["warnings"])
    else:
        assert result["payload"][required_key]


@pytest.mark.asyncio
async def test_capability_adapt_lesson_creates_artifact(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    r = await client.post(
        "/api/teacher/capabilities/adapt_lesson",
        json={"scene": "teacher.prep", "class_id": str(cid),
              "payload": {"topic": "函数", "requirements": "精简"},
              "client_request_id": "cap"},
        headers=_auth(token(tid, "teacher")),
    )
    assert r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["status"] == "draft"
    assert data["artifact_id"]


@pytest.mark.asyncio
async def test_unavailable_workflow_marks_local_artifact_degraded(client):
    """远程工作流关闭时，本地草稿若误标成完整成功则必须失败。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    with patch.object(settings, "xingchen_enabled", False):
        response = await client.post(
            "/api/teacher/capabilities/adapt_lesson",
            json={
                "scene": "teacher.prep",
                "class_id": str(cid),
                "payload": {"topic": "函数"},
                "client_request_id": "cap-degraded",
            },
            headers=_auth(token(tid, "teacher")),
        )
    data = response.json()["data"]
    assert data["engine"] == "local"
    assert data["degraded"] is True
    assert any("workflow_disabled" in warning for warning in data["warnings"])


@pytest.mark.asyncio
async def test_adapter_reads_database_mapping_and_per_workflow_config():
    """适配器若绕过数据库配置或漏做输入/输出映射时必须失败。"""
    flow_output = {
        "data": {"remote_title": "函数单调性教案"},
        "trace_id": "trace-db-config",
    }
    remote = AsyncMock(return_value=flow_output)
    teacher_id = uuid.uuid4()
    try:
        async with async_session_factory() as db:
            await upsert_system_config(
                db,
                "xingchen.global",
                {
                    "enabled": True,
                    "base_url": "https://global.example/api",
                    "api_key": encrypt_api_key("global-key"),
                    "api_secret": encrypt_api_key("global-secret"),
                },
            )
            await upsert_system_config(
                db,
                "workflows",
                {
                    "wf_lesson_plan": {
                        "capability": "adapt_lesson",
                        "enabled": True,
                        "base_url": "https://lesson.example/api",
                        "api_key": encrypt_api_key("lesson-key"),
                        "api_secret": encrypt_api_key("lesson-secret"),
                        "workflow_id": "lesson-flow-id",
                        "input_mapping": {"topic": "subject"},
                        "output_mapping": {"remote_title": "title"},
                        "timeout_seconds": 9,
                        "retry_count": 1,
                        "last_test_status": "success",
                    }
                },
            )
            await db.commit()
            with patch("app.providers.xingchen.run_workflow", new=remote):
                result = await workflow_adapter.run(
                    "adapt_lesson",
                    {"teacher_id": str(teacher_id), "payload": {"topic": "函数单调性"}},
                    db=db,
                )
        assert result["status"] == "succeeded"
        assert result["content"] == {"title": "函数单调性教案"}
        call = remote.await_args
        assert call.kwargs["parameters"] == {"subject": "函数单调性"}
        assert call.kwargs["read_timeout"] == 9.0
        assert call.kwargs["config"].workflow_base_urls["wf_lesson_plan"] == (
            "https://lesson.example/api"
        )
    finally:
        async with async_session_factory() as db:
            await db.execute(
                delete(SystemConfig).where(
                    SystemConfig.key.in_(("workflows", "xingchen.global"))
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_capability_requires_teacher_role(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    r = await client.post(
        "/api/teacher/capabilities/adapt_lesson",
        json={"scene": "teacher.prep", "class_id": str(cid), "payload": {"topic": "t"},
              "client_request_id": "cap"},
        headers=_auth(token(tid, "student")),
    )
    assert r.json()["code"] == 40301


@pytest.mark.asyncio
async def test_capabilities_list_excludes_research(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        await db.commit()
    r = await client.get("/api/teacher/capabilities", headers=_auth(token(tid, "teacher")))
    caps = set(r.json()["data"]["capabilities"])
    assert {"adapt_lesson", "create_slides", "create_quiz", "suggest_grade",
            "explain_problem", "preprocess_course", "understand_document"} <= caps
    assert "review_paper" not in caps


@pytest.mark.asyncio
async def test_capability_rejects_invalid_scene(client):
    """审计 C-02：拒绝任意 scene（不在教师 Scene 白名单）。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    r = await client.post(
        "/api/teacher/capabilities/adapt_lesson",
        json={"scene": "student.chat", "class_id": str(cid), "payload": {"topic": "t"},
              "client_request_id": "cap"},
        headers=_auth(token(tid, "teacher")),
    )
    assert r.json()["code"] == 40001
    assert r.json()["message"] == "invalid_scene"


@pytest.mark.asyncio
async def test_capability_rejects_foreign_class(client):
    """审计 C-02：class scope 强制（他班 class_id 拒绝）。"""
    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, owner)
        await db.commit()
    r = await client.post(
        "/api/teacher/capabilities/adapt_lesson",
        json={"scene": "teacher.prep", "class_id": str(cid), "payload": {"topic": "t"},
              "client_request_id": "cap"},
        headers=_auth(token(other, "teacher")),
    )
    assert r.json()["code"] == 40302


@pytest.mark.asyncio
async def test_capability_rejects_unknown_capability(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        await db.commit()
    r = await client.post(
        "/api/teacher/capabilities/review_paper",
        json={"scene": "teacher.prep", "payload": {}, "client_request_id": "cap"},
        headers=_auth(token(tid, "teacher")),
    )
    assert r.json()["code"] == 40001
    assert r.json()["message"] == "unknown_capability"
