"""R07 语义提取单测：rule 分类 10 类 / 跨 chunk 例题 / 多题共享 / 低置信/图占位/幂等/禁 LLM。

fixture 全部虚构（仿 AC 切片 markdown 风格：`## 子标题` + 正文含 $LaTeX$），
禁止填写任何真实教材摘录——未授权内容零进仓库（AGENTS.md 教材源铁律）。

db fixture 与 test_slice_importer/test_models 同口径：SQLite 内存库 + 真实 metadata。
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.extraction import (
    EXTRACTOR_VERSION,
    detect_figure_refs,
    plan_extraction_section,
    split_problem_text,
    staged_extract_section,
)
from app.textbook_v2.models import TextbookContentUnit, TextbookProblem, TextbookSection, TextbookSource

SECTION_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
SECTION_TITLE = "9.1 合成函数的性质"  # 虚构章节，非真实教材


def synthetic_chunks() -> list[dict[str, object]]:
    """一份虚构章节的 12 个 chunk（覆盖全部 10 类 content unit + 题目拆分场景）。"""
    return [
        {
            "chunk_id": "c1",
            "section": SECTION_TITLE,
            "content": f"## {SECTION_TITLE}\n本节我们学习合成函数的单调性。",
        },  # introduction (body:introduction)
        {
            "chunk_id": "c2",
            "section": SECTION_TITLE,
            "content": "## 定义9.1\n若对任意 $x$ 均有 $f(x)=x$，则称 $f$ 为恒等映射，函数 $g$ 叫做合成函数。",
        },  # definition
        {
            "chunk_id": "c3",
            "section": SECTION_TITLE,
            "content": "## 定理9.1\n若 $f$ 单调递增且 $g$ 单调递增，则复合函数单调递增。",
        },  # theorem
        {
            "chunk_id": "c4",
            "section": SECTION_TITLE,
            "content": "## 公式\n$$\\left| f(x) \\right| \\leq f(x)^{2}+1$$ 该不等式称作绝对值引理。",
        },  # formula
        {
            "chunk_id": "c5",
            "section": SECTION_TITLE,
            "content": "## 推导\n由定义与定理，由此可得复合函数保持单调性。",
        },  # derivation
        {
            "chunk_id": "c6",
            "section": SECTION_TITLE,
            "content": "## 例 9.1\n设 $f(x)=x^{2}$，求 $f(f(x))$ 的表达式。\n解：由复合定义得 $f(f(x))=x^{4}$。",
        },  # example（题干+解答同块）
        {
            "chunk_id": "c7",
            "section": SECTION_TITLE,
            "content": "## 例 9.2\n判断 $h(x)=\\frac{1}{x}$ 在 $(0,+\\infty)$ 的单调性。",
        },  # example（题干，无解答）跨 chunk
        {
            "chunk_id": "c8",
            "section": SECTION_TITLE,
            "content": "解：取任意 $0<x_1<x_2$，由差商可得 $h(x_1)>h(x_2)$。",
        },  # 解答块（承接上题）
        {
            "chunk_id": "c9",
            "section": SECTION_TITLE,
            "content": "## 练习\n1. 求 $f(g(x))$ 的表达式。\n2. 证明 $f(g(x))$ 单调递增。\n答案：略。",
        },  # exercise 多小题共享答案
        {
            "chunk_id": "c10",
            "section": SECTION_TITLE,
            "content": "## 思考\n在什么条件下 $f(g(x))$ 有最小值？",
        },  # inquiry
        {
            "chunk_id": "c11",
            "section": SECTION_TITLE,
            "content": "## 图9-1\n![合成函数图象](images/fig1.png)\n观察合成函数图象。",
        },  # figure 占位（标题"图"开头 → figure 优先）
        {
            "chunk_id": "c12",
            "section": SECTION_TITLE,
            "content": "## 小结\n本章我们学习了合成函数的定义与相关定理。",
        },  # summary
    ]


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


async def _seed_section(db) -> None:
    source = TextbookSource(
        school_id=None,
        publisher="合成出版社",
        edition="全一册",
        grade="高一",
        volume="上册",
        subject="数学",
        source_repo="https://example.invalid/synthetic-textbook.git",
        source_commit="0" * 40,
        source_path="synthetic/r07-fixture.pdf",
        file_sha256="0" * 64,
        rights_status="STAGED",
        rights_evidence_ref="队伍确认授权",
        parser_version="synth-v1",
    )
    db.add(source)
    await db.flush()
    db.add(
        TextbookSection(
            id=SECTION_ID,
            textbook_id=source.id,
            parent_id=None,
            level=2,
            order=1,
            title=SECTION_TITLE,
            normalized_title=SECTION_TITLE,
            page_start=1,
            page_end=8,
        )
    )
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# rule 分类：10 类全命中 + 回溯
# ─────────────────────────────────────────────────────────────────────────────


def test_plan_covers_all_unit_types():
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    types = {u.type for u in plan.units}
    expected = {
        "introduction",
        "definition",
        "theorem",
        "formula",
        "derivation",
        "example",
        "exercise",
        "inquiry",
        "figure",
        "summary",
    }
    assert types >= expected, f"缺类: {expected - types}"
    # 每类至少 1 个成功样例
    for t in expected:
        assert any(u.type == t for u in plan.units), f"缺 {t}"


def test_every_unit_traceable_to_chunk():
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    assert plan.chunk_count == 12
    for u in plan.units:
        assert 1 <= u.chunk_idx_from <= u.chunk_idx_to <= 12, u.chunk_idx_from
        assert u.section_id == SECTION_ID
    for p in plan.problems:
        assert 1 <= p.chunk_idx <= 12
        assert p.provenance == f"textbook_v2/extraction/{EXTRACTOR_VERSION}/chunk_{p.chunk_idx}"
        assert p.section_id == SECTION_ID


def test_extraction_method_explainable():
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    for u in plan.units:
        assert u.extraction_method and u.extraction_method.startswith("rule"), u.extraction_method
        assert u.confidence is not None and 0.0 <= u.confidence <= 1.0
    # 例题/定义命中标题信号 → 高置信
    by_type = {u.type: u for u in plan.units}
    assert by_type["definition"].confidence >= 0.9
    assert by_type["definition"].extraction_method == "rule:heading:definition"


# ─────────────────────────────────────────────────────────────────────────────
# 题目拆分：题干/解答、跨 chunk、多小题共享
# ─────────────────────────────────────────────────────────────────────────────


def test_split_problem_text_basic():
    stem, solution = split_problem_text("设 $f(x)=x$。\n解：由定义得证。")
    assert "设 $f(x)=x$" in stem
    assert "解：由定义得证" in solution


def test_cross_chunk_example_merged():
    """例 9.2 题干在 chunk7，解答在 chunk8（解：…）→ 合并为一个 problem，跨 chunk 关联。"""
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    merged = [p for p in plan.problems if "判断 $h(x)" in p.stem_text]
    assert merged, "未找到例 9.2 problem"
    p = merged[0]
    assert p.kind == "example"
    assert p.solution_text is not None and "差商" in p.solution_text
    assert 7 in p.chunks_span and 8 in p.chunks_span, p.chunks_span


def test_multi_exercise_shared_answer():
    """练习块 2 小题共享「答案：略。」→ 拆 2 个 problem，共享解答挂最后小题 + review。"""
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    exs = [p for p in plan.problems if p.kind == "exercise"]
    assert len(exs) == 2
    assert "求 $f(g(x))$" in exs[0].stem_text
    assert "证明 $f(g(x))" in exs[1].stem_text
    assert exs[1].solution_text and "答案：略" in exs[1].solution_text
    assert any(r.reason == "多小题共享解答" for r in plan.reviews)
    # order 唯一且递增
    assert [p.order for p in plan.problems] == sorted(p.order for p in plan.problems)


def test_problem_kinds_sequence():
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    kinds = sorted(p.kind for p in plan.problems)
    assert kinds == ["example", "example", "exercise", "exercise", "inquiry"]


# ─────────────────────────────────────────────────────────────────────────────
# 零图片 / 低置信 / 无信号延续
# ─────────────────────────────────────────────────────────────────────────────


def test_figure_placeholder_no_asset():
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    fig = [u for u in plan.units if u.type == "figure"]
    assert fig, "缺 figure 占位 unit"
    assert detect_figure_refs(fig[0].text)  # 图引用被保留（供 R13 评估）
    assert any(r.reason and "图" in r.reason for r in plan.reviews)  # 图题关联进 review 队列
    # 零图片：figure unit 不携带任何资产字段（asset 管线归 R13）
    assert fig[0].latex is None or detect_figure_refs(fig[0].text)


def test_no_signal_block_merges_into_previous_unit():
    chunks = synthetic_chunks()
    chunks.append({"chunk_id": "c13", "section": SECTION_TITLE, "content": "这是一段无明确语义信号的补充说明。"})
    plan = plan_extraction_section(chunks, section_id=SECTION_ID, section_title=SECTION_TITLE)
    assert any(r.reason == "无信号并入上一单元" for r in plan.reviews)
    # 合并后 chunk 区间扩展到 13
    summary_units = [u for u in plan.units if u.type == "summary"]
    assert summary_units and summary_units[-1].chunk_idx_to == 13


def test_or_phan_solution_block_review():
    chunks = synthetic_chunks()
    chunks.insert(3, {"chunk_id": "c_x", "section": SECTION_TITLE, "content": "解：这是孤立解答。"})
    plan = plan_extraction_section(chunks, section_id=SECTION_ID, section_title=SECTION_TITLE)
    assert any(r.reason == "孤立解答块（无对应题干）" for r in plan.reviews)


def test_rule_path_works_without_llm():
    """禁用 AI（llm=None 默认）→ rule 路径独立成立（验收门）。"""
    plan = plan_extraction_section(synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    assert plan.units and plan.problems
    assert all("rule" in (u.extraction_method or "") for u in plan.units)


def test_llm_verdict_only_via_dto():
    def fake_llm(text: str) -> dict[str, object]:
        return {"type": "inquiry", "confidence": 0.95, "confirmed": False, "model": "fake-v1"}

    plan = plan_extraction_section(
        synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE, llm=fake_llm
    )
    assert any(u.extraction_method == "llm:fake-v1" for u in plan.units)
    assert any(r.reason == "llm 未确认" for r in plan.reviews)


# ─────────────────────────────────────────────────────────────────────────────
# staged 写库 + 幂等
# ─────────────────────────────────────────────────────────────────────────────


async def test_staged_writes_units_and_problems(db):
    await _seed_section(db)
    plan = await staged_extract_section(
        db, synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE
    )
    units = (await db.execute(select(TextbookContentUnit))).scalars().all()
    problems = (await db.execute(select(TextbookProblem))).scalars().all()
    assert len(units) == len(plan.units) >= 10
    assert len(problems) == len(plan.problems) == 5

    # 回溯：content unit → section；problem → section + page_ref=chunk idx
    for u in units:
        assert u.section_id == SECTION_ID
        assert 1 <= u.page_from <= u.page_to <= 12
        assert u.extraction_method
    for p in problems:
        assert p.section_id == SECTION_ID
        assert p.page_ref >= 1
        assert p.provenance.startswith(f"textbook_v2/extraction/{EXTRACTOR_VERSION}/")


async def test_staged_idempotent_no_duplicates(db):
    await _seed_section(db)
    plan1 = await staged_extract_section(db, synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    units1 = (await db.execute(select(TextbookContentUnit))).scalars().all()
    probs1 = (await db.execute(select(TextbookProblem))).scalars().all()
    assert len(units1) == len(plan1.units)
    assert len(probs1) == len(plan1.problems)

    # 重跑同一节内容 → 全量幂等 SKIP，不新增任何行
    plan2 = await staged_extract_section(db, synthetic_chunks(), section_id=SECTION_ID, section_title=SECTION_TITLE)
    assert any("SKIP 全量幂等命中" in i for i in plan2.issues)
    assert (await db.execute(select(TextbookContentUnit))).scalars().all() == units1
    assert (await db.execute(select(TextbookProblem))).scalars().all() == probs1
