import json
from pathlib import Path


def _write_batch(root: Path, batch_id: str, *, book_name: str, book_id: str) -> Path:
    batch_dir = root / batch_id
    batch_dir.mkdir()
    (batch_dir / "manifest.json").write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "book_id": book_id,
                "book_name": book_name,
                "volume": "必修第一册",
                "book_type": "textbook",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (batch_dir / "chunks.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "chunk-001",
                "book_id": book_id,
                "book_name": book_name,
                "volume": "必修第一册",
                "section": "1.1 集合的概念",
                "subsection": "",
                "content": "集合是由一些确定的、不同的对象组成的整体。",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return batch_dir


def test_discovers_only_pep_a_2019_student_textbooks(tmp_path):
    """错误地把教师用书或其他出版社放入学生教材默认检索域时应失败。"""
    from scripts.import_pep_textbooks import discover_student_textbook_batches

    _write_batch(
        tmp_path,
        "20260726-A-bixi1-01",
        book_id="bixi1",
        book_name="数学必修第一册（人教A版2019）",
    )
    _write_batch(
        tmp_path,
        "20260730-A-tbixi1-01",
        book_id="tbixi1",
        book_name="数学教师用书必修第一册（人教A版2019）",
    )
    _write_batch(
        tmp_path,
        "20260731-B-bixi1-01",
        book_id="bsdx1",
        book_name="数学必修第一册（北师大版2019）",
    )

    batches = discover_student_textbook_batches(tmp_path)

    assert [batch.batch_id for batch in batches] == ["20260726-A-bixi1-01"]
    assert batches[0].content_type == "textbook"
    assert batches[0].scope == "student"


def test_preserves_book_and_section_metadata_for_textbook_association(tmp_path):
    """若章节元数据丢失，课堂的“教材关联”无法给学生可追溯出处。"""
    from scripts.import_pep_textbooks import load_student_textbook_batch

    batch_dir = _write_batch(
        tmp_path,
        "20260726-A-bixi1-01",
        book_id="bixi1",
        book_name="数学必修第一册（人教A版2019）",
    )

    batch = load_student_textbook_batch(batch_dir)

    assert batch.document_meta == {
        "batch_id": "20260726-A-bixi1-01",
        "book_id": "bixi1",
        "book_name": "数学必修第一册（人教A版2019）",
        "volume": "必修第一册",
        "book_type": "textbook",
        "publisher": "人教A版",
        "edition": "2019",
        "scope": "student",
        "corpus_class": "student_textbook",
    }
    # PLAN §7.4.4（B3）：chunk meta 契约补 origin/rights/batch_id 溯源标记
    # （与 import_textbook_slices / 教师端 textbook_v2.integrity_check 同词表）
    # 2026-10-22：补 source_kind=TEXTBOOK——qa_rag 按 source_kinds 过滤切片
    # （TEXTBOOK,CURRICULUM_STANDARD），缺此字段三路召回被整体滤掉。
    assert batch.chunks[0].meta == {
        "source_chunk_id": "chunk-001",
        "book_id": "bixi1",
        "book_name": "数学必修第一册（人教A版2019）",
        "volume": "必修第一册",
        "section": "1.1 集合的概念",
        "subsection": "",
        "source_kind": "TEXTBOOK",
        "origin": "ac_slice",
        "rights": "authorized",
        "batch_id": "20260726-A-bixi1-01",
    }


async def test_persisted_textbook_chunk_keeps_traceable_section_metadata(tmp_path):
    """若落库时丢掉章节元数据，学生端无法展示可信的教材关联。"""
    from sqlalchemy import select

    from app.models.chunk import Chunk
    from app.models.database import async_session_factory
    from app.models.knowledge_doc import KnowledgeDoc
    from scripts.import_pep_textbooks import (
        load_student_textbook_batch,
        persist_student_textbook_batch,
    )

    batch = load_student_textbook_batch(
        _write_batch(
            tmp_path,
            "20260729-A-bixi2-01",
            book_id="bixi2",
            book_name="数学必修第二册（人教A版2019）",
        )
    )

    async with async_session_factory() as db:
        doc_id = await persist_student_textbook_batch(db, batch, [[0.01] * 1024])

    async with async_session_factory() as db:
        doc = await db.get(KnowledgeDoc, doc_id)
        chunk = (await db.execute(select(Chunk).where(Chunk.doc_id == doc_id))).scalar_one()

    assert doc.meta_["corpus_class"] == "student_textbook"
    assert doc.meta_["scope"] == "student"
    assert chunk.meta_["section"] == "1.1 集合的概念"
    assert chunk.meta_["book_id"] == "bixi2"


async def test_textbook_import_assigns_each_chunk_a_stable_section_knowledge_point(tmp_path):
    """教材切片必须绑定教材章节知识点，不能只按全文模糊检索。"""
    from sqlalchemy import select

    from app.models.chunk import Chunk
    from app.models.database import async_session_factory
    from app.models.knowledge_point import KnowledgePoint
    from scripts.import_pep_textbooks import (
        ensure_pep_textbook_knowledge_points,
        load_student_textbook_batch,
        persist_student_textbook_batch,
    )

    batch = load_student_textbook_batch(
        _write_batch(
            tmp_path,
            "20260726-A-bixi1-01",
            book_id="bixi1",
            book_name="数学必修第一册（人教A版2019）",
        )
    )
    async with async_session_factory() as db:
        section_kp_ids = await ensure_pep_textbook_knowledge_points(db, [batch])
        doc_id = await persist_student_textbook_batch(
            db,
            batch,
            [[0.01] * 1024],
            section_kp_ids=section_kp_ids,
            commit=False,
        )
        chunk = (await db.execute(select(Chunk).where(Chunk.doc_id == doc_id))).scalar_one()
        kp = await db.get(KnowledgePoint, chunk.kp_ids[0])
        assert len(chunk.kp_ids) == 1
        assert kp is not None
        assert kp.code.startswith("MATH-PEP-BIXI1-")
        assert kp.name == "1.1 集合的概念"
        await db.rollback()


async def test_imports_only_whitelisted_student_textbook_batches(tmp_path):
    """批量导入不能因目录中混有教师用书或题库而污染学生教材检索域。"""
    from sqlalchemy import select

    from app.models.database import async_session_factory
    from app.models.knowledge_doc import KnowledgeDoc
    from scripts.import_pep_textbooks import import_student_textbook_batches

    _write_batch(
        tmp_path,
        "20260726-A-bixi1-01",
        book_id="bixi1",
        book_name="数学必修第一册（人教A版2019）",
    )
    _write_batch(
        tmp_path,
        "20260730-A-tbixi1-01",
        book_id="tbixi1",
        book_name="数学教师用书必修第一册（人教A版2019）",
    )

    class FixedEmbedding:
        async def embed(self, texts, **_kwargs):
            return [[0.02] * 1024 for _ in texts]

    async with async_session_factory() as db:
        imported = await import_student_textbook_batches(db, tmp_path, FixedEmbedding())

    async with async_session_factory() as db:
        docs = (await db.execute(select(KnowledgeDoc))).scalars().all()

    assert imported == [{"batch_id": "20260726-A-bixi1-01", "chunks": 1}]
    imported_batch_ids = {doc.meta_.get("batch_id") for doc in docs}
    assert "20260726-A-bixi1-01" in imported_batch_ids
    assert "20260730-A-tbixi1-01" not in imported_batch_ids
