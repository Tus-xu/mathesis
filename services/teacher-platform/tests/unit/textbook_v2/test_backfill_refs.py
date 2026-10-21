"""R12 backfill_chunk_refs 单测：page_ref/page_from（chunk 行号）→ canonical locator ref。

fixture 全部虚构；验证映射正确性 + 幂等（第二轮零更新）。
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.backfill_metadata import backfill_chunk_refs
from app.textbook_v2.importers.slice_importer import sha256_bytes
from app.textbook_v2.models import (
    TextbookChunk,
    TextbookContentUnit,
    TextbookProblem,
    TextbookSection,
    TextbookSource,
)

SID = uuid.UUID("51000000-0000-0000-0000-000000000001")
SEC = uuid.UUID("51000000-0000-0000-0000-000000000010")


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


async def _seed(db) -> None:
    db.add(TextbookSource(
        id=SID, publisher="合成社", edition="全一册", grade="高一", volume="上册", subject="数学",
        source_repo="https://example.invalid", source_commit="0" * 40,
        source_path="synth/backfill.jsonl", file_sha256="2" * 64,
        rights_status="STAGED", rights_evidence_ref="队伍确认授权",
        parser_version="synth-v1", source_kind="direct-chunk",
    ))
    await db.flush()
    db.add(TextbookSection(
        id=SEC, textbook_id=SID, parent_id=None, level=1, order=1,
        title="第1章 合成", normalized_title="合成", page_start=1, page_end=3, section_path="1",
    ))
    await db.flush()
    db.add_all([
        TextbookChunk(id=uuid.UUID("51000000-0000-0000-0000-0000000000c1"), section_id=SEC,
                      text="chunk1", order=1, content_hash=sha256_bytes(b"c1"),
                      chunker_version="synth-v1", index_version=0),
        TextbookChunk(id=uuid.UUID("51000000-0000-0000-0000-0000000000c2"), section_id=SEC,
                      text="chunk2", order=2, content_hash=sha256_bytes(b"c2"),
                      chunker_version="synth-v1", index_version=0),
        TextbookChunk(id=uuid.UUID("51000000-0000-0000-0000-0000000000c3"), section_id=SEC,
                      text="chunk3", order=3, content_hash=sha256_bytes(b"c3"),
                      chunker_version="synth-v1", index_version=0),
        TextbookProblem(section_id=SEC, kind="example", order=1, stem_text="例1", page_ref=1),
        TextbookProblem(section_id=SEC, kind="exercise", order=2, stem_text="练1", page_ref=3),
        TextbookContentUnit(section_id=SEC, page_from=2, page_to=2, type="definition",
                            text="定义", extraction_method="synth", review_status="pending"),
    ])
    await db.commit()


async def test_backfill_chunk_refs_maps_order_to_locator(db):
    await _seed(db)
    stats = await backfill_chunk_refs(db)
    assert stats.ref_updates == 3  # 2 problems + 1 unit
    assert stats.ref_skipped == 0

    from sqlalchemy import select

    from app.textbook_v2.models import TextbookProblem as TP

    problems = (await db.execute(select(TP).order_by(TP.order))).scalars().all()
    for p in problems:
        assert p.chunk_ref and p.chunk_ref.startswith("textbook_v2/locator/v1/source/")
        assert "/chunk/" in p.chunk_ref

    # 精确映射：page_ref=1 → order=1 的 chunk；page_ref=3 → order=3 的 chunk
    assert problems[0].chunk_ref.endswith(str(uuid.UUID("51000000-0000-0000-0000-0000000000c1")))
    assert problems[1].chunk_ref.endswith(str(uuid.UUID("51000000-0000-0000-0000-0000000000c3")))

    units = (await db.execute(select(TextbookContentUnit))).scalars().all()
    assert units[0].chunk_ref.endswith(str(uuid.UUID("51000000-0000-0000-0000-0000000000c2")))


async def test_backfill_chunk_refs_idempotent(db):
    await _seed(db)
    await backfill_chunk_refs(db)
    second = await backfill_chunk_refs(db)
    assert second.ref_updates == 0  # 幂等：第二轮零更新
    assert second.ref_skipped == 3  # 已填充 → 全部 skipped
