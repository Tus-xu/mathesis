"""R14 BE-KERNEL 内核单测（W1.2/W1.3/W2.3 + prompt 强化 W2.2）。

覆盖任务书判据：
- W1.2：`textbook/view` 的 `source_title` 无重复段（复用 options.label_of 去重口径）；
- W1.3：`LessonStage.summary` / `LessonPlanStage.summary` additive（旧 body 无 summary
  反序列化不报错；新字段可写可读）；
- W2.2：蓝图 prompt 强化（推导桶数量下限 / summary 陈述句 / items 描述性 /
  hints+board_notes 非空 / 严格重试指令常量在册）；
- W2.3：投影增厚——`_slide_elements` 每内容页 ≥2 项、概念页 definition+bullets、
  椭圆页 figure+knowledge_card+formula；`_stage_columns.design_intent` hints→summary
  兜底；`_board_design` 三栏来源补强；`summary` 透传 LessonPlanStage。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.teacher_prep.blueprint.prompt import (
    STRICT_RETRY_INSTRUCTION,
    build_blueprint_prompt,
    build_compact_blueprint_prompt,
)
from app.teacher_prep.blueprint.schema import LessonStage
from app.teacher_prep.specs.lesson_plan_schema import LessonPlanStage, LessonPlanSpec
from app.teacher_prep.specs.projector import (
    ProjectionRunLineage,
    _board_design,
    _slide_elements,
    _stage_columns,
    project_lesson_plan_spec,
)
from app.teacher_prep.conversation.node_ops import resolve_textbook_ref

from .tp_context_fixtures import LESSON1, SRC, build, seed_synthetic_textbook, selection

# ── W1.2 教材展示名去重 ──────────────────────────────────────────────────────


async def test_textbook_view_source_title_no_duplicate_segments(db) -> None:
    """W1.2：resolve_textbook_ref 的 source_title 逐段无重复（label_of 口径）。"""
    await seed_synthetic_textbook(db)
    ref = f"textbook_v2/locator/v1/source/{SRC}/section/{LESSON1}"
    data = await resolve_textbook_ref(db, ref=ref)
    title = data["source_title"]
    parts = [p.strip() for p in title.split("·")]
    assert len(parts) == len(set(parts)), f"展示名出现重复段：{title}"
    # label_of(publisher, edition, grade, volume) 确定性口径
    assert title == "合成出版社 · 2019 · 高一 · 必修第一册"


# ── W1.3 契约 additive ───────────────────────────────────────────────────────


def test_lesson_stage_legacy_body_without_summary() -> None:
    """旧 body（无 summary）反序列化不报错；summary 默认 None。"""
    stage = LessonStage.model_validate(
        {"stage": "new_content", "title": "椭圆的定义", "items": []}
    )
    assert stage.summary is None


def test_lesson_stage_with_summary() -> None:
    """新字段可写可读（陈述句摘要）；strip 后空串归一为 None。"""
    stage = LessonStage.model_validate(
        {
            "stage": "new_content",
            "title": "椭圆的定义",
            "summary": "从现象到数学语言，给出椭圆的定义并理解其几何意义",
            "items": [],
        }
    )
    assert stage.summary and "椭圆的定义" in stage.summary
    empty = LessonStage.model_validate(
        {"stage": "new_content", "title": "t", "summary": "  ", "items": []}
    )
    assert empty.summary is None


def test_lesson_plan_stage_summary_additive() -> None:
    """LessonPlanStage.summary 与蓝图对齐：旧数据默认 None，新数据透传。"""
    legacy = LessonPlanStage.model_validate(
        {"stage": "introduction", "title": "导入", "items": []}
    )
    assert legacy.summary is None
    modern = LessonPlanStage.model_validate(
        {
            "stage": "introduction",
            "title": "导入",
            "summary": "以生活情境引出课题",
            "items": [],
        }
    )
    assert modern.summary == "以生活情境引出课题"


# ── W2.2 prompt 强化（推导桶质量指令在册）────────────────────────────────────


async def test_blueprint_prompt_rules_present(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    prompt = build_blueprint_prompt(ctx).prompt
    # 数量下限（FROZEN §B.2.3）
    assert "objectives ≥2" in prompt and "key_points ≥2" in prompt and "difficult_points ≥1" in prompt
    # 占位比例红线（§B.2.4）
    assert "<20%" in prompt
    # summary 陈述句 + 禁疑问句
    assert "陈述句" in prompt and "禁止疑问句" in prompt
    # items 描述性教学语言
    assert "描述性教学语言" in prompt
    # board_notes / hints 非空
    assert "board_notes（板书要点 ≥1 条" in prompt
    assert "hints（教学提示 ≥1 条" in prompt
    # schema 示例带 summary 字段
    assert '"summary":"...（陈述句）"' in prompt


def test_strict_retry_instruction_constant() -> None:
    """W2.1 第③段的严格重试指令常量在册且约束输出形态。"""
    assert "合法 JSON" in STRICT_RETRY_INSTRUCTION
    assert "重试指令" in STRICT_RETRY_INSTRUCTION
    assert "禁止编造" in STRICT_RETRY_INSTRUCTION  # 事实边界铁律不变


async def test_compact_prompt_still_partitions(db) -> None:
    """规则 4 替换分区未破坏：紧凑契约仍替换原第 4 条且输出契约在末尾。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    result = build_compact_blueprint_prompt(ctx)
    assert "【本次输出契约" in result.prompt
    assert "flow_items" in result.prompt


# ── W2.3 投影增厚 ────────────────────────────────────────────────────────────

_ELLIPSE_MO = {"kind": "ellipse", "params": {"a": 5.0, "b": 3.0}}


def test_slide_elements_concept_page_uses_bullets_not_definition() -> None:
    """W1-A 角色语义：**非定义页**用 bullets，不得把普通正文塞进定义带（LEAD-BAR §4.2）。"""
    elements = _slide_elements(
        slide_type="concept",
        title="探究椭圆的几何性质",
        items=["探究椭圆的范围", "探究椭圆的对称性"],
        math_object=None,
        note_text="明确本页探究目标",
    )
    roles = [e["role"] for e in elements]
    assert "definition" not in roles, "concept 页不是定义页，不得用 definition 角色"
    assert roles == ["bullets"]
    assert len(elements[0]["items"]) == 2


def test_slide_elements_definition_page_uses_definition_band() -> None:
    """真正的定义页（slide_type=definition）才用 definition 定义带。"""
    elements = _slide_elements(
        slide_type="definition",
        title="核心概念",
        items=["平面内与两个定点距离之和为常数的点的轨迹叫做椭圆", "焦点与焦距"],
        math_object=None,
        note_text=None,
    )
    roles = [e["role"] for e in elements]
    assert roles[0] == "definition"
    assert "bullets" in roles


def test_slide_elements_overview_uses_bullets() -> None:
    """学习目标/概览页用 bullets（修复「学习目标被渲染成定义带」的语义错误）。"""
    elements = _slide_elements(
        slide_type="overview",
        title="学习目标",
        items=["掌握椭圆的定义", "理解标准方程"],
        math_object=None,
        note_text="明确学习目标",
    )
    assert [e["role"] for e in elements] == ["bullets"]


def test_slide_elements_single_item_page_note_is_real_content() -> None:
    """单条目内容页：bullets + note（note 用**真实环节内容**，不是标题标签），≥2 项。"""
    elements = _slide_elements(
        slide_type="practice",
        title="练一练",
        items=["用列举法表示集合 A"],
        math_object=None,
        note_text="通过练习巩固列举法表示集合",
    )
    roles = [e["role"] for e in elements]
    assert roles == ["bullets", "note"]
    assert elements[1]["text"] == "通过练习巩固列举法表示集合"


def test_slide_elements_note_absent_when_already_two_items() -> None:
    """已有 ≥2 条真实内容项时**不加** note（避免无意义重复标签）。"""
    elements = _slide_elements(
        slide_type="practice",
        title="练一练",
        items=["用列举法表示集合 A", "用描述法表示集合 B"],
        math_object=None,
        note_text="巩固练习",
    )
    assert [e["role"] for e in elements] == ["bullets"]


def test_slide_elements_ellipse_page_figure_card_formula() -> None:
    """目标页（承载图形）：figure + knowledge_card + formula（canonical 同源）。"""
    elements = _slide_elements(
        slide_type="concept",
        title="椭圆的标准方程",
        items=["平面内与两个定点的距离之和为定值的轨迹叫做椭圆", "观察方程的结构特征"],
        math_object=_ELLIPSE_MO,
        note_text="建立椭圆的标准方程",
    )
    roles = [e["role"] for e in elements]
    assert "figure" in roles and "knowledge_card" in roles and "formula" in roles
    card = next(e for e in elements if e["role"] == "knowledge_card")
    assert "a > b > 0" in (card["latex"] or "")
    # canonical 与图形同源；教材公式条目不重复
    formulas = [e["latex"] for e in elements if e["role"] == "formula"]
    assert len(formulas) == len(set(formulas))


def test_slide_elements_steps_page() -> None:
    """推导页：steps 承载全部正文；正文已 ≥2 项 → 不再追加 note 标签。"""
    elements = _slide_elements(
        slide_type="derivation",
        title="推导与论证",
        items=["建立坐标系", "写出距离方程"],
        math_object=None,
        note_text="经历推导过程",
    )
    roles = [e["role"] for e in elements]
    assert roles == ["steps"]
    assert len(elements[0]["items"]) == 2


def test_slide_elements_formula_item_projects_formula_role() -> None:
    """整条 `$...$` 的条目 → formula 元素（确定性投影）。"""
    elements = _slide_elements(
        slide_type="concept",
        title="公式页",
        items=[r"$\frac{x^2}{a^2} + \frac{y^2}{b^2} = 1$"],
        math_object=None,
        note_text="标准方程",
    )
    roles = [e["role"] for e in elements]
    assert "formula" in roles and "definition" not in roles
    assert len(elements) >= 2


def test_stage_columns_design_intent_summary_fallback() -> None:
    """design_intent 三级兜底：hints → 环节 summary → 蓝图级 design_intent（L4 根治）。"""
    stage_with_hints = {
        "items": [{"text": "讲解定义"}],
        "hints": [{"text": "借助几何画板动态演示"}],
        "summary": "给出椭圆定义",
    }
    cols = _stage_columns(stage_with_hints)
    assert [i.text for i in cols.design_intent] == ["借助几何画板动态演示"]

    stage_no_hints = {
        "items": [{"text": "讲解定义"}],
        "hints": [],
        "summary": "给出椭圆定义并理解其几何意义",
    }
    cols2 = _stage_columns(stage_no_hints)
    assert [i.text for i in cols2.design_intent] == ["给出椭圆定义并理解其几何意义"]

    stage_no_hints_no_summary = {"items": [{"text": "讲解定义"}], "hints": []}
    plan_intents = [{"text": "通过探究活动建立推导思路", "source_ref": None}]
    cols3 = _stage_columns(stage_no_hints_no_summary, plan_intents=plan_intents)
    assert [i.text for i in cols3.design_intent] == ["通过探究活动建立推导思路"]


def test_board_design_three_columns_sourced_from_facts() -> None:
    """板书三栏来源补强：board_notes→concepts→key_points 退化链；公式区 theorems+
    概念公式条目+canonical，去重保序（§F.4 确定性投影，非 LLM 创作）。"""
    bp = {
        "lesson_flow": [
            {
                "title": "探究",
                "board_notes": [],
                "items": [],
            }
        ],
        "concepts": [],
        "theorems": [],
        "key_points": [{"text": "椭圆的定义", "source_ref": None}],
        "objectives": [{"text": "掌握标准方程", "source_ref": None}],
    }
    design = _board_design(bp, _ELLIPSE_MO)
    assert [i.text for i in design.text_points] == ["椭圆的定义"]
    joined = " ".join(i.text for i in design.formulas)
    assert r"\frac{x^2}{a^2}" in joined and r"c = \sqrt{a^2 - b^2}" in joined
    assert design.math_object is not None


def test_board_design_formula_only_concepts_included() -> None:
    """概念桶中**整条** `$...$` 的公式型条目进公式区（确定性投影）；文字概念退化为
    text_points（W2.3 概念桶兜底）。"""
    bp = {
        "lesson_flow": [],
        "concepts": [
            {"text": r"$\frac{x^2}{a^2}+\frac{y^2}{b^2}=1$", "source_ref": None},
            {"text": "普通概念文字（不进公式区）", "source_ref": None},
        ],
        "theorems": [],
    }
    design = _board_design(bp, None)
    # 整条公式型概念以 $...$ 文本进公式区
    assert any("frac" in i.text for i in design.formulas)
    # 文字概念退化为 text_points（W2.3 概念桶兜底）
    assert any("普通概念文字" in i.text for i in design.text_points)


def _project_lp(ctx, body: dict) -> LessonPlanSpec:
    return project_lesson_plan_spec(
        spec_id=str(uuid.uuid4()),
        blueprint_id=str(uuid.uuid4()),
        blueprint_revision=2,
        blueprint_body=body,
        context=ctx,
        profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="teacher-prep-specs-v1"),
        run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


def _ellipse_body(ctx) -> dict:
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "3.1.2 椭圆的简单几何性质",
        "objectives": [{"text": "掌握椭圆的范围与对称性", "source_ref": None}],
        "prior_knowledge": [],
        "concepts": [{"text": "椭圆的焦点与标准方程", "source_ref": None}],
        "theorems": [],
        "examples": [],
        "inquiries": [{"text": "观察椭圆 $\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1$ 的扁平程度", "source_ref": None}],
        "key_points": [{"text": "椭圆的定义", "source_ref": None}],
        "difficult_points": [{"text": "标准方程的推导", "source_ref": None}],
        "homework": [],
        "reflection": [],
        "lesson_flow": [
            {
                "stage": "new_content",
                "title": "探究椭圆的几何性质",
                "summary": "结合图形归纳椭圆的范围与对称性",
                "duration_minutes": 10,
                "items": [{"text": "探究椭圆的范围", "source_ref": None}],
                "student_activities": [{"text": "动手操作", "source_ref": None}],
                "hints": [],
            },
        ],
        "warnings_ack": [],
    }


async def test_lesson_plan_stage_summary_passthrough_and_design_intent(db) -> None:
    """summary 透传 LessonPlanStage；hints 空时过程表设计意图由 summary 兜底（非空）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    lp = _project_lp(ctx, _ellipse_body(ctx))
    stage = lp.lesson_flow[0]
    assert stage.summary == "结合图形归纳椭圆的范围与对称性"
    assert stage.columns.design_intent, "设计意图列非空（summary 兜底）"
    assert stage.columns.design_intent[0].text == "结合图形归纳椭圆的范围与对称性"
