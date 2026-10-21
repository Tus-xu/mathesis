"""G6 产物复审回炉测试（deck 专属；教案不受影响）：

- 「推导与论证」页：theorems 全为「本节结论：X」标题回声时，点斜式课确定性组装
  真实推导步骤（设点 → 斜率公式 → 整理得点斜式 → 对应关系 → 适用条件/斜截式）；
  回声句不再上推导页；source_refs 保留定理桶溯源；
- 真实定理句（非回声）原样上推导页（诚实投影，不组装覆盖）；
- deck 逐页确定性去重：可见行 vs 同页公式元素归一化重复 → 删行留公式卡；
  归一化完全重复行删除；探究环节「同题两写」inquiry 回声跳过；
  收尾页「小结：」模板回声剔除；
- line_figure 图形：同参数字节恒定（确定性）、对象键版本化（v2 写入 + v1 可解析）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.teacher_prep.specs import math_figures
from app.teacher_prep.specs.projector import ProjectionRunLineage, project_deck_spec

from .tp_context_fixtures import build, seed_synthetic_textbook, selection


def _body(ctx) -> dict:
    """点斜式第一课时蓝图（形态对齐真实会话 69328202 的 approved blueprint）。"""
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "1.2.1 直线的点斜式方程 第一课时",
        "objectives": [
            {"text": "理解直线点斜式方程的推导过程，明确其形式与适用条件", "source_ref": None},
            {"text": "能运用点斜式、斜截式方程求解直线方程，解决相关问题", "source_ref": None},
        ],
        "prior_knowledge": [{"text": "直线斜率的定义与计算公式，斜率的几何意义", "source_ref": None}],
        "concepts": [
            {"text": "直线的点斜式方程", "source_ref": "locator/chunk/a"},
            {"text": "直线的斜截式方程", "source_ref": "locator/chunk/b"},
        ],
        "theorems": [
            {"text": "本节结论：直线点斜式方程的推导过程与形式", "source_ref": "locator/chunk/c"},
            {"text": "本节结论：斜截式方程的形式与截距的几何意义", "source_ref": "locator/chunk/d"},
            {"text": "本节结论：直线的点斜式方程", "source_ref": "locator/chunk/e"},
        ],
        "examples": [],
        "inquiries": [
            {"text": "探究直线y=kx+2的共同特点", "source_ref": None},
            {"text": "思考两点式方程变形的几何意义", "source_ref": None},
        ],
        "key_points": [
            {"text": "直线点斜式方程的推导过程与形式", "source_ref": "locator/chunk/c"},
            {"text": "斜截式方程的形式与截距的几何意义", "source_ref": "locator/chunk/d"},
        ],
        "difficult_points": [{"text": "理解点斜式方程的适用条件与直线方程的意义", "source_ref": None}],
        "lesson_flow": [
            {
                "stage": "introduction",
                "title": "课程引入",
                "duration_minutes": 5,
                "items": [{"text": "结合已有斜率知识，引出本节课对直线方程的学习", "source_ref": None}],
                "board_notes": [{"text": r"直线斜率公式：$k = \frac{y_2 - y_1}{x_2 - x_1}$", "source_ref": None}],
            },
            {
                "stage": "new_content",
                "title": "点斜式方程推导",
                "duration_minutes": 20,
                "items": [{"text": "结合教材开篇问题，一步步推导点斜式方程", "source_ref": None}],
                "board_notes": [
                    {"text": r"直线点斜式方程：$y - y_1 = k(x - x_1)$", "source_ref": None},
                    {"text": "适用条件：直线斜率存在", "source_ref": None},
                ],
            },
            {
                "stage": "new_content",
                "title": "探究与思考",
                "duration_minutes": 10,
                "items": [{"text": "组织学生讨论教材探究和思考问题，总结结论", "source_ref": None}],
                "board_notes": [
                    {"text": r"斜截式方程：$y = kx + b$", "source_ref": None},
                    {"text": r"$b$是直线在$y$轴上的截距", "source_ref": None},
                ],
                "teacher_questions": [
                    {"text": r"根据作图结果，直线$y = kx + 2$有什么共同特点？", "source_ref": None},
                ],
            },
            {
                "stage": "summary",
                "title": "课堂小结",
                "duration_minutes": 5,
                "items": [{"text": "师生共同梳理本节课知识点，总结重点内容", "source_ref": None}],
                "board_notes": [
                    {"text": "小结：点斜式、斜截式的形式与适用条件", "source_ref": None},
                    {"text": "课后作业：教材习题1.2第1-5题", "source_ref": None},
                ],
            },
        ],
        "warnings_ack": [],
    }


def _project(ctx, body: dict):
    return project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="m"), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 27, tzinfo=UTC),
    )


def _slide_items(slide) -> list[str]:
    out: list[str] = []
    for e in slide.elements:
        out.extend(e.items or [])
    return out


async def test_derivation_page_assembles_real_steps_from_echo_theorems(db) -> None:
    """theorems 全为标题回声 + 点斜式课 → 推导页是真实推导步骤，回声句不上版。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    page = next(s for s in deck.slides if s.title == "推导与论证")
    items = _slide_items(page)
    # ≥3 个真实推导步骤：设点 → 斜率公式 → 整理得点斜式（任务裁决口径的标准流程）
    assert len(items) >= 3
    joined = "\n".join(items)
    assert "设点 P(x, y)" in joined
    assert "k = (y - y₁)/(x - x₁)" in joined
    assert "y - y₁ = k(x - x₁)" in joined
    assert "点斜式方程" in joined
    # 回声句零上版
    assert "直线点斜式方程的推导过程与形式" not in joined
    assert "斜截式方程的形式与截距的几何意义" not in joined
    # source_refs 保留定理桶溯源（确定性组装也要可回溯）
    assert page.source_refs


async def test_derivation_page_keeps_real_theorems(db) -> None:
    """真实定理句（非回声）原样上推导页——不组装、不改写。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    real = "过直线上的定点和斜率可以确定直线的点斜式方程，直线上点的坐标都满足方程"
    body["theorems"] = [{"text": real, "source_ref": "locator/chunk/z"}]
    deck = _project(ctx, body)
    page = next(s for s in deck.slides if s.title == "推导与论证")
    items = _slide_items(page)
    assert items and all("设点 P(x, y)" not in it for it in items)
    assert any("直线上点的坐标都满足方程" in it for it in items)


async def test_labeled_formula_line_not_duplicated(db) -> None:
    """「标签：$…$」的板书行：公式元素保留展示，同名可见行不再重复出现。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    intro = next(s for s in deck.slides if s.title == "课程引入")
    items = _slide_items(intro)
    assert not any("直线斜率公式" in it and "=" in it for it in items)
    formulas = [e for e in intro.elements if e.role == "formula"]
    assert any("y_2" in (f.latex or "") for f in formulas)
    # 推导页（image-focus）：点斜式公式卡保留，「直线点斜式方程：y-y₁=…」重复行删除
    derive = next(s for s in deck.slides if s.title == "点斜式方程推导")
    assert not any(
        "直线点斜式方程" in it and "y - y₁" in it for it in _slide_items(derive)
    )
    assert any(e.role == "formula" and "y - y_1" in (e.latex or "") for e in derive.elements)


async def test_inquiry_question_not_written_twice(db) -> None:
    """探究页「共同特点」一问两写：教师提问与 inquiry 回声只保留一条。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    page = next(s for s in deck.slides if s.title == "探究与思考")
    items = _slide_items(page)
    assert sum(1 for it in items if "共同特点" in it) == 1
    assert sum(1 for it in items if "y = kx + 2" in it or "y=kx+2" in it) >= 1


async def test_closing_page_no_formula_dup_and_no_summary_echo(db) -> None:
    """收尾页：公式卡不再以同名行重复；「小结：」回声行剔除；作业行保留。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    page = next(s for s in deck.slides if s.title == "课堂小结")
    formulas = [e for e in page.elements if e.role == "formula" and e.latex]
    assert formulas
    import re

    def _norm(t: str) -> str:
        return re.sub(r"\s+", "", t)

    formula_norms = {_norm(f.latex) for f in formulas}
    items = _slide_items(page)
    # 归一化后行与公式同形 → 删行留公式卡
    for it in items:
        assert _norm(it) not in formula_norms
    # 「小结：点斜式、斜截式的形式与适用条件」游离条目不出现
    assert not any("点斜式、斜截式的形式与适用条件" in it for it in items)
    # 作业行保留（板书作业行归位）
    assert any("教材习题1.2第1-5题" in it for it in items)
    # 归一化后页内可见行零完全重复
    norms = [_norm(it) for it in items]
    assert len(norms) == len(set(norms))


async def test_line_figure_deterministic_and_key_versioned(db) -> None:
    """直线图：同参数 → 同字节（确定性）；对象键 v2 写入、v1 历史键仍可解析。"""
    params = {"k": -3.0, "x0": 1.0, "y0": 2.0}
    first = math_figures.line_figure(params)
    second = math_figures.line_figure(dict(params))
    assert first == second and first[:8] == b"\x89PNG\r\n\x1a\n"
    key = math_figures.object_key_for("line", params)
    assert key.startswith("math-figures/v5/")
    parsed = math_figures.params_from_object_key("math-figures/v1/line__k_-3__x0_1__y0_2.png")
    assert parsed == ("line", params)
    assert math_figures.params_from_object_key(key) == ("line", params)


async def test_line_figure_renders_for_both_slope_signs(db) -> None:
    """k<0 / k>0 都能出图（标注避让排版对两种符号均成立），不抛异常。"""
    assert math_figures.line_figure({"k": -3.0, "x0": 1.0, "y0": 2.0})[:8] == b"\x89PNG\r\n\x1a\n"
    assert math_figures.line_figure({"k": 2.0, "x0": -1.0, "y0": 3.0})[:8] == b"\x89PNG\r\n\x1a\n"
    assert math_figures.line_figure({"k": 0.0, "x0": 1.0, "y0": 2.0})[:8] == b"\x89PNG\r\n\x1a\n"
