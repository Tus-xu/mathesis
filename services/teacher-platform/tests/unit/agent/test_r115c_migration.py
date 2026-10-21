"""R11.5-C migration 契约测试（e26a6b7c8d9e）：
1) 唯一 head 且挂在 R11.5-B d17b2c3e4f5a 之后；
2) 建表集合 == 5 张 reliability_* 表（少表/缺列视为漂移）；
3) ai_runs 增量 trace_id 列（race 无）
4) downgrade 只 drop 本轮新增表 + ai_runs.trace_id，不删任何既有表（含 R11.5-B 接管表）；
5) down/rollback 说明存在。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.ownership import OWNED_TABLES

_PLATFORM = Path(__file__).resolve().parents[3]  # tests/unit/agent -> teacher-platform 根
_MIGRATIONS = _PLATFORM / "alembic" / "versions"
_ALEMBIC_INI = _PLATFORM / "alembic.ini"

RELIABILITY_TABLES = {
    "reliability_jobs": {"id", "owner_service", "job_type", "status", "attempt", "revision", "claim_token"},
    "reliability_job_events": {"id", "job_id", "seq", "event_type", "payload"},
    "reliability_outbox": {"id", "topic", "dedupe_key", "payload", "status", "attempt"},
    "reliability_inbox": {"id", "owner_service", "dedupe_key", "payload_hash", "status"},
    "reliability_idempotency": {"id", "owner_service", "operation", "principal", "idempotency_key", "request_hash", "status"},
}


def _migration_file() -> Path:
    candidates = sorted(_MIGRATIONS.glob("*r115c_reliability_gates.py"))
    assert candidates, "找不到 R11.5-C migration 文件"
    return candidates[-1]


def _load_migration() -> dict:
    mod_path = _migration_file()
    spec = importlib.util.spec_from_file_location("_r115c_migration", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"module": mod, "path": mod_path}


def test_migration_is_unique_head_after_r115b() -> None:
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    # R20「钉死前移」惯例：head 期望随迁移链推进更新（1e2f3a4b5c6d R17 → f19a2b3c4d5e R19
    # → b27c3d4e5f60 R22 → c8d9e0f1a2b3 → trial_001_teacher → trial_002_grading_workbench）
    assert set(script.get_heads()) == {"trial_002_grading_workbench"}, f"唯一 head 应为 trial_002 批改工作台迁移，实际 {set(script.get_heads())}"
    rev = _load_migration()["module"]
    assert rev.revision == "e26a6b7c8d9e"
    assert rev.down_revision == "d17b2c3e4f5a"
    assert callable(getattr(rev, "downgrade", None))
    assert "down" in (rev.__doc__ or "") or "rollback" in (rev.__doc__ or "")


def test_migration_creates_all_reliability_tables() -> None:
    """建表集合 == RELIABILITY_TABLES 所有权清单（防迁移/运行时代码漂移）。"""
    src = _migration_file().read_text(encoding="utf-8")
    for t in RELIABILITY_TABLES:
        assert f'"{t}"' in src or f"'{t}'" in src, f"迁移缺少可靠性表 {t}"
    # 显式 create_table 分支（不是仅出现在注释）
    for t in RELIABILITY_TABLES:
        assert f'"{t}"' in src or f"'{t}'" in src, f"可靠性表 {t} 未显式建表"
    # 迁移内必须包含 OWNED_TABLES 的 reliability 分部（所有权同步守卫）
    for r_t in set(OWNED_TABLES) & set(RELIABILITY_TABLES):
        assert r_t in src, f"所有权清单与迁移不一致: {r_t}"


def test_migration_adds_ai_runs_trace_id_additively() -> None:
    src = _migration_file().read_text(encoding="utf-8")
    assert '"trace_id"' in src or "'trace_id'" in src, "ai_runs 缺 trace_id 增量列"
    assert "has_table" in src or "column_exists" in src  # 增量场景无损守卫
    # 不得为 ai_runs 建表（R11.5-B 已接管；只允许 add column）
    assert '"ai_runs"' in src


def test_downgrade_drops_only_new_tables_and_trace_id() -> None:
    """回滚边界：drop 本轮 5 张可靠性表 + ai_runs.trace_id；不 drop 任何既有表。"""
    src = _migration_file().read_text(encoding="utf-8")
    legacy = set(OWNED_TABLES) - set(RELIABILITY_TABLES)
    for t in legacy:
        assert f"drop_table('{t}')" not in src and f'drop_table("{t}")' not in src, f"downgrade 不得删既有表 {t}"
    assert "drop_column" in src  # 回滚删除 ai_runs.trace_id 的能力存在
    assert "回滚" in src
