# coding: utf-8
"""SlideSemanticSpec / TemplateSlots —— IFC-001 冻结签名（单一事实源）。

依据：D:\\teacher-research\\ifc\\IFC-001-SlideSemanticSpec.md（FROZEN，2026-09-05 用户裁决）。
B1 deck_graph 只 import 本模块，禁止复制定义（防两侧漂移，V2.2.1 收口②）。
本文件签名变更必须走 IFC 流程（02-ARCHITECTURE §8）。
"""
from __future__ import annotations

from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 九种页型词汇表（B2 §3 ppt_slots）
SlidePageType = Literal[
    'concept_intro', 'visual_definition', 'derivation', 'formula_focus',
    'worked_example', 'compare', 'common_error', 'practice', 'summary',
]


class VisualIntent(BaseModel):
    """图形意图：preset 参数化优先，boardJson 兜底（rebuild 产物），figure3d 立体配图。

    figure3d：确定性 3D 场景（MathFigure3D DSL：solids/curves/planes/segments/labels），
    由布局引擎静态化为图（教师课件只取图片边界，不放交互工具栏）。
    """

    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    kind: Literal['preset', 'boardJson', 'figure3d']
    preset: Optional[str] = None          # kind='preset' 时必填（preset_id，如 'conic/ellipse'）
    params: dict[str, float] = Field(default_factory=dict)
    board_json: Optional[dict] = Field(default=None, alias='boardJson')
    scene: Optional[dict] = None          # kind='figure3d' 时必填（MathFigure3D figure 契约）

    @model_validator(mode='after')
    def _check_kind(self) -> 'VisualIntent':
        if self.kind == 'preset' and not self.preset:
            raise ValueError("visual.kind='preset' 需要 preset 字段")
        if self.kind == 'boardJson' and not self.board_json:
            raise ValueError("visual.kind='boardJson' 需要 boardJson 字段")
        if self.kind == 'figure3d' and not self.scene:
            raise ValueError("visual.kind='figure3d' 需要 scene 字段")
        return self


class SlideSemanticSpec(BaseModel):
    """B1 deck_graph 逐页 LLM 输出（Pydantic 校验，失败重试 ≤2）。

    禁字段：x/y/w/h/font_size/color/任何像素值——extra='forbid' 直接 reject
    （README 纪律 14：LLM 禁产像素布局，坐标/字号/颜色由 B4 布局引擎唯一产出）。
    """

    model_config = ConfigDict(extra='forbid')

    page_type: SlidePageType
    title: str = Field(min_length=1, max_length=60)
    points: list[str] = Field(default_factory=list, max_length=5)
    formula: Optional[str] = None         # LaTeX；None = 本页无公式
    visual: Optional[VisualIntent] = None
    teacher_note: Optional[str] = None    # 备课提示，不进 slide 正文

    @model_validator(mode='after')
    def _check_points(self) -> 'SlideSemanticSpec':
        for p in self.points:
            if len(p) > 40:
                raise ValueError(f'points 每条 ≤40 字，超长：{p[:20]}…')
        return self


class TemplateSlots(TypedDict):
    """来源：deck_templates.slots（02-ARCHITECTURE §4.2 注释）。"""

    layout_type: str
    pedagogical_role: str
    required_slots: list[str]
    optional_slots: list[str]
    max_text: int
    formula_capacity: int
    visual_capacity: int
    locked_elements: list[str]


def layout_slide(*args, **kwargs):
    """兼容 IFC-001 契约从 schema 模块访问布局函数。

    真实实现仍在 ``pagination.layout``；惰性导入避免 schema/layout 循环依赖，
    同时为旧契约调用方保留稳定入口。
    """
    from .layout import layout_slide as _layout_slide

    return _layout_slide(*args, **kwargs)
