"""R08 索引单测：FakeEmbedding 确定性 / 索引写入幂等 / 维度 fail closed / index_version 回滚。

fixture 全部虚构（合成教材内容），禁止真实教材摘录（AGENTS.md 教材源铁律）。
db fixture 与 R07 同口径：SQLite 内存库 + 真实 metadata。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.importers.slice_importer import sha256_bytes
from app.textbook_v2.models import TextbookChunk, TextbookSection, TextbookSource
from app.textbook_v2.retrieval import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    FakeEmbedding,
    embed_index,
    locate_sections,
)

SRC_A = uuid.UUID("30000000-0000-0000-0000-0000000000a1")
SRC_B = uuid.UUID("30000000-0000-0000-0000-0000000000b1")
SEC_A = uuid.UUID("30000000-0000-0000-0000-0000000000a2")
SEC_B = uuid.UUID("30000000-0000-0000-0000-0000000000b2")


def _source(
    id: uuid.UUID, *, school_id: uuid.UUID | None, rights: str, volume: str = "必修第一册"
) -> TextbookSource:
    return TextbookSource(
        id=id,
        school_id=school_id,
        publisher="合成出版社",
        edition="全一册",
        grade="高一",
        volume=volume,
        subject="数学",
        source_repo="https://example.invalid/synthetic-r08.git",
        source_commit="0" * 40,
        source_path="synthetic/r08-fixture.jsonl",
        file_sha256="1" * 64,
        rights_status=rights,
        rights_evidence_ref="队伍确认授权",
        parser_version="synth-v1",
    )


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


async def _seed_two_sources(db) -> None:
    """平台级 STAGED 教材 A（3 chunks）+ 平台级 STAGED 教材 B（2 chunks），均已回填 order。"""
    db.add(_source(SRC_A, school_id=None, rights="STAGED"))
    db.add(_source(SRC_B, school_id=None, rights="STAGED"))
    await db.flush()
    db.add(
        TextbookSection(
            id=SEC_A, textbook_id=SRC_A, parent_id=None, level=1, order=1,
            title="1.1 合成代数初步", normalized_title="合成代数初步", page_start=1, page_end=3,
            section_path="1.1",
        )
    )
    db.add(
        TextbookSection(
            id=SEC_B, textbook_id=SRC_B, parent_id=None, level=1, order=1,
            title="1.1 合成代数初步", normalized_title="合成代数初步", page_start=1, page_end=2,
            section_path="1.1",
        )
    )
    await db.flush()
    for i, text in enumerate(
        ["## 1.1 合成代数初步\n定义：合成量。", "定理：合成单调性。", "## 例 1\n求复合函数。"],
        start=1,
    ):
        db.add(
            TextbookChunk(
                section_id=SEC_A, text=text, content_hash=sha256_bytes(text.encode()),
                chunker_version="slice_md-202607", index_version=0, order=i,
            )
        )
    for i, text in enumerate(["## 1.1 合成代数初步\n苏教版集合", "## 练习\n证明恒等映射。"], start=1):
        db.add(
            TextbookChunk(
                section_id=SEC_B, text=text, content_hash=sha256_bytes(text.encode()),
                chunker_version="slice_md-202607", index_version=0, order=i,
            )
        )
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# FakeEmbedding：固定 seed 确定性
# ─────────────────────────────────────────────────────────────────────────────


def test_fake_embedding_deterministic_and_768():
    e = FakeEmbedding(seed=42)
    v1 = e.embed("合成函数的单调性")
    v2 = e.embed("合成函数的单调性")
    v3 = e.embed("完全不同的一段文本")
    assert len(v1) == EMBEDDING_DIM == 768
    assert v1 == v2  # 同文本 → 同向量（gold query 可重复）
    assert v1 != v3  # 异文本 → 异向量（大概率）
    assert e.model == EMBEDDING_MODEL == "fake-768"


def test_fake_embedding_seed_changes_vector():
    e0 = FakeEmbedding(seed=0)
    e1 = FakeEmbedding(seed=1)
    assert e0.embed("同一文本") != e1.embed("同一文本")


# ─────────────────────────────────────────────────────────────────────────────
# 索引写入：幂等 / 元数据 / 维度 fail closed / 版本回滚
# ─────────────────────────────────────────────────────────────────────────────


async def test_embed_index_writes_metadata_and_idempotent(db):
    await _seed_two_sources(db)
    res1 = await embed_index(db, source_ids=[SRC_A], index_version=1)
    assert res1.written == 3 and res1.skipped == 0

    rows = (await db.execute(select(TextbookChunk))).scalars().all()
    for c in rows:
        if c.section_id == SEC_A:  # 仅索引 source A
            assert c.embedding is not None and len(list(c.embedding)) == 768
            assert c.embedding_model == "fake-768"
            assert c.embedding_dim == 768
            assert c.chunker_version == "slice_md-202607"
            assert c.index_version == 1
        else:
            assert c.embedding is None and c.index_version == 0  # 范围外未触碰

    # 重跑同 version → 全量幂等 skip，无新增写入
    res2 = await embed_index(db, source_ids=[SRC_A], index_version=1)
    assert res2.written == 0 and res2.skipped == 3


def _bad_dim_embedder():
    class _Bad:
        model = "bad-512"
        dim = 512

        def embed(self, text: str) -> list[float]:
            return [0.5] * 512

    return _Bad()


async def test_dimension_mismatch_fail_closed(db):
    """dim != 768 → 抛错且不写任何行（先验后写，原子不写库）。"""
    await _seed_two_sources(db)
    with pytest.raises(ValueError, match="dimension mismatch"):
        await embed_index(db, source_ids=[SRC_A], index_version=1, embedder=_bad_dim_embedder())
    rows = (await db.execute(select(TextbookChunk))).scalars().all()
    assert all(c.embedding is None and c.index_version == 0 for c in rows)


async def test_index_version_rollback_old_version_readable(db):
    """回滚 = 切换读取版本：B 以 v2 建索引不触碰 A 的 v1 数据；按版本读取互不干扰。"""
    await _seed_two_sources(db)
    await embed_index(db, source_ids=[SRC_A], index_version=1)
    await embed_index(db, source_ids=[SRC_B], index_version=2)  # 新版本只写 B，不删 A 向量

    # 数据不丢：A 的 chunk 仍为 v1，embedding 完好
    a_rows = (
        (await db.execute(select(TextbookChunk).where(TextbookChunk.section_id == SEC_A))).scalars().all()
    )
    assert all(c.index_version == 1 and c.embedding is not None for c in a_rows)

    # 切换读取版本：v1 → 只有 A；v2 → 只有 B（版本隔离，不混比）
    cand_v1 = await locate_sections(db, "合成代数初步", index_version=1)
    assert [c.section_id for c in cand_v1] == [SEC_A]
    cand_v2 = await locate_sections(db, "合成代数初步", index_version=2)
    assert [c.section_id for c in cand_v2] == [SEC_B]
    # 默认解析为最新版本
    cand_latest = await locate_sections(db, "合成代数初步")
    assert [c.section_id for c in cand_latest] == [SEC_B]


async def test_gold_query_deterministic(db):
    """gold query 两次调用结果完全一致（seed 固定 + 确定性混合评分）。"""
    await _seed_two_sources(db)
    await embed_index(db, source_ids=[SRC_A, SRC_B], index_version=1)
    q = "合成代数初步"
    r1 = await locate_sections(db, q)
    r2 = await locate_sections(db, q)
    assert [(c.section_id, round(c.score, 9)) for c in r1] == [(c.section_id, round(c.score, 9)) for c in r2]
    assert r1 and r1[0].section_id in (SEC_A, SEC_B)


async def test_backfill_then_locate_lexical_anchor(db):
    """未建索引时 lexical-only 仍可 anchor（顺序回填后按 order 展示）。"""
    await _seed_two_sources(db)
    # 未 embed：locate 走 lexical-only（有效版本 None → 全 chunk 参与）
    cands = await locate_sections(db, "合成单调性")
    assert cands and cands[0].section_id == SEC_A
