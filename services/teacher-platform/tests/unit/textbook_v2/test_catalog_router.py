"""T2 教材目录端点单测（catalog_router，B3）。

sqlite 内存库合成教材/章节行，直接调用端点函数（绕过 FastAPI 依赖注入），
验证：visible_scope 可见性（QUARANTINED 永不出现）、LP-01 噪声过滤、编号自然序、
未授权/未知 → 404。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.catalog_router import list_textbook_sections, list_textbooks
from app.textbook_v2.models import TextbookSection, TextbookSource


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


async def _seed(db: object, *, rights: str = "STAGED", batch_id: str | None = "20261012-SYNTH-C01"):
    src = TextbookSource(
        school_id=None,
        publisher="人教A版2019",
        edition="必修第一册",
        grade="高一",
        volume="必修第一册",
        subject="数学",
        source_repo="mirror",
        source_commit="processed",
        source_path="mirror/processed/x",
        file_sha256="b" * 64,
        rights_status=rights,
        parser_version="slice_md-202607",
        batch_id=batch_id,
    )
    db.add(src)
    await db.flush()
    sections = []
    for i, (level, title, path) in enumerate(
        [
            (1, "未分节", None),  # 切片碎片 → 过滤
            (1, "1. 范围", None),  # 无编号无 path 的 level=1 → 过滤（LP-01）
            (2, "1.1 集合的概念", "1.1"),
            (2, "1.10 补集", "1.10"),  # 自然序：1.10 排在 1.2 前（编号序）
            (2, "1.2 集合间的基本关系", "1.2"),
        ]
    ):
        sec = TextbookSection(
            textbook_id=src.id,
            level=level,
            order=i + 1,
            title=title,
            normalized_title=title,
            page_start=i + 1,
            page_end=i + 1,
            section_path=path,
        )
        db.add(sec)
        sections.append(sec)
    await db.commit()
    return src, sections


async def test_list_textbooks_visible_scope_and_counts(db):
    await _seed(db, rights="STAGED")
    await _seed(db, rights="QUARANTINED", batch_id="20261012-SYNTH-C02")

    out = await list_textbooks(_user={}, session=db)
    items = out["items"]
    assert len(items) == 1, "QUARANTINED 不得出现（rights.visible_scope）"
    item = items[0]
    assert set(item) == {"source_id", "batch_id", "book_name", "volume", "rights_status", "section_count"}
    assert item["batch_id"] == "20261012-SYNTH-C01"
    assert item["rights_status"] == "STAGED"
    assert item["section_count"] == 5
    assert "人教A版2019" in item["book_name"]


async def test_list_textbook_sections_filters_noise_and_sorts_naturally(db):
    src, _sections = await _seed(db)
    out = await list_textbook_sections(str(src.id), _user={}, session=db)
    secs = out["sections"]
    titles = [s["title"] for s in secs]
    assert "未分节" not in titles, "无编号无 path 的 level=1 切片碎片必须过滤（LP-01，options.py 同口径）"
    # options.py 同款正则下「1. 范围」算编号行（_SECTION_NO_RE 实测命中）→ 保留，非缺陷；
    # 编号自然序：(1,) < (1,1) < (1,2) < (1,10)
    assert titles == ["1. 范围", "1.1 集合的概念", "1.2 集合间的基本关系", "1.10 补集"]
    for s in secs:
        assert set(s) == {"id", "section_path", "title", "level", "order"}


async def test_list_textbook_sections_unknown_or_unauthorized_404(db):
    await _seed(db, rights="QUARANTINED")
    with pytest.raises(HTTPException) as exc:
        await list_textbook_sections("00000000-0000-0000-0000-000000000009", _user={}, session=db)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc2:
        await list_textbook_sections("not-a-uuid", _user={}, session=db)
    assert exc2.value.status_code == 404
