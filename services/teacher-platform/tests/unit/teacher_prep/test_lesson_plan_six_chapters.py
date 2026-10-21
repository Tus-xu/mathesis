"""LessonPlanSpec 六章扩展投影测试（FROZEN-CONTRACT §F）。

覆盖验收：
- §F.1 六章字段 additive 可选默认空（旧 body 完全兼容）；
- §F.2 过程表三列内容列 + 行数与 lesson_flow 一一对应（KERNEL-RUBRIC §2.2）；
- §F.3 板书三栏（文字要点 / 数学图形 / 公式区）；math_object 与 PPT **同源**；
- §F.4 反幻觉：board_design text_points/formulas **确定性投影**（board_notes / 教材定理，
  非 LLM 创作）；homework/reflection 无依据留空 + warning；标题零「（待确认）」。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.teacher_prep.specs.lesson_plan_schema import (
    BoardDesign,
    LessonPlanSpec,
    PlanItem,
    StageColumns,
)
from app.teacher_prep.specs.projector import (
    ProjectionRunLineage,
    project_deck_spec,
    project_lesson_plan_spec,
)

from .tp_context_fixtures import build, seed_synthetic_textbook, selection


def _lineage() -> ProjectionRunLineage:
    return ProjectionRunLineage(model="teacher-prep-specs-v1")


def _body(ctx, **over) -> dict:
    body: dict = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "3.1.2 椭圆的简单几何性质（第一课时，待确认）",
        "objectives": [{"text": "掌握椭圆的定义与标准方程", "source_ref": None}],
        "prior_knowledge": [],
        "concepts": [{"text": "椭圆的焦点与标准方程", "source_ref": None}],
        "theorems": [],
        "examples": [],
        "inquiries": [
            {"text": "观察椭圆 $\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1$ 的扁平程度", "source_ref": None}
        ],
        "key_points": [{"text": "椭圆的定义", "source_ref": None}],
        "difficult_points": [{"text": "标准方程的推导", "source_ref": None}],
        "homework": [{"text": "完成课本练习第 2、3 题", "source_ref": None}],
        "reflection": [{"text": "关注学生对焦点概念的理解", "source_ref": None}],
        "lesson_flow": [
            {
                "stage": "introduction",
                "title": "生活情境",
                "duration_minutes": 4,
                "items": [{"text": "从天文轨道引入椭圆", "source_ref": None}],
                "student_activities": [{"text": "观察现象", "source_ref": None}],
                "board_notes": [{"text": "椭圆的定义", "source_ref": None}],
                "hints": [{"text": "联系生活激发兴趣", "source_ref": None}],
            },
            {
                "stage": "new_content",
                "title": "探究椭圆的几何性质",
                "duration_minutes": 10,
                "items": [
                    {"text": "探究椭圆的范围", "source_ref": None},
                    {"text": "探究椭圆的对称性与焦点 F_1、F_2 的位置", "source_ref": None},
                ],
                "student_activities": [{"text": "动手操作", "source_ref": None}],
                "hints": [{"text": "发展数学抽象素养", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }
    body.update(over)
    return body


def _project_lp(ctx, body: dict) -> LessonPlanSpec:
    return project_lesson_plan_spec(
        spec_id=str(uuid.uuid4()),
        blueprint_id=str(uuid.uuid4()),
        blueprint_revision=2,
        blueprint_body=body,
        context=ctx,
        profile_ref={"profile_id": "x"},
        lineage=_lineage(),
        run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


# ── 1. 向后兼容：additive 可选默认空 ─────────────────────────────────────────


def test_new_chapters_default_empty_on_legacy_body() -> None:
    """不含新字段的旧 body → LessonPlanSpec 读取默认空（不破坏既有读取方）。"""
    base = {
        "schema_version": "1.0.0",
        "id": str(uuid.uuid4()),
        "revision": 1,
        "purpose": "lesson_plan",
        "metadata": {"title": "旧教案"},
        "blueprint_ref": {"blueprint_id": str(uuid.uuid4()), "revision": 1},
        "context_ref": {
            "context_snapshot_hash": "a" * 64,
            "source_id": str(uuid.uuid4()),
            "section_id": str(uuid.uuid4()),
            "locator": "textbook_v2/locator/v1/source/x/section/y/chunk/z",
        },
        "lesson_flow": [
            {"stage": "introduction", "title": "导入", "duration_minutes": 5, "items": []}
        ],
        "projection": {
            "projector": "teacher_prep.specs.v5",
            "approved_blueprint_id": str(uuid.uuid4()),
            "approved_blueprint_revision": 1,
            "context_snapshot_hash": "a" * 64,
            "run_hash": "b" * 64,
            "body_hash": "c" * 64,
            "projected_at": "2026-09-20T00:00:00Z",
        },
    }
    spec = LessonPlanSpec.model_validate(base)
    assert spec.board_design == BoardDesign()
    assert spec.board_design.text_points == [] and spec.board_design.math_object is None
    assert spec.homework == [] and spec.reflection == []
    assert spec.lesson_flow[0].columns == StageColumns()


# ── 2. 六章投影产出 ──────────────────────────────────────────────────────────


async def test_six_chapters_projected_non_empty(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    lp = _project_lp(ctx, _body(ctx))

    assert [o.text for o in lp.objectives] == ["掌握椭圆的定义与标准方程"]
    # ② 重难点（小胶囊 kind）
    kinds = [p.kind for p in lp.key_difficult_points]
    assert kinds.count("key") == 1 and kinds.count("difficult") == 1
    # ⑤ 作业 / ⑥ 反思（LLM 推导桶，来自已确认 body）
    assert [h.text for h in lp.homework] == ["完成课本练习第 2、3 题"]
    assert [r.text for r in lp.reflection] == ["关注学生对焦点概念的理解"]
    # ④ 板书三栏
    assert lp.board_design.text_points  # 左列：board_notes
    assert lp.board_design.math_object is not None  # 中列：与 PPT 同源椭圆
    assert lp.board_design.formulas  # 右列：canonical 公式


# ── 3. §F.2 过程表：行数一一对应 + 三列内容 ──────────────────────────────────


async def test_lesson_flow_rows_match_blueprint_nodes(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    lp = _project_lp(ctx, body)
    assert len(lp.lesson_flow) == len(body["lesson_flow"])
    for stage in lp.lesson_flow:
        assert stage.columns.teacher_activity, "教师活动列非空"
    assert lp.lesson_flow[0].columns.student_activity[0].text == "观察现象"
    assert lp.lesson_flow[0].columns.design_intent[0].text == "联系生活激发兴趣"


async def test_duration_under_budget_no_warning(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    lp = _project_lp(ctx, _body(ctx))
    assert sum(s.duration_minutes or 0 for s in lp.lesson_flow) == 14
    assert not any(w.startswith("lesson_flow_over_time_budget") for w in lp.warnings)


async def test_over_time_budget_recorded(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    body["lesson_flow"] = [
        {"stage": "new_content", "title": "长环节", "duration_minutes": 30, "items": []},
        {"stage": "new_content", "title": "另一个长环节", "duration_minutes": 30, "items": []},
    ]
    lp = _project_lp(ctx, body)
    assert any(w.startswith("lesson_flow_over_time_budget:60>45") for w in lp.warnings)


# ── 4. §F.3/§F.4 板书：确定性来源 + 与 PPT 同源 ──────────────────────────────


async def test_board_design_text_points_come_from_board_notes(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    body["concepts"] = [{"text": "教材概念（不应覆盖 board_notes）", "source_ref": None}]
    lp = _project_lp(ctx, body)
    texts = [p.text for p in lp.board_design.text_points]
    assert texts == ["椭圆的定义"], f"text_points 必须确定性来自 board_notes，实际 {texts}"


async def test_board_design_text_points_fallback_to_concepts(db) -> None:
    """无 board_notes 时退化为教材事实 concepts（仍是真实事实，非 LLM 创作）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    for stage in body["lesson_flow"]:
        stage["board_notes"] = []
    lp = _project_lp(ctx, body)
    assert [p.text for p in lp.board_design.text_points] == ["椭圆的焦点与标准方程"]


async def test_board_design_math_object_same_source_as_deck(db) -> None:
    """§F.4：board_design.math_object 与 PPT **同源**（同参数、同 kind）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    bid = str(uuid.uuid4())
    lp = project_lesson_plan_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=bid, blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="c" * 64, projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    deck = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=bid, blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="c" * 64, projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    deck_mo = next((s.math_object for s in deck.slides if s.math_object is not None), None)
    assert deck_mo is not None
    assert lp.board_design.math_object is not None
    assert lp.board_design.math_object.kind == deck_mo.kind == "ellipse"
    assert lp.board_design.math_object.params == deck_mo.params
    # c = √(a²−b²) 由服务端派生（不落库）
    assert lp.board_design.math_object.focal_distance == pytest.approx(4.0)


async def test_board_design_formulas_include_canonical_ellipse(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    lp = _project_lp(ctx, _body(ctx))
    joined = " ".join(p.text for p in lp.board_design.formulas)
    assert r"\frac{x^2}{a^2}" in joined and r"c = \sqrt{a^2 - b^2}" in joined


# ── 5. §F.4 反幻觉：homework/reflection 无依据留空 + warning ─────────────────


async def test_homework_reflection_empty_when_no_basis(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx, homework=[], reflection=[])
    lp = _project_lp(ctx, body)
    assert lp.homework == [] and lp.reflection == []
    assert "homework_no_basis" in lp.warnings
    assert "reflection_no_basis" in lp.warnings


# ── 6. §B.3 标题清理贯通到 metadata.title（零「（待确认）」）─────────────────


async def test_placeholder_suffix_stripped_from_titles(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)  # title 带「（第一课时，待确认）」
    lp = _project_lp(ctx, body)
    deck = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="c" * 64, projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert "待确认" not in lp.metadata.title
    assert "待确认" not in deck.metadata.title
    assert lp.metadata.title == "3.1.2 椭圆的简单几何性质"
    assert deck.metadata.title == "3.1.2 椭圆的简单几何性质"


# ── 7. PlanItem.kind 枚举 ────────────────────────────────────────────────────


def test_plan_item_kind_enum() -> None:
    assert PlanItem(text="x", kind="key").kind == "key"
    assert PlanItem(text="x", kind="difficult").kind == "difficult"
    assert PlanItem(text="x").kind is None
    with pytest.raises(ValidationError):
        PlanItem(text="x", kind="hard")
