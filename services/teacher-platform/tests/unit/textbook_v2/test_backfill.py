"""R08 顺序回填单测：order = 切片行序（幂等）/ R06 去重行跳过 / 顺序即页序锚。

fixture 全部虚构（合成切片），禁止真实教材摘录。seed 走 R06 staged_import 真实流程，
backfill 走 R08 backfill_chunk_order_from_slice，验证两者章节树口径一致。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.importers.slice_importer import staged_import
from app.textbook_v2.models import TextbookChunk, TextbookSource
from app.textbook_v2.retrieval.backfill import backfill_chunk_order_from_slice


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


def _write_slice(tmp_path: Path, rows: list[dict]) -> Path:
    """写入合成切片批次（manifest + chunks.jsonl），返回批次目录。"""
    batch = tmp_path / "20260726-A-bixi1-01"
    batch.mkdir(parents=True)
    (batch / "chunks.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (batch / "manifest.json").write_text(
        json.dumps(
            {
                "batch_id": "20260726-A-bixi1-01",
                "book_id": "bixi1",
                "book_name": "数学必修第一册（合成版）",
                "volume": "必修第一册",
                "book_type": "textbook",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return batch


def _row(sec: str, content: str) -> dict:
    return {"chunk_id": str(uuid.uuid4()), "book_id": "bixi1", "book_type": "textbook",
            "book_name": "数学必修第一册（合成版）", "volume": "必修第一册",
            "section": sec, "subsection": "", "content": content}


async def _first_source(db):
    return (await db.execute(select(TextbookSource))).scalars().first()


async def test_backfill_order_global_rows(db, tmp_path):
    """回填后 order = 切片全局 1-based 行号；重跑幂等 updated=0。"""
    rows = [
        _row("1.1 合成集合", "## 1.1 合成集合\n集合的定义。"),
        _row("1.1 合成集合", "元素的确定性。"),
        _row("1.1 合成集合", "## 例 1\n列举法。"),
        _row("1.2 合成关系", "## 1.2 合成关系\n包含关系。"),
        _row("1.2 合成关系", "## 小结\n本章小结。"),
    ]
    batch = _write_slice(tmp_path, rows)
    await staged_import(db, batch)
    source = await _first_source(db)
    assert source is not None

    res1 = await backfill_chunk_order_from_slice(db, source_id=source.id, batch_dir=batch)
    assert res1.file_rows == 5
    assert res1.updated == 5 and res1.skipped_missing_chunk == 0

    chunks = sorted(
        (await db.execute(select(TextbookChunk))).scalars().all(),
        key=lambda c: c.order or 0,
    )
    # order 为全局行号且连续 1..5（页序锚与 R06 section.page_start 同口径）
    assert [c.order for c in chunks] == [1, 2, 3, 4, 5]

    res2 = await backfill_chunk_order_from_slice(db, source_id=source.id, batch_dir=batch)
    assert res2.updated == 0  # 幂等：无任何 order 变化


async def test_backfill_skips_deduped_rows(db, tmp_path):
    """R06 同 section 重复内容去重 → 文件行无 DB 行，跳过并保留首现行号。"""
    rows = [
        _row("1.1 合成集合", "## 1.1 合成集合\n集合的定义。"),
        _row("1.1 合成集合", "重复段落：确定性。"),
        _row("1.1 合成集合", "重复段落：确定性。"),  # 与上一行同 section 同 content → R06 去重
        _row("1.2 合成关系", "## 1.2 合成关系\n包含关系。"),
    ]
    batch = _write_slice(tmp_path, rows)
    plan = await staged_import(db, batch)
    assert any("重复内容跳过 1 条" in i for i in plan.issues)

    source = await _first_source(db)
    res = await backfill_chunk_order_from_slice(db, source_id=source.id, batch_dir=batch)
    assert res.file_rows == 4
    assert res.skipped_missing_chunk == 1  # 去重行被跳过
    assert res.updated == 3

    chunks = sorted(
        (await db.execute(select(TextbookChunk))).scalars().all(),
        key=lambda c: c.order or 0,
    )
    assert [c.order for c in chunks] == [1, 2, 4]  # 首现行号 2 保留，重复行号 3 无 DB 行
