"""R10 ppt_v2 migration 契约测试：
1) migration 为唯一 head（c17d2e3f405a，挂在 R08 b06c2d3e4f5a 之后）；
2) migration 建的表/列/约束与 app/ppt_v2/persistence/models.py 一致（防漂移）；
3) downgrade/upgrade 逆序对称；4) 不触碰任何旧表（旧 Deck 行为不变）。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_PLATFORM = Path(__file__).resolve().parents[3]  # tests/unit/ppt_v2 -> teacher-platform 根
_MIGRATIONS = _PLATFORM / "alembic" / "versions"
_ALEMBIC_INI = _PLATFORM / "alembic.ini"
_APP = _PLATFORM / "app"

EXPECTED_TABLES: dict[str, dict[str, bool]] = {
    "ppt_v2_deck_specs": {
        "id": True,
        "school_id": False,
        "schema_version": True,
        "revision": True,
        "spec": True,
        "created_at": True,
        "updated_at": True,
    },
    "ppt_v2_assets": {
        "id": True,
        "deck_id": True,
        "kind": True,
        "object_key": True,
        "sha256": True,
        "mime_type": True,
        "size_bytes": True,
        "width": False,
        "height": False,
        "rights_status": True,
        "rights_evidence_ref": False,
        "preserve_original": True,
        "source_ref": False,
        "asset_meta": False,
        "created_at": True,
    },
}

EXPECTED_CHECKS: dict[str, tuple[str, ...]] = {
    "ppt_v2_deck_specs": ("revision >= 0",),
    "ppt_v2_assets": (
        "'TEXTBOOK','TEACHER_UPLOAD','MATH_RENDER','AI_IMAGE'",
        "'QUARANTINED','STAGED','ACTIVE'",
    ),
}

EXPECTED_UNIQUE: dict[str, tuple[tuple[str, ...], ...]] = {
    "ppt_v2_assets": (("deck_id", "object_key"),),
}

# T24（b27c3d4e5f60）新增：deck 修订/提案/逐页 QA + 教案修订/提案 + 产物投递（注册/票据/审计）
T24_TABLES: tuple[str, ...] = (
    "ppt_v2_spec_revisions",
    "ppt_v2_edit_proposals",
    "ppt_v2_page_qa",
    "tp_lesson_plan_revisions",
    "tp_lesson_plan_edit_proposals",
    "teacher_artifacts",
    "teacher_artifact_tickets",
    "teacher_artifact_download_audit",
)
T24_MIGRATION_GLOB = "*t24_spec_edit_and_artifacts.py"


def _migration_file() -> Path:
    candidates = sorted(_MIGRATIONS.glob("*ppt_v2_domain.py"))
    assert candidates, "找不到 ppt_v2 migration 文件"
    return candidates[-1]


def _load_migration() -> dict:
    mod_path = _migration_file()
    spec = importlib.util.spec_from_file_location("_ppt_v2_migration", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"module": mod, "path": mod_path}


def test_migration_is_unique_head() -> None:
    """R10 migration 挂 R08（b06c2d3e4f5a）之后；R11.5-C、R12、R14、R17（1e2f3a4b5c6d）、R18（2f3a4b5c6d7e）、R20 profile/specs（3a4b5c6d7e8f）、R19 teaching evidence（f19a2b3c4d5e）、R22（b27c3d4e5f60）→ c8d9e0f1a2b3 → trial_001_teacher → trial_002_grading_workbench 在其后并为唯一 head。"""
    assert _ALEMBIC_INI.is_file(), f"缺少 {_ALEMBIC_INI}"
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    # R20「钉死前移」惯例：head 期望随迁移链推进更新
    # （1e2f3a4b5c6d R17 → f19a2b3c4d5e R19 → b27c3d4e5f60 T24 编辑/QA/产物投递
    #   → c8d9e0f1a2b3 → trial_001_teacher → trial_002_grading_workbench）
    assert set(script.get_heads()) == {"trial_002_grading_workbench"}, (
        f"必须是唯一 head（trial_002 批改工作台迁移），实际 {set(script.get_heads())}"
    )

    rev = _load_migration()["module"]
    assert rev.revision == "c17d2e3f405a"
    assert rev.down_revision == "b06c2d3e4f5a"
    assert callable(getattr(rev, "downgrade", None))


def test_migration_tables_match_models() -> None:
    """migration 建的表集合 == ORM 模型表集合；列集合一致（防迁移/模型漂移）。"""
    from app.db.base import Base
    from app.ppt_v2.persistence import models as ppt  # noqa: F401

    orm_tables = {t for t in Base.metadata.tables if t.startswith("ppt_v2_")}
    assert orm_tables == set(EXPECTED_TABLES) | {
        t for t in T24_TABLES if t.startswith("ppt_v2_")
    }, f"ORM 表与契约不一致: {orm_tables ^ (set(EXPECTED_TABLES) | set(T24_TABLES))}"

    src = _migration_file().read_text(encoding="utf-8")
    created = set(re.findall(r"""op\.create_table\(\s*["']([a-z_0-9]+)["']""", src))
    assert created == set(EXPECTED_TABLES), f"migration 建的表与契约不一致: {created ^ set(EXPECTED_TABLES)}"

    for table, cols in EXPECTED_TABLES.items():
        assert set(Base.metadata.tables[table].columns.keys()) == set(cols), f"{table} 列不一致"


def test_migration_not_null_matches_models() -> None:
    from app.db.base import Base

    src = _migration_file().read_text(encoding="utf-8")
    for table, cols in EXPECTED_TABLES.items():
        orm = Base.metadata.tables[table]
        for col, required in cols.items():
            assert col in src, f"migration 缺列 {table}.{col}"
            if required:
                assert not orm.columns[col].nullable, f"{table}.{col} 应为必填"


def test_migration_checks_and_unique_present() -> None:
    src = _migration_file().read_text(encoding="utf-8")
    for table, fragments in EXPECTED_CHECKS.items():
        assert table in src
        for frag in fragments:
            assert frag in src, f"{table} 缺 CHECK 片段 {frag}"
    for table, cname in {"ppt_v2_assets": "uq_pptasset_deck_key"}.items():
        assert cname in src, f"{table} 缺唯一约束 {cname}"
    from app.db.base import Base

    orm_uqs = [
        tuple(c.name for c in u.columns)
        for u in Base.metadata.tables["ppt_v2_assets"].constraints
        if u.__class__.__name__ == "UniqueConstraint"
    ]
    assert EXPECTED_UNIQUE["ppt_v2_assets"] == tuple(sorted(orm_uqs))


def test_preserve_original_not_null_with_default_true() -> None:
    """preserve_original 必填 + server_default true（原件保护在 DB 层兜底）。"""
    src = _migration_file().read_text(encoding="utf-8")
    assert 'server_default=sa.text("true")' in src or "server_default=sa.text('true')" in src


def _t24_migration_file() -> Path:
    candidates = sorted(_MIGRATIONS.glob(T24_MIGRATION_GLOB))
    assert candidates, "找不到 T24 migration 文件"
    return candidates[-1]


def test_t24_migration_creates_and_drops_exactly_its_own_tables() -> None:
    """T24 迁移必须纯新增：建的 == 删的 == 8 张新表，且不出现任何改写既有表的操作。"""
    src = _t24_migration_file().read_text(encoding="utf-8")
    created = set(re.findall(r"""op\.create_table\(\s*["']([a-z_0-9]+)["']""", src))
    dropped = set(re.findall(r"""op\.drop_table\(\s*["']([a-z_0-9]+)["']""", src))
    assert created == set(T24_TABLES), f"T24 建表集合不符: {created ^ set(T24_TABLES)}"
    assert dropped == set(T24_TABLES), f"T24 downgrade 集合不符: {dropped ^ set(T24_TABLES)}"
    for mutation in ("op.add_column(", "op.alter_column(", "op.drop_column(", "op.drop_constraint("):
        assert mutation not in src, f"T24 迁移不应含 {mutation}（纯新增）"


def test_t24_orm_tables_exist_in_metadata() -> None:
    from app.db.base import Base

    for table in T24_TABLES:
        assert table in Base.metadata.tables, f"T24 表 {table} 未注册到 Base.metadata"


def test_downgrade_symmetric_and_legacy_untouched() -> None:
    src = _migration_file().read_text(encoding="utf-8")
    for t in EXPECTED_TABLES:
        assert f"op.drop_table('{t}')" in src or f'op.drop_table("{t}")' in src, f"downgrade 缺少 drop_table('{t}')"

    # 只 create/drop 本轮 ppt_v2 两表：旧 deck/contents/generation/textbook 结构零耦合
    created = set(re.findall(r"""op\.create_table\(\s*["']([a-z_0-9]+)["']""", src))
    dropped = set(re.findall(r"""op\.drop_table\(\s*["']([a-z_0-9]+)["']""", src))
    assert created == dropped == set(EXPECTED_TABLES)
    # 除建表/索引外，不得出现 add_column/alter/drop_column 等改写操作（纯新增、可回滚）
    for mutation in ("op.add_column(", "op.alter_column(", "op.drop_column(", "op.drop_constraint("):
        assert mutation not in src, f"migration 不应含 {mutation}（R10 纯新增表）"
    # 不得引用旧域实体标识符（textbook_sources / legacy deck 表名）
    for legacy_id in ("textbook_sources", "textbook_sections", "textbook_chunks", "contents", "generation"):
        assert legacy_id not in src, f"migration 引用 legacy 标识符 {legacy_id}"
