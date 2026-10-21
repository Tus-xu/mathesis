"""管理后台配置 API 测试（/api/admin）

覆盖：
- 非 admin 访问全部 admin 端点 → 403；/api/auth/role/apply 禁止自助申请 admin
- admin 引导：phone 命中 ADMIN_PHONES → 登录后 roles 含 admin
- system/model PUT→GET 脱敏回显；model.global 三层回退（无用户配置时吃到 global 的 model 名）
- system/xingchen PUT→GET 脱敏回显 + resolve 链 env ← xingchen.global
- system/cloud-kb PUT→GET；test 端点模块缺失 → 50301（不崩溃）
- system/embedding PUT→GET 脱敏回显、空串清除回退 env；test 端点信封永不崩溃
- workflows 列表含 10 项；PUT 覆盖后 resolve_xingchen_config 读到覆盖的 flow_id

需要 PostgreSQL + Redis，不可用时跳过；写入 system_configs 的用例 finally 清理并失效缓存，
防串扰后续测试文件。

注意：登录必须在测试体内进行（fixture loop 为 session 级，测试体为 function 级；
fixture 内登录会让 Redis 单例绑定到 session loop，测试体内再用即跨 loop 报错）。
"""

import contextlib
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway.redis import get_redis
from app.main import app
from app.models.database import get_db
from app.models.system_config import SystemConfig
from app.providers.router import clear_global_model_config_cache
from app.providers.xingchen import FLOW_REGISTRY, resolve_xingchen_config


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
    """裸 AsyncClient；PostgreSQL 不可用时跳过（登录在各测试体内完成）"""
    try:
        async with _test_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL 不可用，跳过管理后台集成测试")
        return

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register(client) -> tuple[str, str]:
    """普通用户验证码登录，返回 (token, user_id)"""
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    try:
        await client.post("/api/auth/sms-code", json={"phone": phone})
        resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        data = resp.json()["data"]
    except Exception:
        pytest.skip("依赖服务（PostgreSQL/Redis）不可用，跳过管理后台集成测试")
    return data["token"], data["user"]["id"]


async def _register_admin(client) -> tuple[str, str]:
    """ADMIN_PHONES 白名单手机号登录（真实引导路径），返回 (token, user_id)"""
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    try:
        # 仅登录期间将手机号放入白名单；绑定落库后恢复配置
        with patch.object(settings, "admin_phones", phone):
            await client.post("/api/auth/sms-code", json={"phone": phone})
            resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        data = resp.json()["data"]
    except Exception:
        pytest.skip("依赖服务（PostgreSQL/Redis）不可用，跳过管理后台集成测试")
    # 统一认证下默认 active_role 为 student；admin 端点要求显式切换到 admin
    switch = await client.post(
        "/api/auth/role/switch",
        json={"role": "admin"},
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert switch.json()["code"] == 0, switch.text
    return switch.json()["data"]["token"], data["user"]["id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _cleanup_system_keys(*keys: str):
    """清理测试写入的 system_configs 键并失效 model.global 缓存（防串扰后续测试文件）"""
    async with _test_session_factory() as session:
        for key in keys:
            row = await session.get(SystemConfig, key)
            if row is not None:
                await session.delete(row)
        await session.commit()
    clear_global_model_config_cache()


# ========== 权限与 admin 引导 ==========


class TestAdminAccess:
    async def test_non_admin_forbidden_all_endpoints(self, client):
        """非 admin 访问全部 admin 端点 → 403"""
        token, _ = await _register(client)
        endpoints = [
            ("GET", "/api/admin/overview"),
            ("GET", "/api/admin/system/model"),
            ("PUT", "/api/admin/system/model"),
            ("POST", "/api/admin/system/model/test"),
            ("GET", "/api/admin/system/xingchen"),
            ("PUT", "/api/admin/system/xingchen"),
            ("GET", "/api/admin/system/cloud-kb"),
            ("PUT", "/api/admin/system/cloud-kb"),
            ("POST", "/api/admin/system/cloud-kb/test"),
            ("GET", "/api/admin/system/embedding"),
            ("PUT", "/api/admin/system/embedding"),
            ("POST", "/api/admin/system/embedding/test"),
            ("GET", "/api/admin/system/butler"),
            ("PUT", "/api/admin/system/butler"),
            ("GET", "/api/admin/workflows"),
            ("PUT", "/api/admin/workflows/wf_smart_quiz"),
            ("POST", "/api/admin/workflows/wf_smart_quiz/test"),
        ]
        for method, url in endpoints:
            kwargs = {"json": {}} if method == "PUT" else {}
            resp = await client.request(method, url, headers=_auth(token), **kwargs)
            assert resp.status_code == 403, f"{method} {url} 应 403，实际 {resp.status_code}"

    async def test_role_apply_rejects_admin(self, client):
        """/api/auth/role/apply 不允许自助申请 admin（pydantic 约束 → 422）"""
        token, _ = await _register(client)
        resp = await client.post(
            "/api/auth/role/apply", json={"role": "admin"}, headers=_auth(token)
        )
        assert resp.status_code == 422

    async def test_admin_bootstrap(self, client):
        """手机号命中 ADMIN_PHONES → 登录后 roles 含 admin，可访问 admin 端点"""
        token, _ = await _register_admin(client)
        me = await client.get("/api/auth/me", headers=_auth(token))
        roles = [r["role"] for r in me.json()["data"]["roles"]]
        assert "admin" in roles

        resp = await client.get("/api/admin/workflows", headers=_auth(token))
        assert resp.status_code == 200


# ========== system/model ==========


class TestSystemModel:
    async def test_put_get_masked_roundtrip(self, client):
        """PUT 后 GET 脱敏回显；部分更新保持；空串清除回退 env"""
        token, _ = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/model",
                json={
                    "primary": {"model": "global-spark-x", "api_key": "gk-primary-123456"},
                    "secondary": {"model": "global-ds-x"},
                },
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0

            data = (await client.get("/api/admin/system/model", headers=_auth(token))).json()[
                "data"
            ]
            assert data["configured"] is True
            assert data["primary"]["model"] == "global-spark-x"
            assert data["primary"]["api_key"] == "gk-***3456"  # 脱敏
            assert data["primary"]["source"] == "global"
            assert data["secondary"]["model"] == "global-ds-x"

            # 部分更新：只改 secondary.base_url，primary 保持
            resp = await client.put(
                "/api/admin/system/model",
                json={"secondary": {"base_url": "https://ds2.example.com/v1/chat/completions"}},
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0
            data = (await client.get("/api/admin/system/model", headers=_auth(token))).json()[
                "data"
            ]
            assert data["primary"]["model"] == "global-spark-x"
            assert data["secondary"]["base_url"] == "https://ds2.example.com/v1/chat/completions"

            # 空串清除 → 回退 env
            resp = await client.put(
                "/api/admin/system/model", json={"primary": {"model": ""}}, headers=_auth(token)
            )
            assert resp.json()["code"] == 0
            data = (await client.get("/api/admin/system/model", headers=_auth(token))).json()[
                "data"
            ]
            assert data["primary"]["model"] == settings.spark_model

            # 未知字段 → 40001
            resp = await client.put(
                "/api/admin/system/model", json={"tertiary": {}}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40001
        finally:
            await _cleanup_system_keys("model.global")

    async def test_model_global_three_layer_fallback(self, client):
        """无用户配置时 get_model_router_for_user 吃到 model.global 的 model 名"""
        from app.providers.router import get_model_router, get_model_router_for_user

        token, admin_id = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/model",
                json={"primary": {"model": "global-fallback-model"}},
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0

            # admin_id 为全新用户，无 UserModelConfig 行 → 走全局有效 router
            async with _test_session_factory() as session:
                router = await get_model_router_for_user(admin_id, session)
            assert router is not get_model_router()
            assert router._spark._model == "global-fallback-model"
            # secondary 未覆盖 → 回退 env
            assert router._deepseek._model == settings.deepseek_model
        finally:
            await _cleanup_system_keys("model.global")


# ========== system/xingchen ==========


class TestSystemXingchen:
    async def test_put_get_masked_and_resolve_chain(self, client):
        """PUT→GET 脱敏回显；resolve 链 env ← xingchen.global"""
        token, user_id = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/xingchen",
                json={
                    "base_url": "https://xc.example.com",
                    "api_key": "xckey-123456",
                    "api_secret": "xcsec-654321",
                },
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0

            data = (await client.get("/api/admin/system/xingchen", headers=_auth(token))).json()[
                "data"
            ]
            assert data["configured"] is True
            assert data["base_url"] == "https://xc.example.com"
            assert data["api_key"] == "xck***3456"
            assert data["api_secret"] == "xcs***4321"

            # resolve 链：无用户覆盖时 source=global，凭证来自 xingchen.global
            async with _test_session_factory() as session:
                cfg = await resolve_xingchen_config(user_id, session)
            assert cfg.source == "global"
            assert cfg.base_url == "https://xc.example.com"
            assert cfg.api_key == "xckey-123456"
            assert cfg.api_secret == "xcsec-654321"
        finally:
            await _cleanup_system_keys("xingchen.global")

    async def test_put_enabled_master_switch(self, client):
        """PUT enabled 总开关 → GET 回显；resolve 链吃到 enabled 覆盖；非法类型 → 40001"""
        token, user_id = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/xingchen", json={"enabled": True}, headers=_auth(token)
            )
            assert resp.json()["code"] == 0

            data = (await client.get("/api/admin/system/xingchen", headers=_auth(token))).json()[
                "data"
            ]
            assert data["enabled"] is True

            # resolve 链：无用户覆盖时 enabled 来自 xingchen.global
            async with _test_session_factory() as session:
                cfg = await resolve_xingchen_config(user_id, session)
            assert cfg.enabled is True

            # 非法类型 → 40001
            resp = await client.put(
                "/api/admin/system/xingchen", json={"enabled": "yes"}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40001
        finally:
            await _cleanup_system_keys("xingchen.global")


# ========== system/cloud-kb ==========


class TestSystemCloudKb:
    async def test_put_get_roundtrip(self, client):
        """PUT→GET：credentials 脱敏回显；非法 provider → 40001"""
        token, _ = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/cloud-kb",
                json={
                    "enabled": True,
                    "provider": "tencent_lkeap",
                    "credentials": {"secret_id": "sid-123456789"},
                    "knowledge_base_id": "kb-demo",
                    "top_k": 8,
                    "score_threshold": 0.7,
                },
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0

            data = (await client.get("/api/admin/system/cloud-kb", headers=_auth(token))).json()[
                "data"
            ]
            assert data["configured"] is True
            assert data["enabled"] is True
            assert data["provider"] == "tencent_lkeap"
            assert data["credentials"]["secret_id"] == "sid***6789"  # 脱敏
            assert data["knowledge_base_id"] == "kb-demo"
            assert data["top_k"] == 8
            assert data["score_threshold"] == 0.7

            # 非法 provider → 40001
            resp = await client.put(
                "/api/admin/system/cloud-kb", json={"provider": "bogus"}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40001
        finally:
            await _cleanup_system_keys("cloud_kb")

    async def test_test_endpoint_module_missing_50301(self, client):
        """cloud_kb 模块未就绪 → 50301（不崩溃）；已就绪则正常信封"""
        token, _ = await _register_admin(client)
        resp = await client.post("/api/admin/system/cloud-kb/test", headers=_auth(token))
        try:
            import app.providers.cloud_kb  # noqa: F401
        except ImportError:
            assert resp.json()["code"] == 50301
        else:
            assert resp.json()["code"] == 0


# ========== system/embedding ==========


class TestSystemEmbedding:
    async def test_put_get_roundtrip(self, client):
        """PUT→GET：api_key 脱敏回显；空串清除回退 env；非法 provider/dimension → 40001"""
        token, _ = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/embedding",
                json={
                    "provider": "aliyun",
                    "base_url": "https://emb.example.com/v1",
                    "api_key": "sk-1234567890ab",
                    "model": "text-embedding-v4",
                    "dimension": 1024,
                },
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0

            data = (await client.get("/api/admin/system/embedding", headers=_auth(token))).json()[
                "data"
            ]
            assert data["configured"] is True
            assert data["provider"] == "aliyun"
            assert data["base_url"] == "https://emb.example.com/v1"
            assert data["api_key"] == "sk-***90ab"  # 脱敏
            assert data["model"] == "text-embedding-v4"
            assert data["dimension"] == 1024

            # 空串/null 清除 → 回退 env 兜底层
            resp = await client.put(
                "/api/admin/system/embedding",
                json={
                    "provider": "",
                    "base_url": "",
                    "api_key": "",
                    "model": "",
                    "dimension": None,
                },
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0
            data = (await client.get("/api/admin/system/embedding", headers=_auth(token))).json()[
                "data"
            ]
            assert data["configured"] is False
            env_provider = str(getattr(settings, "embedding_provider", "local") or "local")
            if env_provider not in ("local", "aliyun", "tencent"):
                env_provider = "local"
            assert data["provider"] == env_provider
            assert data["dimension"] == 1024  # pgvector 库表红线

            # 非法 provider / dimension → 40001
            resp = await client.put(
                "/api/admin/system/embedding", json={"provider": "bogus"}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40001
            resp = await client.put(
                "/api/admin/system/embedding", json={"dimension": 32}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40001
        finally:
            await _cleanup_system_keys("embedding")

    async def test_test_endpoint_envelope_never_crashes(self, client):
        """test 端点永不崩溃：信封 code=0，data 含 ok/latency_ms；失败时带 error"""
        token, _ = await _register_admin(client)
        resp = await client.post("/api/admin/system/embedding/test", headers=_auth(token))
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert isinstance(data["ok"], bool)
        assert isinstance(data["latency_ms"], int)
        if not data["ok"]:
            assert data.get("error")


# ========== system/butler ==========


class TestSystemButler:
    async def test_put_get_roundtrip(self, client):
        """PUT→GET：Butler 授权开关回显；部分更新保持；非法类型 → 40001"""
        token, _ = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/butler",
                json={
                    "external_allowed": True,
                    "web_search_enabled": True,
                },
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0

            data = (await client.get("/api/admin/system/butler", headers=_auth(token))).json()[
                "data"
            ]
            assert data["configured"] is True
            assert data["external_allowed"] is True
            assert data["web_search_enabled"] is True

            # 部分更新：只改 external_allowed，其余保持
            resp = await client.put(
                "/api/admin/system/butler", json={"external_allowed": False}, headers=_auth(token)
            )
            assert resp.json()["code"] == 0
            data = (await client.get("/api/admin/system/butler", headers=_auth(token))).json()[
                "data"
            ]
            assert data["external_allowed"] is False
            assert data["web_search_enabled"] is True

            # 未知字段 / 非法类型 → 40001（web_search_local_refused 是运行事实，不可写）
            resp = await client.put(
                "/api/admin/system/butler", json={"bogus": True}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40001
            resp = await client.put(
                "/api/admin/system/butler",
                json={"web_search_local_refused": True},
                headers=_auth(token),
            )
            assert resp.json()["code"] == 40001
            resp = await client.put(
                "/api/admin/system/butler", json={"external_allowed": "yes"}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40001
        finally:
            await _cleanup_system_keys("butler.authorization")

    async def test_clear_falls_back_to_defaults(self, client):
        """空值清除 → 回退默认（external_allowed=False，web_search 回退 env）"""
        token, _ = await _register_admin(client)
        try:
            await client.put(
                "/api/admin/system/butler",
                json={"external_allowed": True, "web_search_enabled": True},
                headers=_auth(token),
            )
            resp = await client.put(
                "/api/admin/system/butler",
                json={"external_allowed": None, "web_search_enabled": None},
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0
            data = (await client.get("/api/admin/system/butler", headers=_auth(token))).json()[
                "data"
            ]
            assert data["configured"] is False
            assert data["external_allowed"] is False
            assert data["web_search_enabled"] is settings.web_search_enabled
            assert "web_search_local_refused" not in data
        finally:
            await _cleanup_system_keys("butler.authorization")


# ========== features 端点联动 butler 授权（阶段 6B） ==========


class TestFeaturesButlerLinkage:
    async def test_features_reflects_web_search_enabled(self, client):
        """/api/agent/features 的 web_search_opt_in_enabled 反映 butler 授权有效配置"""
        admin_token, _ = await _register_admin(client)
        user_token, _ = await _register(client)
        try:
            # 默认（未配置）→ 回退 env
            resp = await client.get("/api/agent/features", headers=_auth(user_token))
            caps = resp.json()["data"]["capabilities"]
            assert caps["web_search_opt_in_enabled"] is settings.web_search_enabled

            # 管理员开启 → true
            await client.put(
                "/api/admin/system/butler",
                json={"web_search_enabled": True},
                headers=_auth(admin_token),
            )
            resp = await client.get("/api/agent/features", headers=_auth(user_token))
            caps = resp.json()["data"]["capabilities"]
            assert caps["web_search_opt_in_enabled"] is True

            # 关闭 → false
            await client.put(
                "/api/admin/system/butler",
                json={"web_search_enabled": False},
                headers=_auth(admin_token),
            )
            resp = await client.get("/api/agent/features", headers=_auth(user_token))
            caps = resp.json()["data"]["capabilities"]
            assert caps["web_search_opt_in_enabled"] is False
        finally:
            await _cleanup_system_keys("butler.authorization")


# ========== workflows ==========


class TestWorkflows:
    async def test_list_contains_m2_visible_flows(self, client):
        """M2 profile 列表 = FLOW_REGISTRY - 科研工作流(9 项)，字段完整，不含 F14"""
        token, _ = await _register_admin(client)
        with patch.object(settings, "m3_enable_teacher", False):
            resp = await client.get("/api/admin/workflows", headers=_auth(token))
        assert resp.status_code == 200
        items = resp.json()["data"]["workflows"]
        # 阶段 1 契约护栏：M2 默认排除 wf_verify_derivation（保留科研代码，仅控制管理面）
        assert len(items) == len(FLOW_REGISTRY) - 1 == 9
        names = {i["name"] for i in items}
        assert "wf_verify_derivation" not in names
        assert names == set(FLOW_REGISTRY) - {"wf_verify_derivation"}
        for item in items:
            for f in ("purpose", "flow_id", "enabled", "timeout", "today_calls", "configured"):
                assert f in item

    async def test_put_override_resolve_chain(self, client):
        """PUT flow_id/timeout/enabled 后 resolve 链读到覆盖；清除后回退"""
        token, user_id = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/workflows/wf_smart_quiz",
                json={"flow_id": "fid-admin-001", "timeout": 25, "enabled": False},
                headers=_auth(token),
            )
            assert resp.json()["code"] == 0
            item = resp.json()["data"]
            assert item["flow_id"] == "fid-admin-001"
            assert item["timeout"] == 25.0
            assert item["enabled"] is False

            # resolve 链读到覆盖（真实 DB，无用户配置 → workflows 层生效）
            async with _test_session_factory() as session:
                cfg = await resolve_xingchen_config(user_id, session)
            assert cfg.flow_ids["wf_smart_quiz"] == "fid-admin-001"
            assert cfg.timeouts["wf_smart_quiz"] == 25.0

            # Redis 运行时开关已写入
            val = await get_redis().get("switch:xingchen:wf_smart_quiz")
            assert val == "off"

            # 空串清除 flow_id → 回退 env map
            resp = await client.put(
                "/api/admin/workflows/wf_smart_quiz", json={"flow_id": ""}, headers=_auth(token)
            )
            assert resp.json()["code"] == 0
            async with _test_session_factory() as session:
                cfg = await resolve_xingchen_config(user_id, session)
            assert cfg.flow_ids.get("wf_smart_quiz") == settings.xingchen_flow_id_map.get(
                "wf_smart_quiz"
            )

            # 未知工作流 → 40400
            resp = await client.put(
                "/api/admin/workflows/wf_nope", json={"flow_id": "x"}, headers=_auth(token)
            )
            assert resp.json()["code"] == 40400
        finally:
            await _cleanup_system_keys("workflows")
            with contextlib.suppress(Exception):
                await get_redis().delete("switch:xingchen:wf_smart_quiz")

    async def test_master_enabled_reads_xingchen_global_effective(self, client):
        """PUT /system/xingchen enabled 后 GET /workflows 立即一致（master_enabled 读有效配置）。"""
        token, _ = await _register_admin(client)
        try:
            resp = await client.put(
                "/api/admin/system/xingchen", json={"enabled": True}, headers=_auth(token)
            )
            assert resp.json()["code"] == 0
            data = (await client.get("/api/admin/workflows", headers=_auth(token))).json()["data"]
            assert data["master_enabled"] is True

            resp = await client.put(
                "/api/admin/system/xingchen", json={"enabled": False}, headers=_auth(token)
            )
            assert resp.json()["code"] == 0
            data = (await client.get("/api/admin/workflows", headers=_auth(token))).json()["data"]
            assert data["master_enabled"] is False
        finally:
            await _cleanup_system_keys("xingchen.global")

    async def test_test_endpoint_guards(self, client):
        """test 端点：总开关关闭 → 明确提示（不报错不触网）；未知 flow → 40400"""
        token, _ = await _register_admin(client)
        with patch.object(settings, "xingchen_enabled", False):
            resp = await client.post(
                "/api/admin/workflows/wf_smart_quiz/test", headers=_auth(token)
            )
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["ok"] is False
        assert "开关" in data["data"]["error"]

        resp = await client.post("/api/admin/workflows/wf_nope/test", headers=_auth(token))
        assert resp.json()["code"] == 40400


# ========== overview ==========


class TestOverview:
    async def test_overview_structure(self, client):
        """驾驶舱结构完整（通道真实探测，ok 与否不断言——取决于环境配置）"""
        token, _ = await _register_admin(client)
        resp = await client.get("/api/admin/overview", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["db"]["ok"] is True
        assert "ok" in data["redis"]
        for ch in ("spark", "deepseek", "embedding", "reranker", "xingchen", "cloud_kb"):
            assert ch in data["channels"]
        assert "by_provider" in data["ai_calls_today"]
        for k in ("users", "classes", "submissions_today"):
            assert k in data["counts"]
