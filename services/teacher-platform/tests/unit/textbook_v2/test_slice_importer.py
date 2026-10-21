"""R06 切片 importer 单测：幂等/路径安全/缺文件/异 hash/DB 写入与回溯。

用合成切片 fixture（tests/unit/textbook_v2/fixtures.py 风格，虚构内容）；
SQLite 内存库 + 真实 app.models 建表（与 R05 test_models 同口径）。
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.importers import batch_import_id, plan_import, staged_import, validate_batch
from app.textbook_v2.importers.slice_importer import RIGHTS_EVIDENCE_REF, build_section_tree
from app.textbook_v2.models import TextbookChunk, TextbookSection, TextbookSource


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def make_synthetic_batch(
    tmp_path, *, chunk_count=6, batch_id="20260909-SYNTH-01", book_name="数学必修第一册（人教A版2019）"
):
    """生成一份合成切片批次：编号章节 + 非编号栏目 + 图片子目录。"""
    b = tmp_path / batch_id
    b.mkdir(parents=True)
    (b / "images").mkdir()
    (b / "images" / "img_001_P1_插图.jpg").write_bytes(b"fake-jpg-1")
    (b / "images" / "img_002_P2_插图.png").write_bytes(b"fake-png-1")

    chunks = []
    for i in range(chunk_count):
        if i < 2:
            section = "1.1 合成节标题A" if i == 0 else "1.2 合成节标题B"
        else:
            section = "思考" if i % 2 else "1.2 合成节标题B"
        chunks.append(
            {
                "chunk_id": f"c{i}",
                "book_id": "synth",
                "section": section,
                "content": f"## 合成内容 #{i} 公式 $x^{{2}}$",
            }
        )
    (b / "chunks.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
    (b / "manifest.json").write_text(
        json.dumps({"batch_id": batch_id, "book_name": book_name, "volume": "必修第一册", "book_type": "textbook"}),
        encoding="utf-8",
    )
    return b


# ─────────────────────────────────────────────────────────────────────────────
# dry-run / 校验（不触库）
# ─────────────────────────────────────────────────────────────────────────────


def test_plan_import_basic(tmp_path):
    b = make_synthetic_batch(tmp_path)
    plan = plan_import(b)
    assert plan.chunk_count == 6
    assert plan.image_count == 2
    assert len(plan.sources) == 1
    assert plan.sources[0]["publisher"] == "人教A版2019"
    assert plan.sources[0]["rights_evidence_ref"] == RIGHTS_EVIDENCE_REF
    assert plan.sources[0]["rights_status"] == "STAGED"
    assert plan.issues == []
    assert len(plan.sections) >= 2  # 1.1 / 1.2 / 思考挂靠


def test_plan_import_missing_manifest(tmp_path):
    b = make_synthetic_batch(tmp_path)
    (b / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError):
        plan_import(b)


def test_plan_import_empty_chunks(tmp_path):
    b = make_synthetic_batch(tmp_path)
    (b / "chunks.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="为空"):
        plan_import(b)


def test_section_tree_numbering(tmp_path):
    b = make_synthetic_batch(tmp_path, chunk_count=4)
    plan = plan_import(b)
    titles = [n["title"] for n in plan.section_tree]
    assert "1.1 合成节标题A" in titles
    assert "1.2 合成节标题B" in titles
    # 思考（非编号）不产生独立节点
    assert "思考" not in titles


def test_import_id_is_stable(tmp_path):
    b = make_synthetic_batch(tmp_path)
    _, sha, _ = validate_batch(b)
    assert batch_import_id("20260909-SYNTH-01", sha) == batch_import_id("20260909-SYNTH-01", sha)


# ─────────────────────────────────────────────────────────────────────────────
# staged DB 写入（SQLite 等价）
# ─────────────────────────────────────────────────────────────────────────────


async def test_staged_import_writes_three_layers(db, tmp_path):
    b = make_synthetic_batch(tmp_path)
    await staged_import(db, b)

    src = (await db.execute(select(TextbookSource))).scalars().first()
    assert src is not None
    assert src.rights_status == "STAGED"
    assert src.rights_evidence_ref == RIGHTS_EVIDENCE_REF
    assert src.publisher == "人教A版2019"

    sections = (await db.execute(select(TextbookSection))).scalars().all()
    assert len(sections) >= 2
    # 父链已重建（level 1.1 无父，1.1.2 父=1.1 或按编号）
    roots = [s for s in sections if s.parent_id is None]
    assert roots, "至少一个根章节"

    chunks = (await db.execute(select(TextbookChunk))).scalars().all()
    assert len(chunks) == 6
    # 所有 chunk 可回溯到 section → source
    for c in chunks:
        sec = await db.get(TextbookSection, c.section_id)
        assert sec.textbook_id == src.id


async def test_staged_import_idempotent(db, tmp_path):
    b = make_synthetic_batch(tmp_path)
    plan1 = await staged_import(db, b)
    assert plan1.issues == []

    # 重跑同批次 → SKIP 幂等，不重复写
    plan2 = await staged_import(db, b)
    assert any("SKIP" in i for i in plan2.issues)
    srcs = (await db.execute(select(TextbookSource))).scalars().all()
    assert len(srcs) == 1
    chunks = (await db.execute(select(TextbookChunk))).scalars().all()
    assert len(chunks) == 6


async def test_changed_hash_creates_new_source_version(db, tmp_path):
    b = make_synthetic_batch(tmp_path, chunk_count=6)
    await staged_import(db, b)

    # 修改 content → hash 变化 → 必须新 source 版本，不覆盖旧数据
    (b / "chunks.jsonl").write_text(
        "\n".join(
            json.dumps(
                {"chunk_id": f"c{i}", "book_id": "synth", "section": "1.1 合成节标题A", "content": f"## 新内容 {i}"},
                ensure_ascii=False,
            )
            for i in range(6)
        ),
        encoding="utf-8",
    )
    plan2 = await staged_import(db, b)
    assert not any("SKIP" in i for i in plan2.issues)  # 新版本入库
    srcs = (await db.execute(select(TextbookSource))).scalars().all()
    assert len(srcs) == 2  # 旧版本保留 + 新版本


def test_build_section_tree_empty_mapping():
    sections, mapping = build_section_tree([])
    assert sections == [] and mapping == []


async def test_staged_import_dedups_repeated_content_within_section(db, tmp_path):
    """同 section 内的完全重复段落必须去重（uq_tbchunk_section_hash 拒绝重复）。"""
    b = tmp_path / "20260909-DUP-01"
    b.mkdir(parents=True)
    (b / "images").mkdir()
    dup_content = "## 重复段落 完全一致"
    chunks = [
        {"chunk_id": "c0", "book_id": "synth", "section": "1.1 合成节标题A", "content": dup_content},
        {"chunk_id": "c1", "book_id": "synth", "section": "1.1 合成节标题A", "content": dup_content},
        {"chunk_id": "c2", "book_id": "synth", "section": "1.1 合成节标题A", "content": "## 唯一段落"},
    ]
    (b / "chunks.jsonl").write_text("\n".join(json.dumps(c) for c in chunks), encoding="utf-8")
    (b / "manifest.json").write_text(
        json.dumps(
            {
                "batch_id": "20260909-DUP-01",
                "book_name": "数学必修第一册（人教A版2019）",
                "volume": "必修第一册",
                "book_type": "textbook",
            }
        ),
        encoding="utf-8",
    )

    plan = await staged_import(db, b)
    assert any("重复内容跳过 1 条" in i for i in plan.issues)
    chunks_in_db = (await db.execute(select(TextbookChunk))).scalars().all()
    assert len(chunks_in_db) == 2  # 3 条输入 - 1 条重复
