"""Layout Family System —— 家族表达"教学作用"，不是颜色模板。

同一 8–12 页 Golden 至少实际使用 4 个有教学意义的族（任务书 §8 硬门）。
每个族定义：教学作用（中文）、slot 语义、内容映射规则、是否含视觉槽位、密度形态。
坐标（x/y/w/h）禁止出现在本文件 —— 布局几何在引擎 layout resolver 产生。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LayoutFamily:
    id: str
    teaching_purpose: str          # 教学作用（中文，可展示）
    slots: tuple[str, ...]         # 有序槽位语义
    has_visual_slot: bool          # 是否含 figure/equation 等视觉锚
    is_text_only: bool             # 全文字族（streak 检查用）
    card_skeleton: bool = False    # 以卡片为页面骨架（card overuse 检查用）
    content_mapper: str = "lines"  # 内容→槽位映射规则名（resolve.py 实现）

    def slot_spec(self) -> list[dict[str, str]]:
        return [{"slot": s, "semantic": s} for s in self.slots]


REGISTRY: dict[str, LayoutFamily] = {
    f.id: f for f in [
        LayoutFamily("hero-context", "封面开场：大标题建立第一印象，副题点明章节语境",
                     ("kicker", "title", "subtitle", "meta"), True, False, content_mapper="hero"),
        LayoutFamily("question-prompt", "情境导入：编号情境并列铺陈，引导观察发问",
                     ("title", "numbered-items"), False, True, content_mapper="numbered"),
        LayoutFamily("observe-and-infer", "图象观察：大图左置，观察要点右列，先看后说",
                     ("title", "figure", "observations"), True, False, content_mapper="observe"),
        LayoutFamily("concept-focus", "概念建构：概念陈述大字居主，要点双栏支撑",
                     ("title", "concept-statement", "points-2col"), False, True, content_mapper="concept"),
        LayoutFamily("definition-emphasis", "定义锚定：全宽定义横幅强调，辅助说明下置",
                     ("title", "definition-banner", "supporting"), True, False, content_mapper="definition"),
        LayoutFamily("equation-focus", "公式聚焦：集合/符号表达式居中放大，注解环绕",
                     ("title", "equation", "annotations"), True, False, content_mapper="equation"),
        LayoutFamily("progressive-derivation", "逐步推导：步骤纵向推进，步步可回看",
                     ("title", "steps"), False, True, content_mapper="steps"),
        LayoutFamily("worked-example", "例题示范：题干成框，解答分步，示范规范表达",
                     ("title", "problem", "solution-steps"), True, False, content_mapper="worked"),
        LayoutFamily("practice-prompt", "练习巩固：题目醒目，提示辅助，留白作答",
                     ("title", "prompt", "hints"), False, True, content_mapper="practice"),
        LayoutFamily("two-column-evidence", "对比辨析：两栏并置，异同一眼可辨",
                     ("title", "column-left", "column-right"), False, True, content_mapper="twocol"),
        LayoutFamily("checkpoint", "即时检测：小步快问，检验刚建立的概念",
                     ("title", "question", "check-items"), False, True, content_mapper="checkpoint"),
        LayoutFamily("summary-map", "总结结构化：双栏脉络收束全课，不复读标题",
                     ("title", "map-2col"), False, True, content_mapper="summary"),
    ]
}

# teaching_role（intent.py）→ 有序 family 候选（确定性，无随机）
ROLE_FAMILY_CANDIDATES: dict[str, list[str]] = {
    "TITLE_OPENER": ["hero-context"],
    "CONTEXT_HOOK": ["question-prompt", "observe-and-infer"],
    "QUESTION_RAISE": ["question-prompt", "checkpoint"],
    "CONCEPT_BUILD": ["concept-focus", "definition-emphasis"],
    "DEFINITION_ANCHOR": ["definition-emphasis", "equation-focus"],
    "REASONING_CHAIN": ["progressive-derivation", "equation-focus"],
    "MODEL_EXAMPLE": ["worked-example"],
    "GUIDED_PRACTICE": ["practice-prompt", "worked-example"],
    "CONTRAST_VERIFY": ["two-column-evidence", "concept-focus"],
    "VISUAL_EVIDENCE": ["observe-and-infer"],
    "SYNTHESIS_MAP": ["summary-map"],
}


def family(id_: str) -> LayoutFamily:
    return REGISTRY[id_]


def family_manifest() -> list[dict[str, Any]]:
    """供报告/审计引用的族清单。"""
    return [
        {"id": f.id, "purpose": f.teaching_purpose, "slots": list(f.slots),
         "has_visual_slot": f.has_visual_slot, "is_text_only": f.is_text_only}
        for f in REGISTRY.values()
    ]
