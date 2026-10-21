"""星辰有效配置业务链路测试（resolve_effective_xingchen_config + 调用点消费）

覆盖：
1. resolve_effective_xingchen_config 三层优先级：env ← system_configs["xingchen.global"]
   ← system_configs["workflows"]（flow_id/timeout）← 用户覆盖；
   无库内记录且无用户覆盖时解析结果必须等于 env（不破坏既有 settings monkeypatch 测试）
2. 异常回退：库表查询失败（模拟 system_configs 表缺失）→ 回退 env 且 SAVEPOINT 隔离
   不污染调用方共享事务（InFailedSQLTransactionError 教训）；db=None 直走 env
3. 端到端：管理后台 workflows 写 wf_smart_quiz flow_id 覆盖 → student 出题调用点
   （_generate_one_quiz_item）mock run_workflow 断言收到的 config.flow_ids 含该覆盖

需要 PostgreSQL 运行中（与 test_iter06_workflows.py 同一测试库口径）。
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.models.system_config import SystemConfig, upsert_system_config
from app.models.user_integration_config import UserIntegrationConfig
from app.providers import xingchen as xc

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


async def _register(client) -> tuple[str, str]:
    """注册用户，返回 (token, user_id)"""
    phone = f"139{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


async def _cleanup_system_keys(*keys: str) -> None:
    async with _test_session_factory() as session:
        await session.execute(delete(SystemConfig).where(SystemConfig.key.in_(keys)))
        await session.commit()


# ==================== 1. 三层优先级 ====================


class TestResolveEffectiveLayers:
    async def test_db_none_returns_env(self):
        """db=None（无会话上下文）→ 直走 env，永不抛错"""
        cfg = await xc.resolve_effective_xingchen_config(None, "u1")
        assert cfg == xc.xingchen_config_from_settings()

    async def test_no_rows_matches_env(self):
        """库内无记录且无用户覆盖 → 解析结果必须等于 env（既有 monkeypatch 测试的兼容底线）"""
        await _cleanup_system_keys("xingchen.global", "workflows")
        with patch.object(settings, "xingchen_enabled", True), patch.object(
            settings, "xingchen_flow_ids", '{"wf_smart_quiz": "fid-env-1"}'
        ):
            async with _test_session_factory() as session:
                cfg = await xc.resolve_effective_xingchen_config(session, str(uuid.uuid4()))
            assert cfg == xc.xingchen_config_from_settings()
            assert cfg.enabled is True
            assert cfg.flow_ids["wf_smart_quiz"] == "fid-env-1"
            assert cfg.source == "env"

    async def test_workflows_layer_overrides_env(self):
        """workflows 层 flow_id/timeout 覆盖 env map，其余字段保持 env"""
        await _cleanup_system_keys("workflows")
        try:
            async with _test_session_factory() as session:
                await upsert_system_config(
                    session, "workflows", {"wf_smart_quiz": {"flow_id": "fid-admin-1", "timeout": 33}}
                )
                cfg = await xc.resolve_effective_xingchen_config(session, None)
            assert cfg.flow_ids["wf_smart_quiz"] == "fid-admin-1"
            assert cfg.timeouts["wf_smart_quiz"] == 33.0
            assert cfg.enabled == settings.xingchen_enabled  # 未被覆盖字段回退 env
        finally:
            await _cleanup_system_keys("workflows")

    async def test_global_layer_overrides_env(self):
        """xingchen.global 层可覆盖 env 的 enabled/base_url（管理后台全局开关即时生效）"""
        await _cleanup_system_keys("xingchen.global")
        try:
            with patch.object(settings, "xingchen_enabled", False):
                async with _test_session_factory() as session:
                    await upsert_system_config(
                        session,
                        "xingchen.global",
                        {"enabled": True, "base_url": "https://xc-admin.example.com"},
                    )
                    cfg = await xc.resolve_effective_xingchen_config(session, None)
                assert cfg.enabled is True
                assert cfg.base_url == "https://xc-admin.example.com"
                assert cfg.source == "global"
        finally:
            await _cleanup_system_keys("xingchen.global")

    async def test_user_layer_beats_admin(self, client):
        """用户覆盖层优先级最高：user flow_id > workflows flow_id > env"""
        await _cleanup_system_keys("workflows")
        _, user_id = await _register(client)
        try:
            async with _test_session_factory() as session:
                await upsert_system_config(
                    session, "workflows", {"wf_smart_quiz": {"flow_id": "fid-admin-2"}}
                )
                session.add(
                    UserIntegrationConfig(
                        user_id=uuid.UUID(user_id),
                        kind="xingchen",
                        config={"flow_ids": {"wf_smart_quiz": "fid-user-2"}},
                    )
                )
                await session.commit()
                cfg = await xc.resolve_effective_xingchen_config(session, user_id)
            assert cfg.flow_ids["wf_smart_quiz"] == "fid-user-2"
            assert cfg.source == "user"

            # 对照：无用户覆盖时读到 workflows 层
            async with _test_session_factory() as session:
                cfg2 = await xc.resolve_effective_xingchen_config(session, str(uuid.uuid4()))
            assert cfg2.flow_ids["wf_smart_quiz"] == "fid-admin-2"
        finally:
            async with _test_session_factory() as session:
                await session.execute(
                    delete(UserIntegrationConfig).where(
                        UserIntegrationConfig.user_id == uuid.UUID(user_id)
                    )
                )
                await session.commit()
            await _cleanup_system_keys("workflows")


# ==================== 2. 异常回退（SAVEPOINT 不污染共享事务） ====================


class TestResolveEffectiveFallback:
    async def test_query_failure_falls_back_env_and_keeps_transaction(self):
        """库表查询真实失败（模拟表缺失）→ 回退 env；调用方事务不被污染仍可读写"""

        async def _boom(db, key, default=None):
            await db.execute(text("SELECT * FROM system_configs__missing"))
            return default

        await _cleanup_system_keys("xingchen.global", "workflows")
        with patch.object(settings, "xingchen_enabled", True), patch(
            "app.models.system_config.get_system_config", new=_boom
        ):
            async with _test_session_factory() as session:
                cfg = await xc.resolve_effective_xingchen_config(session, str(uuid.uuid4()))
                assert cfg == xc.xingchen_config_from_settings()
                # SAVEPOINT 隔离：失败查询只回滚到保存点，共享事务仍可用
                assert (await session.execute(text("SELECT 1"))).scalar() == 1
                await session.execute(delete(SystemConfig).where(SystemConfig.key == "__nope__"))
                await session.commit()


# ==================== 3. 端到端：管理后台配置进入 student 出题链路 ====================


class TestSmartQuizCallSiteE2E:
    async def test_admin_flow_id_reaches_run_workflow(self):
        """workflows 写 wf_smart_quiz 覆盖 → _generate_one_quiz_item 的 run_workflow 收到该 config"""
        from app.gateway import student_router as sr
        from app.models.coursework import Quiz

        wf_out = {
            "question_text": "求 sin 30°",
            "options": ["A. 1/2", "B. 1", "C. 0", "D. -1"],
            "answer": "A",
            "explanation": "sin 30° = 1/2",
            "difficulty": "easy",
        }
        quiz = Quiz(user_id=uuid.uuid4(), source="ai_generated", title="t", kp_codes=["MATH-G1-TRIG-001"])
        await _cleanup_system_keys("workflows")
        try:
            async with _test_session_factory() as session:
                await upsert_system_config(
                    session, "workflows", {"wf_smart_quiz": {"flow_id": "fid-e2e-admin"}}
                )
                with patch.object(settings, "xingchen_enabled", True), patch(
                    "app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)
                ) as m_run:
                    data = await sr._generate_one_quiz_item(
                        None,
                        quiz,
                        "MATH-G1-TRIG-001",
                        "三角函数",
                        "easy",
                        "choice",
                        db=session,
                    )
            m_run.assert_awaited_once()
            cfg = m_run.await_args.kwargs["config"]
            assert cfg is not None
            assert cfg.enabled is True
            assert cfg.flow_ids["wf_smart_quiz"] == "fid-e2e-admin"  # 管理后台覆盖进入业务链路
            assert data["question_text"] == "求 sin 30°"
        finally:
            await _cleanup_system_keys("workflows")

    async def test_no_db_callsite_matches_env(self):
        """调用点不传 db（既有测试形态）→ run_workflow 收到 env 配置（行为与改造前一致）"""
        from app.gateway import student_router as sr
        from app.models.coursework import Quiz

        wf_out = {
            "question_text": "求 sin 30°",
            "options": ["A. 1/2", "B. 1", "C. 0", "D. -1"],
            "answer": "A",
            "explanation": "sin 30° = 1/2",
            "difficulty": "easy",
        }
        quiz = Quiz(user_id=uuid.uuid4(), source="ai_generated", title="t", kp_codes=["MATH-G1-TRIG-001"])
        await _cleanup_system_keys("workflows")
        with patch.object(settings, "xingchen_enabled", True), patch(
            "app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)
        ) as m_run:
            await sr._generate_one_quiz_item(
                None, quiz, "MATH-G1-TRIG-001", "三角函数", "easy", "choice"
            )
            m_run.assert_awaited_once()
            cfg = m_run.await_args.kwargs["config"]
            assert cfg == xc.xingchen_config_from_settings()
