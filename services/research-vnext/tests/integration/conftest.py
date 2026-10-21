"""RV02 · 集成测试夹具。

**真实 PostgreSQL 门槛**：schema 由 Alembic `upgrade head` 在真实库上创建，
不是 `create_all` 伪造，也不是 SQLite。
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

# RV14 · 关闭 WorkBuddy 沙箱的批量删除守卫（仅本 pytest 进程环境）。
# 导出 worker 会在自己的专用临时目录（RESEARCH_TEX_WORKDIR，服务自建）
# 内做正常清理；该守卫按 turn 累计删除计数（50 个/轮），会把多次编译的
# 临时文件清理误判为危险批量删除并 SystemExit(1)，掩盖真实测试结果。
# 生产服务进程不含此 shim，不受影响。
for _k in (
    "CODEBUDDY_SAFE_DELETE_BULK_GUARD",
    "CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR",
    "CODEBUDDY_TOOL_CALL_ID",
):
    os.environ.pop(_k, None)

TEACHER_ENV = SERVICE_ROOT.parent / "teacher-platform" / ".env"

from tests.conftest import (  # noqa: E402
    TEST_HMAC_SECRET,
    assert_real_postgres,
    auth_headers,
    envelope,
)


@pytest.fixture(scope="session")
def it_schema(pg_url: str) -> Iterator[str]:
    """用 Alembic 在真实 PostgreSQL 上建一套独立 schema（upgrade head）。"""
    assert_real_postgres(pg_url)
    from tests.conftest import IT_SCHEMA

    schema = IT_SCHEMA
    env = dict(os.environ)
    env["RESEARCH_DATABASE_URL"] = pg_url
    env["RESEARCH_DB_SCHEMA"] = schema
    env["RESEARCH_ENV"] = "test"
    env["RESEARCH_GATEWAY_HMAC_SECRET"] = TEST_HMAC_SECRET

    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(SERVICE_ROOT),
        env=env,
        capture_output=True,
        text=True,
        # Windows 默认 GBK 解 alembic 输出（含中文迁移 docstring）会抛
        # UnicodeDecodeError 于 reader 线程 —— 固定 utf-8 并容错替换。
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        pytest.fail(f"alembic upgrade head 失败:\n{r.stdout}\n{r.stderr}")
    yield schema

    # teardown
    env2 = dict(env)
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=str(SERVICE_ROOT), env=env2, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    import psycopg

    sync_url = pg_url.replace("+psycopg", "").replace("+asyncpg", "")
    with psycopg.connect(sync_url, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture(scope="session")
def app_env(it_schema: str, pg_url: str):
    """把环境变量注入 app 工厂（供 TestClient 使用）。"""
    os.environ["RESEARCH_DATABASE_URL"] = pg_url
    os.environ["RESEARCH_DB_SCHEMA"] = it_schema
    os.environ["RESEARCH_ENV"] = "test"
    os.environ["RESEARCH_GATEWAY_HMAC_SECRET"] = TEST_HMAC_SECRET
    os.environ["RESEARCH_SSE_HEARTBEAT_SECONDS"] = "1"
    os.environ["RESEARCH_SSE_RETENTION_EVENTS"] = "200"
    return os.environ


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    from research_vnext.core.api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def new_ids():
    return {
        "tenant_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
    }


def session_scope(tenant_id, project_id) -> dict:
    return {"kind": "project", "id": str(project_id), "role": "owner"}


def teacher_database_url() -> str | None:
    """Teacher direct-chunk 的只读 DSN（RV06 FoundationalMath 用）。

    只从环境变量或 `services/teacher-platform/.env` 读取，**从不**打印内容。
    """
    import re

    explicit = os.getenv("RESEARCH_FOUNDATIONAL_DATABASE_URL")
    if explicit:
        return re.sub(r"^postgresql\+\w+://", "postgresql+psycopg://", explicit)
    if not TEACHER_ENV.exists():
        return None
    for line in TEACHER_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*DATABASE_URL\s*=\s*(.*)$", line)
        if m:
            raw = m.group(1).strip().strip('"').strip("'")
            return re.sub(r"^postgresql\+\w+://", "postgresql+psycopg://", raw)
    return None


@pytest.fixture(scope="module")
def foundational_dsn() -> str:
    """真实 Teacher 只读 DSN。不可达时 `skip`，**不**降级为假数据。"""
    from sqlalchemy import text as sa_text

    from research_vnext.core.federation.providers.foundational_math import (
        build_readonly_engine,
    )

    dsn = teacher_database_url()
    if not dsn:
        pytest.skip("Teacher DSN 不可用 → 跳过真实 FoundationalMath 用例")
    try:
        engine = build_readonly_engine(dsn)
        with engine.connect() as conn:
            conn.execute(sa_text("select 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Teacher 只读库不可达（{type(exc).__name__}）→ 跳过")
    os.environ["RESEARCH_FOUNDATIONAL_DATABASE_URL"] = dsn
    return dsn


def create_session(client, ctx: dict, ids: dict, *, title="t", trace_extra=None, crid=None):
    """走真实 API 建会话，返回 (resp, body)。"""
    payload = {
        "title": title,
        "mode": "MODELING",
        "project_id": str(ids["project_id"]),
        "tenant_id": str(ids["tenant_id"]),
        "policy_refs": ["policy:research.default@v1"],
    }
    body = envelope(
        command_type="session.create",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "project", "id": str(ids["project_id"]), "role": "owner"},
        payload=payload,
        expected_revision=0,
        tenant_id=ids["tenant_id"],
        client_request_id=crid,
    )
    return client.post("/api/research-v1/sessions:create", json=body, headers=auth_headers(ctx)), body
