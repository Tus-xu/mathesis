"""二次函数（抛物线）课件图形测试：MATH_RENDER 资产通道补图（v16）。

背景（实测）：新课件「2.3 二次函数」12 页 spec 全部 assets=[]——math_figures 只有
line/line_family/ellipse 三种 kind，二次函数无分支。修复后（契约约束下）：
- **不挂 math_object**：MathObjectSpec.kind 契约锁死（8200 deck.py 只收 ellipse）→
  二次函数只走纯 MATH_RENDER 资产通道（同 line/line_family）；
- 系数由蓝图真实方程 y=ax²+bx+c **确定性**解析（缺项补 0、支持分式系数；
  解析不出 → 课堂基线 y=x²，a=1, b=0, c=0）；
- 附图去掉「整册一张」限制 → 按页内容相关性附图：每页至多 1 张、相邻连续带图
  ≤2 页（8200 视觉族「同族连续 ≤2」硬门）、deck 级按 id 去重；
- 直线/椭圆旧路径零回归。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.teacher_prep.specs import math_figures
from app.teacher_prep.specs.projector import (
    ProjectionRunLineage,
    _quadratic_math_for_blueprint,
    project_deck_spec,
)

from .tp_context_fixtures import build, seed_synthetic_textbook, selection

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _lineage() -> ProjectionRunLineage:
    return ProjectionRunLineage(model="teacher-prep-specs-v1")


def _body(ctx) -> dict:
    """「2.3 二次函数」形态蓝图（对齐真实会话的二次函数第一课时）。"""
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "2.3 二次函数的图象与性质",
        "objectives": [
            {"text": "理解二次函数 y=x²-2x-3 的图象是抛物线，能说出开口方向、对称轴与顶点坐标", "source_ref": None},
            {"text": "能用配方法把二次函数化为 y=(x-1)²-4 的形式并求顶点", "source_ref": None},
        ],
        "prior_knowledge": [{"text": "一次函数图象的作法与平移", "source_ref": None}],
        "concepts": [
            {"text": "二次函数 y=ax²+bx+c 的图象是抛物线", "source_ref": "locator/chunk/a"},
            {"text": "抛物线的顶点、对称轴与开口方向", "source_ref": "locator/chunk/b"},
        ],
        "theorems": [
            {"text": "本节结论：抛物线 y=x²-2x-3 的顶点坐标是 (1, -4)，对称轴是 x=1，开口向上", "source_ref": "locator/chunk/c"},
        ],
        "examples": [
            {
                "text": "例：求二次函数 y=x²-2x-3 的顶点坐标与对称轴，并说明开口方向",
                "solution": "解：配方得 y=(x-1)²-4\n顶点坐标 (1, -4)，对称轴 x=1，开口向上",
                "source_ref": None,
            },
        ],
        "inquiries": [
            {"text": "观察抛物线 y=x²-2x-3 与 x 轴交点的横坐标和方程 x²-2x-3=0 的根的关系", "source_ref": None},
        ],
        "key_points": [
            {"text": "抛物线的顶点坐标 (1, -4)、对称轴 x=1 与开口方向", "source_ref": "locator/chunk/d"},
        ],
        "difficult_points": [{"text": "理解抛物线与 x 轴交点横坐标和对应方程根的关系", "source_ref": None}],
        "lesson_flow": [
            {
                "stage": "introduction",
                "title": "情境引入",
                "duration_minutes": 5,
                "items": [{"text": "从投篮球飞行路径引入二次函数图象的研究", "source_ref": None}],
            },
            {
                "stage": "new_content",
                "title": "二次函数的图象与对称轴",
                "duration_minutes": 15,
                "items": [{"text": "用描点法画出 y=x²-2x-3 的图象，观察对称轴与顶点", "source_ref": None}],
                "board_notes": [{"text": "二次函数：$y = x^2 - 2x - 3$", "source_ref": None}],
            },
            {
                "stage": "new_content",
                "title": "抛物线的开口方向与顶点",
                "duration_minutes": 10,
                "items": [{"text": "结合 y=x²-2x-3 的图象归纳开口方向、顶点坐标与对称轴", "source_ref": None}],
                "board_notes": [{"text": "顶点 (1, -4)，对称轴 x=1，开口向上", "source_ref": None}],
            },
            {
                "stage": "summary",
                "title": "课堂小结",
                "duration_minutes": 5,
                "items": [{"text": "二次函数图象的开口、对称轴、顶点与增减性", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }


def _project(ctx, body: dict):
    return project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="c" * 64,
        projected_at=datetime(2026, 10, 4, tzinfo=UTC),
    )


# ── 系数确定性解析（纯函数；不触 db）──────────────────────────────────────────


def test_quadratic_coeffs_from_text_cases() -> None:
    """y=ax²+bx+c 数字系数逐式解析：缺项补 0、分式系数、符号归一；解析不出 → None。"""
    parse = math_figures.quadratic_coeffs_from_text
    assert parse(r"已知二次函数 $y = x^2 - 2x - 3$ 的图象") == {"a": 1.0, "b": -2.0, "c": -3.0}
    assert parse("抛物线 y=2x^2+3x+1") == {"a": 2.0, "b": 3.0, "c": 1.0}
    assert parse("y=-x^2+4x") == {"a": -1.0, "b": 4.0, "c": 0.0}
    assert parse(r"函数 $y = \frac{1}{2}x^2 + 2x + 1$") == {"a": 0.5, "b": 2.0, "c": 1.0}
    assert parse("y = −x² − 4") == {"a": -1.0, "b": 0.0, "c": -4.0}
    assert parse("y=x²") == {"a": 1.0, "b": 0.0, "c": 0.0}
    assert parse("y=x²+1/2") == {"a": 1.0, "b": 0.0, "c": 0.5}
    # symbolic 系数 / 非二次函数文本 / 零二次项 → None（不猜测）
    assert parse("y=ax^2+bx+c") is None
    assert parse("二次函数的图象与性质") is None
    assert parse("y=kx+b") is None
    assert parse("y=0x^2+2x+1") is None
    assert parse("") is None


def test_quadratic_figure_deterministic_and_key_versioned() -> None:
    """抛物线图：同参数 → 同字节（确定性）；对象键 v2 内容寻址且可回参重渲。"""
    params = {"a": 1.0, "b": -2.0, "c": -3.0}
    first = math_figures.quadratic_figure(params)
    second = math_figures.quadratic_figure(dict(params))
    assert first == second and first[:8] == PNG_MAGIC
    key = math_figures.object_key_for("quadratic", params)
    assert key == "math-figures/v5/quadratic__a_1__b_-2__c_-3.png"
    assert math_figures.params_from_object_key(key) == ("quadratic", params)
    # 资产字节按键重渲 + sha256 一致（跨进程确定性；导出链同口径）
    asset, data = math_figures.asset_for("quadratic", params)
    assert asset.kind == "MATH_RENDER" and data[:8] == PNG_MAGIC
    assert math_figures.asset_bytes(asset) == data


def test_quadratic_figure_covers_delta_and_baseline_cases() -> None:
    """Δ>0 / Δ=0 / Δ<0、开口向下、分式 a、课堂基线 y=x² 都能出图，不抛异常。"""
    assert math_figures.quadratic_figure({"a": 1.0, "b": -2.0, "c": -3.0})[:8] == PNG_MAGIC  # Δ=16
    assert math_figures.quadratic_figure({"a": 1.0, "b": 2.0, "c": 1.0})[:8] == PNG_MAGIC  # Δ=0 切点
    assert math_figures.quadratic_figure({"a": 1.0, "b": 0.0, "c": 1.0})[:8] == PNG_MAGIC  # Δ<0 无交点
    assert math_figures.quadratic_figure({"a": -0.5, "b": 2.0, "c": 1.0})[:8] == PNG_MAGIC  # 开口向下
    assert math_figures.quadratic_figure({"a": 0.5, "b": 2.0, "c": 1.0})[:8] == PNG_MAGIC  # 分式 a
    assert math_figures.quadratic_figure(dict(math_figures.QUADRATIC_BASELINE))[:8] == PNG_MAGIC
    # a=0 / 缺参：可见失败（绝不静默空图）
    import pytest

    with pytest.raises(ValueError):
        math_figures.quadratic_figure({"a": 0.0, "b": 2.0, "c": 1.0})
    with pytest.raises(ValueError):
        math_figures.quadratic_figure({"a": 1.0, "b": 2.0})


# ── 投影端到端：二次函数蓝图 → spec 页 assets 非空 ────────────────────────────


async def test_quadratic_deck_pages_get_figure_assets(db) -> None:
    """二次函数蓝图 → 相关 concept 页 assets 非空（PNG 真实可渲）；全程不挂 math_object。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))

    carrying = [s for s in deck.slides if s.assets]
    assert carrying, "二次函数课的图形相关页必须附抛物线资产（此前 12 页 assets=[] 的根因）"
    # 每页至多 1 张，且都是同一张 MATH_RENDER 抛物线（同参数内容寻址）
    assert all(len(s.assets) == 1 for s in carrying)
    assert len({s.assets[0] for s in carrying}) == 1
    asset = next(a for a in deck.assets if a.id == carrying[0].assets[0])
    assert asset.kind == "MATH_RENDER"
    assert asset.object_key.startswith("math-figures/v5/quadratic__")
    # 契约红线：二次函数不挂 math_object（MathObjectSpec.kind 锁死，挂了导出必 4xx）
    assert all(s.math_object is None for s in deck.slides)
    # 图形页走「图象观察」族（observe-and-infer 唯一候选 → 图形必进版面）
    assert all(s.type == "image-focus" for s in carrying)
    # PNG 真实存在且与资产 sha256 一致（不是占位字节）
    data = math_figures.asset_bytes(asset)
    assert data is not None and data[:8] == PNG_MAGIC


async def test_quadratic_coeffs_derived_from_blueprint_equation(db) -> None:
    """蓝图真实方程 y=x²-2x-3 → 资产键编码 a_1__b_-2__c_-3（同页公式 ↔ 图形参数同源）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    assert deck.assets
    assert deck.assets[0].object_key == "math-figures/v5/quadratic__a_1__b_-2__c_-3.png"
    parsed = math_figures.params_from_object_key(deck.assets[0].object_key)
    assert parsed == ("quadratic", {"a": 1.0, "b": -2.0, "c": -3.0})


async def test_quadratic_attach_bounded_two_consecutive_and_deduped(db) -> None:
    """附图页去「整册一张」限制但不越界：相邻连续带图 ≤2 页；deck 级按 id 去重。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    # 再加一个图形相关 concept 环节（若放任附图会形成连续 3 页 → 8200 视觉族规划无解）
    body["lesson_flow"].insert(
        3,
        {
            "stage": "new_content",
            "title": "图象的增减性与最值",
            "duration_minutes": 8,
            "items": [{"text": "结合抛物线 y=x²-2x-3 的图象归纳增减性与最值", "source_ref": None}],
            "board_notes": [{"text": "对称轴 x=1 左减右增，最小值 -4", "source_ref": None}],
        },
    )
    deck = _project(ctx, body)
    carrying_flags = [bool(s.assets) for s in deck.slides]
    assert any(carrying_flags), "图形相关页仍须附图"
    for i in range(2, len(carrying_flags)):
        assert not (carrying_flags[i] and carrying_flags[i - 1] and carrying_flags[i - 2]), (
            "相邻连续带图页必须 ≤2（8200 视觉族「同族连续 ≤2」硬门）"
        )
    assert len(deck.assets) == len({a.id for a in deck.assets}) == 1


async def test_quadratic_baseline_when_symbolic_only(db) -> None:
    """蓝图只有 symbolic 方程（y=ax²+bx+c）→ 退课堂基线 y=x²（a=1, b=0, c=0），页仍附图。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "2.3 二次函数的图象与性质",
        "objectives": [{"text": "理解二次函数的图象是抛物线并认识其顶点与对称轴", "source_ref": None}],
        "prior_knowledge": [{"text": "一次函数图象的作法", "source_ref": None}],
        "concepts": [{"text": "二次函数：$y = ax^2 + bx + c$（a ≠ 0）", "source_ref": "locator/chunk/s"}],
        "theorems": [],
        "examples": [],
        "inquiries": [{"text": "抛物线的顶点坐标与对称轴由系数 a、b、c 如何确定", "source_ref": None}],
        "key_points": [{"text": "抛物线的开口方向由 a 的符号决定", "source_ref": "locator/chunk/k"}],
        "difficult_points": [],
        "lesson_flow": [
            {
                "stage": "new_content",
                "title": "二次函数的图象",
                "duration_minutes": 15,
                "items": [{"text": "用描点法画出二次函数的图象，观察对称轴与顶点", "source_ref": None}],
                "board_notes": [{"text": "二次函数：$y = ax^2 + bx + c$（a ≠ 0）", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }
    math = _quadratic_math_for_blueprint(body)
    assert math is not None and math[0] == "quadratic" and math[1] == {"a": 1.0, "b": 0.0, "c": 0.0}
    deck = _project(ctx, body)
    carrying = [s for s in deck.slides if s.assets]
    assert carrying and deck.assets
    assert deck.assets[0].object_key == "math-figures/v5/quadratic__a_1__b_0__c_0.png"


async def test_non_quadratic_deck_has_no_figure_asset(db) -> None:
    """非二次函数课题（且非直线课）→ 零图形资产、零 math_object（不猜内容）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [{"text": "理解集合与元素的概念", "source_ref": None}],
        "prior_knowledge": [{"text": "分类讨论的方法", "source_ref": None}],
        "concepts": [{"text": "集合与元素的关系", "source_ref": "locator/chunk/a"}],
        "theorems": [],
        "examples": [],
        "inquiries": [{"text": "观察集合与元素的从属关系", "source_ref": None}],
        "key_points": [{"text": "集合元素的确定性", "source_ref": "locator/chunk/b"}],
        "difficult_points": [],
        "lesson_flow": [
            {
                "stage": "new_content",
                "title": "元素与集合的关系",
                "duration_minutes": 15,
                "items": [{"text": "用符号 ∈ 与 ∉ 表达元素与集合的关系", "source_ref": None}],
                "board_notes": [{"text": "元素 a 属于集合 A，记作 a∈A", "source_ref": None}],
            },
            {
                "stage": "summary",
                "title": "课堂小结",
                "duration_minutes": 5,
                "items": [{"text": "元素与集合的从属关系", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }
    deck = _project(ctx, body)
    assert deck.assets == []
    assert all(s.assets == [] for s in deck.slides)
    assert all(s.math_object is None for s in deck.slides)


# ── 旧路径零回归 ─────────────────────────────────────────────────────────────


async def test_line_course_figure_asset_not_regressed(db) -> None:
    """直线课题：line 资产照常派生并附到图形相关页（键仍是 line，且混入 quadratic 键为零）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "1.2.1 直线的点斜式方程 第一课时",
        "objectives": [{"text": "理解直线点斜式方程的推导过程与形式", "source_ref": None}],
        "prior_knowledge": [{"text": "直线斜率的定义与计算公式", "source_ref": None}],
        "concepts": [{"text": "直线的点斜式方程", "source_ref": "locator/chunk/a"}],
        "theorems": [{"text": "本节结论：直线的点斜式方程", "source_ref": "locator/chunk/e"}],
        "examples": [],
        "inquiries": [{"text": "探究直线y=kx+2的共同特点", "source_ref": None}],
        "key_points": [{"text": "直线点斜式方程的推导过程与形式", "source_ref": "locator/chunk/c"}],
        "difficult_points": [{"text": "理解点斜式方程的适用条件", "source_ref": None}],
        "lesson_flow": [
            {
                "stage": "new_content",
                "title": "点斜式方程推导",
                "duration_minutes": 20,
                "items": [{"text": "结合教材开篇问题，推导点斜式方程", "source_ref": None}],
                "board_notes": [
                    {"text": r"直线点斜式方程：$y - y_1 = k(x - x_1)$", "source_ref": None},
                    {"text": "斜率 k = -2，过点 (-1, 3)", "source_ref": None},
                ],
            },
        ],
        "warnings_ack": [],
    }
    deck = _project(ctx, body)
    carrying = [s for s in deck.slides if s.assets]
    assert carrying, "直线课图形页必须仍有 line 资产（旧路径不回归）"
    assert all(len(s.assets) == 1 for s in carrying)
    keys = [a.object_key for a in deck.assets]
    assert keys and all(k.startswith("math-figures/v5/line") for k in keys)
    assert not any("quadratic" in k for k in keys)
    data = math_figures.asset_bytes(deck.assets[0])
    assert data is not None and data[:8] == PNG_MAGIC
