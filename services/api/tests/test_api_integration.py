"""API 集成测试

使用 httpx.AsyncClient 测试完整 API 流程。
需要 PostgreSQL、Redis 运行中，且 DeepSeek API Key 可用。
"""

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db


def _make_test_engine():
    """创建测试用 NullPool engine —— 每次连接独立，无跨循环问题"""
    return create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )


_test_engine = _make_test_engine()
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    """测试用 get_db 覆盖 —— NullPool 每次请求独立连接"""
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
    """创建测试客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ========== 健康检查 ==========


class TestHealthEndpoints:
    """健康检查端点测试"""

    async def test_health(self, client):
        """GET /api/health → 200"""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    async def test_model_health(self, client):
        """GET /api/health/models → 200, 包含三通道"""
        resp = await client.get("/api/health/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "spark" in data
        assert "deepseek" in data
        assert "embedding" in data
        # Verify response structure
        assert "ok" in data["deepseek"]
        assert "latency_ms" in data["deepseek"]
        # Skip ok=True assertion when DeepSeek API is unavailable
        # (e.g. CI without API key)
        if not data["deepseek"]["ok"]:
            pytest.skip("DeepSeek API not available in this environment")


# ========== 认证流程 ==========


class TestAuthFlow:
    """认证完整流程测试"""

    async def test_send_sms_code(self, client):
        """POST /api/auth/sms-code → 200"""
        import uuid as _uuid

        phone = f"138{_uuid.uuid4().int % 100000000:08d}"
        resp = await client.post("/api/auth/sms-code", json={"phone": phone})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0

    async def test_send_sms_code_invalid_phone(self, client):
        """无效手机号 → 422"""
        resp = await client.post("/api/auth/sms-code", json={"phone": "123"})
        assert resp.status_code == 422

    async def test_login_with_correct_code(self, client):
        """正确验证码登录 → 200, 获取 token"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        # 先发送验证码
        await client.post("/api/auth/sms-code", json={"phone": phone})
        # 登录（开发环境固定验证码 123456）
        resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "token" in data["data"]
        assert data["data"]["user"]["is_new"] is True
        assert data["data"]["user"]["active_role"] == "student"

    async def test_login_wrong_code(self, client):
        """错误验证码登录 → 验证码错误"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        # 先发送验证码
        await client.post("/api/auth/sms-code", json={"phone": phone})
        resp = await client.post("/api/auth/login", json={"phone": phone, "code": "000000"})
        data = resp.json()
        assert data["code"] != 0

    async def test_login_no_code_sent(self, client):
        """未发送验证码就登录 → 验证码已过期"""
        phone = f"139{str(uuid.uuid4().int)[:8]}"
        resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        data = resp.json()
        assert data["code"] != 0

    async def test_me_endpoint(self, client):
        """GET /api/auth/me → 200, 返回用户信息"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        # 登录获取 token
        await client.post("/api/auth/sms-code", json={"phone": phone})
        login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token = login_resp.json()["data"]["token"]

        # 调用 /me
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["active_role"] == "student"

    async def test_me_without_auth(self, client):
        """未认证访问 /me → 401"""
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_role_switch(self, client):
        """POST /api/auth/role/switch → 换发 token"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone})
        login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token = login_resp.json()["data"]["token"]

        # 切换到 student（默认角色，应该成功）
        resp = await client.post(
            "/api/auth/role/switch",
            json={"role": "student"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "token" in data["data"]

    async def test_role_switch_unauthorized(self, client):
        """未认证切换角色 → 401"""
        resp = await client.post("/api/auth/role/switch", json={"role": "student"})
        assert resp.status_code == 401


# ========== Agent 流程 ==========


class TestAgentFlow:
    """Agent 完整流程测试"""

    @pytest_asyncio.fixture
    async def auth_client(self, client):
        """已认证的客户端"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone})
        login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token = login_resp.json()["data"]["token"]
        return client, token, phone

    async def test_create_conversation(self, auth_client):
        """POST /api/agent/conversations → 200（API 文档 §4.3）"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/agent/conversations",
            json={"workspace": "student"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "id" in data["data"]
        assert data["data"]["activeRole"] == "student"

    async def test_list_conversations(self, auth_client):
        """GET /api/agent/conversations → 200"""
        client, token, _ = auth_client
        # 先创建一个会话
        await client.post(
            "/api/agent/conversations",
            json={"workspace": "student"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 获取列表
        resp = await client.get(
            "/api/agent/conversations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert data["data"]["total"] >= 1

    async def test_chat_sse_flow(self, auth_client):
        """POST /api/agent/chat → SSE 流式响应（对齐 API 文档 §4.1）"""
        client, token, _ = auth_client
        client_msg_id = str(uuid.uuid4())

        resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "1+1等于多少？",
                "context": {
                    "client_msg_id": client_msg_id,
                    "workspace": "student",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        # 解析 SSE 事件（协议 §4.3：event: <type>\ndata: <flat_json>\n\n）
        text = resp.text
        events = []
        for line in text.split("\n"):
            if line.startswith("event: "):
                events.append(line[7:])

        # 至少包含 meta 事件
        assert len(events) > 0
        assert "meta" in events

    async def test_chat_and_get_messages(self, auth_client):
        """发消息后获取历史消息（对齐 API 文档 §4.1 schema）"""
        client, token, _ = auth_client
        client_msg_id = str(uuid.uuid4())

        # 发送消息（会创建新会话）—— 使用 API 文档 §4.1 schema
        chat_resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "什么是勾股定理？",
                "context": {
                    "client_msg_id": client_msg_id,
                    "workspace": "student",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert chat_resp.status_code == 200

        # 从 SSE meta 事件提取 conversation_id（协议 §4.3：data 行直接是事件内容）
        text = chat_resp.text
        conversation_id = None
        for line in text.split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    # 新协议：data 行直接包含 conversation_id（无 type/data 嵌套）
                    if "conversation_id" in data and "msg_id" in data:
                        conversation_id = data["conversation_id"]
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

        assert conversation_id is not None

        # 获取历史消息
        msg_resp = await client.get(
            f"/api/agent/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert msg_resp.status_code == 200
        msg_data = msg_resp.json()
        assert msg_data["code"] == 0
        items = msg_data["data"]["items"]
        assert len(items) >= 1  # 至少有用户消息

    async def test_conversations_unauthorized(self, client):
        """未认证访问会话列表 → 401"""
        resp = await client.get("/api/agent/conversations")
        assert resp.status_code == 401

    async def test_chat_unauthorized(self, client):
        """未认证聊天 → 401"""
        resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "hi",
                "context": {"client_msg_id": "test-123", "workspace": "student"},
            },
        )
        assert resp.status_code == 401


# ========== SSE 协议格式测试 ==========


class TestSSEProtocolFormat:
    """验证 SSE 输出格式符合协议 §4.3：event: <type>\ndata: <flat_json>\n\n"""

    @pytest_asyncio.fixture
    async def auth_client(self, client):
        """已认证的客户端"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone})
        login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token = login_resp.json()["data"]["token"]
        return client, token

    async def test_sse_no_nested_type_data(self, auth_client):
        """SSE data 行不应包含嵌套的 type/data 包装"""
        client, token = auth_client
        client_msg_id = str(uuid.uuid4())

        resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "hello",
                "context": {
                    "client_msg_id": client_msg_id,
                    "workspace": "student",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        text = resp.text
        for line in text.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                # 新协议：data 行直接是事件内容，不应有 {type, data} 嵌套
                has_nested = "type" in data and "data" in data
                assert not has_nested, f"SSE data 行存在嵌套 type/data 包装: {data}"

    async def test_sse_meta_event_has_flat_fields(self, auth_client):
        """meta 事件 data 行直接包含 conversation_id, msg_id 等字段"""
        client, token = auth_client
        client_msg_id = str(uuid.uuid4())

        resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "test",
                "context": {
                    "client_msg_id": client_msg_id,
                    "workspace": "student",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        text = resp.text
        meta_found = False
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if (
                line.startswith("event: meta")
                and i + 1 < len(lines)
                and lines[i + 1].startswith("data: ")
            ):
                # 下一行应为 data: {...}
                data = json.loads(lines[i + 1][6:])
                assert "conversation_id" in data, "meta 事件缺少 conversation_id"
                assert "msg_id" in data, "meta 事件缺少 msg_id"
                assert "skill" in data, "meta 事件缺少 skill"
                meta_found = True
                break

        assert meta_found, "未找到 meta 事件"


# ========== 幂等重放测试 ==========


class TestIdempotentReplay:
    """幂等重放测试（手册 §6.2）"""

    @pytest_asyncio.fixture
    async def auth_client(self, client):
        """已认证的客户端"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone})
        login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token = login_resp.json()["data"]["token"]
        return client, token

    async def test_idempotent_replay_returns_header(self, auth_client):
        """同 client_msg_id 第二次请求应收到 SSE 重放 + X-Idempotent-Replay: true"""
        client, token = auth_client
        client_msg_id = str(uuid.uuid4())

        chat_body = {
            "message": "幂等测试消息",
            "context": {
                "client_msg_id": client_msg_id,
                "workspace": "student",
            },
        }

        # 第一次请求
        resp1 = await client.post(
            "/api/agent/chat",
            json=chat_body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 200
        # 消耗第一次 SSE 流
        first_text = resp1.text

        # CI 环境可能没有 API Key，模型调用会失败导致无内容可重放
        if "event: error" in first_text:
            pytest.skip("Model API not available in CI, skipping idempotent replay test")

        # 第二次请求（相同 client_msg_id）
        resp2 = await client.post(
            "/api/agent/chat",
            json=chat_body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200

        # 检查 X-Idempotent-Replay 头
        replay_header = resp2.headers.get("X-Idempotent-Replay", "")
        assert (
            replay_header.lower() == "true"
        ), f"第二次请求应返回 X-Idempotent-Replay: true，实际: {replay_header}"

        # 重放流也应包含 SSE 事件
        text2 = resp2.text
        events = [ln[7:] for ln in text2.split("\n") if ln.startswith("event: ")]
        assert "meta" in events, "重放流应包含 meta 事件"


class TestSSEHeartbeat:
    """心跳不杀慢事件（回归：旧实现 wait_for 超时会 cancel 在产的 __anext__，
    非流式 LLM 调用超过心跳间隔时整个回答静默丢失——实测压轴题曾因此空流）"""

    @pytest_asyncio.fixture
    async def auth_client(self, client):
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone})
        login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token = login_resp.json()["data"]["token"]
        return client, token

    async def test_slow_skill_event_survives_heartbeat(self, auth_client):
        import asyncio as _asyncio
        from unittest.mock import patch

        from app.kernel.router import RouteDecision

        client, token = auth_client

        class _SlowSkill:
            manifest = {"id": "chat", "name": "对话", "version": "0.0.1"}

            async def run(self, params, ctx):
                await _asyncio.sleep(0.3)  # 超过心跳间隔（测试 patch 为 0.05s）
                yield {"type": "token", "data": {"text": "slow-ok"}}
                yield {"type": "_result_meta", "data": {"confidence": 0.9}}

        class _FakeRegistry:
            def get(self, skill_id):
                return _SlowSkill() if skill_id == "chat" else None

        class _FakeIntentRouter:
            async def route(self, message, **kwargs):
                # 复用真实已注册的 chat skill_id（skill_runs 有外键约束）
                return RouteDecision(
                    skill_id="chat", confidence=0.99, params={"question": message}
                )

        with (
            patch("app.gateway.agent_router.SSE_HEARTBEAT_SECONDS", 0.05),
            patch("app.gateway.agent_router.get_intent_router", return_value=_FakeIntentRouter()),
            patch("app.gateway.agent_router.get_skill_registry", return_value=_FakeRegistry()),
        ):
            resp = await client.post(
                "/api/agent/chat",
                json={
                    "message": "测试慢事件",
                    "context": {"client_msg_id": str(uuid.uuid4()), "workspace": "student"},
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert "slow-ok" in resp.text
