"""R4 椭圆页 → 参数化 math_object 投影测试（复用合成教材 fixture，不触外部资源）。

验收锚点（ACCEPTANCE-MATRIX 4.2/4.3 的投影侧）：
- 含椭圆语义的 concept/derivation/inquiry 页 → 确定性生成 kind=ellipse math_object
  （a=5, b=3 基线；c 恒派生）；
- 仅首个命中页携带 math_object（同族页不重复贴图）；
- 非椭圆页/封面/小结页零 math_object；
- projector 版本升到 v4（旧投影不重放 → 可重新投影出带 math_object 的新 deck）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.teacher_prep.specs.projector import (
    PROJECTOR_VERSION,
    ProjectionRunLineage,
    _ellipse_params_from_text,
    project_deck_spec,
    project_lesson_plan_spec,
)

from .tp_context_fixtures import build, seed_synthetic_textbook, selection


def _lineage() -> ProjectionRunLineage:
    return ProjectionRunLineage(model="teacher-prep-specs-v1")


def _body(ctx) -> dict:
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成椭圆 3.1.2 教学蓝图",
        "objectives": [{"text": "掌握椭圆的范围、对称性、特殊点", "source_ref": None}],
        "prior_knowledge": [],
        "concepts": [{"text": "椭圆的焦点与标准方程", "source_ref": None}],
        "theorems": [],
        "examples": [],
        "inquiries": [{"text": "观察椭圆 $\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1$ 的扁平程度", "source_ref": None}],
        "key_points": [{"text": "椭圆的对称性", "source_ref": None}],
        "difficult_points": [],
        "lesson_flow": [
            {
                "stage": "new_content",
                "title": "探究椭圆的几何性质",
                "duration_minutes": 10,
                "items": [
                    {"text": "探究椭圆的范围：结合图形观察椭圆边界", "source_ref": None},
                    {"text": "探究椭圆的对称性与焦点 F_1、F_2 的位置", "source_ref": None},
                ],
            },
            {
                "stage": "summary",
                "title": "课堂小结",
                "duration_minutes": 5,
                "items": [{"text": "椭圆的范围、对称性、特殊点", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }


def _project(ctx, body: dict, *, blueprint_id: str | None = None):
    return project_deck_spec(
        spec_id=str(uuid.uuid4()),
        blueprint_id=blueprint_id or str(uuid.uuid4()),
        blueprint_revision=2,
        blueprint_body=body,
        context=ctx,
        profile_ref={"profile_id": "x"},
        lineage=_lineage(),
        run_hash="c" * 64,
        projected_at=datetime(2026, 9, 19, tzinfo=UTC),
    )


def test_projector_version_is_v5() -> None:
    # v5：教案六章扩展（board_design/homework/reflection/过程表五列）——投影行为变更必须
    # bump 版本，使既有投影按新输入 hash 重新投影（而非命中旧 idempotency 重放）。
    # v9：板书三栏清洗（剥离教材章节号 / 公式区只留公式 / 公式不回填文字栏）。
    # v10：教案重难点确定性归并（同类「重点/难点」至多一条）。
    # v11/v12：deck 内容净化（R8-T）+ 椭圆 math_object 参数确定性解析（R8-U，同页公式↔图形一致）。
    # v13（R3e 产物投影层回炉）：每页 ≥2 条真实内容项（宁少页不留空页）+ 数学源码零进产物
    # + 公式 canonical + 幽灵图引用/术语守卫 + 直线 math_object/图形资产 + G20 同源 + 例题带解答。
    # v14（G6 产物复审回炉）：推导页标题回声根治（点斜式课确定性组装真实推导步骤）
    # + deck 逐页确定性去重（行 vs 公式 / 完全重复 / 标题回声）+ 同题两写跳过 + 小结回声剔除。
    # v15（R6-planfix）：推导第 2 步去尾括号改「，其中 x ≠ x₁」（强调行大字号下孤「）」根治）。
    # v16（二次函数补图）：图形候选扩 quadratic（纯 MATH_RENDER 资产通道、不挂 math_object）
    # + 附图按页内容相关（去「整册一张」限制；每页 ≤1 张、相邻连续带图 ≤2、deck 级去重）。
    # v17（幂函数补图，specs/projector.py 头注）：图形候选再扩 power（五个常见幂函数
    # 图象族，同走 MATH_RENDER 资产通道；判定与 quadratic 互斥）——升版使已确认蓝图
    # 重新投影产生新 spec（旧 v16 spec 不回写）。
    assert PROJECTOR_VERSION == "teacher_prep.specs.v17"


async def test_ellipse_pages_get_math_object(db):
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx), blueprint_id=str(uuid.UUID(int=7)))

    carrying = [s for s in deck.slides if s.math_object is not None]
    assert len(carrying) == 1, f"仅首个承载椭圆语义的页带 math_object，实际 {len(carrying)} 页"
    slide = carrying[0]
    assert slide.type in ("concept", "derivation", "inquiry")
    assert slide.math_object.kind == "ellipse"
    assert slide.math_object.semi_major == 5.0
    assert slide.math_object.semi_minor == 3.0
    assert slide.math_object.focal_distance == pytest.approx(4.0)  # c=√(a²−b²)

    # 封面与小结页零 math_object
    assert deck.slides[0].type == "title" and deck.slides[0].math_object is None
    assert deck.slides[-1].type == "summary" and deck.slides[-1].math_object is None

    # 确定性：同输入 → 同 body（不含 id/projection 血缘时间戳；blueprint_id 同值传入）
    other = _project(ctx, _body(ctx), blueprint_id=str(uuid.UUID(int=7)))
    assert deck.model_dump(mode="json", exclude={"id", "projection"}) == other.model_dump(
        mode="json", exclude={"id", "projection"}
    )


async def test_non_ellipse_deck_has_no_math_object(db):
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    body["title"] = "合成集合 1.1 教学蓝图"
    for stage in body["lesson_flow"]:
        stage["title"] = stage["title"].replace("椭圆", "集合")
        stage["items"] = [
            {**it, "text": it["text"].replace("椭圆", "集合").replace("F_1、F_2", "代表元素")}
            for it in stage["items"]
        ]
    body["concepts"] = [{"text": "集合与元素", "source_ref": None}]
    body["inquiries"] = [{"text": "集合的元素可以重复吗", "source_ref": None}]
    body["objectives"] = [{"text": "理解集合与元素的概念", "source_ref": None}]
    # fixture 自洽：非椭圆 deck 的所有桶都不得残留椭圆语义（补页用 key_points，漏清洗会误触发）
    body["key_points"] = [{"text": it["text"].replace("椭圆", "集合"), "source_ref": None} for it in body["key_points"]]
    body["difficult_points"] = [{"text": it["text"].replace("椭圆", "集合"), "source_ref": None} for it in body["difficult_points"]]
    deck = _project(ctx, body)
    assert all(s.math_object is None for s in deck.slides)


# ── R8-U：math_object 参数确定性解析（同页公式 ↔ 图形参数一致）────────────────


def test_ellipse_params_from_text_standard_form() -> None:
    """标准式 \\frac{x^2}{25}+\\frac{y^2}{9}=1 → a=5, b=3（c=4 派生）。"""
    p = _ellipse_params_from_text(r"椭圆 $\frac{x^2}{25} + \frac{y^2}{9} = 1$ 的顶点")
    assert p == {"a": 5.0, "b": 3.0}


def test_ellipse_params_from_text_general_form() -> None:
    """一般式 4x^2 + 9y^2 = 36 → x²/9 + y²/4 = 1 → a=3, b=2。"""
    p = _ellipse_params_from_text("已知椭圆$4x^2 + 9y^2 = 36$，求其性质")
    assert p == {"a": 3.0, "b": 2.0}


def test_ellipse_params_from_text_slash_form() -> None:
    p = _ellipse_params_from_text("椭圆 x^2/16 + y^2/7 = 1 的范围")
    assert p == {"a": 4.0, "b": pytest.approx(7**0.5, rel=1e-6)}


def test_ellipse_params_from_text_symbolic_or_circle_returns_none() -> None:
    """符号式（a²/b²）与圆（a²=b²）都解析不出合法椭圆 → None（不猜测）。"""
    assert _ellipse_params_from_text(r"$\frac{x^2}{a^2} + \frac{y^2}{b^2} = 1$") is None
    assert _ellipse_params_from_text(r"$\frac{x^2}{25} + \frac{y^2}{25} = 1$") is None
    # 长轴在 y 轴（a² < b²）→ 与「焦点在 x 轴」渲染口径不符 → 不猜测
    assert _ellipse_params_from_text(r"$\frac{x^2}{9} + \frac{y^2}{25} = 1$") is None


async def test_math_object_params_derived_from_blueprint_equation(db):
    """蓝图含具体方程 → deck 与教案板书 math_object 同源且参数与方程一致。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    body["examples"] = [{"text": "已知椭圆$4x^2 + 9y^2 = 36$，求焦点坐标", "source_ref": None}]
    bid = str(uuid.uuid4())

    deck = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=bid, blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="c" * 64, projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    lp = project_lesson_plan_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=bid, blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="c" * 64, projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    deck_mo = next(s.math_object for s in deck.slides if s.math_object is not None)
    assert deck_mo.params == {"a": 3.0, "b": 2.0}
    assert deck_mo.semi_major == 3.0 and deck_mo.semi_minor == 2.0
    assert deck_mo.focal_distance == pytest.approx(5**0.5)
    # 同源：教案板书 math_object 与 deck 完全一致
    assert lp.board_design.math_object is not None
    assert lp.board_design.math_object.kind == deck_mo.kind
    assert lp.board_design.math_object.params == deck_mo.params


async def test_math_object_params_default_when_symbolic(db):
    """蓝图只有符号式标准方程 → 退回课堂基线 a=5, b=3（既有契约不回归）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    mo = next(s.math_object for s in deck.slides if s.math_object is not None)
    assert mo.params == {"a": 5.0, "b": 3.0}
    assert mo.focal_distance == pytest.approx(4.0)
