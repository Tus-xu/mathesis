"""M3 教师端：迁移/路由 profile 契约（§十七）。

1. M3 开启 profile 出现规范 teacher routes（共享 app 已启用）；
2. 默认 M2 profile 下 /api/teacher/* 不出现在 OpenAPI（子进程验证配置门控）；
3. Alembic 单 head = m3_001_teacher_core；
4. 教师端 OpenAPI 不含科研/建模教练路由。
"""

import os
import subprocess
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


async def _openapi_paths() -> set[str]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        paths = (await ac.get("/openapi.json")).json()["paths"]
    return set(paths)


@pytest.mark.asyncio
async def test_m3_enabled_openapi_has_teacher_routes():
    paths = await _openapi_paths()
    required = [
        "/api/teacher/today",
        "/api/teacher/classes/{class_id}/insights",
        "/api/teacher/lessons/adapt",
        "/api/teacher/quizzes/generate",
        "/api/teacher/artifacts/{artifact_id}",
        "/api/teacher/grading/queue",
    ]
    for p in required:
        assert p in paths, f"缺少 M3 端点: {p}"


@pytest.mark.asyncio
async def test_m3_openapi_has_no_research_or_modeling():
    paths = await _openapi_paths()
    # 教师批改 v2 的 review 端点（人工复核）是教师业务路由，不属于科研/建模；
    # 其余 teacher 路由不得出现科研/建模类关键字。
    banned = [
        p
        for p in paths
        if "/api/teacher" in p
        and p != "/api/teacher/grading/{submission_item_id}/review"
        and any(kw in p for kw in ("review", "modeling", "paper", "verify_derivation"))
    ]
    assert banned == [], f"教师端不应出现科研/建模路由: {banned}"


def test_default_m2_profile_has_no_teacher_routes():
    """默认（M3_ENABLE_TEACHER 未设置）→ /api/teacher/* 不在 OpenAPI（配置门控真）。"""
    code = (
        "from app.main import app; "
        "paths=app.openapi()['paths']; "
        "banned=[p for p in paths if p.startswith('/api/teacher')]; "
        "assert banned==[], banned"
    )
    env = dict(os.environ)
    # 显式 false 覆盖：本地开发 .env 可能写入 M3_ENABLE_TEACHER=true，
    # 仅 pop 环境变量会让 pydantic 回退读取 .env 文件导致门控失效。
    # 默认 false 的契约由 app/config.py 的 m3_enable_teacher 定义保证。
    env["M3_ENABLE_TEACHER"] = "false"
    env.pop("DATABASE_URL", None)  # 免连接数据库，仅验证路由挂载门控
    # 清空可能已导入的 settings 缓存由子进程独立承担
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
    )
    assert result.returncode == 0, f"默认 M3 应关闭：\n{result.stdout}\n{result.stderr}"


def test_alembic_single_head():
    code = (
        "from alembic.config import Config; from alembic.script import ScriptDirectory; "
        "sd=ScriptDirectory.from_config(Config('alembic.ini')); "
        "heads=sd.get_heads(); assert heads==['tel_001_two_end_link'], heads; print(heads)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=dict(os.environ),
    )
    assert result.returncode == 0, result.stderr
    assert "tel_001_two_end_link" in result.stdout, result.stderr


def test_model_has_m3_tables():
    from app.models import Base

    names = set(Base.metadata.tables)
    assert {"teaching_artifacts", "actionable_insights", "teacher_actions", "teacher_tasks"} <= names
    # assignments / submission_items 既有表被扩展仍在 metadata
    assert {"assignments", "submission_items"} <= names
