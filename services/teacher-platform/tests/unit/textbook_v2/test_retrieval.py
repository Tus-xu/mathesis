"""R08 检索 Provider/Context 单测：anchor → 完整 section 恢复 / 版本隔离 / tenant / rights。

fixture 全部虚构（合成教材，含同名章节跨版本、多租户、QUARANTINED 隔离），
禁止真实教材摘录（AGENTS.md 教材源铁律）。
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.importers.slice_importer import sha256_bytes
from app.textbook_v2.models import (
    TextbookChunk,
    TextbookContentUnit,
    TextbookProblem,
    TextbookSection,
    TextbookSource,
)
from app.textbook_v2.retrieval import (
    embed_index,
    list_example_candidates,
    load_teaching_context,
    locate_sections,
    resolve_source_ref,
)

SCHOOL_S1 = uuid.UUID("40000000-0000-0000-0000-000000000001")
SCHOOL_S2 = uuid.UUID("40000000-0000-0000-0000-000000000002")

SRC_A = uuid.UUID("40000000-0000-0000-0000-0000000000a1")  # 平台级 STAGED 人教A版
SRC_B = uuid.UUID("40000000-0000-0000-0000-0000000000b1")  # 平台级 STAGED 苏教版（同名章节）
SRC_C = uuid.UUID("40000000-0000-0000-0000-0000000000c1")  # 租户 S2 STAGED
SRC_D = uuid.UUID("40000000-0000-0000-0000-0000000000d1")  # 平台级 QUARANTINED

CHAPTER = uuid.UUID("40000000-0000-0000-0000-0000000000a2")
L1 = uuid.UUID("40000000-0000-0000-0000-0000000000a3")  # 1.1 集合的概念（人教A）
L2 = uuid.UUID("40000000-0000-0000-0000-0000000000a4")  # 1.2 集合间的基本关系
L_B = uuid.UUID("40000000-0000-0000-0000-0000000000b2")  # 苏教版 1.1
L_C = uuid.UUID("40000000-0000-0000-0000-0000000000c2")  # 租户教材 1.1
L_D = uuid.UUID("40000000-0000-0000-0000-0000000000d2")  # QUARANTINED 隔离教材


def _source(id: uuid.UUID, *, publisher: str, school_id: uuid.UUID | None, rights: str) -> TextbookSource:
    return TextbookSource(
        id=id,
        school_id=school_id,
        publisher=publisher,
        edition="2019",
        grade="高一",
        volume="必修第一册",
        subject="数学",
        source_repo="https://example.invalid/synthetic-r08.git",
        source_commit="0" * 40,
        source_path="synthetic/r08-retrieval.jsonl",
        file_sha256=f"{id.hex}"[:64],
        rights_status=rights,
        rights_evidence_ref="队伍确认授权",
        parser_version="synth-v1",
    )


def _chunk(section_id: uuid.UUID, text: str, order: int) -> TextbookChunk:
    return TextbookChunk(
        section_id=section_id,
        text=text,
        content_hash=sha256_bytes(text.encode("utf-8")),
        chunker_version="slice_md-202607",
        index_version=0,
        order=order,
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
    """四 source + 章节树 + chunks + R07 units/problems；A/B/C 建索引 v1。"""
    db.add(_source(SRC_A, publisher="人教A版2019", school_id=None, rights="STAGED"))
    db.add(_source(SRC_B, publisher="苏教版", school_id=None, rights="STAGED"))
    db.add(_source(SRC_C, publisher="自建版", school_id=SCHOOL_S2, rights="STAGED"))
    db.add(_source(SRC_D, publisher="隔离社", school_id=None, rights="QUARANTINED"))
    await db.flush()

    db.add(TextbookSection(id=CHAPTER, textbook_id=SRC_A, parent_id=None, level=1, order=1,
                           title="第1章 集合与常用逻辑用语", normalized_title="集合与常用逻辑用语",
                           page_start=1, page_end=7, section_path="1"))
    db.add(TextbookSection(id=L1, textbook_id=SRC_A, parent_id=CHAPTER, level=2, order=2,
                           title="1.1 集合的概念", normalized_title="集合的概念",
                           page_start=1, page_end=5, section_path="1.1"))
    db.add(TextbookSection(id=L2, textbook_id=SRC_A, parent_id=CHAPTER, level=2, order=3,
                           title="1.2 集合间的基本关系", normalized_title="集合间的基本关系",
                           page_start=6, page_end=7, section_path="1.2"))
    db.add(TextbookSection(id=L_B, textbook_id=SRC_B, parent_id=None, level=1, order=1,
                           title="1.1 集合的概念", normalized_title="集合的概念",
                           page_start=1, page_end=1, section_path="1.1"))
    db.add(TextbookSection(id=L_C, textbook_id=SRC_C, parent_id=None, level=1, order=1,
                           title="1.1 集合的概念", normalized_title="集合的概念",
                           page_start=1, page_end=1, section_path="1.1"))
    db.add(TextbookSection(id=L_D, textbook_id=SRC_D, parent_id=None, level=1, order=1,
                           title="隔离教材", normalized_title="隔离教材",
                           page_start=1, page_end=1, section_path=""))
    await db.flush()

    chunks = [
        _chunk(L1, "## 1.1 集合的概念\n集合是数学的基本概念，把研究对象统称为元素。", 1),
        _chunk(L1, "元素的性质：确定性、互异性、无序性。", 2),
        _chunk(L1, "## 例 1\n用列举法表示下列集合。\n解：由定义可得。", 3),
        _chunk(L1, "## 思考\n观察下列集合有什么共同特征？", 4),
        _chunk(L1, "## 小结\n本章学习了集合与集合间的关系。", 5),
        _chunk(L2, "## 1.2 集合间的基本关系\n包含与相等。", 6),
        _chunk(L2, "## 例 2\n判断集合间的关系。", 7),
        _chunk(L_B, "## 1.1 集合的概念\n苏教版教材中的集合概念表述。", 1),
        _chunk(L_C, "## 1.1 集合的概念\n租户自建教材概念 zwyxtqnmk 专属。", 1),
        _chunk(L_D, "## 隔离教材\n未授权内容 qzwxecrvtbynum 绝不可见。", 1),
    ]
    db.add_all(chunks)

    # R07 语义提取产物（units + problems），挂 L1
    db.add_all(
        [
            TextbookContentUnit(section_id=L1, page_from=1, page_to=1, type="definition",
                                text="把研究对象统称为元素，把一些元素组成的总体叫做集合。",
                                extraction_method="rule:heading:definition", confidence=0.95,
                                review_status="pending"),
            TextbookContentUnit(section_id=L1, page_from=3, page_to=3, type="example",
                                text="用列举法表示下列集合。", extraction_method="rule:heading:example",
                                confidence=0.9, review_status="pending"),
            TextbookContentUnit(section_id=L1, page_from=3, page_to=3, type="figure",
                                text="图1-1 集合关系图（占位，资产归 R13）。", extraction_method="rule:heading:figure",
                                confidence=0.4, review_status="pending"),
            TextbookContentUnit(section_id=L1, page_from=5, page_to=5, type="summary",
                                text="本章学习了集合的概念。", extraction_method="rule:heading:summary",
                                confidence=0.9, review_status="pending"),
            TextbookContentUnit(section_id=L2, page_from=6, page_to=6, type="definition",
                                text="如果集合A的任意元素都是集合B的元素，称A是B的子集。",
                                extraction_method="rule:body:definition", confidence=0.85,
                                review_status="pending"),
        ]
    )
    db.add_all(
        [
            TextbookProblem(section_id=L1, kind="example", order=1,
                            stem_text="用列举法表示小于10的自然数组成的集合。",
                            solution_text="解：A={0,1,2,…,9}。", page_ref=3,
                            knowledge_tags=["集合"], difficulty="易", provenance="textbook_v2/extraction/r07-rule-v1/chunk_3"),
            TextbookProblem(section_id=L1, kind="exercise", order=2,
                            stem_text="判断两个集合是否相等。", solution_text="解：元素相同即相等。",
                            page_ref=5, knowledge_tags=["集合"], difficulty="中",
                            provenance="textbook_v2/extraction/r07-rule-v1/chunk_5"),
        ]
    )
    await db.flush()

    await embed_index(db, source_ids=[SRC_A, SRC_B, SRC_C], index_version=1)


async def _section_ids(cands) -> set:
    return {c.section_id for c in cands}


# ─────────────────────────────────────────────────────────────────────────────
# locate_sections：anchor 召回 / 跨版本 / tenant / rights / 空结果
# ─────────────────────────────────────────────────────────────────────────────


async def test_locate_anchor_hits_correct_section(db):
    await _seed(db)
    cands = await locate_sections(db, "集合的概念")
    assert cands and cands[0].section_id == L1  # 人教A 1.1（chunk1 含标题整串命中）
    assert cands[0].rights_status == "STAGED"
    assert cands[0].page_range == [1, 5]


async def test_locate_gold_query_deterministic(db):
    await _seed(db)
    r1 = await locate_sections(db, "元素的性质")
    r2 = await locate_sections(db, "元素的性质")
    assert [(c.section_id, round(c.score, 9)) for c in r1] == [(c.section_id, round(c.score, 9)) for c in r2]


async def test_same_title_cross_version_isolation(db):
    """同名章节跨版本隔离：course_context 过滤 publisher/volume。"""
    await _seed(db)
    all_hits = await locate_sections(db, "集合的概念")
    hit_ids = await _section_ids(all_hits)
    assert L1 in hit_ids and L_B in hit_ids and L_C not in hit_ids  # 平台级查询看不到租户教材

    sujiao = await locate_sections(db, "集合的概念", course_context={"publisher": "苏教版"})
    assert [c.section_id for c in sujiao] == [L_B]

    rena = await locate_sections(db, "集合的概念", course_context={"publisher": "人教A版2019"})
    assert {c.section_id for c in rena} == {L1, L2}  # 人教A 内同名/相邻节均命中，版本隔离仍成立


async def test_tenant_isolation(db):
    """school_id=S1 查询者只能看到平台级 + 本租户，看不到 S2 租户教材。"""
    await _seed(db)
    cands = await locate_sections(db, "集合的概念", course_context={"school_id": SCHOOL_S1})
    hit_ids = await _section_ids(cands)
    assert L1 in hit_ids and L_B in hit_ids
    assert L_C not in hit_ids  # S2 租户教材不可见（越权返回空）


async def test_quarantined_invisible(db):
    await _seed(db)
    cands = await locate_sections(db, "qzwxecrvtbynum")  # 只存在于 QUARANTINED 源
    assert cands == []  # 只匹配 QUARANTINED 源 → 不可见 → 空


async def test_empty_result_no_visible_matches(db):
    await _seed(db)
    # 只匹配不可见内容（QUARANTINED / 其他租户的专属 token）→ 空
    assert await locate_sections(db, "qzwxecrvtbynum") == []
    assert await locate_sections(db, "zwyxtqnmk") == []  # 仅存在于 S2 租户教材


# ─────────────────────────────────────────────────────────────────────────────
# load_teaching_context：完整 section 恢复 / 父子节 / 聚合 R07 / 越权空
# ─────────────────────────────────────────────────────────────────────────────


async def test_topk_middle_hit_recovers_full_section(db):
    """top-k 命中章节中部（chunk3）仍恢复完整 section（5 chunks + 全部 facts + 页序）。"""
    await _seed(db)
    cands = await locate_sections(db, "列举法表示下列集合")
    assert cands and cands[0].section_id == L1
    ctx = await load_teaching_context(db, L1)
    assert ctx is not None
    assert ctx.section.title == "1.1 集合的概念"
    assert ctx.ordered_page_refs == [1, 2, 3, 4, 5]
    assert [c.order for c in ctx.chunks] == [1, 2, 3, 4, 5]
    assert len(ctx.facts) == 4 + 2  # 4 units + 2 problems（R07 产物全部聚合）


async def test_parent_child_order(db):
    await _seed(db)
    ctx_l1 = await load_teaching_context(db, L1)
    assert ctx_l1 is not None and ctx_l1.parent is not None
    assert ctx_l1.parent.title == "第1章 集合与常用逻辑用语"

    ctx_chapter = await load_teaching_context(db, CHAPTER)
    assert ctx_chapter is not None
    assert [c.title for c in ctx_chapter.children] == ["1.1 集合的概念", "1.2 集合间的基本关系"]
    # 聚合后代：L1（4 units + 2 problems）+ L2（1 unit）= 7
    assert len(ctx_chapter.facts) == 7
    # 教材原序：facts 按 page 锚升序交错（unit/problem 均以 chunk 行号为锚）
    anchors = [f.page_from if f.page_from is not None else f.page_ref for f in ctx_chapter.facts]
    assert anchors == sorted(anchors)


async def test_chunk_only_section_derives_read_only_facts(db):
    """已有授权切片但尚未持久化语义单元时，T23 仍能读到可回溯事实。

    回退只在内存中运行 R07 确定性规则，不写 textbook_content_units /
    textbook_problems，也不生成 page_refs/pNN。
    """
    await _seed(db)
    ctx = await load_teaching_context(db, L_B)
    assert ctx is not None
    assert ctx.facts
    assert all(f.chunk_ref and f.source_ref for f in ctx.facts)
    assert all(f.page_ref is None for f in ctx.facts)
    assert "rule_extraction_fallback" in {w.code for w in ctx.warnings}
    view = await resolve_source_ref(db, ctx.facts[0].source_ref)
    assert view is not None and view.kind == "chunk"


async def test_aggregates_r07_units_and_problems_with_source_ref(db):
    await _seed(db)
    ctx = await load_teaching_context(db, L1)
    assert ctx is not None
    assert {f.fact_type for f in ctx.definitions} == {"definition"}
    assert len(ctx.examples) == 2  # unit example + problem example（both type=example）
    assert len(ctx.figures) == 1
    assert any(f.solution and "解：" in f.solution for f in ctx.examples)

    # 每条事实带 source_ref 且可解析
    for f in ctx.facts:
        assert f.source_ref.startswith("textbook_v2/source/")
        view = await resolve_source_ref(db, f.source_ref)
        assert view is not None and view.rights_status == "STAGED"
        assert view.text_preview  # 截断预览非空

    # 缺失/低置信警告
    codes = {w.code for w in ctx.warnings}
    assert "pending_review" in codes and "figure_placeholder" in codes
    assert any(w.code == "low_confidence" for w in ctx.warnings)


async def test_context_empty_when_unauthorized(db):
    await _seed(db)
    # S1 查询者访问 S2 租户教材 → None（越权返回空，不抛敏感错误）
    assert await load_teaching_context(db, L_C, course_context={"school_id": SCHOOL_S1}) is None
    # QUARANTINED 源 section 同样不可见
    assert await load_teaching_context(db, L_D) is None


# ─────────────────────────────────────────────────────────────────────────────
# list_example_candidates / resolve_source_ref
# ─────────────────────────────────────────────────────────────────────────────


async def test_list_example_candidates_keeps_problems(db):
    await _seed(db)
    problems = await list_example_candidates(db, L1)
    assert [p.kind for p in problems] == ["example", "exercise"]
    assert problems[0].stem_text and "解：" in problems[0].solution_text
    assert problems[0].page_ref == 3 and problems[1].page_ref == 5
    assert all(p.source_ref.startswith("textbook_v2/source/") for p in problems)

    only_exercise = await list_example_candidates(db, L1, {"kind": "exercise"})
    assert len(only_exercise) == 1 and only_exercise[0].kind == "exercise"

    by_tag = await list_example_candidates(db, L1, {"knowledge_tags": ["集合"]})
    assert len(by_tag) == 2


async def test_list_example_candidates_unauthorized_empty(db):
    await _seed(db)
    assert await list_example_candidates(db, L_C, course_context={"school_id": SCHOOL_S1}) == []
    assert await list_example_candidates(db, L_D) == []


async def test_resolve_source_ref_views(db):
    await _seed(db)
    ctx = await load_teaching_context(db, L1)
    assert ctx is not None
    chunk_view = await resolve_source_ref(db, ctx.chunks[1].source_ref)  # chunk order=2
    assert chunk_view is not None and chunk_view.kind == "chunk"
    assert chunk_view.order == 2
    assert "确定性" in chunk_view.text_preview
    assert chunk_view.textbook == "人教A版2019·2019·必修第一册"

    # 越权解析租户教材 chunk → None
    c_chunk_id = (
        await db.execute(select(TextbookChunk).where(TextbookChunk.section_id == L_C))
    ).scalars().first()
    assert c_chunk_id is not None
    c_ref = f"textbook_v2/source/{SRC_C}/section/{L_C}/chunk/{c_chunk_id.id}"
    assert await resolve_source_ref(db, c_ref, course_context={"school_id": SCHOOL_S1}) is None
    # 非法 / 非教材域 ref → None
    assert await resolve_source_ref(db, "textbook_v2/source/not-a-uuid") is None
    assert await resolve_source_ref(db, "web://http/search/result") is None  # 教材事实纯净：非教材域 ref 不解析
