# coding: utf-8
"""确定性布局引擎 layout_slide —— 坐标/字号档位/颜色的唯一产出（IFC-001）。

输入 SlideSemanticSpec（B1 语义规格，禁像素字段）+ TemplateSlots（deck_templates.slots
版式骨架）→ 输出 Element 列表（对齐前端 V3Element 契约：text/formula/geometry，
1280×720 逻辑坐标，teacher_confirmed 恒 False——AI 产物草稿态，R5）。

纯函数：同输入同输出（B5 验收钩子 ②）。校验层 reject 一切像素字段
（schemas.SlideSemanticSpec extra='forbid' 兜底）+ 槽位约束（required_slots /
max_text / formula_capacity / visual_capacity）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from .schemas import SlideSemanticSpec, TemplateSlots

CANVAS_W, CANVAS_H = 1280, 720
MARGIN_X = 72
TITLE_FONT = 32
BODY_FONT_TIERS = (26, 24, 22, 20)   # 正文档位，最低 20（R4/R9：宁加页不缩字）
FORMULA_FONT_TIERS = (30, 28, 26, 24)
COLOR_TITLE = '#1e3a2f'
COLOR_BODY = '#2d3748'
COLOR_FORMULA = '#1a365d'

REQUIRED_SLOT_RULES = {
    'formula': ('formula_capacity', lambda s: s.formula is not None),
    'visual': ('visual_capacity', lambda s: s.visual is not None),
}


class LayoutError(ValueError):
    """spec 与模板槽位不兼容（调用方转为业务错误码，非 500）。"""


class Element(BaseModel):
    """课件元素（V3Element 子集：布局引擎产出的类型）。"""

    id: str
    type: str                                  # text / formula / geometry / functionPlot / anchorPhoto / figure3d
    left: int
    top: int
    width: int
    height: int
    z: int = 1
    teacher_confirmed: bool = False
    html: str | None = None
    latex: str | None = None
    font_size: int | None = None
    color: str | None = None
    bold: bool | None = None
    preset_id: str | None = None
    params: dict[str, Any] | None = None
    board_json: dict[str, Any] | None = None
    src: str | None = None  # image 元素：dataURI（模板背景层）或 MinIO key
    scene: dict[str, Any] | None = None  # figure3d 元素：MathFigure3D 确定性场景（静态化渲染）
    expr: str | None = None  # functionPlot 元素：教师画布可编辑表达式
    domain: list[float] | tuple[float, float] | None = None
    live_sliders: bool | None = None
    caption: str | None = None
    alt: str | None = None
    no: int | None = None


def _stable_id(*parts: Any) -> str:
    raw = '|'.join(str(p) for p in parts)
    return 'el-' + hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


def _check_slots(spec: SlideSemanticSpec, slots: TemplateSlots) -> None:
    for slot, (cap_field, present) in REQUIRED_SLOT_RULES.items():
        if slot in slots.get('required_slots', []) and not present(spec):
            raise LayoutError(f'模板必含槽位 {slot!r}（{cap_field}>0）但 spec 未提供')
    for slot, (cap_field, present) in REQUIRED_SLOT_RULES.items():
        if present(spec) and slots.get(cap_field, 1) == 0:
            raise LayoutError(f'spec 含 {slot!r}，但模板 {cap_field}=0 不容纳')
    max_text = slots.get('max_text', 5)
    if len(spec.points) > max_text:
        raise LayoutError(f'points {len(spec.points)} 条超过模板 max_text={max_text}')


def _tier(series: tuple[int, ...], pressure: int) -> int:
    return series[min(pressure, len(series) - 1)]


def layout_slide(spec: SlideSemanticSpec, slots: TemplateSlots) -> list[Element]:
    """语义规格 + 槽位骨架 → 确定性元素列表（坐标/字号/颜色唯一产出）。

    模板主题（slots["_theme"]：bg_datauri/title/body/accent，六套模板差异化）驱动
    背景层与文字色；无主题回退内置默认色。"""
    _check_slots(spec, slots)
    theme = slots.get("_theme") if isinstance(slots.get("_theme"), dict) else {}
    has_visual = spec.visual is not None
    # 版面压力：要点越多/越长，字号档位越靠后（仍 ≥20）
    pressure = 0 if len(spec.points) <= 2 else (1 if len(spec.points) <= 3 else (2 if len(spec.points) <= 4 else 3))
    if any(len(p) > 28 for p in spec.points):
        pressure = min(pressure + 1, 3)
    body_font = _tier(BODY_FONT_TIERS, pressure)
    formula_font = _tier(FORMULA_FONT_TIERS, pressure)

    elements: list[Element] = []

    # 模板背景层（z=0，1280×720 dataURI PNG——六套模板视觉差异化的载体）
    bg_uri = theme.get("bg_datauri")
    if bg_uri:
        elements.append(Element(
            id=_stable_id('bg', theme.get('accent', '')),
            type='image', left=0, top=0, width=CANVAS_W, height=CANVAS_H,
            z=0, src=bg_uri,
        ))

    title_color = theme.get("title") or COLOR_TITLE
    body_color = theme.get("body") or COLOR_BODY
    formula_color = theme.get("accent") or COLOR_FORMULA

    # 标题（锁定元素位置由模板决定，本引擎固定顶部——locked_elements 只声明不可删，不改变布局）
    elements.append(Element(
        id=_stable_id(spec.page_type, spec.title, 'title'),
        type='text', left=MARGIN_X, top=52, width=CANVAS_W - 2 * MARGIN_X, height=64,
        z=2, html=spec.title, font_size=TITLE_FONT, color=title_color, bold=True,
    ))

    # 主体区：有图形 → 左文右图；无图形 → 通栏
    text_width = 620 if has_visual else CANVAS_W - 2 * MARGIN_X
    visual_left, visual_width = MARGIN_X + text_width + 28, (CANVAS_W - 2 * MARGIN_X) - text_width - 28

    top = 140
    if spec.formula:
        # OMML 实际渲染高度 > 2.2×字号（分式上下结构 + 段距）——高度与后续 top 偏移按 3.2×
        formula_h = int(formula_font * 3.2)
        elements.append(Element(
            id=_stable_id(spec.page_type, spec.title, 'formula', spec.formula),
            type='formula', left=MARGIN_X, top=top, width=text_width, height=formula_h,
            z=2, latex=spec.formula, font_size=formula_font, color=formula_color,
        ))
        top += formula_h + 24

    line_h = int(body_font * 1.6)
    for i, point in enumerate(spec.points):
        elements.append(Element(
            id=_stable_id(spec.page_type, spec.title, 'point', i, point),
            type='text', left=MARGIN_X, top=top, width=text_width, height=line_h,
            z=2, html=point, font_size=body_font, color=body_color,
        ))
        top += line_h + 10

    if has_visual:
        v = spec.visual
        if v.kind == 'preset':
            elements.append(Element(
                id=_stable_id(spec.page_type, spec.title, 'visual', v.preset),
                type='geometry', left=visual_left, top=140, width=visual_width, height=460,
                z=1, preset_id=v.preset, params=dict(v.params),
            ))
        elif v.kind == 'figure3d':
            # 立体几何确定性场景：落 figure3d 元素（前端 MathFigure3D 静态化渲染，只取图片边界）
            elements.append(Element(
                id=_stable_id(spec.page_type, spec.title, 'visual', 'figure3d'),
                type='figure3d', left=visual_left, top=140, width=visual_width, height=460,
                z=1, scene=v.scene,
            ))
        else:
            bj = dict(v.board_json or {})
            # 例题图片（题库 stem_image）：boardJson 携带 __stem_image__ → 落为 image 元素
            # （前端 SlideCanvasV3 直接渲染 <img>；MinIO key 由对象存储公开 URL 提供）
            if bj.get('__stem_image__'):
                elements.append(Element(
                    id=_stable_id(spec.page_type, spec.title, 'visual', 'stem_image'),
                    type='image', left=visual_left, top=140, width=visual_width, height=460,
                    z=1, src=str(bj['__stem_image__']),
                ))
            elif bj.get('type') == 'plot2d' and bj.get('expr'):
                domain = bj.get('domain')
                if not isinstance(domain, (list, tuple)) or len(domain) != 2:
                    try:
                        domain = [float(bj.get('x0', -6.0)), float(bj.get('x1', 6.0))]
                    except (TypeError, ValueError):
                        domain = [-6.0, 6.0]
                elements.append(Element(
                    id=_stable_id(spec.page_type, spec.title, 'visual', 'plot2d', bj.get('expr')),
                    type='functionPlot', left=visual_left, top=140, width=visual_width, height=460,
                    z=1, expr=str(bj['expr']), params=dict(bj.get('params') or {}),
                    domain=[float(domain[0]), float(domain[1])], live_sliders=bool(bj.get('live_sliders')),
                ))
            else:
                elements.append(Element(
                    id=_stable_id(spec.page_type, spec.title, 'visual', 'boardJson'),
                    type='geometry', left=visual_left, top=140, width=visual_width, height=460,
                    z=1, board_json=bj,
                ))

    # 越界兜底（不该发生：正文超版心即槽位/字数校验问题，显式暴露而非静默缩字）
    if not has_visual and top > CANVAS_H - 40:
        raise LayoutError(f'内容高度 {top} 超出版心（points 过多/过长）——应加页而非缩字（R4）')

    return elements
