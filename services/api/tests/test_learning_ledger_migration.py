"""stu_002_learning_ledger_tables 迁移测试（stu-r2 管线通电轨）。

验收（对齐定稿方案 C）：
- 干净库（仅 alembic upgrade head，无 create_all）：learning_events /
  exam_papers / exam_paper_items / ai_recommendations 四表存在，trial/metrics
  探针不抛；
- 已有四表的库：迁移为 no-op 不报错（has_table 守卫），既有数据零丢失；
- alembic_version 推进到链头（tel_001_two_end_link，tel_001 在 stu_002 之后
  仅单向加宽 knowledge_docs.source_type；head 随迁移链前进属门禁同步惯例）。

隔离策略：在 54329 实例上创建一次性临时库（CREATE EXTENSION vector 后跑全链），
跑完 DROP，不污染 test_math_arena / dev math_arena。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import settings

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"

FOUR_TABLES = ("learning_events", "exam_papers", "exam_paper_items", "ai_recommendations")
# tel 门禁同步（同 test_teacher_grading_v2_workspace 惯例）：head 随迁移链前进
# stu_002_learning_ledger_tables -> tel_001_two_end_link，仍为单一线性 head。
_HEAD = "tel_001_two_end_link"

_scratch_url: str | None = None
_scratch_name: str | None = None


def _conn_kwargs(database: str) -> dict:
    url = make_url(settings.database_url)
    return {
        "host": url.host or "localhost",
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "database": database,
    }


async def _create_scratch_db(name: str) -> None:
    conn = await asyncpg.connect(**_conn_kwargs("postgres"))
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
        # 链中 pgvector 迁移假设 vector 类型可用（dev 库由容器镜像提供）
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()


async def _drop_scratch_db(name: str) -> None:
    conn = await asyncpg.connect(**_conn_kwargs("postgres"))
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


def _run_alembic(command_name: str, target: str, url: str) -> None:
    """在指定 URL 上执行 alembic 命令（env.py 运行时读 settings.database_url）。"""
    from app.config import settings as app_settings

    old_url = app_settings.database_url
    app_settings.database_url = url
    try:
        cfg = Config(str(ALEMBIC_INI))
        # script_location 在 ini 中是相对路径；显式钉到 services/api/alembic
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        getattr(command, command_name)(cfg, target)
    finally:
        app_settings.database_url = old_url


def _fetchall(query: str) -> list:
    assert _scratch_url is not None

    async def _run():
        conn = await asyncpg.connect(**_conn_kwargs(_scratch_name))
        try:
            return await conn.fetch(query)
        finally:
            await conn.close()

    return asyncio.run(_run())


def _scalar(query: str) -> object:
    assert _scratch_url is not None

    async def _run():
        conn = await asyncpg.connect(**_conn_kwargs(_scratch_name))
        try:
            return await conn.fetchval(query)
        finally:
            await conn.close()

    return asyncio.run(_run())


def _table_exists(table: str) -> bool:
    return _scalar(
        f"SELECT to_regclass('public.{table}') IS NOT NULL"
    ) is True


def setup_module(module) -> None:
    """一次性：建临时库 + alembic upgrade head（干净库全链验证）。"""
    global _scratch_url, _scratch_name
    _scratch_name = f"test_stu002_{uuid.uuid4().hex[:10]}"
    asyncio.run(_create_scratch_db(_scratch_name))
    base = settings.database_url.rsplit("/", 1)[0]
    _scratch_url = f"{base}/{_scratch_name}"
    _run_alembic("upgrade", "head", _scratch_url)


def teardown_module(module) -> None:
    global _scratch_url, _scratch_name
    if _scratch_name is not None:
        try:
            asyncio.run(_drop_scratch_db(_scratch_name))
        finally:
            _scratch_url = None
            _scratch_name = None


# ==================== 干净库：upgrade head 后四表存在 ====================


def test_clean_db_upgrade_head_creates_four_tables():
    for table in FOUR_TABLES:
        assert _table_exists(table), f"干净库缺表: {table}"
    assert _scalar("SELECT version_num FROM alembic_version") == _HEAD
    # 迁移链完整性的旁证：trial_001 的表也建出
    assert _table_exists("trial_feedback")


def test_clean_db_column_mirrors_model():
    """列/索引/唯一约束与模型一致（learning_event.py:25-42 / exam_paper.py:23-55 /
    ai_recommendation.py:19-32 的关键列抽查）。"""

    async def _check():
        conn = await asyncpg.connect(**_conn_kwargs(_scratch_name))
        try:
            le_cols = {
                r["column_name"]
                for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='learning_events'"
                )
            }
            assert {
                "id", "user_id", "event_type", "source_type", "source_id", "payload",
                "idempotency_key", "status", "retry_count", "processed_at",
                "created_at", "updated_at",
            } <= le_cols, le_cols
            ar_cols = {
                r["column_name"]
                for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='ai_recommendations'"
                )
            }
            assert {
                "id", "user_id", "kind", "source", "payload", "llm_model",
                "user_feedback", "shown_at", "acted_at", "created_at", "updated_at",
            } <= ar_cols, ar_cols
            ep_cols = {
                r["column_name"]
                for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='exam_papers'"
                )
            }
            assert {
                "id", "title", "source", "year", "subject", "scope", "total_score",
                "duration_minutes", "structure", "status", "created_at", "updated_at",
            } <= ep_cols, ep_cols
            epi_cols = {
                r["column_name"]
                for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='exam_paper_items'"
                )
            }
            assert {
                "id", "paper_id", "item_no", "q_type", "stem", "options", "answer",
                "analysis", "kp_code", "difficulty", "score", "created_at", "updated_at",
            } <= epi_cols, epi_cols
            # 索引（learning_event.py:31-34 / ai_recommendation.py:22-32 命名）
            le_idx = {
                r["indexname"]
                for r in await conn.fetch(
                    "SELECT indexname FROM pg_indexes WHERE tablename='learning_events'"
                )
            }
            assert {"idx_learning_events_user_status", "idx_learning_events_type_time"} <= le_idx
            ar_idx = {
                r["indexname"]
                for r in await conn.fetch(
                    "SELECT indexname FROM pg_indexes WHERE tablename='ai_recommendations'"
                )
            }
            assert "idx_ai_rec_user_kind_time" in ar_idx
            # 幂等键唯一（learning_event.py:39 unique）
            le_uq = await conn.fetch(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'learning_events'::regclass AND contype = 'u'"
            )
            assert le_uq, "learning_events.idempotency_key 唯一约束缺失"
        finally:
            await conn.close()

    asyncio.run(_check())


def test_clean_db_trial_metrics_probe_runs():
    """/api/trial/metrics 的聚合探针（缺表即 500 的路径）在迁移后的干净库不抛。"""
    rows = _fetchall(
        "SELECT event_type, count(id) FROM learning_events GROUP BY event_type"
    )
    assert rows == []
    assert _scalar("SELECT count(id) FROM ai_recommendations") == 0


# ==================== 已有四表库：守卫 no-op + 数据零丢失 ====================


def test_existing_tables_noop_and_data_preserved():
    """已有四表的库（模拟 dev 库形态）：stamp 回 trial_001 后 upgrade head →
    守卫跳过建表、报错为 0、alembic_version 推进、预置数据保留。"""
    seed_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    async def _seed():
        conn = await asyncpg.connect(**_conn_kwargs(_scratch_name))
        try:
            # 初始迁移无 users 种子数据 → 插入最小用户行（其余列均有 server_default）
            await conn.execute(
                "INSERT INTO users (id) VALUES ($1)",
                user_id,
            )
            await conn.execute(
                "INSERT INTO learning_events (id, user_id, event_type, source_type, payload)"
                " VALUES ($1, $2, 'quiz_judge', 'practice', '{}')",
                seed_id,
                user_id,
            )
        finally:
            await conn.close()

    asyncio.run(_seed())

    # stamp 回上一版 → upgrade head 只跑 stu_002（此时四表已存在 → 守卫 no-op）
    _run_alembic("stamp", "trial_001_feedback", _scratch_url)
    _run_alembic("upgrade", "head", _scratch_url)

    assert _scalar("SELECT version_num FROM alembic_version") == _HEAD
    for table in FOUR_TABLES:
        assert _table_exists(table)
    # 数据零丢失
    assert _scalar("SELECT count(id) FROM learning_events") == 1
    assert _scalar(
        "SELECT count(id) FROM learning_events WHERE id = '%s'" % seed_id
    ) == 1
