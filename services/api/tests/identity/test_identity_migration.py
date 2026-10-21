"""Alembic round-trip and legacy identity backfill tests."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import TEST_DB

API_ROOT = Path(__file__).resolve().parents[2]
ADMIN_DSN = (
    f"postgresql://{TEST_DB['user']}:{TEST_DB['password']}"
    f"@{TEST_DB['host']}:{TEST_DB['port']}/postgres"
)


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    env["AUTH_MIGRATION_APPROVED_RESEARCHER_PHONES"] = "13800138003"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
async def migration_database():
    database_name = f"test_auth_migration_{uuid.uuid4().hex[:12]}"
    assert database_name.replace("_", "").isalnum()
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'CREATE DATABASE "{database_name}"')
    database_url = (
        f"postgresql://{TEST_DB['user']}:{TEST_DB['password']}"
        f"@{TEST_DB['host']}:{TEST_DB['port']}/{database_name}"
    )
    try:
        yield database_url
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await admin.execute(f'DROP DATABASE "{database_name}"')
        await admin.close()


async def _table_exists(connection: asyncpg.Connection, table_name: str) -> bool:
    return bool(
        await connection.fetchval(
            "SELECT to_regclass('public.' || $1) IS NOT NULL",
            table_name,
        )
    )


async def _column_exists(
    connection: asyncpg.Connection, table_name: str, column_name: str
) -> bool:
    return bool(
        await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=$1 AND column_name=$2)",
            table_name,
            column_name,
        )
    )


async def test_identity_migration_backfills_roles_and_round_trips(migration_database):
    before = _run_alembic(migration_database, "upgrade", "m3_002_fullstack_closure")
    assert before.returncode == 0, before.stdout + before.stderr

    student_id, teacher_id, researcher_id, seeded_researcher_id = [uuid.uuid4() for _ in range(4)]
    class_id, class_member_id = uuid.uuid4(), uuid.uuid4()
    connection = await asyncpg.connect(migration_database)
    try:
        await connection.executemany(
            "INSERT INTO users (id, phone, nickname) VALUES ($1, $2, $3)",
            [
                (student_id, "13800138000", "student"),
                (teacher_id, "13800138001", "teacher"),
                (researcher_id, "13800138002", "researcher"),
                (seeded_researcher_id, "13800138003", "seeded researcher"),
            ],
        )
        await connection.executemany(
            "INSERT INTO role_bindings (user_id, role, verified) VALUES ($1, $2, $3)",
            [
                (student_id, "student", False),
                (teacher_id, "teacher", True),
                (researcher_id, "researcher", True),
                (seeded_researcher_id, "researcher", True),
            ],
        )
        await connection.execute(
            "INSERT INTO classes (id, name, invite_code, owner_id, grade) "
            "VALUES ($1, '迁移验证班', 'MIGR2608', $2, '高二')",
            class_id,
            teacher_id,
        )
        await connection.execute(
            "INSERT INTO class_members (id, class_id, user_id, member_role, confirmed) "
            "VALUES ($1, $2, $3, 'student', true)",
            class_member_id,
            class_id,
            student_id,
        )
        await connection.execute(
            "INSERT INTO events (user_id, event, props) "
            "VALUES ($1, 'migration.learning_record', '{\"source\": \"migration-test\"}')",
            student_id,
        )
        before_counts = {
            "users": await connection.fetchval("SELECT count(*) FROM users"),
            "role_bindings": await connection.fetchval("SELECT count(*) FROM role_bindings"),
            "classes": await connection.fetchval("SELECT count(*) FROM classes"),
            "class_members": await connection.fetchval("SELECT count(*) FROM class_members"),
            "events": await connection.fetchval("SELECT count(*) FROM events"),
        }
    finally:
        await connection.close()

    upgraded = _run_alembic(migration_database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

    connection = await asyncpg.connect(migration_database)
    try:
        assert await _table_exists(connection, "auth_sessions")
        assert await _table_exists(connection, "user_credentials")
        assert await _table_exists(connection, "user_consents")
        assert await _column_exists(connection, "users", "security_version")
        assert await _column_exists(connection, "student_profiles", "school_stage")
        after_counts = {
            name: await connection.fetchval(f'SELECT count(*) FROM "{name}"')
            for name in before_counts
        }
        assert before_counts == after_counts == {
            "users": 4,
            "role_bindings": 4,
            "classes": 1,
            "class_members": 1,
            "events": 1,
        }

        statuses = dict(
            await connection.fetch(
                "SELECT rb.role, rb.status FROM role_bindings rb "
                "JOIN users u ON u.id = rb.user_id ORDER BY u.phone"
            )
        )
        # A dict cannot distinguish the two researcher rows; assert them by phone below.
        assert statuses["student"] == "approved"
        assert statuses["teacher"] == "approved"
        role_rows = await connection.fetch(
            "SELECT u.phone, rb.status FROM role_bindings rb "
            "JOIN users u ON u.id = rb.user_id ORDER BY u.phone"
        )
        assert {row["phone"]: row["status"] for row in role_rows} == {
            "13800138000": "approved",
            "13800138001": "approved",
            "13800138002": "pending",
            "13800138003": "approved",
        }
    finally:
        await connection.close()

    downgraded = _run_alembic(migration_database, "downgrade", "m3_002_fullstack_closure")
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
    connection = await asyncpg.connect(migration_database)
    try:
        assert not await _table_exists(connection, "auth_sessions")
        assert not await _column_exists(connection, "users", "security_version")
        assert await _column_exists(connection, "role_bindings", "verified")
        assert await connection.fetchval("SELECT count(*) FROM users") == 4
        assert await connection.fetchval("SELECT count(*) FROM role_bindings") == 4
        assert await connection.fetchval("SELECT count(*) FROM classes") == 1
        assert await connection.fetchval("SELECT count(*) FROM class_members") == 1
        assert await connection.fetchval("SELECT count(*) FROM events") == 1
    finally:
        await connection.close()

    reupgraded = _run_alembic(migration_database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stdout + reupgraded.stderr
    connection = await asyncpg.connect(migration_database)
    try:
        assert await connection.fetchval("SELECT count(*) FROM users") == 4
        assert await connection.fetchval("SELECT count(*) FROM role_bindings") == 4
        assert await connection.fetchval("SELECT count(*) FROM classes") == 1
        assert await connection.fetchval("SELECT count(*) FROM class_members") == 1
        assert await connection.fetchval("SELECT count(*) FROM events") == 1
    finally:
        await connection.close()
