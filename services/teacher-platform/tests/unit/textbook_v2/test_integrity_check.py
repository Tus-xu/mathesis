"""「一套完整教材」完整性校验单测（PLAN §7.3，B3）。

纯函数校验（check_volume）+ run_check 注入路径（sqlite 内存库 + 注入磁盘/学生计数），
不依赖真实镜像与真实双库。全部数据虚构；行数对齐 R4 §7 基线（bixi1=145）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.importers.slice_importer import staged_import
from app.textbook_v2.integrity_check import (
    PLAN_BASELINE_CHUNKS,
    WHITELIST_BATCH_IDS,
    WHITELIST_BOOK_IDS,
    DbSection,
    DbVolume,
    DiskVolume,
    IntegrityReport,
    check_volume,
    collect_disk_volumes,
    render_markdown,
    run_check,
)
from app.textbook_v2.models import TextbookSource

_BATCH = "20260726-A-bixi1-01"
_LINES = PLAN_BASELINE_CHUNKS[_BATCH]  # 145
_HALF = 73  # 1.1 覆盖 [1,73]，1.2 覆盖 [74,145]


def _disk(chunk_lines=_LINES, paths=("1.1", "1.2")):
    return DiskVolume(
        batch_id=_BATCH,
        book_id="bixi1",
        batch_dir=Path("D:/synthetic/processed") / _BATCH,
        book_name="数学必修第一册（人教A版2019）",
        volume="必修第一册",
        book_type="textbook",
        file_sha256="a" * 64,
        chunk_lines=chunk_lines,
        section_paths=list(paths),
        section_spans={"1.1": (1, _HALF), "1.2": (_HALF + 1, _LINES)},
    )


def _db_vol(
    rows=_LINES,
    paths=("1.1", "1.2"),
    spans=None,
    row_split=None,
    rights="STAGED",
):
    """DB 侧投影：缺省两节 spans (1,73)/(74,145)、行数 73+72=145（健康态）。"""
    spans = spans or {"1.1": (1, _HALF), "1.2": (_HALF + 1, _LINES)}
    split = row_split if row_split is not None else _HALF
    sections = []
    allocated = 0
    for i, p in enumerate(paths):
        if i == 0:
            n = min(split, rows)
        elif i == len(paths) - 1:
            n = max(rows - allocated, 0)
        else:  # pragma: no cover - 测试只用 1-2 节
            n = 0
        allocated += n
        sections.append(
            DbSection(
                section_id=f"s-{p}",
                title=f"{p} 合成节",
                section_path=p,
                chunk_start=spans[p][0],
                chunk_end=spans[p][1],
                chunk_rows=n,
            )
        )
    return DbVolume(
        source_id="00000000-0000-0000-0000-000000000001",
        batch_id=_BATCH,
        book_volume="必修第一册",
        rights_status=rights,
        file_sha256="a" * 64,
        chunk_total=rows,
        sections=sections,
    )


def test_whitelist_constants_match_plan():
    """白名单 5 册与 R4 §7 基线（bixi1=145/bixi2=246/xbixi1=145/xbixi2=105/xbixi3=151）。"""
    assert len(WHITELIST_BATCH_IDS) == 5
    assert PLAN_BASELINE_CHUNKS == {
        "20260726-A-bixi1-01": 145,
        "20260729-A-bixi2-01": 246,
        "20260729-A-xbixi1-01": 145,
        "20260729-A-xbixi2-01": 105,
        "20260729-A-xbixi3-01": 151,
    }
    assert set(WHITELIST_BOOK_IDS.values()) == {"bixi1", "bixi2", "xbixi1", "xbixi2", "xbixi3"}


def test_check_volume_all_pass():
    rep = check_volume(_BATCH, _disk(), _db_vol(), student_count=_LINES)
    assert rep.ok, rep.gaps()
    assert set(rep.checks) == {"disk_batch", "db_source", "chunk_count", "section_coverage", "chunk_integrity", "two_end"}


def test_check_volume_missing_disk_batch():
    rep = check_volume(_BATCH, None, None, student_count=None)
    assert not rep.ok
    assert any("不在镜像中" in g for g in rep.gaps())


def test_check_volume_missing_db_row():
    rep = check_volume(_BATCH, _disk(), None, student_count=_LINES)
    assert not rep.ok
    assert any("STAGED/ACTIVE" in g for g in rep.gaps())


def test_check_volume_chunk_below_baseline_and_two_end_mismatch():
    dbv = _db_vol(rows=2, spans={"1.1": (1, 1), "1.2": (2, 2)})
    rep = check_volume(_BATCH, _disk(chunk_lines=2), dbv, student_count=_LINES)
    assert not rep.ok
    assert any("基线" in g for g in rep.gaps())
    assert any("两端 chunk 数不一致" in g for g in rep.gaps())


def test_check_volume_missing_section_path():
    dbv = _db_vol(paths=("1.1",), spans={"1.1": (1, _LINES)})  # DB 缺 1.2
    rep = check_volume(_BATCH, _disk(), dbv, student_count=_LINES)
    assert not rep.ok
    assert any("章节树缺小节 1.2" in g for g in rep.gaps())
    # 单一缺口：其余校验不受牵连
    assert not any("并集缺口" in g for g in rep.gaps())


def test_check_volume_span_gap_detected():
    dbv = _db_vol(spans={"1.1": (1, 72), "1.2": (74, _LINES)})  # 行 73 缺口
    rep = check_volume(_BATCH, _disk(), dbv, student_count=_LINES)
    assert not rep.ok
    assert any("并集缺口 [73,73]" in g for g in rep.gaps())


def test_check_volume_span_tail_shortfall():
    dbv = _db_vol(spans={"1.1": (1, _HALF), "1.2": (74, _LINES - 1)})  # 行 145 未覆盖
    rep = check_volume(_BATCH, _disk(), dbv, student_count=_LINES)
    assert not rep.ok
    assert any(f"覆盖到 {_LINES - 1} != 磁盘行数 {_LINES}" in g for g in rep.gaps())


def test_check_volume_db_rows_lost():
    dbv = _db_vol(rows=_LINES - 1)  # 行数合计 < 磁盘
    rep = check_volume(_BATCH, _disk(), dbv, student_count=_LINES)
    assert not rep.ok
    assert any("DB chunk 行总数" in g for g in rep.gaps())


def test_check_volume_zero_chunk_numbered_section():
    dbv = _db_vol(paths=("1.1", "1.2"), row_split=_LINES)  # 1.2 零行
    dbv.sections[1].chunk_rows = 0
    dbv.chunk_total = _LINES
    rep = check_volume(_BATCH, _disk(), dbv, student_count=_LINES)
    assert not rep.ok
    assert any("零 chunk 编号小节 1.2" in g for g in rep.gaps())


def test_check_volume_scattered_span_overlap_is_note_not_gap():
    """编号复用节（如「1. 填空题」跨章重复）span=min..max 重叠是既有语义，不算缺口。"""
    dbv = _db_vol(spans={"1.1": (1, _LINES), "1.2": (2, _LINES)}, row_split=_HALF)
    rep = check_volume(_BATCH, _disk(), dbv, student_count=_LINES)
    assert rep.ok, rep.gaps()
    assert "重叠" in rep.checks["chunk_integrity"][1]


def test_check_volume_teacher_guide_disk_rejected():
    disk = _disk()
    disk.book_type = "teacher_guide"
    disk.book_name = "数学教师用书必修第一册（人教A版2019）"
    rep = check_volume(_BATCH, disk, _db_vol(), student_count=_LINES)
    assert not rep.ok
    assert any("不是学生教材批次" in g for g in rep.gaps())


def test_report_render_contains_counts_and_gaps():
    rep_vol = check_volume(_BATCH, _disk(), _db_vol(), student_count=_LINES)
    report = IntegrityReport(generated_at="2026-10-12T00:00:00+00:00", volumes=[rep_vol])
    md = render_markdown(report)
    assert "✅ 一套完整教材齐备" in md
    assert _BATCH in md and str(_LINES) in md
    bad = check_volume(_BATCH, None, None, None)
    md_bad = render_markdown(IntegrityReport(generated_at="x", volumes=[bad]))
    assert "存在缺口" in md_bad and "❌" in md_bad


# ── 端到端（sqlite 内存库 + 注入磁盘/学生计数） ──────────────────────────────


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


def _make_mirror_batch(tmp_path, batch_id=_BATCH, *, chunk_lines=_LINES):
    """合成白名单批次目录（内容互异防去重；1.1 覆盖 [1,N-1]、1.2 覆盖 [N,N]）。"""
    root = tmp_path / "mirror" / "processed"
    b = root / batch_id
    b.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(chunk_lines - 1):
        lines.append(
            json.dumps(
                {"chunk_id": f"c{i}", "section": "1.1 合成节", "content": f"内容{batch_id}甲{i} 公式 $x^{i}+{i}$"},
                ensure_ascii=False,
            )
        )
    lines.append(
        json.dumps(
            {"chunk_id": "d0", "section": "1.2 合成节乙", "content": f"内容{batch_id}乙 块公式 $$0+y$$"},
            ensure_ascii=False,
        )
    )
    (b / "chunks.jsonl").write_text("\n".join(lines), encoding="utf-8")
    (b / "manifest.json").write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "book_id": WHITELIST_BOOK_IDS[batch_id],
                "book_name": f"合成教材 {WHITELIST_BOOK_IDS[batch_id]}（人教A版2019）",
                "volume": f"合成册 {WHITELIST_BOOK_IDS[batch_id]}",
                "book_type": "textbook",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return b


def test_collect_disk_volumes_reads_synthetic_mirror(tmp_path):
    _make_mirror_batch(tmp_path)
    vols = collect_disk_volumes([tmp_path / "mirror"])
    assert _BATCH in vols
    v = vols[_BATCH]
    assert v.book_id == "bixi1" and v.chunk_lines == _LINES
    assert v.section_paths == ["1.1", "1.2"]
    assert v.section_spans == {"1.1": (1, _LINES - 1), "1.2": (_LINES, _LINES)}


def _seed_full_mirror(tmp_path) -> dict[str, int]:
    """5 册白名单全量合成（磁盘目录），返回学生侧计数注入值。"""
    counts: dict[str, int] = {}
    for bid in WHITELIST_BATCH_IDS:
        n = PLAN_BASELINE_CHUNKS[bid]
        _make_mirror_batch(tmp_path, batch_id=bid, chunk_lines=n)
        counts[WHITELIST_BOOK_IDS[bid]] = n
    return counts


async def _import_mirror_into_db(db, tmp_path):
    for bid in WHITELIST_BATCH_IDS:
        await staged_import(db, tmp_path / "mirror" / "processed" / bid)


async def test_run_check_end_to_end_with_sqlite_and_injected_counts(tmp_path, db):
    """5 册 staged_import 真实写库 → run_check（注入磁盘/学生计数）全绿；--promote 置 ACTIVE。"""
    _seed_full_mirror(tmp_path)
    await _import_mirror_into_db(db, tmp_path)

    disk = collect_disk_volumes([tmp_path / "mirror"])
    counts = {WHITELIST_BOOK_IDS[bid]: PLAN_BASELINE_CHUNKS[bid] for bid in WHITELIST_BATCH_IDS}
    report = await run_check(session=db, disk_vols=disk, student_counts=counts, promote=True)
    assert report.ok, report.all_gaps()
    assert sorted(report.promoted) == sorted(WHITELIST_BATCH_IDS)
    srcs = (await db.execute(select(TextbookSource))).scalars().all()
    assert {s.rights_status for s in srcs} == {"ACTIVE"}  # STAGED→ACTIVE 唯一通路：本校验 + --promote


async def test_run_check_reports_student_gap(tmp_path, db):
    """学生端无该册计数 → two_end 缺口 + 报告诚实标注（生产重放前的真实形态）。"""
    _seed_full_mirror(tmp_path)
    await _import_mirror_into_db(db, tmp_path)
    disk = collect_disk_volumes([tmp_path / "mirror"])
    report = await run_check(session=db, disk_vols=disk, student_counts={})
    assert not report.ok
    assert any("学生端" in g for g in report.all_gaps())


# ── 教师用书红线（tel F2）：教师用书批次必须全部 QUARANTINED ─────────────────


def test_is_teacher_guide_batch_id_matches_mirror_five_batches():
    """镜像教师用书 5 批全命中；学生册/课标/模板/培训批次零误报。"""
    from app.textbook_v2.rights import is_teacher_guide_batch_id

    for bid in (
        "20260730-A-tbixi1-01", "20260730-A-tbixi2-01",
        "20260730-A-txbixi1-01", "20260730-A-txbixi2-01", "20260730-A-txbixi3-01",
    ):
        assert is_teacher_guide_batch_id(bid), bid
    for bid in (
        "20260726-A-bixi1-01", "20260729-A-bixi2-01", "20260729-A-xbixi1-01",
        "20260729-A-xbixi2-01", "20260729-A-xbixi3-01", "20260729-A-bbixi1-01",
        "20260729-A-bxbixi1-01", "20260729-A-sbixi1-01", "20260729-A-sxbixi1-01",
        "20260729-A-kebiao-01", "20260730-A-kebiao2-01", "20260730-C-moban-01",
        "20260730-C-peixun-01", "", None,
    ):
        assert not is_teacher_guide_batch_id(bid), bid


async def test_run_check_blocks_promote_when_teacher_guide_not_quarantined(tmp_path, db):
    """教师用书行 STAGED → 红线缺口（ok=False，--promote 亦被阻断）；QUARANTINED 后恢复。"""
    _seed_full_mirror(tmp_path)
    await _import_mirror_into_db(db, tmp_path)
    leak = TextbookSource(
        batch_id="20260730-A-tbixi1-01",
        volume="必修第一册",
        subject="数学",
        publisher="人教A版2019",
        edition="2019",
        grade="高一",
        source_repo="synthetic",
        source_commit="synthetic-commit",
        parser_version="synthetic-v1",
        source_path="synthetic\\processed\\20260730-A-tbixi1-01",
        file_sha256="b" * 64,
        rights_status="STAGED",
    )
    db.add(leak)
    await db.flush()

    disk = collect_disk_volumes([tmp_path / "mirror"])
    counts = {WHITELIST_BOOK_IDS[bid]: PLAN_BASELINE_CHUNKS[bid] for bid in WHITELIST_BATCH_IDS}
    report = await run_check(session=db, disk_vols=disk, student_counts=counts, promote=True)
    assert not report.ok
    assert any("教师用书红线违规" in g and "tbixi1" in g for g in report.all_gaps())
    assert report.promoted == []  # 红线违规绝不放行 STAGED→ACTIVE 发布

    # 修复后（rights 状态机迁移 QUARANTINED）→ 红线通过、promote 恢复
    from app.textbook_v2.rights import assert_can_transition as _can

    _can(leak.rights_status, "QUARANTINED")
    leak.rights_status = "QUARANTINED"
    await db.flush()
    report2 = await run_check(session=db, disk_vols=disk, student_counts=counts, promote=True)
    assert report2.ok, report2.all_gaps()
    assert sorted(report2.promoted) == sorted(WHITELIST_BATCH_IDS)
