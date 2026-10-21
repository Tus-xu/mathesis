"""用户级集成配置测试（/api/integrations）

覆盖：
- 合并器字段级回退与敏感字段解密（纯函数，无需 DB）
- resolve_*_config / get_storage_for_user（mock DB）
- 星辰通用 flow：未注册名 → 通用处理器；注册表 flow 行为不变
- PUT/GET/DELETE 端点（真实 PostgreSQL + JWT，不可用时跳过）
- /test 端点：mock boto3 / httpx，覆盖分类错误路径
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from botocore.exceptions import ClientError, EndpointConnectionError
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway.integration_router import _classify_storage_error
from app.main import app
from app.models.database import get_db
from app.providers.crypto import encrypt_api_key
from app.providers.storage import (
    StorageConfig,
    clear_user_storage_cache,
    get_storage,
    get_storage_for_user,
    merge_storage_overrides,
    resolve_storage_config,
    storage_config_from_settings,
)
from app.providers.xingchen import (
    FLOW_REGISTRY,
    XingchenConfig,
    _parse_output,
    merge_xingchen_overrides,
    resolve_flow_spec,
    resolve_xingchen_config,
    run_workflow,
    xingchen_config_from_settings,
)


def _mock_db_with_config(config: dict | None):
    """构造返回指定 config 的 mock DB（config=None 表示无用户配置行）"""
    row = MagicMock() if config is not None else None
    if row is not None:
        row.config = config
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


# ========== storage 合并器 ==========


class TestMergeStorageConfig:
    """merge_storage_overrides：字段级回退 env，敏感字段解密"""

    def test_no_overrides_returns_env(self):
        cfg = merge_storage_overrides(None)
        assert cfg.source == "env"
        assert cfg == storage_config_from_settings()

    def test_empty_overrides_returns_env(self):
        cfg = merge_storage_overrides({})
        assert cfg.source == "env"

    def test_partial_override_field_level_fallback(self):
        """只覆盖 bucket，其余字段回退 env"""
        cfg = merge_storage_overrides({"bucket": "user-bucket"})
        assert cfg.source == "user"
        assert cfg.bucket == "user-bucket"
        assert cfg.provider == settings.storage_provider
        assert cfg.presign_expires == settings.storage_presign_expires

    def test_sensitive_fields_decrypted(self):
        """敏感字段密文落库 → 合并时解密"""
        cfg = merge_storage_overrides(
            {
                "access_key": encrypt_api_key("ak-user-12345"),
                "secret_key": encrypt_api_key("sk-user-67890"),
                "session_token": encrypt_api_key("token-abc"),
            }
        )
        assert cfg.access_key == "ak-user-12345"
        assert cfg.secret_key == "sk-user-67890"
        assert cfg.session_token == "token-abc"

    def test_endpoint_derivation(self):
        """endpoint_url 优先，其次 scheme/host/port 拼接"""
        cfg = StorageConfig(endpoint_url="https://full.example.com", host="h", port=9000)
        assert cfg.endpoint == "https://full.example.com"
        cfg2 = StorageConfig(endpoint_url="", host="minio.local", port=9000, scheme="http")
        assert cfg2.endpoint == "http://minio.local:9000"
        cfg3 = StorageConfig(endpoint_url="", host="", provider="cos", region="ap-guangzhou")
        assert cfg3.endpoint == "https://cos.ap-guangzhou.myqcloud.com"


class TestMergeXingchenConfig:
    """merge_xingchen_overrides：字段级回退 env"""

    def test_no_overrides_returns_env(self):
        cfg = merge_xingchen_overrides(None)
        assert cfg.source == "env"
        assert cfg == xingchen_config_from_settings()

    def test_partial_override(self):
        cfg = merge_xingchen_overrides(
            {
                "enabled": True,
                "api_key": encrypt_api_key("xc-key-12345"),
                "flow_ids": {"wf_custom": "fid-001"},
                "timeouts": {"wf_custom": "15"},
            }
        )
        assert cfg.source == "user"
        assert cfg.enabled is True
        assert cfg.api_key == "xc-key-12345"
        assert cfg.flow_ids == {"wf_custom": "fid-001"}
        assert cfg.timeouts == {"wf_custom": 15.0}  # 字符串 coercion
        # 未覆盖字段回退 env
        assert cfg.base_url == settings.xingchen_base_url
        assert cfg.max_concurrency == settings.xingchen_max_concurrency


class TestResolveConfigs:
    """resolve_*_config（mock DB）"""

    async def test_resolve_storage_no_row(self):
        cfg = await resolve_storage_config("no-such-user", _mock_db_with_config(None))
        assert cfg.source == "env"

    async def test_resolve_storage_with_row(self):
        cfg = await resolve_storage_config(
            "user-1",
            _mock_db_with_config({"bucket": "b1", "access_key": encrypt_api_key("ak-000000001")}),
        )
        assert cfg.source == "user"
        assert cfg.bucket == "b1"
        assert cfg.access_key == "ak-000000001"

    async def test_resolve_xingchen_with_row(self):
        cfg = await resolve_xingchen_config(
            "user-1",
            _mock_db_with_config({"enabled": True, "flow_ids": {"wf_a": "fid"}}),
        )
        assert cfg.source == "user"
        assert cfg.enabled is True
        assert cfg.flow_ids == {"wf_a": "fid"}


class TestGetStorageForUser:
    """get_storage_for_user：无配置 → 全局单例；有配置 → 指纹缓存"""

    async def test_no_config_returns_global(self):
        provider = await get_storage_for_user("no-such-user", _mock_db_with_config(None))
        assert provider is get_storage()

    async def test_user_config_cached_by_fingerprint(self):
        clear_user_storage_cache()
        db = _mock_db_with_config({"bucket": "user-b1"})
        p1 = await get_storage_for_user("fp-user-1", db)
        assert p1 is not get_storage()
        p2 = await get_storage_for_user("fp-user-1", db)
        assert p1 is p2  # 指纹不变 → 复用，不重建
        assert p1.bucket == "user-b1"

    async def test_fingerprint_change_rebuilds(self):
        clear_user_storage_cache()
        p1 = await get_storage_for_user("fp-user-2", _mock_db_with_config({"bucket": "b1"}))
        p2 = await get_storage_for_user("fp-user-2", _mock_db_with_config({"bucket": "b2"}))
        assert p1 is not p2
        assert p2.bucket == "b2"


# ========== 星辰通用 flow ==========


class TestGenericFlow:
    """flow_ids 中配置但注册表未注册的名字 → 通用处理器"""

    def test_resolve_flow_spec_registered(self):
        spec = resolve_flow_spec("wf_intent_router", {"wf_intent_router": "fid"})
        assert spec is FLOW_REGISTRY["wf_intent_router"]

    def test_resolve_flow_spec_generic(self):
        spec = resolve_flow_spec("wf_my_custom", {"wf_my_custom": "fid-123"})
        assert spec is not None
        assert spec["generic"] is True
        assert spec["stream"] is False
        assert spec["scene"] == "wf_my_custom"

    def test_resolve_flow_spec_unknown(self):
        assert resolve_flow_spec("wf_not_configured", {}) is None

    def test_parse_output_generic_json(self):
        """通用 flow：JSON dict → {raw, data}"""
        out = _parse_output("wf_my_custom", '{"answer": "42", "score": 0.9}')
        assert out["data"] == {"answer": "42", "score": 0.9}
        assert out["raw"] == '{"answer": "42", "score": 0.9}'

    def test_parse_output_generic_text(self):
        """通用 flow：非 JSON → 仅 raw 文本，不抛错"""
        out = _parse_output("wf_my_custom", "plain text answer")
        assert out == {"raw": "plain text answer"}

    def test_parse_output_registered_unchanged(self):
        """注册表 flow 行为不变：wf_intent_router 走 Pydantic 校验"""
        out = _parse_output("wf_intent_router", '{"intent": "quiz", "confidence": 0.8, "reason": "r"}')
        assert out["intent"] == "quiz"
        assert out["confidence"] == 0.8

    async def test_run_workflow_generic_flow(self):
        """通用 flow 端到端：mock httpx，验证请求路径与原始输出"""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "code": 0,
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=resp)

        cfg = XingchenConfig(
            enabled=True,
            base_url="https://xc.example.com:8443",
            api_key="k",
            api_secret="s",
            flow_ids={"wf_custom_x": "fid-x"},
            source="user",
        )
        with patch("app.providers.xingchen.get_http", return_value=mock_client):
            result = await run_workflow(
                "wf_custom_x", uid="u1", parameters={"q": "hi"}, config=cfg
            )

        assert result == {"raw": '{"ok": true}', "data": {"ok": True}}
        # 请求 URL/鉴权/flow_id 来自用户配置
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["flow_id"] == "fid-x"
        assert kwargs["headers"]["Authorization"] == "Bearer k:s"
        assert mock_client.post.call_args.args[0] == "https://xc.example.com:8443/workflow/v1/chat/completions"

    async def test_run_workflow_disabled_raises(self):
        with pytest.raises(RuntimeError, match="总开关关闭"):
            await run_workflow("wf_custom_x", uid="u1", parameters={}, config=XingchenConfig())


# ========== 存储错误分类 ==========


class TestStorageErrorClassification:
    def test_endpoint_unreachable(self):
        cat, msg = _classify_storage_error(EndpointConnectionError(endpoint_url="http://x:9000"))
        assert cat == "endpoint_unreachable"

    def test_bucket_not_found(self):
        err = ClientError({"Error": {"Code": "NoSuchBucket"}}, "HeadBucket")
        cat, msg = _classify_storage_error(err)
        assert cat == "bucket_not_found"

    def test_auth_failed(self):
        err = ClientError({"Error": {"Code": "SignatureDoesNotMatch"}}, "HeadBucket")
        cat, msg = _classify_storage_error(err)
        assert cat == "auth_failed"

    def test_unknown(self):
        cat, msg = _classify_storage_error(ValueError("boom"))
        assert cat == "unknown"


# ========== API 端点集成测试（需 PostgreSQL + Redis） ==========


def _make_test_engine():
    return create_async_engine(settings.database_url, poolclass=NullPool)


_test_engine = _make_test_engine()
_test_session_factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


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
    """返回 (client, token, user_id)；PostgreSQL / Redis 不可用时跳过"""
    try:
        async with _test_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL 不可用，跳过集成配置集成测试")
        return

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            token, user_id = await _register_and_login(ac)
        except Exception:
            pytest.skip("依赖服务（PostgreSQL/Redis）不可用，跳过集成配置集成测试")
            return
        yield ac, token, user_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestIntegrationAPI:
    """PUT/GET/DELETE 端点契约"""

    async def test_unauthorized(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/integrations")
        assert resp.status_code == 401

    async def test_get_default_env(self, auth_client):
        """无用户配置：configured=False，source=env"""
        client, token, _ = auth_client
        resp = await client.get("/api/integrations", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["storage"]["configured"] is False
        assert data["storage"]["source"] == "env"
        assert data["xingchen"]["configured"] is False
        assert data["xingchen"]["source"] == "env"
        # 结构完整
        for f in ("provider", "bucket", "access_key", "presign_expires", "endpoint"):
            assert f in data["storage"]
        for f in ("enabled", "base_url", "api_key", "flow_ids", "timeouts", "registered_flows"):
            assert f in data["xingchen"]

    async def test_put_get_storage_roundtrip(self, auth_client):
        """PUT 后 GET 脱敏回显；合并器解密 roundtrip"""
        client, token, user_id = auth_client
        resp = await client.put(
            "/api/integrations/storage",
            json={
                "provider": "minio",
                "endpoint_url": "http://minio.local:9000",
                "bucket": "user-bucket",
                "access_key": "ak123456789",
                "secret_key": "sk987654321",
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

        resp = await client.get("/api/integrations", headers=_auth(token))
        data = resp.json()["data"]["storage"]
        assert data["configured"] is True
        assert data["source"] == "user"
        assert data["bucket"] == "user-bucket"
        assert data["endpoint"] == "http://minio.local:9000"
        # 脱敏：****尾 4 位风格
        assert data["access_key"] == "ak1***6789"
        assert data["secret_key"] == "sk9***4321"

        # 合并器解密 roundtrip（真实 DB）
        async with _test_session_factory() as session:
            cfg = await resolve_storage_config(user_id, session)
        assert cfg.access_key == "ak123456789"
        assert cfg.secret_key == "sk987654321"
        assert cfg.bucket == "user-bucket"
        # 未覆盖字段回退 env
        assert cfg.region == settings.storage_region

    async def test_put_empty_string_keeps_null_clears(self, auth_client):
        """空字符串=保持原值；显式 null=清除回退 env"""
        client, token, _ = auth_client
        await client.put(
            "/api/integrations/storage",
            json={"bucket": "keep-bucket", "endpoint_url": "http://a.local:9000"},
            headers=_auth(token),
        )
        # 空字符串保持
        resp = await client.put(
            "/api/integrations/storage", json={"bucket": ""}, headers=_auth(token)
        )
        assert resp.json()["code"] == 0
        data = (await client.get("/api/integrations", headers=_auth(token))).json()["data"]["storage"]
        assert data["bucket"] == "keep-bucket"
        # null 清除 → 回退 env
        resp = await client.put(
            "/api/integrations/storage", json={"endpoint_url": None}, headers=_auth(token)
        )
        assert resp.json()["code"] == 0
        data = (await client.get("/api/integrations", headers=_auth(token))).json()["data"]["storage"]
        assert data["endpoint_url"] == settings.storage_endpoint_url

    async def test_put_validation(self, auth_client):
        client, token, _ = auth_client
        # 未知 kind
        resp = await client.put("/api/integrations/bogus", json={}, headers=_auth(token))
        assert resp.json()["code"] == 40001
        # 未知字段
        resp = await client.put(
            "/api/integrations/storage", json={"nope": 1}, headers=_auth(token)
        )
        assert resp.json()["code"] == 40001
        # 非法 provider
        resp = await client.put(
            "/api/integrations/storage", json={"provider": "gcs"}, headers=_auth(token)
        )
        assert resp.json()["code"] == 40001
        # 非法 port
        resp = await client.put(
            "/api/integrations/storage", json={"port": "abc"}, headers=_auth(token)
        )
        assert resp.json()["code"] == 40001

    async def test_put_xingchen_with_generic_flow(self, auth_client):
        """xingchen 配置：未注册 flow 名可保存并在 GET 中回显"""
        client, token, user_id = auth_client
        resp = await client.put(
            "/api/integrations/xingchen",
            json={
                "enabled": True,
                "base_url": "https://xc.private.local:8443",
                "api_key": "xckey123456",
                "api_secret": "xcsec123456",
                "flow_ids": {"wf_my_custom": "fid-999", "wf_web_search": "fid-100"},
                "timeouts": {"wf_my_custom": 45},
                "max_concurrency": 2,
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

        data = (await client.get("/api/integrations", headers=_auth(token))).json()["data"]["xingchen"]
        assert data["configured"] is True
        assert data["enabled"] is True
        assert data["api_key"] == "xck***3456"
        assert data["flow_ids"] == {"wf_my_custom": "fid-999", "wf_web_search": "fid-100"}
        assert data["timeouts"] == {"wf_my_custom": 45.0}
        assert data["max_concurrency"] == 2
        assert "wf_web_search" in data["registered_flows"]

        # 合并器：未注册名 → 通用处理器
        async with _test_session_factory() as session:
            cfg = await resolve_xingchen_config(user_id, session)
        spec = resolve_flow_spec("wf_my_custom", cfg.flow_ids)
        assert spec is not None and spec["generic"] is True
        # 注册表内 flow 行为不变
        assert resolve_flow_spec("wf_web_search", cfg.flow_ids) is FLOW_REGISTRY["wf_web_search"]

    async def test_delete_resets_to_env(self, auth_client):
        client, token, _ = auth_client
        await client.put(
            "/api/integrations/storage", json={"bucket": "to-delete"}, headers=_auth(token)
        )
        resp = await client.delete("/api/integrations/storage", headers=_auth(token))
        assert resp.json()["code"] == 0
        data = (await client.get("/api/integrations", headers=_auth(token))).json()["data"]["storage"]
        assert data["configured"] is False
        assert data["source"] == "env"


class TestIntegrationTestEndpoint:
    """POST /api/integrations/{kind}/test：mock boto3 / httpx"""

    async def _put_full_storage(self, client, token):
        await client.put(
            "/api/integrations/storage",
            json={
                "provider": "minio",
                "endpoint_url": "http://minio.local:9000",
                "bucket": "probe-bucket",
                "access_key": "ak123456789",
                "secret_key": "sk987654321",
            },
            headers=_auth(token),
        )

    async def test_storage_ok(self, auth_client):
        client, token, _ = auth_client
        await self._put_full_storage(client, token)
        mock_s3 = MagicMock()  # head_bucket / generate_presigned_url 默认不抛
        with patch("app.providers.storage.boto3.client", return_value=mock_s3):
            resp = await client.post("/api/integrations/storage/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is True
        names = [c["name"] for c in data["checks"]]
        assert names == ["config", "bucket", "presign"]
        assert all(c["ok"] for c in data["checks"])
        mock_s3.head_bucket.assert_called_once_with(Bucket="probe-bucket")

    async def test_storage_bucket_not_found(self, auth_client):
        client, token, _ = auth_client
        await self._put_full_storage(client, token)
        mock_s3 = MagicMock()
        mock_s3.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucket"}}, "HeadBucket"
        )
        with patch("app.providers.storage.boto3.client", return_value=mock_s3):
            resp = await client.post("/api/integrations/storage/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["error_category"] == "bucket_not_found"
        assert "bucket 不存在" in data["detail"]

    async def test_storage_endpoint_unreachable(self, auth_client):
        client, token, _ = auth_client
        await self._put_full_storage(client, token)
        mock_s3 = MagicMock()
        mock_s3.head_bucket.side_effect = EndpointConnectionError(
            endpoint_url="http://minio.local:9000"
        )
        with patch("app.providers.storage.boto3.client", return_value=mock_s3):
            resp = await client.post("/api/integrations/storage/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["error_category"] == "endpoint_unreachable"

    async def test_storage_incomplete_config(self, auth_client):
        """合并后仍缺配置 → config 检查失败，不触网"""
        client, token, _ = auth_client
        with patch(
            "app.gateway.integration_router.resolve_storage_config",
            new=AsyncMock(return_value=StorageConfig()),
        ):
            resp = await client.post("/api/integrations/storage/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["checks"][0]["name"] == "config"
        assert data["checks"][0]["ok"] is False

    async def test_xingchen_ok(self, auth_client):
        client, token, _ = auth_client
        await client.put(
            "/api/integrations/xingchen",
            json={
                "enabled": True,
                "base_url": "https://xc.private.local:8443",
                "api_key": "xckey123456",
                "api_secret": "xcsec123456",
                "flow_ids": {"wf_my_custom": "fid-999"},
            },
            headers=_auth(token),
        )
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=MagicMock(status_code=404))  # 任意响应即可达
        with (
            patch("app.gateway.integration_router.get_http", return_value=mock_http),
            patch(
                "app.gateway.integration_router.run_workflow",
                new=AsyncMock(return_value={"raw": "ok"}),
            ) as mock_run,
        ):
            resp = await client.post("/api/integrations/xingchen/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is True
        assert data["flows"] == [{"flow": "wf_my_custom", "flow_id": "fid-999", "type": "generic"}]
        names = [c["name"] for c in data["checks"]]
        assert names == ["config", "reachability", "flows", "probe"]
        assert all(c["ok"] for c in data["checks"])
        # 最小调用使用合并后配置
        assert mock_run.call_args.kwargs["config"].base_url == "https://xc.private.local:8443"

    async def test_xingchen_unreachable_and_probe_nonfatal(self, auth_client):
        """端点不可达 → ok=False；probe 失败不致命"""
        client, token, _ = auth_client
        await client.put(
            "/api/integrations/xingchen",
            json={
                "enabled": True,
                "base_url": "https://xc.private.local:8443",
                "api_key": "xckey123456",
                "api_secret": "xcsec123456",
                "flow_ids": {"wf_my_custom": "fid-999"},
            },
            headers=_auth(token),
        )
        # 场景 1：不可达
        mock_http = MagicMock()
        mock_http.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        with patch("app.gateway.integration_router.get_http", return_value=mock_http):
            resp = await client.post("/api/integrations/xingchen/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is False
        reach = [c for c in data["checks"] if c["name"] == "reachability"][0]
        assert reach["ok"] is False

        # 场景 2：可达但 probe 失败 → 整体 ok，probe 标注
        mock_http.get = AsyncMock(return_value=MagicMock(status_code=200))
        with (
            patch("app.gateway.integration_router.get_http", return_value=mock_http),
            patch(
                "app.gateway.integration_router.run_workflow",
                new=AsyncMock(side_effect=Exception("flow broken")),
            ),
        ):
            resp = await client.post("/api/integrations/xingchen/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is True
        probe = [c for c in data["checks"] if c["name"] == "probe"][0]
        assert probe["ok"] is False
        assert "非致命" in probe["detail"]

    async def test_xingchen_disabled(self, auth_client):
        """enabled=false → config 检查失败（patch 合并器保证确定性与 env 无关）"""
        client, token, _ = auth_client
        with patch(
            "app.gateway.integration_router.resolve_xingchen_config",
            new=AsyncMock(return_value=XingchenConfig(enabled=False)),
        ):
            resp = await client.post("/api/integrations/xingchen/test", headers=_auth(token))
        data = resp.json()["data"]
        assert data["ok"] is False
        assert "enabled" in data["detail"] or "开关" in data["detail"]
