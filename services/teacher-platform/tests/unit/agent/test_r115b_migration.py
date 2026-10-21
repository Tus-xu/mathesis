"""R11.5-B migration 契约测试（d17b2c3e4f5a）：
1) 唯一 head 且挂在 R10 c17d2e3f405a 之后；
2) 建表集合 == OWNED_TABLES 所有权清单 + checkpoint 4 表；少表/缺列视为漂移；
3) downgrade 可回滚（drop 新增表 + ai_runs 审计列）且不删接管前运行时表；
4) 迁移头/回滚说明存在。
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


def _migration_file() -> Path:
    candidates = sorted(_MIGRATIONS.glob("*r115b_runtime_persistence_takeover.py"))
    assert candidates, "找不到 R11.5-B migration 文件"
    return candidates[-1]


def _load_migration() -> dict:
    mod_path = _migration_file()
    spec = importlib.util.spec_from_file_location("_r115b_migration", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"module": mod, "path": mod_path}


def test_migration_is_unique_head_after_r10() -> None:
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    # R20「钉死前移」惯例：head 期望随迁移链推进更新（1e2f3a4b5c6d R17 → f19a2b3c4d5e R19
    # → b27c3d4e5f60 R22 → c8d9e0f1a2b3 → trial_001_teacher → trial_002_grading_workbench）
    assert set(script.get_heads()) == {"trial_002_grading_workbench"}, f"唯一 head 应为 trial_002 批改工作台迁移，实际 {set(script.get_heads())}"
    rev = _load_migration()["module"]
    assert rev.revision == "d17b2c3e4f5a"
    assert rev.down_revision == "c17d2e3f405a"
    assert callable(getattr(rev, "downgrade", None))
    assert "down" in (rev.__doc__ or "") or "rollback" in (rev.__doc__ or "")


def test_migration_creates_owned_tables_and_checkpoint_tables() -> None:
    """建表覆盖 == 所有权清单（除 R11.5-C 可靠性表）+ checkpoint 4 表（防迁移/运行时代码漂移）。"""
    src = _migration_file().read_text(encoding="utf-8")
    cp_tables = {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
    # R11.5-C 新增 reliability_* 表归 e26a6b7c8d9e 所有，不在 R11.5-B 迁移内（契约分层）
    r115c_tables = {
        "reliability_jobs", "reliability_job_events", "reliability_outbox",
        "reliability_inbox", "reliability_idempotency",
    }
    # R14 新增 tp_* 表归 16a2b3c4d5e6 所有；R17 新增 tp_conversation_*/tp_message_runs/tp_context_snapshots
    # 归 1e2f3a4b5c6d 所有；均不在 R11.5-B 迁移内。
    # R22（b27c3d4e5f60，T24 编辑/QA/产物投递）新增 ppt_v2_spec_revisions/ppt_v2_page_qa/
    # ppt_v2_edit_proposals/teacher_artifact_*/tp_lesson_plan_revisions/tp_lesson_plan_edit_proposals
    # 亦不在 R11.5-B 迁移内（tel F5：OWNED_TABLES 已注册 → 排除集随所有权分层同步）
    r14_tables = {
        "tp_sessions", "tp_blueprints", "tp_blueprint_revisions",
        "tp_session_commands", "tp_session_events",
    }
    r22_tables = {
        "ppt_v2_spec_revisions", "ppt_v2_page_qa", "ppt_v2_edit_proposals",
        "teacher_artifacts", "teacher_artifact_tickets", "teacher_artifact_download_audit",
        "tp_lesson_plan_revisions", "tp_lesson_plan_edit_proposals",
    }
    expected = (set(OWNED_TABLES) - r115c_tables - r14_tables - r22_tables) | cp_tables
    # 迁移内明确出现全部表标识符（create/drop/add 分支均算）
    for t in expected:
        assert t in src, f"迁移缺少表 {t}"
    # 新增正式表必须在 create_table 分支出现（接管表允许 has_table preflight 分支）
    for t in {"conversation_threads", "conversation_summaries", "prompt_releases",
              "tool_call_records", "pending_actions"}:
        assert f'"{t}"' in src or f"'{t}'" in src, f"新增表 {t} 未显式建表"


def test_migration_adds_ai_runs_audit_columns_idempotently() -> None:
    src = _migration_file().read_text(encoding="utf-8")
    for col in ("lineage", "input_hash", "output_hash"):
        assert f'"{col}"' in src or f"'{col}'" in src, f"ai_runs 缺审计列 {col}"
    # 增量场景用 has_table / column_exists 守卫（无损 upgrade 语义）
    assert "has_table" in src
    assert "column_exists" in src


def test_downgrade_drops_only_new_tables_and_audit_cols() -> None:
    """回滚边界：drop 本轮新增表 + ai_runs 审计列；不得 drop 接管前运行时表。"""
    src = _migration_file().read_text(encoding="utf-8")
    legacy = {"teacher_session_contexts", "teacher_memories", "teacher_memory_history",
              "butler_messages", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
    for t in legacy:
        # downgrade 列表只出现在「保留」注释上下文中，不允许 drop
        assert f"drop_table('{t}')" not in src and f'drop_table("{t}")' not in src, f"downgrade 不得删接管表 {t}"
    for col in ("drop_column",):
        assert col in src  # 回滚删除 ai_runs 审计列的能力存在
    # 回滚语义注释
    assert "回滚" in _migration_file().read_text(encoding="utf-8")
