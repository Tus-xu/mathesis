"""自定义模型配置测试

覆盖：加密工具 / Provider 参数化 / 工厂函数 / API 端点。
- 加密与 Provider 参数化为纯单元测试，无需 DB。
- 工厂函数使用 mock DB，无需真实 PostgreSQL。
- API 端点集成测试需要 PostgreSQL + Redis，不可用时自动跳过。
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.providers.crypto import decrypt_api_key, encrypt_api_key, mask_api_key
from app.providers.deepseek import DeepSeekProvider
from app.providers.router import get_model_router, get_model_router_for_user
from app.providers.spark import SPARK_API_URL, SparkProvider

# ========== 加密 / 解密 / 脱敏 ==========


class TestCrypto:
    """API Key 对称加密工具"""

    def test_encrypt_decrypt_roundtrip(self):
        """加密后解密应恢复原文"""
        plain = "sk-test-key-12345"
        encrypted = encrypt_api_key(plain)
        assert encrypted != plain
        decrypted = decrypt_api_key(encrypted)
        assert decrypted == plain

    def test_encrypt_empty_string(self):
        """空字符串不加密"""
        assert encrypt_api_key("") == ""
        assert decrypt_api_key("") == ""

    def test_decrypt_plaintext_fallback(self):
        """解密明文（兼容旧数据）应返回原值"""
        plain = "sk-plain-key"
        assert decrypt_api_key(plain) == plain

    def test_mask_api_key(self):
        """脱敏显示"""
        assert mask_api_key("sk-czg2uf7ku9s4v8524") == "sk-***8524"
        assert mask_api_key("") == "***"
        assert mask_api_key("short") == "***"


# ========== Provider 参数化 ==========


class TestProviderParameterization:
    """Provider __init__ 参数化：自定义参数覆盖 settings，缺省回退 settings"""

    def test_spark_default_uses_settings(self):
        """无参构造回退 settings"""
        with patch("app.providers.spark.settings") as mock_settings:
            mock_settings.spark_api_password = "env-spark-password"
            mock_settings.spark_model = "env-spark-model"
            mock_settings.spark_base_url = ""
            provider = SparkProvider()
            assert provider._api_password == "env-spark-password"
            assert provider._model == "env-spark-model"
            # 端点：env 未配 SPARK_BASE_URL 时回退模块常量（默认 spark-api-open）
            assert provider._api_url == SPARK_API_URL

    def test_spark_custom_config(self):
        """自定义参数覆盖 settings"""
        provider = SparkProvider(
            api_password="sk-custom-spark",
            model="custom-model",
            base_url="https://custom.api.com/v1/chat/completions",
        )
        assert provider._api_password == "sk-custom-spark"
        assert provider._model == "custom-model"
        assert provider._api_url == "https://custom.api.com/v1/chat/completions"

    def test_deepseek_custom_config(self):
        """DeepSeek 自定义参数覆盖 settings"""
        provider = DeepSeekProvider(
            api_key="sk-custom-deepseek",
            model="custom-mimo",
            base_url="https://custom.mimo.com/v1/chat/completions",
            thinking=True,
        )
        assert provider._api_key == "sk-custom-deepseek"
        assert provider._model == "custom-mimo"
        assert provider._api_url == "https://custom.mimo.com/v1/chat/completions"
        assert provider._thinking is True

    def test_spark_partial_override(self):
        """只覆盖部分字段，其余回退 settings"""
        with patch("app.providers.spark.settings") as mock_settings:
            mock_settings.spark_api_password = "env-spark-password"
            mock_settings.spark_model = "env-spark-model"
            mock_settings.spark_base_url = "https://env.maas.example/v2/chat/completions"
            provider = SparkProvider(model="custom-model")
            assert provider._model == "custom-model"
            # api_password 回退 settings；端点回退 env 的 SPARK_BASE_URL（如 MaaS）
            assert provider._api_password == "env-spark-password"
            assert provider._api_url == "https://env.maas.example/v2/chat/completions"


# ========== 工厂函数 ==========


class TestModelRouterFactory:
    """get_model_router_for_user：无配置返回全局单例，有配置返回自定义 router"""

    async def test_no_config_returns_global_singleton(self):
        """无用户配置时返回全局单例"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        router = await get_model_router_for_user("nonexistent-user-id", mock_db)
        assert router is get_model_router()

    async def test_with_config_returns_custom_router(self):
        """有用户配置时返回自定义 router"""
        cfg = MagicMock()
        cfg.primary_api_key = encrypt_api_key("sk-custom-primary")
        cfg.primary_model = "custom-spark"
        cfg.primary_base_url = None
        cfg.secondary_api_key = encrypt_api_key("sk-custom-secondary")
        cfg.secondary_model = "custom-mimo"
        cfg.secondary_base_url = None
        cfg.secondary_thinking = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = cfg
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        router = await get_model_router_for_user("some-user-id", mock_db)
        assert router is not get_model_router()
        assert router._spark._model == "custom-spark"
        assert router._spark._api_password == "sk-custom-primary"
        assert router._deepseek._model == "custom-mimo"
        assert router._deepseek._api_key == "sk-custom-secondary"
        assert router._deepseek._thinking is True


# ========== API 端点集成测试（需 PostgreSQL + Redis） ==========


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


async def _register_and_login(client, phone=None) -> tuple[str, str]:
    """注册并登录，返回 (token, user_id)"""
    phone = phone or f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


@pytest_asyncio.fixture
async def auth_client():
    """返回 (client, token)；PostgreSQL / Redis 不可用时跳过"""
    # 探测 DB 可用性
    try:
        async with _test_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL 不可用，跳过模型配置集成测试")
        return  # 仅供类型检查器消歧，不可达

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            token, _ = await _register_and_login(ac)
        except Exception:
            pytest.skip("依赖服务（PostgreSQL/Redis）不可用，跳过模型配置集成测试")
            return
        yield ac, token


class TestModelConfigAPI:
    """模型配置 API 端点集成测试"""

    async def test_get_config_no_user_config(self, auth_client):
        """无配置时返回 env_default"""
        client, token = auth_client
        resp = await client.get("/api/model-config", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["configured"] is False
        assert data["primary"]["source"] == "env_default"
        assert data["secondary"]["source"] == "env_default"

    async def test_save_and_get_config(self, auth_client):
        """保存后读取应返回用户配置"""
        client, token = auth_client
        resp = await client.put(
            "/api/model-config",
            json={
                "primary": {
                    "api_key": "sk-test-primary",
                    "model": "test-spark",
                    "base_url": "",
                },
                "secondary": {
                    "api_key": "sk-test-secondary",
                    "model": "test-mimo",
                    "base_url": "",
                    "thinking": False,
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

        # 读取
        resp = await client.get("/api/model-config", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["configured"] is True
        assert data["primary"]["source"] == "user"
        assert data["primary"]["model"] == "test-spark"
        assert data["secondary"]["model"] == "test-mimo"
        # API Key 脱敏
        assert data["primary"]["api_key"].startswith("sk-***")
        assert data["primary"]["api_key"].endswith("mary")

    async def test_reset_config(self, auth_client):
        """删除后恢复默认"""
        client, token = auth_client
        # 先保存一条配置
        await client.put(
            "/api/model-config",
            json={
                "primary": {"api_key": "sk-to-reset", "model": "to-reset"},
                "secondary": {"api_key": "sk-to-reset-2", "model": "to-reset-2"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        # 删除
        resp = await client.delete(
            "/api/model-config", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
        # 验证恢复默认
        resp = await client.get("/api/model-config", headers={"Authorization": f"Bearer {token}"})
        data = resp.json()["data"]
        assert data["configured"] is False
