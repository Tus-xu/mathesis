"""W1-A 内核质量修复单测（LEAD-BAR §1.3 / §4 / FROZEN-CONTRACT §B）。

覆盖本轮修复：
- 编辑内部标记净化（「（教师修订）」等）——提案入口 + 投影入口 + 标题；
- 投影角色语义正确（definition 只用于定义页；目标/要点用 bullets）；
- 页间去重（收尾页不复读其它页）；
- 教材无例题 → AI 变式并标 `ai_generated`（不冒充教材例题）；
- 推导桶产出真实内容时消除 `no_*_evidence` 误导性 warning。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

from app.teacher_prep.blueprint import parse_and_validate, project_blueprint, sanitize_title
from app.teacher_prep.specs.projector import (
    ProjectionRunLineage,
    project_deck_spec,
    project_lesson_plan_spec,
)
from app.teacher_prep.text_safety import sanitize_visible_text, strip_edit_markers

from .tp_context_fixtures import build, seed_synthetic_textbook

_MARKERS = ("（教师修订）", "（教师新增）", "（待确认）")


# ── 1. 净化原语 ──────────────────────────────────────────────────────────────


def test_strip_edit_markers_removes_internal_markers() -> None:
    assert strip_edit_markers("课堂导入（教师修订）") == "课堂导入"
    assert strip_edit_markers("探究（教师新增）椭圆") == "探究椭圆"
    assert strip_edit_markers("定义（待确认）") == "定义"
    assert strip_edit_markers("目标（待确认：依据不足）") == "目标"
    # 自动化编辑验证标记（R8 实测泄漏形态，含 `-N` 说明段）
    assert strip_edit_markers("椭圆的定义（教师A10编辑验证-1）") == "椭圆的定义"
    assert strip_edit_markers("课堂小结（教师A1编辑验证）") == "课堂小结"


def test_strip_edit_markers_keeps_teacher_authored_suffix() -> None:
    # 教师自撰的正式后缀（（教师版）/（第 2 稿））不是内部标记，必须保留
    assert strip_edit_markers("情境导入（教师版）") == "情境导入（教师版）"
    assert strip_edit_markers("新授（第 2 稿）") == "新授（第 2 稿）"


def test_sanitize_visible_text_collapses_and_drops_empty() -> None:
    assert sanitize_visible_text("  （教师修订）椭圆，  ") == "椭圆"
    assert sanitize_visible_text("（教师修订）") == ""
    assert sanitize_visible_text(None) == ""


def test_sanitize_title_strips_edit_marker_keeps_suffix() -> None:
    assert sanitize_title("3.1.2 椭圆的简单几何性质（第一课时，待确认）") == "3.1.2 椭圆的简单几何性质"
    assert sanitize_title("椭圆（教师修订）") == "椭圆"
    assert sanitize_title("情境导入（教师版）") == "情境导入（教师版）"


# ── 2. 提案入口净化（flow 标题/条目）──────────────────────────────────────────


def test_parse_and_validate_sanitizes_flow_title_and_items() -> None:
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": "a" * 64,
        "title": "椭圆（教师修订）",
        "lesson_flow": [
            {
                "stage": "introduction",
                "title": "课堂导入（教师修订）",
                "items": [{"text": "导入（教师新增）问题", "source_ref": None}],
            }
        ],
    }
    proposal = parse_and_validate(json.dumps(payload, ensure_ascii=False))
    assert "教师修订" not in proposal.title and "教师新增" not in proposal.title
    assert proposal.lesson_flow[0].title == "课堂导入"
    assert proposal.lesson_flow[0].items[0].text == "导入问题"


# ── 3. 投影入口净化（含旧脏 body）+ 角色语义 + 页间去重 ────────────────────────


def _kw(ctx, body):
    return dict(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=1,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="m"), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 22, tzinfo=UTC),
    )


def _dirty_body(ctx) -> dict:
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合（教师修订）",
        "objectives": [{"text": "理解集合（教师新增）与元素", "source_ref": None}],
        "prior_knowledge": [{"text": "回顾（待确认）小学集合", "source_ref": None}],
        "concepts": [{"text": "集合是（待确认）确定的整体", "source_ref": None}],
        "theorems": [],
        "examples": [],
        "inquiries": [{"text": "集合元素可以重复吗（待确认）", "source_ref": None}],
        "key_points": [{"text": "集合的三条性质（教师修订）", "source_ref": None}],
        "difficult_points": [{"text": "元素与集合的从属关系", "source_ref": None}],
        "reflection": [{"text": "关注确定性（教师新增）", "source_ref": None}],
        "lesson_flow": [
            {"stage": "introduction", "title": "课堂导入（教师修订）",
             "items": [{"text": "从生活实例引入（教师新增）", "source_ref": None}], "duration_minutes": 25},
            {"stage": "new_content", "title": "新授（教师修订）",
             "items": [{"text": "讲解集合概念", "source_ref": None},
                       {"text": "辨析元素与集合关系", "source_ref": None}], "duration_minutes": 10},
            {"stage": "summary", "title": "课堂小结",
             "items": [{"text": "归纳集合与元素的关系", "source_ref": None}], "duration_minutes": 5},
            {"stage": "homework", "title": "作业布置",
             "items": [{"text": "完成课本练习一", "source_ref": None}], "duration_minutes": 5},
        ],
        "warnings_ack": [],
    }


async def test_projection_strips_markers_everywhere(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    body = _dirty_body(ctx)
    deck = project_deck_spec(**_kw(ctx, body))
    plan = project_lesson_plan_spec(**_kw(ctx, body))
    marker_re = re.compile(r"教师修订|教师新增|待确认")
    for s in deck.slides:
        assert not marker_re.search(s.title or ""), s.title
        for e in s.elements:
            for t in [*(e.items or [])] + ([e.text] if e.text else []):
                assert not marker_re.search(t or ""), t
    for st in plan.lesson_flow:
        assert not marker_re.search(st.title or ""), st.title
    for it in (*plan.objectives, *plan.reflection, *plan.key_difficult_points):
        assert not marker_re.search(it.text or ""), it.text
    # 定义带只应出现在真正的定义页；这里没有 definition 页 → 全 deck 无 definition
    assert all(e.role != "definition" for s in deck.slides for e in s.elements)


async def test_deck_summary_page_does_not_readback_other_pages(db) -> None:
    """收尾页只取其它页未用过的真实事实（页间去重，禁止「小结复读全桶」）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    deck = project_deck_spec(**_kw(ctx, _dirty_body(ctx)))

    def _keyset(slide) -> set[str]:
        out: set[str] = set()
        for e in slide.elements:
            for t in [*(e.items or [])] + ([e.text] if e.text else []):
                if t:
                    out.add(re.sub(r"\s+", "", t))
        return out

    keysets = {s.id: _keyset(s) for s in deck.slides}
    summary = next(s for s in deck.slides if s.type == "summary")
    for sid, ks in keysets.items():
        if sid == summary.id:
            continue
        assert len(ks & keysets[summary.id]) <= 1, (sid, ks & keysets[summary.id])


# ── 4. AI 变式例题（教材无例题时）─────────────────────────────────────────────


async def test_ai_example_variants_generated_and_tagged(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    base = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [{"text": "理解集合与元素的概念", "source_ref": None}],
        "concepts": [{"text": "集合是确定的一堆对象", "source_ref": None}],
        "theorems": [],
        "examples": [],  # 教材无例题 → 触发 AI 变式
        "inquiries": [],
        "key_points": [{"text": "集合的确定性", "source_ref": None}],
        "lesson_flow": [{"stage": "new_content", "title": "新授",
                         "items": [{"text": "讲解概念", "source_ref": None}], "duration_minutes": 20}],
        "warnings_ack": [],
    }
    calls: list[str] = []

    def _llm(prompt: str) -> str:
        calls.append(prompt)
        if "设计 1-2 道" in prompt:  # 变式指令
            return json.dumps(
                {"examples": [{"text": "用列举法表示集合 $A=\\{1,2,3\\}$", "solution": "直接写出"}]},
                ensure_ascii=False,
            )
        return json.dumps(base, ensure_ascii=False)

    proposal = await project_blueprint(ctx, _llm)
    assert len(proposal.examples) == 1
    ex = proposal.examples[0]
    assert ex.source_origin == "ai_generated"
    assert ex.source_ref is None, "AI 变式不得携带教材 locator（不冒充教材例题）"
    assert "examples_ai_generated" in proposal.warnings_ack


async def test_textbook_examples_are_not_overwritten(db) -> None:
    """教材已有例题 → 不打 AI 变式、不调变式 LLM。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    base = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [{"text": "理解集合", "source_ref": None}],
        "examples": [{"text": "教材例题：用列举法表示集合", "source_ref": None}],
        "lesson_flow": [{"stage": "new_content", "title": "新授",
                         "items": [{"text": "讲解", "source_ref": None}], "duration_minutes": 20}],
        "warnings_ack": [],
    }
    calls: list[str] = []

    def _llm(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(base, ensure_ascii=False)

    proposal = await project_blueprint(ctx, _llm)
    assert len(proposal.examples) == 1
    assert proposal.examples[0].source_origin is None
    assert "examples_ai_generated" not in proposal.warnings_ack


# ── 5. no_*_evidence warning 消除（推导桶产出真实内容后）────────────────────────


async def test_no_evidence_warnings_cleared_when_derived_content_present(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [{"text": "理解集合与元素", "source_ref": None}],
        "key_points": [{"text": "集合的确定性", "source_ref": None}],
        "difficult_points": [{"text": "元素与集合关系", "source_ref": None}],
        "examples": [{"text": "教材例题", "source_ref": None}],
        "lesson_flow": [{"stage": "new_content", "title": "新授",
                         "items": [{"text": "讲解", "source_ref": None}], "duration_minutes": 20}],
        "warnings_ack": ["no_objectives_evidence", "no_key_difficult_evidence", "incomplete_chapter"],
    }
    proposal = await project_blueprint(ctx, lambda _p: json.dumps(payload, ensure_ascii=False))
    assert "no_objectives_evidence" not in proposal.warnings_ack
    assert "no_key_difficult_evidence" not in proposal.warnings_ack
    # 无关 warning 不受影响（诚实披露保留）
    assert "incomplete_chapter" in proposal.warnings_ack


# ── 6. 桶非空与 ref 覆盖（LEAD-BAR §4.1 / §4.3）──────────────────────────────


def _bucket_texts(proposal, key: str) -> list[str]:
    return [i.text for i in getattr(proposal, key)]


async def test_theorems_and_reflection_nonempty_and_refs_backfilled(db) -> None:
    """教材无定理 / LLM 漏反思 → 由本节要点确定性派生；四桶各 ≥1 条带真实 locator。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [{"text": "理解集合与元素", "source_ref": None}],
        "concepts": [{"text": "集合是确定的整体", "source_ref": None}],
        "theorems": [],  # 教材无定理原文 → 派生「本节结论」
        "key_points": [{"text": "集合的确定性", "source_ref": None}],
        "difficult_points": [{"text": "元素与集合的从属关系", "source_ref": None}],
        "reflection": [],  # LLM 未产出 → 派生「预设反思」
        "examples": [{"text": "教材例题：判断集合", "source_ref": None}],
        "lesson_flow": [{"stage": "new_content", "title": "新授",
                         "items": [{"text": "讲解", "source_ref": None}], "duration_minutes": 20}],
        "warnings_ack": [],
    }
    proposal = parse_and_validate(json.dumps(payload, ensure_ascii=False), context=ctx)
    assert proposal.theorems, "theorems 桶必须非空（LEAD-BAR §4.1）"
    assert proposal.reflection, "reflection 桶必须非空（LEAD-BAR §4.3 六章齐全）"
    assert "reflection_no_basis" not in proposal.warnings_ack
    # 派生条目文本自披露派生关系
    assert any(t.startswith("本节结论：") for t in _bucket_texts(proposal, "theorems"))
    assert any(t.startswith("预设反思：") for t in _bucket_texts(proposal, "reflection"))
    # objectives/key_points/concepts/theorems 至少各 1 条带真实 locator（§4.1 覆盖）
    for key in ("objectives", "key_points", "concepts", "theorems"):
        assert any(i.source_ref for i in getattr(proposal, key)), f"{key} 缺真实 source_ref"
    # 互不重复（禁止同一句出现在两个桶）
    all_texts = [
        t
        for key in ("objectives", "key_points", "difficult_points", "concepts", "theorems", "reflection")
        for t in _bucket_texts(proposal, key)
    ]
    assert len(all_texts) == len(set(all_texts)), "派生条目与源条目文本重复"


async def test_theorems_not_overwritten_when_present(db) -> None:
    """教材有定理 → 不派生、不改写服务端事实桶。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [{"text": "理解集合", "source_ref": None}],
        "concepts": [{"text": "集合是确定的整体", "source_ref": None}],
        "theorems": [{"text": "教材定理：集合元素的确定性", "source_ref": None}],
        "key_points": [{"text": "集合的确定性", "source_ref": None}],
        "difficult_points": [{"text": "元素与集合的从属关系", "source_ref": None}],
        "reflection": [{"text": "关注学生对确定性的理解", "source_ref": None}],
        "examples": [{"text": "教材例题", "source_ref": None}],
        "lesson_flow": [{"stage": "new_content", "title": "新授",
                         "items": [{"text": "讲解", "source_ref": None}], "duration_minutes": 20}],
        "warnings_ack": [],
    }
    proposal = parse_and_validate(json.dumps(payload, ensure_ascii=False), context=ctx)
    assert [i.text for i in proposal.theorems] == ["教材定理：集合元素的确定性"]
    assert [i.text for i in proposal.reflection] == ["关注学生对确定性的理解"]
    assert "reflection_no_basis" not in proposal.warnings_ack
