"""RV02 · 迁移可重现性：upgrade → downgrade → upgrade 往返受测（真实 PostgreSQL）。"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]

from tests.conftest import assert_real_postgres  # noqa: E402


def _run(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(SERVICE_ROOT), env=env, capture_output=True, text=True,
    )


@pytest.fixture()
def mig_env(pg_url: str):
    assert_real_postgres(pg_url)
    schema = f"rv8300_mig_{uuid.uuid4().hex[:12]}"
    env = dict(os.environ)
    env["RESEARCH_DATABASE_URL"] = pg_url
    env["RESEARCH_DB_SCHEMA"] = schema
    env["RESEARCH_ENV"] = "test"
    return env, schema


def _sync_url(pg_url: str) -> str:
    return pg_url.replace("+psycopg", "").replace("+asyncpg", "")


def test_upgrade_head_creates_expected_tables(mig_env, pg_url) -> None:
    env, schema = mig_env
    try:
        r = _run(["upgrade", "head"], env)
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        with psycopg.connect(_sync_url(pg_url)) as c:
            tables = {
                t[0]
                for t in c.execute(
                    "select table_name from information_schema.tables where table_schema=%s", (schema,)
                ).fetchall()
            }
        for t in (
            "research_session",
            "domain_event",
            "outbox_event",
            "command_record",
            "pending_action",
            "pending_action_audit",
            "session_summary",
            "actor_security_floor",
        ):
            assert t in tables, f"缺少表 {t}"
    finally:
        _run(["downgrade", "base"], env)
        with psycopg.connect(_sync_url(pg_url), autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_downgrade_base_removes_everything(mig_env, pg_url) -> None:
    env, schema = mig_env
    try:
        assert _run(["upgrade", "head"], env).returncode == 0
        r = _run(["downgrade", "base"], env)
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        with psycopg.connect(_sync_url(pg_url)) as c:
            n = c.execute(
                "select count(*) from information_schema.tables "
                "where table_schema=%s and table_name <> %s",
                (schema, "alembic_version_rv8300"),
            ).fetchone()[0]
        assert n == 0, f"downgrade base 后仍有 {n} 张表"
    finally:
        with psycopg.connect(_sync_url(pg_url), autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_upgrade_downgrade_upgrade_roundtrip(mig_env, pg_url) -> None:
    env, schema = mig_env
    try:
        assert _run(["upgrade", "head"], env).returncode == 0
        assert _run(["downgrade", "base"], env).returncode == 0
        r = _run(["upgrade", "head"], env)
        assert r.returncode == 0, f"二次 upgrade 失败:\n{r.stdout}\n{r.stderr}"
        with psycopg.connect(_sync_url(pg_url)) as c:
            n = c.execute(
                "select count(*) from information_schema.tables where table_schema=%s", (schema,)
            ).fetchone()[0]
        assert n >= 8
    finally:
        _run(["downgrade", "base"], env)
        with psycopg.connect(_sync_url(pg_url), autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_single_head(mig_env) -> None:
    r = _run(["heads"], env=mig_env[0])
    assert r.returncode == 0
    heads = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith(" ")]
    assert len(heads) == 1, f"必须单链单 head，实际: {heads}"


def test_no_runtime_ddl_in_production_startup() -> None:
    """生产启动不得执行 runtime DDL（create_all）。"""
    import re

    src = (SERVICE_ROOT / "research_vnext" / "core" / "api" / "app.py").read_text(encoding="utf-8")
    code_only = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"', "'")))
    assert re.search(r"\.create_all\s*\(", code_only) is None, "生产启动路径出现 runtime DDL"
    # 且必须有 schema 校验（缺失即失败，而不是兜底建表）
    assert "assert_no_runtime_ddl_in_prod" in src


def test_ensure_schema_refuses_production() -> None:
    """`ensure_schema()` 在生产环境必须拒绝（只允许 test profile）。"""
    import os

    from research_vnext.core.db.engine import ensure_schema

    saved = os.environ.get("RESEARCH_ENV")
    os.environ["RESEARCH_ENV"] = "prod"
    try:
        try:
            ensure_schema()
        except RuntimeError as e:
            assert "runtime DDL" in str(e)
        else:
            raise AssertionError("生产环境 ensure_schema 未拒绝")
    finally:
        if saved is None:
            os.environ.pop("RESEARCH_ENV", None)
        else:
            os.environ["RESEARCH_ENV"] = saved
