"""教材入库质量门禁单测（PLAN §7.2 七项门禁，B3）。

用合成切片 fixture（虚构内容，禁止真实教材摘录），验证：
门禁 1 book_type 枚举 / 教师用书双保险与显式放行；门禁 2 逐行 JSON/content/乱码；
门禁 3 LaTeX 配对；门禁 5 embedding 768 红线；门禁 6 幂等；门禁 7 入库仅 STAGED。
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.importers.slice_importer import (
    EMBEDDING_DIM_TEACHER,
    VALID_BOOK_TYPES,
    load_chunks,
    parse_manifest,
    plan_import,
    staged_import,
    validate_chunks_lines,
)
from app.textbook_v2.models import TextbookChunk, TextbookSource


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


def make_batch(
    tmp_path,
    *,
    batch_id="20261012-SYNTH-G01",
    book_type="textbook",
    book_name="数学必修第一册（人教A版2019）",
    lines: list[str] | None = None,
    manifest_extra: dict | None = None,
):
    """合成批次目录；lines 缺省 = 两条配对 LaTeX 的合法切片。"""
    b = tmp_path / batch_id
    b.mkdir(parents=True, exist_ok=True)
    if lines is None:
        lines = [
            json.dumps(
                {"chunk_id": "c0", "book_id": "synth", "section": "1.1 合成节", "content": "## 公式 $x^2$ 配对"},
                ensure_ascii=False,
            ),
            json.dumps(
                {"chunk_id": "c1", "book_id": "synth", "section": "1.2 合成节乙", "content": "## 块公式 $$y=x$$ 配对"},
                ensure_ascii=False,
            ),
        ]
    (b / "chunks.jsonl").write_text("\n".join(lines), encoding="utf-8")
    manifest = {"batch_id": batch_id, "book_name": book_name, "volume": "必修第一册", "book_type": book_type}
    manifest.update(manifest_extra or {})
    (b / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return b


# ── 门禁 1：book_type 枚举 + 教师用书 ────────────────────────────────────────


def test_book_type_enum_rejects_non_textbook_types(tmp_path):
    """book_type ∉ {textbook,teacher_guide,reference} → 整批拒绝（镜像实测 lecture/lesson_plan）。"""
    for bad in ("lecture", "lesson_plan"):
        b = make_batch(tmp_path / f"bt-{bad}", book_type=bad)
        with pytest.raises(ValueError, match="book_type 非法"):
            plan_import(b)


def test_book_type_empty_counts_as_missing(tmp_path):
    """book_type 为空串 → 按缺字段拒绝（必填四件套口径）。"""
    b = make_batch(tmp_path / "bt-empty", book_type="")
    with pytest.raises(ValueError, match="缺字段"):
        plan_import(b)


def test_valid_book_types_accepted(tmp_path):
    assert set(VALID_BOOK_TYPES) == {"textbook", "teacher_guide", "reference"}


def test_teacher_guide_by_name_rejected_even_if_mislabeled(tmp_path):
    """镜像实测 tbixi1 的 manifest 误标 book_type=textbook——书名含「教师用书」必须拦下。"""
    b = make_batch(
        tmp_path,
        batch_id="20261012-SYNTH-TG01",
        book_type="textbook",  # 误标
        book_name="数学教师用书必修第一册（人教A版2019）",
    )
    with pytest.raises(ValueError, match="教师用书批次禁止入库"):
        plan_import(b)


async def test_teacher_guide_explicit_allow_flag_imports_staged(tmp_path, db):
    """显式 allow_teacher_guide=True 才放行，且入库仅 STAGED（门禁 7）。"""
    b = make_batch(
        tmp_path,
        batch_id="20261012-SYNTH-TG02",
        book_type="teacher_guide",
        book_name="数学教师用书必修第二册（人教A版2019）",
    )
    plan = await staged_import(db, b, allow_teacher_guide=True)
    assert plan.issues == []
    src = (await db.execute(select(TextbookSource))).scalars().first()
    assert src.rights_status == "STAGED"


def test_manifest_missing_fields_rejected(tmp_path):
    b = make_batch(tmp_path)
    m = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    del m["book_type"]
    (b / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="缺字段"):
        parse_manifest(b / "manifest.json")


# ── 门禁 2：逐行 JSON / content / 乱码 ───────────────────────────────────────


def test_invalid_json_line_rejected_with_line_number(tmp_path):
    lines = [
        json.dumps({"chunk_id": "c0", "section": "1.1 合成节", "content": "ok $x$"}, ensure_ascii=False),
        "{broken json",
    ]
    b = make_batch(tmp_path, lines=lines)
    with pytest.raises(ValueError, match="第 2 行 JSON 非法"):
        load_chunks(b)


def test_empty_content_rejected_with_line_number(tmp_path):
    lines = [
        json.dumps({"chunk_id": "c0", "section": "1.1 合成节", "content": "ok $x$"}, ensure_ascii=False),
        json.dumps({"chunk_id": "c1", "section": "1.1 合成节", "content": "  "}, ensure_ascii=False),
    ]
    b = make_batch(tmp_path, lines=lines)
    with pytest.raises(ValueError, match="第 2 条 content 为空"):
        load_chunks(b)


def test_mojibake_and_control_chars_rejected(tmp_path):
    lines = [
        json.dumps({"chunk_id": "c0", "section": "1.1 合成节", "content": "乱码替换符\ufffd"}, ensure_ascii=False),
    ]
    b = make_batch(tmp_path, lines=lines)
    with pytest.raises(ValueError, match="乱码/控制字符"):
        load_chunks(b)
    lines2 = [
        json.dumps({"chunk_id": "c0", "section": "1.1 合成节", "content": "a\x01b"}, ensure_ascii=False),
    ]
    b2 = make_batch(tmp_path / "ctl", lines=lines2)
    with pytest.raises(ValueError, match="乱码/控制字符"):
        load_chunks(b2)


def test_non_utf8_file_rejected(tmp_path):
    b = make_batch(tmp_path)
    (b / "chunks.jsonl").write_bytes(b'{"content": "\xff\xfe bad"}\n')
    with pytest.raises(ValueError, match="UTF-8"):
        load_chunks(b)


# ── 门禁 3：LaTeX 配对 ───────────────────────────────────────────────────────


def test_unpaired_latex_rejected(tmp_path):
    lines = [
        json.dumps({"chunk_id": "c0", "section": "1.1 合成节", "content": "公式 $x 没闭合"}, ensure_ascii=False),
    ]
    b = make_batch(tmp_path, lines=lines)
    with pytest.raises(ValueError, match="公式配对检查未通过"):
        load_chunks(b)


def test_paired_latex_accepted(tmp_path):
    b = make_batch(tmp_path)  # 缺省两行均配对（$ 与 $$）
    chunks = load_chunks(b)
    assert len(chunks) == 2


# ── 门禁 5：embedding 非空 + 768 维红线 ─────────────────────────────────────


def test_embedding_wrong_dim_rejected(tmp_path):
    lines = [
        json.dumps(
            {"chunk_id": "c0", "section": "1.1 合成节", "content": "ok", "embedding": [0.1] * 1024},
            ensure_ascii=False,
        ),
    ]
    b = make_batch(tmp_path, lines=lines)
    with pytest.raises(ValueError, match=f"!= 教师端铁律 {EMBEDDING_DIM_TEACHER}"):
        load_chunks(b)


def test_embedding_null_rejected(tmp_path):
    lines = [
        json.dumps({"chunk_id": "c0", "section": "1.1 合成节", "content": "ok", "embedding": []}, ensure_ascii=False),
    ]
    b = make_batch(tmp_path, lines=lines)
    with pytest.raises(ValueError, match="embedding 为空"):
        validate_chunks_lines(b)


def test_embedding_768_ok(tmp_path):
    lines = [
        json.dumps(
            {"chunk_id": "c0", "section": "1.1 合成节", "content": "ok", "embedding": [0.1] * 768},
            ensure_ascii=False,
        ),
    ]
    b = make_batch(tmp_path, lines=lines)
    assert len(load_chunks(b)) == 1


# ── 门禁 6/7：幂等 + 仅 STAGED ───────────────────────────────────────────────


async def test_staged_import_idempotent_and_staged_only(db, tmp_path):
    b = make_batch(tmp_path)
    plan1 = await staged_import(db, b)
    assert plan1.issues == []
    src = (await db.execute(select(TextbookSource))).scalars().first()
    assert src.rights_status == "STAGED"  # 门禁 7：入库仅到 STAGED
    assert src.batch_id == "20261012-SYNTH-G01"  # 溯源列随写
    plan2 = await staged_import(db, b)
    assert any("SKIP" in i for i in plan2.issues)  # 门禁 6：file_sha256 幂等跳过
    assert (await db.execute(select(TextbookChunk))).scalars().all() != []


async def test_section_path_backfilled_for_numbered_sections(db, tmp_path):
    """编号小节回填权威 section_path（§7.3.2 章节覆盖与 T2 目录消费）。"""
    b = make_batch(tmp_path)
    await staged_import(db, b)
    from app.textbook_v2.models import TextbookSection

    paths = {
        s.section_path
        for s in (await db.execute(select(TextbookSection))).scalars().all()
    }
    assert {"1.1", "1.2"} <= paths
