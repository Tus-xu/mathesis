"""R08 order 迁移契约测试：挂在 R05 之后 / 加 order 列 / down 可回滚。

R10 起迁移链 head 前移为 c17d2e3f405a（ppt_v2 domain），R08 不再是 head；
本文件只校验 R08 迁移自身定义（revision/down_revision/列/downgrade）与链上位置不变。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_PLATFORM = Path(__file__).resolve().parents[3]  # tests/unit/textbook_v2 -> teacher-platform 根
_MIGRATIONS = _PLATFORM / "alembic" / "versions"
_ALEMBIC_INI = _PLATFORM / "alembic.ini"


def _load_order_migration() -> dict:
    candidates = sorted(_MIGRATIONS.glob("*textbook_chunk_order.py"))
    assert candidates, "找不到 R08 order migration 文件"
    mod_path = candidates[-1]
    spec = importlib.util.spec_from_file_location("_tb_v2_migration_order", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"module": mod, "path": mod_path}


def test_r08_migration_position_in_chain() -> None:
    """R08 迁移定义不变（down_revision=a05b1c2d3e4f）；链 head 已由 R11.5-C、R12、R14、R17（1e2f3a4b5c6d）、R18、R20 profile/specs、R19（f19a2b3c4d5e）、
    R22（b27c3d4e5f60）、c8d9e0f1a2b3、trial_001_teacher 前移至 trial_002_grading_workbench。"""
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    # R20「钉死前移」惯例：head 期望随迁移链推进更新（1e2f3a4b5c6d R17 → f19a2b3c4d5e R19 →
    # b27c3d4e5f60 R22 → c8d9e0f1a2b3 → trial_001_teacher → trial_002_grading_workbench）
    assert set(script.get_heads()) == {"trial_002_grading_workbench"}, f"唯一 head 应为 trial_002 批改工作台迁移，实际 {set(script.get_heads())}"
    mod = _load_order_migration()["module"]
    assert mod.revision == "b06c2d3e4f5a"
    assert mod.down_revision == "a05b1c2d3e4f"
    # down/rollback 说明与实现都存在（AGENTS.md 迁移要求）
    assert "down" in (mod.__doc__ or "") or "rollback" in (mod.__doc__ or "")
    assert callable(getattr(mod, "downgrade", None))


def test_r08_migration_adds_order_column_and_index() -> None:
    src = _load_order_migration()["path"].read_text(encoding="utf-8")
    assert 'op.add_column("textbook_chunks"' in src
    assert '"order", sa.Integer(), nullable=True' in src
    assert 'op.create_index("ix_tbchunk_section_order"' in src


def test_r08_downgrade_drops_order_only() -> None:
    src = _load_order_migration()["path"].read_text(encoding="utf-8")
    assert 'op.drop_column("textbook_chunks", "order")' in src
    assert "op.drop_index" in src
    # downgrade 不触碰向量/其他列（回滚不删索引数据）
    assert "drop_table" not in src


def test_orm_chunk_has_nullable_order() -> None:
    """ORM 模型必须带 order 列且 nullable（null = 尚未回填）。"""
    from app.db.base import Base

    col = Base.metadata.tables["textbook_chunks"].columns["order"]
    assert col.nullable is True
    assert col.type.python_type is int
