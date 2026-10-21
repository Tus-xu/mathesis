# coding: utf-8
"""layout_slide golden 向量 + IFC-001 schema 校验（禁字段 reject / 纯函数性）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.pagination.layout import COLOR_BODY, COLOR_FORMULA, COLOR_TITLE, LayoutError, layout_slide
from app.pagination.schemas import SlideSemanticSpec, TemplateSlots, VisualIntent

SLOTS: TemplateSlots = {
    'layout_type': 'concept',
    'pedagogical_role': 'concept_intro',
    'required_slots': ['title'],
    'optional_slots': ['formula', 'visual'],
    'max_text': 5,
    'formula_capacity': 1,
    'visual_capacity': 1,
    'locked_elements': [],
}


def spec(**kw) -> SlideSemanticSpec:
    base = dict(page_type='concept_intro', title='椭圆的定义', points=['到两定点距离之和为常数', '常数大于两焦点间距'])
    return SlideSemanticSpec(**{**base, **kw})


def test_ifc001_forbidden_pixel_fields_rejected():
    """B1 若偷带 x/y/w/h/font_size/color → 校验层直接 reject（README 纪律 14）。"""
    for field in ('x', 'y', 'w', 'h', 'font_size', 'color'):
        with pytest.raises(ValidationError):
            spec(**{field: 100 if field != 'color' else '#fff'})


def test_ifc001_points_constraints():
    with pytest.raises(ValidationError):
        spec(points=['x' * 41])          # 每条 ≤40 字
    with pytest.raises(ValidationError):
        spec(points=[f'第{i}条' for i in range(6)])  # ≤5 条
    with pytest.raises(ValidationError):
        spec(title='')                    # 标题必填


def test_ifc001_visual_intent_kind():
    with pytest.raises(ValidationError):
        VisualIntent(kind='preset')                      # preset 缺 preset 字段
    with pytest.raises(ValidationError):
        VisualIntent(kind='boardJson')                   # boardJson 缺 boardJson
    v = VisualIntent(kind='preset', preset='conic/ellipse', params={'a': 3.0, 'b': 2.0})
    assert v.preset == 'conic/ellipse'


def test_layout_golden_text_only():
    els = layout_slide(spec(), SLOTS)
    by_type = [e.type for e in els]
    assert by_type == ['text', 'text', 'text']  # 标题 + 2 要点（无公式/图形）
    title, p1, p2 = els
    # golden：标题固定顶部；要点自 y=140 起步，行高 26*1.6=41（2 点压力=26pt 档）
    assert (title.left, title.top, title.font_size, title.bold) == (72, 52, 32, True)
    assert title.color == COLOR_TITLE
    assert (p1.left, p1.top) == (72, 140)
    assert (p2.left, p2.top) == (72, 140 + 41 + 10)
    assert p1.font_size == 26 and p1.color == COLOR_BODY
    assert all(e.teacher_confirmed is False for e in els)  # R5：AI 产物恒草稿


def test_layout_golden_with_formula_and_visual():
    s = spec(formula='\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1',
             visual=VisualIntent(kind='preset', preset='conic/ellipse', params={'a': 3, 'b': 2}))
    els = layout_slide(s, SLOTS)
    types = [e.type for e in els]
    assert types == ['text', 'formula', 'text', 'text', 'geometry']
    f = els[1]
    assert f.latex.startswith('\\frac') and f.color == COLOR_FORMULA
    assert f.height == int(30 * 3.2)            # 公式档 30pt，占位高 = font*3.2（OMML 实际渲染高度修正）
    geo = els[-1]
    assert geo.preset_id == 'conic/ellipse' and geo.params == {'a': 3.0, 'b': 2.0}
    # 左文右图：正文列宽 620，图形列 x=72+620+28=720
    assert geo.left == 720 and geo.width == 1280 - 72 * 2 - 620 - 28


def test_layout_slot_violations():
    slots_tight = {**SLOTS, 'max_text': 2}
    with pytest.raises(LayoutError, match='max_text'):
        layout_slide(spec(points=['一', '二', '三']), slots_tight)    # 布局层槽位约束
    with pytest.raises(ValidationError):
        spec(points=[f'第{i}条' for i in range(6)])                   # schema 层 ≤5 条
    slots_no_formula = {**SLOTS, 'required_slots': ['title', 'formula']}
    with pytest.raises(LayoutError, match='formula'):
        layout_slide(spec(), slots_no_formula)                    # 必含公式但 spec 无
    slots_zero = {**SLOTS, 'formula_capacity': 0}
    with pytest.raises(LayoutError, match='formula_capacity'):
        layout_slide(spec(formula='x+y=1'), slots_zero)           # 模板不容纳公式


def test_layout_purity_and_stable_ids():
    s = spec(visual=VisualIntent(kind='preset', preset='conic/ellipse', params={'a': 3}))
    r1, r2 = layout_slide(s, SLOTS), layout_slide(s, SLOTS)
    assert r1 == r2                                               # 同输入同输出（B5 验收钩子②）
    assert [e.id for e in r1] == [e.id for e in r2]               # id 确定性（内容 hash）
