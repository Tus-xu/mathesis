"""迭代06 三个工作流最终落地测试（wf_verify_derivation / wf_intent_router / wf_socratic_chat）

覆盖：
1. research 端点 POST /api/research/derivations/verify：星辰优先（mock）→ 本地降级（真实沙箱）→ 学生越权 403
2. wf_intent_router 影子评测：路由决策后 router_eval_logs 落库（agree 判定）；星辰关闭不落库
3. wf_socratic_chat 流式通道：星辰成功 token 流 → 星辰失败降级本地 LLM → 双挂 error 事件

需要 PostgreSQL + Redis 运行中。
"""

import asyncio
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
from app.models.database import get_db
from app.models.role_binding import RoleBinding

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
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


async def _register(client, role: str | None = None) -> tuple[str, str]:
    """注册用户（可选提升角色），返回 (token, user_id)"""
    phone = f"139{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    token, user_id = data["token"], data["user"]["id"]
    if role:
        async with _test_session_factory() as db:
            db.add(RoleBinding(user_id=user_id, role=role, verified=True))
            await db.commit()
        switch = await client.post(
            "/api/auth/role/switch",
            json={"role": role},
            headers={"Authorization": f"Bearer {token}"},
        )
        token = switch.json().get("data", {}).get("token") or token
    return token, user_id


# ==================== 1. wf_verify_derivation（research 端点） ====================


class TestResearchVerify:
    # 阶段 1 契约护栏：F14 科研端点仅在科研模式（M2_ENABLE_RESEARCH=true）挂载。
    # M2 默认 profile 下路由面排除（见 tests/test_m2_route_profile.py）；科研代码不删除。
    pytestmark = pytest.mark.skipif(
        not settings.m2_enable_research,
        reason="M2 默认 profile 不挂载 research_router（F14）；科研模式置 M2_ENABLE_RESEARCH=true 后恢复",
    )

    async def test_student_forbidden(self, client):
        """学生访问 research 端点 → 403（SSOT §1.2：student 端 Out of Scope）"""
        token, _ = await _register(client)
        resp = await client.post(
            "/api/research/derivations/verify",
            json={"derivation_text": "1+1=2"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_xingchen_preferred(self, client):
        """星辰开启 + flow 配置 → 工作流输出（engine=xingchen）"""
        token, _ = await _register(client, role="teacher")
        wf_out = {
            "verdict": "consistent",
            "steps": [{"step_no": 1, "claim": "c", "code": "print(True)", "exec_status": "pass", "doubt": ""}],
            "generated_code": "print(True)",
        }
        with patch.object(settings, "xingchen_enabled", True), patch.object(
            settings, "xingchen_flow_ids", '{"wf_verify_derivation": "flow-1"}'
        ), patch(
            "app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)
        ) as m_run:
            resp = await client.post(
                "/api/research/derivations/verify",
                json={"derivation_text": "若 a=b 则 a+1=b+1", "expected_result": "a+1=b+1"},
                headers={"Authorization": f"Bearer {token}"},
            )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["verdict"] == "consistent"
        assert body["data"]["engine"] == "xingchen"
        m_run.assert_awaited_once()

    async def test_local_fallback_real_sandbox(self, client):
        """星辰挂/关闭 → 本地降级：LLM 生成代码 + 真实沙箱执行（engine=local）"""
        token, _ = await _register(client, role="researcher")
        fake_code = (
            "import sympy as sp\n"
            "x = sp.symbols('x')\n"
            "assert sp.simplify((x+1)**2 - (x**2 + 2*x + 1)) == 0\n"
            'print("VERIFIED")\n'
        )
        fake_llm = AsyncMock()
        fake_llm.chat.return_value = {
            "content": fake_code, "provider": "deepseek", "model": "m",
            "input_tokens": 10, "output_tokens": 10, "latency_ms": 1, "tool_calls": None,
        }
        with patch.object(settings, "xingchen_enabled", False), patch(
            "app.providers.router.get_model_router", return_value=fake_llm
        ):
            resp = await client.post(
                "/api/research/derivations/verify",
                json={"derivation_text": "展开 (x+1)^2 得 x^2+2x+1"},
                headers={"Authorization": f"Bearer {token}"},
            )
        body = resp.json()
        assert body["code"] == 0, body
        assert body["data"]["verdict"] == "consistent"
        assert body["data"]["engine"] == "local"
        assert body["data"]["generated_code"].startswith("import sympy")

    async def test_llm_down_50301(self, client):
        """星辰挂 + LLM 不可用 → 50301（不静默伪造结果）"""
        token, _ = await _register(client, role="teacher")
        fake_llm = AsyncMock()
        fake_llm.chat.side_effect = RuntimeError("llm down")
        with patch.object(settings, "xingchen_enabled", False), patch(
            "app.providers.router.get_model_router", return_value=fake_llm
        ):
            resp = await client.post(
                "/api/research/derivations/verify",
                json={"derivation_text": "1+1=2"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.json()["code"] == 50301

    async def test_invalid_domain_hint_422(self, client):
        """非法 domain_hint → 422（pydantic 约束）"""
        token, _ = await _register(client, role="teacher")
        resp = await client.post(
            "/api/research/derivations/verify",
            json={"derivation_text": "x", "domain_hint": "bogus"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422


# ==================== 2. wf_intent_router 影子评测 ====================


class TestShadowEval:
    async def test_shadow_log_written_with_agree(self, client):
        """星辰开启 + flow 配置：路由决策后 router_eval_logs 落库（agree 判定正确）"""
        from app.kernel.router import get_intent_router

        token, user_id = await _register(client)
        with patch.object(settings, "xingchen_enabled", True), patch.object(
            settings, "xingchen_flow_ids", '{"wf_intent_router": "flow-1"}'
        ), patch(
            "app.providers.xingchen.run_workflow", new=AsyncMock(return_value={"intent": "chat"})
        ):
            router = get_intent_router()
            async with _test_session_factory() as db:
                decision = await router.route(
                    "帮我解这道题",
                    db=db,
                    user_id=user_id,
                    surface="student",
                    request_id="t",
                )
            # 等待后台影子任务完成
            await asyncio.sleep(0.5)

        async with _test_session_factory() as db:
            from app.models.m2_logs import RouterEvalLog

            rows = (
                await db.execute(
                    select(RouterEvalLog)
                    .where(RouterEvalLog.utterance == "帮我解这道题")
                    .order_by(RouterEvalLog.id.desc())
                    .limit(1)
                )
            ).scalars().all()
        assert rows, "影子评测未落库"
        row = rows[0]
        assert row.local_decision == decision.skill_id
        assert row.xc_decision == "chat"
        assert row.agree == (decision.skill_id == "chat")

    async def test_no_log_when_xingchen_disabled(self, client):
        """星辰关闭：不触发影子评测（无 router_eval_logs 记录）"""
        from app.kernel.router import get_intent_router

        token, user_id = await _register(client)
        with patch.object(settings, "xingchen_enabled", False), patch(
            "app.providers.xingchen.run_workflow", new=AsyncMock()
        ) as m_run:
            router = get_intent_router()
            async with _test_session_factory() as db:
                await router.route(
                    "随便聊聊", db=db, user_id=user_id, surface="student", request_id="t"
                )
            await asyncio.sleep(0.3)
        m_run.assert_not_awaited()


# ==================== 3. wf_socratic_chat 流式通道 ====================


class TestSocraticChatChannel:
    async def test_xingchen_stream_success(self):
        """星辰成功：delta → token 事件 + _result_meta"""
        from app.gateway.agent_router import _socratic_chat_stream

        async def fake_stream(flow, **kwargs):
            yield {"type": "delta", "content": "你好"}
            yield {"type": "delta", "content": "，同学"}
            yield {"type": "step", "step": "s1"}

        llm = AsyncMock()
        events = []
        with patch("app.providers.xingchen.stream_workflow", new=fake_stream):
            async for ev in _socratic_chat_stream("你好", uid="u", chat_id=None, llm=llm):
                events.append(ev)
        tokens = [e["data"]["text"] for e in events if e["type"] == "token"]
        assert tokens == ["你好", "，同学"]
        assert any(e["type"] == "_result_meta" for e in events)
        llm.chat.assert_not_awaited()  # 星辰成功不降级

    async def test_fallback_local_llm(self):
        """星辰失败 → 降级本地 LLM 补全（degraded 标记）"""
        from app.gateway.agent_router import _socratic_chat_stream

        async def broken_stream(flow, **kwargs):
            raise RuntimeError("xingchen down")
            yield  # pragma: no cover

        llm = AsyncMock()
        llm.chat.return_value = {
            "content": "本地补全回答", "provider": "deepseek", "model": "m",
            "input_tokens": 5, "output_tokens": 5, "latency_ms": 1, "tool_calls": None,
        }
        events = []
        with patch("app.providers.xingchen.stream_workflow", new=broken_stream):
            async for ev in _socratic_chat_stream("你好", uid="u", chat_id=None, llm=llm):
                events.append(ev)
        tokens = [e["data"]["text"] for e in events if e["type"] == "token"]
        assert tokens == ["本地补全回答"]
        meta = [e["data"] for e in events if e["type"] == "_result_meta"][0]
        assert meta.get("degraded") is True
        llm.chat.assert_awaited_once()

    async def test_double_down_error_event(self):
        """星辰挂 + 本地 LLM 也挂 → error 事件（recoverable，不静默）"""
        from app.gateway.agent_router import _socratic_chat_stream

        async def broken_stream(flow, **kwargs):
            raise RuntimeError("xingchen down")
            yield  # pragma: no cover

        llm = AsyncMock()
        llm.chat.side_effect = RuntimeError("llm down")
        events = []
        with patch("app.providers.xingchen.stream_workflow", new=broken_stream):
            async for ev in _socratic_chat_stream("你好", uid="u", chat_id=None, llm=llm):
                events.append(ev)
        err = [e["data"] for e in events if e["type"] == "error"]
        assert err and err[0]["code"] == 50301 and err[0].get("recoverable") is True
