"""Butler Kernel v2 阶段 6A：HTTP/前端授权契约预接线（Task 阶段6）

覆盖：
- ChatContext 接受 web_search_opt_in（默认 False，extra 策略不变）；
- Facade build_student_chat_butler_request 固定 STUDENT + scene="student.chat"，
  不接受 scene/role 参数，web_search_opt_in 透传；
- xingchen.web_search allowed_scenes 含 student.chat（D4 最小范围）；
- Policy：student.chat 场景学生可执行 web_search，教师/管理员仍拒绝；
- GET /api/agent/features 返回 capabilities.web_search_opt_in_enabled=false
  （复用现有接口，不新建；v2 未切流期间恒为 false）。
"""

import uuid

from httpx import ASGITransport, AsyncClient
from pytest_asyncio import fixture as async_fixture

from app.butler.contracts import ActorContext, ActorRole, ButlerRequest, PlannedAction
from app.butler.facade import build_student_chat_butler_request
from app.butler.policy import PolicyGate
from app.butler.workflow_tools import build_workflow_registry
from app.config import settings
from app.gateway.agent_router import ChatContext
from app.main import app

# ---------- ChatContext ----------


def test_chat_context_accepts_web_search_opt_in():
    ctx = ChatContext(client_msg_id="c-1", web_search_opt_in=True)
    assert ctx.web_search_opt_in is True


def test_chat_context_web_search_opt_in_default_false():
    ctx = ChatContext(client_msg_id="c-1")
    assert ctx.web_search_opt_in is False


def test_chat_context_extra_policy_unchanged():
    """只新增字段，不改动 extra 策略：未知字段仍按原策略（默认忽略）。"""
    ctx = ChatContext(client_msg_id="c-1", unknown_field="x")
    assert ctx.web_search_opt_in is False


# ---------- Facade ----------


def test_facade_maps_web_search_opt_in():
    uid = uuid.uuid4()
    req = build_student_chat_butler_request(
        user_id=uid,
        message="帮我搜一下",
        conversation_id=None,
        client_request_id="crid-1",
        web_search_opt_in=True,
    )
    assert req.web_search_opt_in is True


def test_facade_web_search_opt_in_default_false():
    req = build_student_chat_butler_request(
        user_id=uuid.uuid4(),
        message="帮我搜一下",
        conversation_id=None,
        client_request_id="crid-1",
    )
    assert req.web_search_opt_in is False


def test_facade_fixed_student_chat_scene():
    """Facade 不接受 scene/role 参数，固定 STUDENT + student.chat。"""
    uid = uuid.uuid4()
    req = build_student_chat_butler_request(
        user_id=uid,
        message="帮我搜一下",
        conversation_id=None,
        client_request_id="crid-1",
    )
    assert req.actor.role == ActorRole.STUDENT
    assert req.actor.user_id == uid
    assert req.scene == "student.chat"


def test_facade_rejects_scene_role_params():
    """调用者不得传入 scene/role：签名无此参数（TypeError）。"""
    import inspect

    sig = inspect.signature(build_student_chat_butler_request)
    assert "scene" not in sig.parameters
    assert "role" not in sig.parameters


# ---------- Registry / Policy ----------


def test_registry_web_search_allows_student_chat():
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    assert "student.chat" in tool.allowed_scenes


def test_registry_other_tools_not_expanded():
    """D4 最小范围：仅 xingchen.web_search 增加 student.chat，其他工具不动。"""
    reg = build_workflow_registry()
    for name in reg.names():
        if name == "xingchen.web_search":
            continue
        assert "student.chat" not in reg.get(name).allowed_scenes


def test_policy_student_chat_web_search_visible():
    reg = build_workflow_registry()
    policy = PolicyGate(reg)
    request = ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT),
        message="帮我搜一下",
        scene="student.chat",
        client_request_id="crid-1",
    )
    action = PlannedAction(
        tool_name="xingchen.web_search",
        arguments={"query": "导数应用", "max_results": 3},
        reason="test",
    )
    decision = policy.validate_action(
        request, action, external_allowed=True, web_search_enabled=True
    )
    assert decision.allowed is True


def test_policy_student_chat_web_search_denied_for_teacher():
    reg = build_workflow_registry()
    policy = PolicyGate(reg)
    request = ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.TEACHER),
        message="帮我搜一下",
        scene="student.chat",
        client_request_id="crid-1",
    )
    action = PlannedAction(
        tool_name="xingchen.web_search",
        arguments={"query": "导数应用", "max_results": 3},
        reason="test",
    )
    decision = policy.validate_action(
        request, action, external_allowed=True, web_search_enabled=True
    )
    assert decision.allowed is False


# ---------- features 端点 ----------


@async_fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register(client) -> tuple[str, str]:
    phone = f"138{uuid.uuid4().int % 100000000:08d}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    login = await client.post(
        "/api/auth/login", json={"phone": phone, "code": settings.dev_sms_code}
    )
    data = login.json()["data"]
    return data["token"], data["user"]["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_features_capabilities_web_search_opt_in_enabled_false(client):
    token, _ = await _register(client)
    resp = await client.get("/api/agent/features", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    caps = body["data"]["capabilities"]
    assert caps["web_search_opt_in_enabled"] is False
