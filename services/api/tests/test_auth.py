"""Auth 单元测试

测试 JWT 签发/验证、验证码流程、登录、角色切换。
"""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from jose import jwt

from app.config import settings
from app.gateway.jwt import create_access_token, create_token_with_role, decode_token
from app.models.database import async_session_factory
from app.models.role_binding import RoleBinding
from app.models.user import User


@pytest.fixture
async def auth_db():
    async with async_session_factory() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            if transaction.is_active:
                await transaction.rollback()

# ========== JWT 测试 ==========


class TestJWT:
    """JWT 签发和验证测试"""

    def test_create_access_token(self):
        """创建 access token 包含正确数据"""
        data = {"sub": "test-user-id", "role": "student"}
        token = create_access_token(data)
        decoded = decode_token(token)
        assert decoded["sub"] == "test-user-id"
        assert decoded["role"] == "student"
        assert "exp" in decoded

    def test_create_access_token_custom_expiry(self):
        """自定义过期时间"""
        data = {"sub": "test-user-id"}
        token = create_access_token(data, expires_delta=timedelta(hours=1))
        decoded = decode_token(token)
        assert decoded["sub"] == "test-user-id"
        assert "exp" in decoded

    def test_create_token_with_role(self):
        """创建包含角色信息的 token"""
        user_id = str(uuid.uuid4())
        token = create_token_with_role(
            user_id=user_id,
            role="student",
            roles=["student", "teacher"],
            verified=True,
        )
        decoded = decode_token(token)
        assert decoded["sub"] == user_id
        assert decoded["active_role"] == "student"
        assert decoded["roles"] == ["student", "teacher"]
        assert decoded["verified"] is True

    def test_create_token_default_roles(self):
        """默认 roles 列表包含当前角色"""
        user_id = str(uuid.uuid4())
        token = create_token_with_role(user_id=user_id, role="teacher")
        decoded = decode_token(token)
        assert decoded["roles"] == ["teacher"]

    def test_decode_invalid_token_raises(self):
        """无效 token 解码抛出异常"""
        from jose import JWTError

        with pytest.raises(JWTError):
            decode_token("invalid.token.here")

    def test_decode_expired_token_raises(self):
        """过期 token 解码抛出异常"""
        from jose import JWTError

        data = {"sub": "test-user-id"}
        token = create_access_token(data, expires_delta=timedelta(seconds=-1))
        with pytest.raises(JWTError):
            decode_token(token)

    def test_token_uses_configured_algorithm(self):
        """token 使用配置的算法"""
        token = create_access_token({"sub": "test"})
        # 验证头部算法
        header = jwt.get_unverified_header(token)
        assert header["alg"] == settings.jwt_algorithm


# ========== 验证码 Redis 工具测试 ==========


class TestRedisUtils:
    """Redis 验证码工具测试（mock Redis）"""

    async def test_set_and_get_sms_code(self):
        """设置和获取验证码"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock(return_value="123456")

        with patch("app.gateway.redis.get_redis", return_value=mock_redis):
            from app.gateway.redis import get_sms_code, set_sms_code

            await set_sms_code("13800138000", "123456")
            mock_redis.set.assert_called_once()

            code = await get_sms_code("13800138000")
            assert code == "123456"

    async def test_delete_sms_code(self):
        """删除验证码"""
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()

        with patch("app.gateway.redis.get_redis", return_value=mock_redis):
            from app.gateway.redis import delete_sms_code

            await delete_sms_code("13800138000")
            mock_redis.delete.assert_called_once()

    async def test_check_sms_rate_limit_first_time(self):
        """首次发送验证码，频率检查通过"""
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=False)
        mock_redis.set = AsyncMock()

        with patch("app.gateway.redis.get_redis", return_value=mock_redis):
            from app.gateway.redis import check_sms_rate_limit

            result = await check_sms_rate_limit("13800138000")
            assert result is True

    async def test_check_sms_rate_limit_too_fast(self):
        """频率过快，返回 False"""
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=True)

        with patch("app.gateway.redis.get_redis", return_value=mock_redis):
            from app.gateway.redis import check_sms_rate_limit

            result = await check_sms_rate_limit("13800138000")
            assert result is False


# ========== Auth 依赖测试 ==========


class TestAuthDependency:
    """认证依赖注入测试"""

    async def test_get_current_user_no_credentials(self, auth_db):
        """无凭据时抛出 401"""
        from fastapi import HTTPException

        from app.gateway.auth import get_current_user

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(None, auth_db)
        assert exc_info.value.status_code == 401

    async def test_get_current_user_invalid_token(self, auth_db):
        """无效 token 抛出 401"""
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from app.gateway.auth import get_current_user

        creds = HTTPAuthorizationCredentials(credentials="invalid.token.here", scheme="Bearer")
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(creds, auth_db)
        assert exc_info.value.status_code == 401

    async def test_get_current_user_valid_token(self, auth_db):
        """有效 token 返回用户信息"""
        from fastapi.security import HTTPAuthorizationCredentials

        from app.gateway.auth import get_current_user

        user = User(
            id=uuid.uuid4(),
            phone="13800138090",
            nickname="auth test",
            onboarding_status="completed",
        )
        auth_db.add(user)
        await auth_db.flush()
        auth_db.add(
            RoleBinding(user_id=user.id, role="student", status="approved", verified=True)
        )
        await auth_db.flush()
        user_id = str(user.id)
        token = create_token_with_role(user_id=user_id, role="student")
        creds = HTTPAuthorizationCredentials(credentials=token, scheme="Bearer")
        result = await get_current_user(creds, auth_db)
        assert result["sub"] == user_id
        assert result["active_role"] == "student"
        assert "student" in result["roles"]

    async def test_get_current_user_no_sub(self, auth_db):
        """token 无 sub 字段时 401"""
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from app.gateway.auth import get_current_user

        token = create_access_token({"role": "student"})
        creds = HTTPAuthorizationCredentials(credentials=token, scheme="Bearer")
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(creds, auth_db)
        assert exc_info.value.status_code == 401


# ========== 短信登录自动开通（2026-08-29：任意手机号验证码模拟登录即注册） ==========


class _StubChallenges:
    """跳过真实验证码核验（登录自动开通的测试只关注账号/身份开通逻辑）"""

    async def consume(self, *args, **kwargs):
        return None


class TestLoginSmsAutoProvision:
    """此前 login/sms 路由对不存在的手机号直接 AUTH_ROLE_NOT_AVAILABLE，
    service.login_sms 的自动开通能力未接线；演示环境要求任意手机号可模拟登录。"""

    @staticmethod
    def _override_db():
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.main import app
        from app.models.database import get_db

        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def _override():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_db] = _override
        return app, engine

    async def _post_login(self, phone: str, preferred_role: str = "student"):
        from httpx import ASGITransport, AsyncClient

        from app.domains.identity.router import get_challenge_service
        from app.main import app

        app.dependency_overrides[get_challenge_service] = lambda: _StubChallenges()
        app_obj, engine = self._override_db()
        try:
            async with AsyncClient(transport=ASGITransport(app=app_obj), base_url="http://test") as ac:
                return await ac.post(
                    "/api/auth/login/sms",
                    json={
                        "phone": phone,
                        "challenge_id": "test-challenge",
                        "code": "123456",
                        "remember": False,
                        "preferred_role": preferred_role,
                    },
                )
        finally:
            app_obj.dependency_overrides.pop(get_challenge_service, None)
            await engine.dispose()

    async def test_new_phone_login_auto_creates_student_account(self):
        """全新手机号：验证码登录直接开通学生账号（登录即注册）"""
        from sqlalchemy import select

        phone = f"137{uuid.uuid4().int % 100000000:08d}"
        r = await self._post_login(phone)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["access_token"]
        assert data["user"]["active_role"] == "student"
        roles = {x["role"]: x["status"] for x in data["user"]["roles"]}
        assert roles.get("student") == "approved"
        async with async_session_factory() as db:
            user = (
                await db.execute(select(User).where(User.phone == phone))
            ).scalar_one()
            assert user.status == "active"

    async def test_existing_user_without_student_binding_gets_provisioned(self):
        """已有账号但缺学生绑定：登录幂等补绑学生身份，不再报"尚未申请此身份\""""
        from sqlalchemy import delete, select

        phone = f"136{uuid.uuid4().int % 100000000:08d}"
        async with async_session_factory() as db:
            user = User(phone=phone, nickname="", status="active")
            db.add(user)
            await db.commit()
        # 确认该账号没有任何角色绑定
        async with async_session_factory() as db:
            await db.execute(delete(RoleBinding).where(RoleBinding.user_id == user.id))
            await db.commit()

        r = await self._post_login(phone)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        roles = {x["role"]: x["status"] for x in data["user"]["roles"]}
        assert roles.get("student") == "approved"
        assert data["user"]["active_role"] == "student"

    async def test_new_phone_teacher_login_auto_provisions_in_demo(self):
        """演示模式（专业审核关闭）：未注册手机号选教师端登录 → 自动开通教师身份"""
        phone = f"135{uuid.uuid4().int % 100000000:08d}"
        r = await self._post_login(phone, preferred_role="teacher")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["user"]["active_role"] == "teacher"
        roles = {x["role"]: x["status"] for x in data["user"]["roles"]}
        assert roles.get("teacher") == "approved"
        assert roles.get("student") == "approved"  # 学生身份始终同时开通

    async def test_new_phone_researcher_login_auto_provisions_in_demo(self):
        phone = f"135{uuid.uuid4().int % 100000000:08d}"
        r = await self._post_login(phone, preferred_role="researcher")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["user"]["active_role"] == "researcher"
        roles = {x["role"]: x["status"] for x in data["user"]["roles"]}
        assert roles.get("researcher") == "approved"

    async def test_professional_login_rejected_when_review_enabled(self):
        """生产模式（专业审核开启）：未注册手机号选教师端仍被拒绝，必须走申请审核"""
        from unittest.mock import patch

        phone = f"135{uuid.uuid4().int % 100000000:08d}"
        with patch.object(settings, "auth_professional_review_enabled", True):
            r = await self._post_login(phone, preferred_role="teacher")
        assert r.status_code == 403
        assert r.json()["error_key"] == "AUTH_ROLE_NOT_AVAILABLE"
        # 且不应当创建账号（拒绝即不开通）
        from sqlalchemy import select

        async with async_session_factory() as db:
            user = (
                await db.execute(select(User).where(User.phone == phone))
            ).scalar_one_or_none()
            assert user is None
