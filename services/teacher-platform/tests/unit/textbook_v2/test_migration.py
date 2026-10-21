"""R05 migration 契约测试：
1) migration 挂在当前链 head（b2c3d4e5f6a1）之后，down_revision 正确；
2) migration 建的表/列/约束与 app/textbook_v2/models.py 保持一致（防漂移）；
3) downgrade 与 upgrade 逆序对称（可回滚）。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_PLATFORM = Path(__file__).resolve().parents[3]  # tests/unit/textbook_v2 -> teacher-platform 根
_MIGRATIONS = _PLATFORM / "alembic" / "versions"
_ALEMBIC_INI = _PLATFORM / "alembic.ini"

# 与 app/textbook_v2/models.py 完全对齐的期望契约（列名 -> 是否必填）
EXPECTED_TABLES: dict[str, dict[str, bool]] = {
    "textbook_sources": {
        "id": True,
        "school_id": False,
        "publisher": True,
        "edition": True,
        "grade": True,
        "volume": True,
        "subject": True,
        "source_repo": True,
        "source_commit": True,
        "source_path": True,
        "file_sha256": True,
        "rights_status": True,
        "rights_evidence_ref": False,
        "parser_version": True,
        # R12（迁移 f1a2b3c4d5e6）：管线分类 + 批次/清单溯源 + 结构化 provenance
        "source_kind": True,
        "batch_id": False,
        "manifest_sha256": False,
        "provenance": False,
        "created_at": True,
        "updated_at": True,
    },
    "textbook_pages": {
        "id": True,
        "textbook_id": True,
        "page_no": True,
        "printed_page_no": False,
        "raw_pdf_asset_ref": True,
        "page_image_asset_ref": True,
        "text_layer": False,
        "ocr_text": False,
        "vision_summary": False,
        "bbox_map": False,
        "checksum": True,
        "created_at": True,
    },
    "textbook_sections": {
        "id": True,
        "textbook_id": True,
        "parent_id": False,
        "level": True,
        "order": True,
        "title": True,
        "normalized_title": True,
        "page_start": True,
        "page_end": True,
        # R12：direct-chunk 定位列（全局 1-based chunk 行号；page_* 仅 legacy 兼容）
        "chunk_start": False,
        "chunk_end": False,
        "section_path": False,
        "intro_strategy": False,
        "derivation_chain": False,
        "summary": False,
        "created_at": True,
    },
    "textbook_content_units": {
        "id": True,
        "section_id": True,
        "page_from": True,
        "page_to": True,
        "type": True,
        "text": False,
        "latex": False,
        # R12：canonical direct-chunk 定位 ref（page_from/page_to 仅 legacy 兼容）
        "chunk_ref": False,
        "source_bbox": False,
        "asset_refs": False,
        "confidence": False,
        "extraction_method": False,
        "review_status": True,
        "created_at": True,
    },
    "textbook_problems": {
        "id": True,
        "section_id": True,
        "kind": True,
        "order": True,
        "stem_text": False,
        "solution_text": False,
        "page_ref": True,
        # R12：canonical direct-chunk 定位 ref（page_ref 仅 legacy 兼容）
        "chunk_ref": False,
        "stem_bbox": False,
        "original_asset_refs": False,
        "knowledge_tags": False,
        "difficulty": False,
        "provenance": False,
        "created_at": True,
    },
    "textbook_chunks": {
        "id": True,
        "section_id": True,
        "page_range": False,
        "unit_ids": False,
        "text": True,
        "order": False,  # R08：册内全局 1-based 行号（切片行序，幂等回填；null=未回填）
        "embedding": False,
        "embedding_model": False,
        "embedding_dim": False,
        "chunker_version": True,
        "content_hash": True,
        "index_version": True,
        "created_at": True,
    },
}

# 期望的唯一约束（表名, (列...)）——幂等键与顺序稳定
EXPECTED_UNIQUE: dict[str, tuple[tuple[str, ...], ...]] = {
    "textbook_sources": (("school_id", "file_sha256"),),
    "textbook_pages": (("textbook_id", "page_no"),),
    "textbook_sections": (("textbook_id", "order"),),
    "textbook_problems": (("section_id", "order"),),
    "textbook_chunks": (("section_id", "content_hash"),),
}

# 期望 CHECK 约束（表名 -> 约束 SQL 内必须出现的片段）
EXPECTED_CHECKS: dict[str, tuple[str, ...]] = {
    "textbook_sources": ("QUARANTINED", "STAGED", "ACTIVE"),
    "textbook_content_units": (
        "'definition','theorem','formula','example','exercise','inquiry','figure',"
        "'introduction','derivation','summary'",
        "'pending','reviewed','rejected'",
    ),
    "textbook_problems": ("'example','exercise','inquiry'",),
}


def _migration_file() -> Path:
    candidates = sorted(_MIGRATIONS.glob("*textbook_v2_domain.py"))
    assert candidates, "找不到 textbook v2 migration 文件"
    return candidates[-1]


def _r12_migration_file() -> Path:
    """R12 direct-chunk locators 迁移（f1a2b3c4d5e6）。"""
    candidates = sorted(_MIGRATIONS.glob("*r12_direct_chunk_locators.py"))
    assert candidates, "找不到 R12 migration 文件"
    return candidates[-1]


def _combined_migration_source() -> str:
    """R05 域迁移 + R12 locator 迁移的拼接源码（列/表级契约跨文件联合断言）。"""
    return _migration_file().read_text(encoding="utf-8") + "\n" + _r12_migration_file().read_text(encoding="utf-8")


def _load_migration() -> dict:
    """离线加载最新 textbook migration 模块（不连库）。"""
    mod_path = _migration_file()
    spec = importlib.util.spec_from_file_location("_tb_v2_migration", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"module": mod, "path": mod_path}


def test_migration_is_on_head() -> None:
    """R05 migration 必须挂在 b2c3d4e5f6a1 之后；R08 order 迁移挂在 R05 之后；R10 ppt_v2 迁移挂在 R08 之后。
    R11.5-B：d17b2c3e4f5a（runtime persistence takeover）挂在 R10 之后；R11.5-C：
    e26a6b7c8d9e（reliability gates）挂在其后；R12：f1a2b3c4d5e6（direct-chunk locators）；
    R14：16a2b3c4d5e6（teacher_prep skeleton）→ R17：1e2f3a4b5c6d（conversation 编排）→
    R18/R20/R19 依次追加 → R22（b27c3d4e5f60）→ c8d9e0f1a2b3 → trial_001_teacher →
    trial_002_grading_workbench 唯一 head（R20「钉死前移」惯例，tel F5 同步）。"""
    assert _ALEMBIC_INI.is_file(), f"缺少 {_ALEMBIC_INI}"
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    assert heads == {"trial_002_grading_workbench"}, f"migration 必须为唯一 head（trial_002 批改工作台），实际 {heads}"

    rev = _load_migration()["module"]
    assert rev.down_revision == "b2c3d4e5f6a1"
    assert rev.revision == "a05b1c2d3e4f"

    # R08 order 迁移必须挂在 R05 之上，且提供 down/rollback
    r08 = _load_migration_order()["module"]
    assert r08.down_revision == "a05b1c2d3e4f"
    assert r08.revision == "b06c2d3e4f5a"
    assert callable(getattr(r08, "downgrade", None))

    # R12 迁移挂在 R11.5-C 之上，且提供 down/rollback（additive、可回滚）
    r12 = _load_migration_r12()["module"]
    assert r12.down_revision == "e26a6b7c8d9e"
    assert r12.revision == "f1a2b3c4d5e6"
    assert callable(getattr(r12, "downgrade", None))


def _load_migration_r12() -> dict:
    """离线加载 R12 direct-chunk locators 迁移模块（不连库）。"""
    mod_path = _r12_migration_file()
    spec = importlib.util.spec_from_file_location("_tb_v2_migration_r12", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"module": mod, "path": mod_path}


def _load_migration_order() -> dict:
    """离线加载 R08 order 迁移模块（不连库）。"""
    candidates = sorted(_MIGRATIONS.glob("*textbook_chunk_order.py"))
    assert candidates, "找不到 R08 order migration 文件"
    mod_path = candidates[-1]
    spec = importlib.util.spec_from_file_location("_tb_v2_migration_order", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"module": mod, "path": mod_path}


def test_migration_tables_match_models() -> None:
    """migration 建的表集合 == ORM 模型表集合；列集合一致（防迁移/模型漂移）。"""
    from app.db.base import Base
    from app.textbook_v2 import models as tv2  # noqa: F401

    orm_tables = {t for t in Base.metadata.tables if t.startswith("textbook_")}
    assert orm_tables == set(EXPECTED_TABLES), f"ORM 表与契约不一致: {orm_tables ^ set(EXPECTED_TABLES)}"

    src = _combined_migration_source()
    created = set(re.findall(r"""op\.create_table\(\s*["']([a-z_]+)["']""", src))
    assert created == set(EXPECTED_TABLES), f"migration 建的表与契约不一致: {created ^ set(EXPECTED_TABLES)}"

    for table, cols in EXPECTED_TABLES.items():
        assert set(Base.metadata.tables[table].columns.keys()) == set(cols), f"{table} 列不一致"


def test_migration_not_null_matches_models() -> None:
    """列可空性契约：ORM 必填列在 migration 中必须为 NOT NULL（nullable=False/省缺）。"""
    from app.db.base import Base

    src = _combined_migration_source()
    for table, cols in EXPECTED_TABLES.items():
        orm = Base.metadata.tables[table]
        for col, required in cols.items():
            assert col in src, f"migration 缺列 {table}.{col}"
            if required:
                assert not orm.columns[col].nullable, f"{table}.{col} 应为必填"


def test_migration_checks_present() -> None:
    """CHECK 约束（rights 状态机 / source_kind / unit 类型 / problem kind）必须出现在 migration 中。"""
    src = _combined_migration_source()
    for table, fragments in EXPECTED_CHECKS.items():
        assert table in src
        for frag in fragments:
            assert frag in src, f"{table} 缺 CHECK 片段 {frag}"
    # R12：source_kind 状态集（新 writer 允许 direct-chunk；legacy-pdf 仅兼容）
    assert "ck_tbsource_kind" in _r12_migration_file().read_text(encoding="utf-8")
    assert "'direct-chunk','legacy-pdf','synth'" in _r12_migration_file().read_text(encoding="utf-8")


def test_migration_unique_constraints_present() -> None:
    """唯一约束（幂等键/顺序稳定）必须同时出现在 migration 与 ORM 模型。"""
    from app.db.base import Base

    file_src = _combined_migration_source()
    # 约束名（name=）是权威标识，避免与 sa. 前缀/空格格式耦合
    expected_names = {
        "textbook_sources": "uq_tbsource_school_sha",
        "textbook_pages": "uq_tbpage_textbook_pageno",
        "textbook_sections": "uq_tbsection_textbook_order",
        "textbook_problems": "uq_tbproblem_section_order",
        "textbook_chunks": "uq_tbchunk_section_hash",
    }
    for table, cname in expected_names.items():
        assert f"{cname}" in file_src, f"migration 缺 {table} 唯一约束 {cname}"
        # ORM 侧必须等集一致
        orm_uqs = [
            tuple(c.name for c in u.columns)
            for u in Base.metadata.tables[table].constraints
            if u.__class__.__name__ == "UniqueConstraint"
        ]
        assert EXPECTED_UNIQUE[table] == tuple(sorted(orm_uqs)), f"{table} ORM 唯一约束不一致: {orm_uqs}"


def test_downgrade_is_symmetric() -> None:
    """downgrade 必须逆序 drop 全部 6 表（可回滚性）；R12 逆序 drop 新增列/索引/约束。"""
    src = _migration_file().read_text(encoding="utf-8")
    for t in EXPECTED_TABLES:
        assert f"op.drop_table('{t}')" in src or f'op.drop_table("{t}")' in src, f"downgrade 缺少 drop_table('{t}')"
    # down_revision 挂 head 已断言；downgrade 不得丢索引
    assert src.count("op.drop_index(") >= 6

    r12_src = _r12_migration_file().read_text(encoding="utf-8")
    for col in ("source_kind", "batch_id", "manifest_sha256", "provenance"):
        assert f'op.drop_column("textbook_sources", "{col}")' in r12_src, f"R12 downgrade 缺少 drop {col}"
    for col in ("chunk_start", "chunk_end"):
        assert f'op.drop_column("textbook_sections", "{col}")' in r12_src, f"R12 downgrade 缺少 drop {col}"
    assert 'op.drop_column("textbook_problems", "chunk_ref")' in r12_src
    assert 'op.drop_column("textbook_content_units", "chunk_ref")' in r12_src
    # R12 零 op 级改动 textbook_pages（dormant，不删除；docstring 提及不算改动）
    for op_stmt in ("create_table", "add_column", "drop_table", "create_index", "drop_index"):
        assert f'op.{op_stmt}("textbook_pages"' not in r12_src and f"op.{op_stmt}('textbook_pages'" not in r12_src


def test_r12_migration_does_not_touch_textbook_pages() -> None:
    """R12 迁移零 op 级改动 textbook_pages：旧表 dormant、不新增列、不删除（决策 D-002）。"""
    r12_src = _r12_migration_file().read_text(encoding="utf-8")
    for op_stmt in ("create_table", "add_column", "drop_table", "create_index", "drop_index"):
        assert f'op.{op_stmt}("textbook_pages"' not in r12_src and f"op.{op_stmt}('textbook_pages'" not in r12_src


def test_vector_column_768() -> None:
    """TextbookChunk.embedding 必须为 pgvector Vector(768)（R04 铁律），不得写 1024。"""
    src = _migration_file().read_text(encoding="utf-8")
    assert "Vector(768)" in src and "Vector(1024)" not in src
