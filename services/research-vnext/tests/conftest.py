"""RV02 · 测试夹具。

铁律：
1. **禁止 SQLite / in-memory 冒充生产证据** —— `assert_real_postgres` 会主动拒绝非 PostgreSQL URL；
2. AccessContext 由**网关签名**注入（HMAC），测试用同一机制生成，**不是绕过鉴权的 mock**；
3. 每个 integration 用例使用**独立 schema**，互不污染。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Iterator

import pytest

# 服务根目录入 sys.path（包为 research_vnext，core 在其下）
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

TEST_HMAC_SECRET = "rv02-test-gateway-secret-not-a-production-value"

# --------------------------------------------------------------------------
# 必须在**任何** research_vnext 导入前固定 schema
# （`Base.metadata.schema` 在类定义期读取配置，之后不可改）
# --------------------------------------------------------------------------
DEFAULT_PG_URL = "postgresql+psycopg://postgres:postgres@localhost:54329/research_vnext"
IT_SCHEMA = os.getenv("RESEARCH_DB_SCHEMA") or f"rv8300_it_{uuid.uuid4().hex[:12]}"
os.environ["RESEARCH_DB_SCHEMA"] = IT_SCHEMA
os.environ.setdefault("RESEARCH_DATABASE_URL", DEFAULT_PG_URL)
os.environ.setdefault("RESEARCH_ENV", "test")
os.environ.setdefault("RESEARCH_GATEWAY_HMAC_SECRET", TEST_HMAC_SECRET)
os.environ.setdefault("RESEARCH_SSE_HEARTBEAT_SECONDS", "1")
os.environ.setdefault("RESEARCH_SSE_RETENTION_EVENTS", "200")


# --------------------------------------------------------------------------
# 环境守卫
# --------------------------------------------------------------------------


def test_database_url() -> str:
    return os.getenv(
        "RESEARCH_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:54329/research_vnext",
    )


def assert_real_postgres(url: str) -> None:
    """生产证据门槛：非 PostgreSQL 一律视为伪造证据。"""
    assert url.startswith("postgresql"), (
        f"RV02 禁止以非 PostgreSQL 结果冒充生产证据，当前 URL 方案: {url.split(':')[0]!r}"
    )


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = test_database_url()
    assert_real_postgres(url)
    return url


# --------------------------------------------------------------------------
# AccessContext（网关签名）
# --------------------------------------------------------------------------


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign(payload: str, secret: str = TEST_HMAC_SECRET) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_access_context(
    *,
    actor_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    workspace: str = "research",
    domain: str = "research-vnext",
    capabilities: list[str] | None = None,
    resource_scopes: list[dict] | None = None,
    security_version: int = 1,
    policy_version: str = "p1",
) -> dict:
    return {
        "actor_id": str(actor_id or uuid.uuid4()),
        "tenant_id": str(tenant_id or uuid.uuid4()),
        "session_id": str(session_id or uuid.uuid4()),
        "workspace": workspace,
        "domain": domain,
        "capabilities": capabilities if capabilities is not None else ["workspace.research.enter", "research.session.write"],
        "resource_scopes": resource_scopes or [],
        "security_version": security_version,
        "policy_version": policy_version,
        "trace_id": hashlib.md5(uuid.uuid4().bytes).hexdigest(),
        "authority": "api",
    }


def auth_headers(ctx: dict, *, secret: str = TEST_HMAC_SECRET, tamper: bool = False) -> dict[str, str]:
    payload = b64u(json.dumps(ctx, separators=(",", ":")).encode())
    sig = sign(payload, secret)
    if tamper:
        sig = "0" * 64
    return {
        "X-Research-Access-Context": payload,
        "X-Research-Access-Sig": sig,
        "Content-Type": "application/json",
    }


@pytest.fixture()
def ctx_factory():
    return make_access_context


@pytest.fixture()
def hdr_factory():
    return auth_headers


def traceparent(trace_hex: str | None = None) -> str:
    trace_hex = trace_hex or hashlib.md5(uuid.uuid4().bytes).hexdigest() * 2
    return f"00-{trace_hex[:32]}-{trace_hex[32:48] if len(trace_hex) >= 48 else '0000000000000001'}-01"


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def envelope(
    *,
    command_type: str,
    actor_id: uuid.UUID,
    scope: dict,
    payload: dict,
    expected_revision: int,
    client_request_id: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """构造 P00 CommandEnvelope 兼容 body。"""
    return {
        "client_request_id": client_request_id or f"crq-{uuid.uuid4().hex[:16]}",
        "expected_revision": expected_revision,
        "actor_id": str(actor_id),
        "scope": scope,
        "traceparent": traceparent(),
        "deadline": "2030-01-01T00:00:00Z",
        "idempotency_fingerprint": fingerprint(payload),
        "command_type": command_type,
        "payload_ref": f"inline:{fingerprint(payload)[:16]}",
        "tenant_id": str(tenant_id) if tenant_id else None,
        "payload": payload,
    }


@pytest.fixture()
def env_factory():
    return envelope


@pytest.fixture()
def fp():
    return fingerprint
