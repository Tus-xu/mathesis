"""R12 SourceLocator 单测：round-trip / legacy 只读解析 / resolve 权限 / 工件 page_refs=0。

fixture 全部虚构（合成教材），禁止真实教材摘录（AGENTS.md 教材源铁律）。
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2 import read_legacy_context_artifact, write_direct_chunk_context
from app.textbook_v2.importers.slice_importer import sha256_bytes
from app.textbook_v2.locator import (
    SourceLocator,
    chunk_locator,
    problem_locator,
    resolve_locator,
    resolve_source_ref,
    section_locator,
    unit_locator,
)
from app.textbook_v2.models import TextbookChunk, TextbookSource

SID = uuid.UUID("50000000-0000-0000-0000-000000000001")  # source
SID_TENANT = uuid.UUID("50000000-0000-0000-0000-000000000002")  # 租户 source
SID_QR = uuid.UUID("50000000-0000-0000-0000-000000000003")  # QUARANTINED
SEC = uuid.UUID("50000000-0000-0000-0000-000000000010")
CH1 = uuid.UUID("50000000-0000-0000-0000-0000000000a1")
CH2 = uuid.UUID("50000000-0000-0000-0000-0000000000a2")
SCHOOL = uuid.UUID("60000000-0000-0000-0000-000000000001")


def _source(id: uuid.UUID, *, school_id=None, rights="STAGED") -> TextbookSource:
    return TextbookSource(
        id=id,
        school_id=school_id,
        publisher="合成社",
        edition="全一册",
        grade="高一",
        volume="上册",
        subject="数学",
        source_repo="https://example.invalid/r12-synth.git",
        source_commit="0" * 40,
        source_path="synthetic/r12.jsonl",
        file_sha256=f"{id.hex}"[:64],
        rights_status=rights,
        rights_evidence_ref="队伍确认授权",
        parser_version="synth-v1",
        source_kind="direct-chunk",
        batch_id="20260910-synth-01",
        manifest_sha256="a" * 64,
        provenance={"source_repo": "https://example.invalid", "source_commit": "0" * 40, "source_path": "synth"},
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


async def _seed(db) -> None:
    db.add(_source(SID))
    db.add(_source(SID_TENANT, school_id=SCHOOL))
    db.add(_source(SID_QR, rights="QUARANTINED"))
    await db.flush()
    from app.textbook_v2.models import TextbookSection

    db.add(
        TextbookSection(
            id=SEC, textbook_id=SID, parent_id=None, level=1, order=1,
            title="第1章 合成集合", normalized_title="合成集合",
            page_start=1, page_end=2, chunk_start=1, chunk_end=2, section_path="1",
        )
    )
    await db.flush()
    db.add_all(
        [
            TextbookChunk(id=CH1, section_id=SEC, text="合成分块一（确定性元素）", order=1,
                          content_hash=sha256_bytes(b"c1"), chunker_version="synth-v1", index_version=0),
            TextbookChunk(id=CH2, section_id=SEC, text="合成分块二（互异性）", order=2,
                          content_hash=sha256_bytes(b"c2"), chunker_version="synth-v1", index_version=0),
        ]
    )
    await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# SourceLocator：round-trip / legacy / 非法
# ─────────────────────────────────────────────────────────────────────────────


def test_locator_ref_roundtrip_all_variants() -> None:
    cases = [
        section_locator(SID, SEC, chunk_start=1, chunk_end=2),
        chunk_locator(SID, SEC, CH1),
        unit_locator(SID, SEC, CH1, uuid.UUID("50000000-0000-0000-0000-0000000000b1")),
        problem_locator(SID, SEC, CH2, uuid.UUID("50000000-0000-0000-0000-0000000000b2")),
    ]
    for loc in cases:
        parsed = SourceLocator.from_ref(loc.to_ref())
        assert parsed is not None
        assert parsed == loc
        assert parsed.schema_version == 1


def test_legacy_ref_parses_as_readonly() -> None:
    ref = f"textbook_v2/source/{SID}/section/{SEC}/chunk/{CH1}"
    loc = SourceLocator.from_ref(ref)
    assert loc is not None
    assert loc.schema_version == 0  # legacy 只读
    assert loc.chunk_id == CH1
    try:
        loc.to_ref()
        raise AssertionError("legacy locator 禁止新写序列化")
    except ValueError:
        pass


def test_from_ref_rejects_non_domain() -> None:
    assert SourceLocator.from_ref("web://http/search/result") is None
    assert SourceLocator.from_ref("textbook_v2/source/not-a-uuid") is None
    assert SourceLocator.from_ref("") is None


def test_chunk_id_and_range_mutually_exclusive() -> None:
    import pytest

    with pytest.raises(ValueError):
        SourceLocator(source_id=SID, section_id=SEC, chunk_id=CH1, chunk_start=1, chunk_end=2)
    with pytest.raises(ValueError):
        SourceLocator(source_id=SID, section_id=SEC, chunk_start=1)  # start/end 必须成对


# ─────────────────────────────────────────────────────────────────────────────
# resolve_locator：权限 + 租户 + 越权空
# ─────────────────────────────────────────────────────────────────────────────


async def test_resolve_locator_chunk(db):
    await _seed(db)
    view = await resolve_locator(db, chunk_locator(SID, SEC, CH1))
    assert view is not None and view.kind == "chunk"
    assert view.order == 1 and "确定性" in view.text_preview
    assert view.locator == chunk_locator(SID, SEC, CH1).to_ref()
    assert view.rights_status == "STAGED"


async def test_resolve_locator_section_range(db):
    await _seed(db)
    loc = section_locator(SID, SEC, chunk_start=1, chunk_end=2)
    view = await resolve_locator(db, loc)
    assert view is not None and view.kind == "section"
    assert view.page_range == [1, 2]


async def test_resolve_locator_quarantined_and_tenant_denied(db):
    await _seed(db)
    # QUARANTINED source 不可见
    qr_sec = uuid.UUID("50000000-0000-0000-0000-000000000011")
    from app.textbook_v2.models import TextbookSection

    db.add(TextbookSection(id=qr_sec, textbook_id=SID_QR, parent_id=None, level=1, order=1,
                           title="隔离节", normalized_title="隔离节", page_start=1, page_end=1))
    await db.commit()
    assert await resolve_locator(db, section_locator(SID_QR, qr_sec)) is None

    # 平台级查询者看不到租户 source
    assert await resolve_locator(db, section_locator(SID_TENANT, SEC)) is None


async def test_resolve_source_ref_legacy_and_v1_equivalent(db):
    await _seed(db)
    v1 = chunk_locator(SID, SEC, CH1).to_ref()
    legacy = f"textbook_v2/source/{SID}/section/{SEC}/chunk/{CH1}"
    v1_view = await resolve_source_ref(db, v1)
    legacy_view = await resolve_source_ref(db, legacy)
    assert v1_view is not None and legacy_view is not None
    assert v1_view.kind == legacy_view.kind == "chunk"
    assert v1_view.order == legacy_view.order == 1
    assert v1_view.locator == v1  # v1 携带 canonical locator
    assert legacy_view.locator is None  # legacy 只读：不伪造 canonical 定位


# ─────────────────────────────────────────────────────────────────────────────
# TeachingContext 工件：new writer page_refs=0 / legacy reader 可打开旧工件
# ─────────────────────────────────────────────────────────────────────────────


async def test_write_direct_chunk_context_has_zero_page_refs(db):
    await _seed(db)
    artifact = await write_direct_chunk_context(db, SEC)
    assert artifact is not None
    assert artifact["page_refs"] == []  # 验收：新 artifact 的 page_refs=0
    assert artifact["locator"].startswith("textbook_v2/locator/v1/source/")
    assert artifact["chunk_order"] == [1, 2]
    for fact in artifact["facts"]:
        assert "page_ref" not in fact and "page_from" not in fact and "pNN" not in fact
    # 每条 chunk 带 canonical chunk_ref 且可解析
    for c in artifact["chunks"]:
        assert c["chunk_ref"].startswith("textbook_v2/locator/v1/")
        assert await resolve_source_ref(db, c["chunk_ref"]) is not None


def test_read_legacy_context_artifact_opens_old_artifact():
    old = {
        "artifact_type": "textbook_teaching_context",
        "schema_version": 0,
        "source": {"id": str(SID), "publisher": "合成社"},
        "section": {"id": str(SEC), "title": "旧节"},
        "page_refs": ["12", "13"],
        "facts": [{"kind": "unit", "fact_type": "definition", "text": "旧定义", "page_from": 1, "page_to": 1}],
        "chunks": [],
    }
    normalized, warnings = read_legacy_context_artifact(old)
    assert normalized["page_refs"] == ["12", "13"]  # 旧页码仍可读
    assert normalized.get("legacy_page_refs_present") is True
    assert any("legacy 页码键" in w for w in warnings)  # 兼容读取被标记
    assert "page_from" in normalized["facts"][0]  # 事实中的旧字段保持可见


async def test_write_direct_chunk_context_unauthorized_returns_none(db):
    # 不可见 section（不存在）→ None 而非抛错
    await _seed(db)
    assert await write_direct_chunk_context(db, uuid.UUID("50000000-0000-0000-0000-00000000dead")) is None
