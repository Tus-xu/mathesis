"""R16 共享测试基建：合成教材种子（虚构内容，未授权内容零进仓库）+ 构建助手。

结构与 R05/R08 测试同构：SQLite 引擎 + STAGED 教材源 + 章/节/课时树 +
chunks + R07 units/problems；禁止真实教材摘录（AGENTS.md 教材源铁律）。
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.teacher_prep.context import ContextSelection, build_teaching_context
from app.textbook_v2.importers.slice_importer import sha256_bytes
from app.textbook_v2.models import (
    TextbookChunk,
    TextbookContentUnit,
    TextbookProblem,
    TextbookSection,
    TextbookSource,
)

# 稳定 ID（确定性：同种子 → 同 hash）
SRC = uuid.UUID("50000000-0000-0000-0000-0000000000a1")
CHAPTER = uuid.UUID("50000000-0000-0000-0000-0000000000a2")
LESSON1 = uuid.UUID("50000000-0000-0000-0000-0000000000a3")  # 1.1 合成集合
LESSON2 = uuid.UUID("50000000-0000-0000-0000-0000000000a4")  # 1.2 合成关系
SUB1 = uuid.UUID("50000000-0000-0000-0000-0000000000a5")  # 1.1.1 子课时

SCHOOL_S = uuid.UUID("50000000-0000-0000-0000-0000000000f1")


def _source(*, school_id: uuid.UUID | None = None) -> TextbookSource:
    return TextbookSource(
        id=SRC,
        school_id=school_id,
        publisher="合成出版社",
        edition="2019",
        grade="高一",
        volume="必修第一册",
        subject="数学",
        source_repo="https://example.invalid/synthetic-r16.git",
        source_commit="0" * 40,
        source_path="synthetic/r16.jsonl",
        file_sha256="f" * 64,
        rights_status="STAGED",
        rights_evidence_ref="队伍确认授权",
        parser_version="slice_md-202607",
        source_kind="direct-chunk",
        batch_id="synth-batch-01",
        manifest_sha256="m" * 64,
        provenance={"import_id": "synth-import-01", "manifest_sha256": "m" * 64},
    )


def _chunk(section_id: uuid.UUID, order: int, text: str) -> TextbookChunk:
    return TextbookChunk(
        section_id=section_id,
        text=text,
        content_hash=sha256_bytes(text.encode("utf-8")),
        chunker_version="slice_md-202607",
        index_version=0,
        order=order,
    )


async def seed_synthetic_textbook(
    db,
    *,
    school_id: uuid.UUID | None = None,
    lesson1_extra: list[TextbookContentUnit] | None = None,
    lesson1_extra_chunks: list[tuple[int, str]] | None = None,
) -> None:
    """种子一份合成教材：章→两节（1.1/1.2）+ 1.1 子课时；chunks+units+problems。

    覆盖：definition/theorem/formula/example/inquiry/introduction/summary/figure
    + example/exercise problem；1.1 的 chunk 区间 1..12，1.2 13..16。
    """
    db.add(_source(school_id=school_id))
    db.add(
        TextbookSection(
            id=CHAPTER, textbook_id=SRC, parent_id=None, level=1, order=1,
            title="第1章 合成集合", normalized_title="合成集合",
            page_start=1, page_end=16, section_path="1", chunk_start=1, chunk_end=16,
        )
    )
    db.add(
        TextbookSection(
            id=LESSON1, textbook_id=SRC, parent_id=CHAPTER, level=2, order=2,
            title="1.1 合成集合的概念", normalized_title="合成集合的概念",
            page_start=1, page_end=12, section_path="1.1", chunk_start=1, chunk_end=12,
        )
    )
    db.add(
        TextbookSection(
            id=LESSON2, textbook_id=SRC, parent_id=CHAPTER, level=2, order=3,
            title="1.2 合成集合间的关系", normalized_title="合成集合间的关系",
            page_start=13, page_end=16, section_path="1.2", chunk_start=13, chunk_end=16,
        )
    )
    db.add(
        TextbookSection(
            id=SUB1, textbook_id=SRC, parent_id=LESSON1, level=3, order=4,
            title="1.1.1 合成子课时", normalized_title="合成子课时",
            page_start=3, page_end=5, section_path="1.1.1", chunk_start=3, chunk_end=5,
        )
    )
    # 1.1 chunks（1..12）
    texts = {
        1: "## 1.1 合成集合的概念\n在小学和初中，我们已经接触过一些集合。本节我们学习集合的含义。",
        2: "## 元素与集合\n一般地，把研究对象统称为元素，把一些元素组成的总体叫做集合。",
        3: "## 集合的确定性\n给定的集合，它的元素必须是确定的。",
        4: "## 集合的表示法\n## 列举法\n把集合的所有元素一一列举出来，用花括号括起来表示集合的方法叫做列举法。",
        5: "## 描述法\n## 描述法\n利用元素的共同特征表示集合的方法叫做描述法。",
        6: "## 例1\n例 1 用列举法表示下列集合。\n解：略。",
        7: "## 思考\n思考：集合的元素可以重复吗？",
        8: "## 集合相等\n如果两个集合的元素完全相同，那么这两个集合相等。",
        9: "## 定理 1\n定理 1 若两个集合互为子集，则它们相等。",
        10: "## 公式 1\n$$ A \\subseteq B, B \\subseteq A \\Rightarrow A = B $$",
        11: "## 小结\n本节我们学习了集合的概念、元素的性质与集合的表示法。",
        12: "## 练习\n1．判断下列哪些是集合。\n2．用列举法表示集合。",
    }
    for order, text in texts.items():
        db.add(_chunk(LESSON1, order, text))
    # 1.2 chunks（13..16）
    db.add(_chunk(LESSON2, 13, "## 1.2 合成集合间的关系\n本节我们学习子集与相等。"))
    db.add(_chunk(LESSON2, 14, "## 子集\n一般地，集合 A 的任意元素都是集合 B 的元素，称 A 是 B 的子集。"))
    db.add(_chunk(LESSON2, 15, "## 真子集\n如果 A 是 B 的子集，且 B 中至少有一个元素不属于 A，称 A 是 B 的真子集。"))
    db.add(_chunk(LESSON2, 16, "## 小结\n本节我们学习了子集、真子集与集合相等。"))
    await db.flush()

    units = [
        TextbookContentUnit(section_id=LESSON1, page_from=1, page_to=1, type="introduction", text=texts[1], confidence=0.9, extraction_method="rule:body:introduction", review_status="pending"),
        TextbookContentUnit(section_id=LESSON1, page_from=2, page_to=2, type="definition", text="元素与集合定义：把研究对象统称为元素，把一些元素组成的总体叫做集合。", latex=r"A = \{1,2\}", confidence=0.95, extraction_method="rule:body:definition", review_status="reviewed"),
        TextbookContentUnit(section_id=LESSON1, page_from=3, page_to=3, type="definition", text="确定性：给定的集合，它的元素必须是确定的。", confidence=0.9, extraction_method="rule:body:definition", review_status="reviewed"),
        TextbookContentUnit(section_id=LESSON1, page_from=5, page_to=5, type="formula", text="描述法一般式：$\\{x \\in A \\mid P(x)\\}$。", latex=r"\{x \in A \mid P(x)\}", confidence=0.8, extraction_method="rule:heading:formula", review_status="pending"),
        TextbookContentUnit(section_id=LESSON1, page_from=6, page_to=6, type="example", text="例 1 用列举法表示下列集合。", confidence=0.9, extraction_method="rule:heading:example", review_status="reviewed"),
        TextbookContentUnit(section_id=LESSON1, page_from=7, page_to=7, type="inquiry", text="思考：集合的元素可以重复吗？", confidence=0.85, extraction_method="rule:heading:inquiry", review_status="reviewed"),
        TextbookContentUnit(section_id=LESSON1, page_from=9, page_to=9, type="theorem", text="定理 1 若两个集合互为子集，则它们相等。", confidence=0.8, extraction_method="rule:heading:theorem", review_status="pending"),
        TextbookContentUnit(section_id=LESSON1, page_from=10, page_to=10, type="formula", text=r"公式 1：$A \subseteq B, B \subseteq A \Rightarrow A = B$。", latex=r"A \subseteq B, B \subseteq A \Rightarrow A = B", confidence=0.8, extraction_method="rule:heading:formula", review_status="pending"),
        TextbookContentUnit(section_id=LESSON1, page_from=11, page_to=11, type="summary", text="小结：本节我们学习了集合的概念、元素的性质与集合的表示法。", confidence=0.85, extraction_method="rule:heading:summary", review_status="reviewed"),
        TextbookContentUnit(section_id=LESSON1, page_from=12, page_to=12, type="exercise", text="练习：判断下列哪些是集合。", confidence=0.8, extraction_method="rule:heading:exercise", review_status="pending"),
        TextbookContentUnit(section_id=LESSON1, page_from=4, page_to=4, type="figure", text="（图占位：插图位置，R13 图题资产）", confidence=0.7, extraction_method="rule:figure_ref", review_status="pending"),
        TextbookContentUnit(section_id=LESSON2, page_from=13, page_to=13, type="introduction", text="1.2 引入：本节我们学习子集与相等。", confidence=0.85, extraction_method="rule:body:introduction", review_status="pending"),
        TextbookContentUnit(section_id=LESSON2, page_from=14, page_to=14, type="definition", text="子集：A 的任意元素都是 B 的元素，称 A 是 B 的子集。", confidence=0.9, extraction_method="rule:body:definition", review_status="reviewed"),
        TextbookContentUnit(section_id=LESSON2, page_from=16, page_to=16, type="summary", text="小结：本节我们学习了子集、真子集与集合相等。", confidence=0.85, extraction_method="rule:heading:summary", review_status="reviewed"),
    ]
    if lesson1_extra:
        units.extend(lesson1_extra)
    for u in units:
        db.add(u)

    db.add(
        TextbookProblem(
            section_id=LESSON1, kind="example", order=1, stem_text="例 1 用列举法表示集合 A。", solution_text="解：A={1,2}。",
            page_ref=6, chunk_ref=f"textbook_v2/locator/v1/source/{SRC}/section/{LESSON1}/chunk/{'60000000-0000-0000-0000-000000000006'}",
            knowledge_tags=["合成集合"], difficulty="易", provenance="synth-r16",
        )
    )
    db.add(
        TextbookProblem(
            section_id=LESSON1, kind="exercise", order=2, stem_text="练习 1．判断下列哪些是集合。", solution_text=None,
            page_ref=12, knowledge_tags=["合成集合"], difficulty="易", provenance="synth-r16",
        )
    )
    if lesson1_extra_chunks:
        for order, text in lesson1_extra_chunks:
            db.add(_chunk(LESSON1, order, text))
    await db.commit()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def selection(
    section_id: str = str(LESSON1),
    *,
    points: list[str] | None = None,
    lesson_scope: str | None = None,
    chunk_range: tuple[int, int] | None = None,
) -> ContextSelection:
    return ContextSelection(
        source_id=str(SRC),
        section_id=section_id,
        class_id="30000000-0000-0000-0000-000000000001",
        knowledge_points=points or ["集合", "元素"],
        lesson_scope=lesson_scope,
        chunk_range=chunk_range,
    )


async def build(db, sel: ContextSelection | None = None, **kwargs):
    """默认构建 1.1 合成集合的上下文（知识点：集合/元素）。"""
    return await build_teaching_context(db, selection=sel or selection(), **kwargs)
