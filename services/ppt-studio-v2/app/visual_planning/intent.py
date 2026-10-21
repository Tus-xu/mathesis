"""SlideVisualIntent —— 每页先产生视觉意图，绝不直接选模板坐标。

核心原则：ONE SLIDE ≈ ONE PRIMARY TEACHING INTENT。
中文角色标签（role_label_zh）同时是版面上唯一可见的类型标签（修 R21 内部枚举泄漏）。
"""
from __future__ import annotations

import re
from typing import Any

from app.visual_planning.families import ROLE_FAMILY_CANDIDATES

MATH_CHARS = set("∈∉∋⊆⊇⊂⊃∪∩∅≤≥≠≈±×÷²³∑∫√∞∥⊥∠°′″∀∃→←↔⇒⇔·−")

ROLE_ZH: dict[str, str] = {
    "TITLE_OPENER": "封面开场",
    "CONTEXT_HOOK": "情境导入",
    "QUESTION_RAISE": "提出问题",
    "CONCEPT_BUILD": "概念建构",
    "DEFINITION_ANCHOR": "定义锚定",
    "REASONING_CHAIN": "推理推导",
    "MODEL_EXAMPLE": "例题示范",
    "GUIDED_PRACTICE": "练习巩固",
    "CONTRAST_VERIFY": "对比辨析",
    "VISUAL_EVIDENCE": "图象观察",
    "SYNTHESIS_MAP": "总结结构化",
}

_TYPE_TO_ROLE: dict[str, str] = {
    "title": "TITLE_OPENER",
    "introduction": "CONTEXT_HOOK",
    "inquiry": "QUESTION_RAISE",
    "concept": "CONCEPT_BUILD",
    "definition": "DEFINITION_ANCHOR",
    "derivation": "REASONING_CHAIN",
    "worked-example": "MODEL_EXAMPLE",
    "practice": "GUIDED_PRACTICE",
    "comparison": "CONTRAST_VERIFY",
    "image-focus": "VISUAL_EVIDENCE",
    "summary": "SYNTHESIS_MAP",
}

# 内容关键词 → 图示需求信号（数学视觉规划器用）
DIAGRAM_HINT_WORDS = ("数轴", "图象", "图形", "函数图", "几何", "示意图", "维恩", "数形")


def _is_math_line(line: str) -> bool:
    return any(ch in MATH_CHARS for ch in line)


def _formula_density(content: str) -> str:
    lines = [ln for ln in content.split("\n") if ln.strip()]
    if not lines:
        return "none"
    ratio = sum(1 for ln in lines if _is_math_line(ln)) / len(lines)
    if ratio == 0:
        return "none"
    if ratio < 0.2:
        return "low"
    if ratio < 0.5:
        return "medium"
    return "high"


def _content_density(content: str) -> str:
    lines = [ln for ln in content.split("\n") if ln.strip()]
    chars = sum(len(ln) for ln in lines)
    if chars <= 60 and len(lines) <= 4:
        return "light"
    if chars <= 160 and len(lines) <= 8:
        return "moderate"
    return "dense"


def _attention_anchor(role: str, formula_density: str, has_asset: bool, slide_type: str) -> str:
    if role == "TITLE_OPENER":
        return "title-figures"
    if has_asset:
        return "figure"
    if slide_type == "definition":
        return "definition-banner"
    if formula_density in ("medium", "high") and role in ("DEFINITION_ANCHOR", "REASONING_CHAIN"):
        return "equation"
    if role == "MODEL_EXAMPLE":
        return "problem-frame"
    if role == "SYNTHESIS_MAP":
        return "structure-map"
    if role == "CONTEXT_HOOK":
        return "numbered-scene"
    if role == "CONCEPT_BUILD":
        return "concept-statement"
    return "title"


def _primary_message(slide: Any) -> str:
    return slide.title


def _secondary_message(slide: Any) -> str | None:
    lines = [ln.strip() for ln in (slide.content or "").split("\n") if ln.strip()]
    return lines[-1] if len(lines) > 1 else None


def build_slide_intent(slide: Any, prev_role: str | None, next_role: str | None,
                       asset_plan: dict[str, Any], phase: str) -> dict[str, Any]:
    role = _TYPE_TO_ROLE.get(slide.type, "CONCEPT_BUILD")
    formula_density = _formula_density(slide.content or "")
    density = _content_density(slide.content or "")
    # R8-U：页承载参数化 math_object 等同「本页有图形来源」（attention_anchor=figure）。
    has_asset = bool(slide.assets) or getattr(slide, "math_object", None) is not None
    diagram_need = asset_plan["diagram_need"]

    candidates = list(ROLE_FAMILY_CANDIDATES.get(role, ["concept-focus"]))
    # 数学视觉优先：公式密度高且角色允许 → equation-focus 提前
    if formula_density == "high" and "equation-focus" in candidates:
        candidates.remove("equation-focus")
        candidates.insert(0, "equation-focus")
    # 需要图示的页：有图族提前
    if diagram_need == "required" and has_asset and "observe-and-infer" in candidates:
        candidates.remove("observe-and-infer")
        candidates.insert(0, "observe-and-infer")

    avoid: list[str] = []
    if density == "dense":
        avoid += ["card-grid", "full-width-text-wall"]
    if diagram_need == "required":
        avoid.append("text-only-fallback")
    if formula_density in ("medium", "high"):
        avoid.append("tiny-math-type")

    return {
        "slide_id": slide.id,
        "teaching_role": role,
        "role_label_zh": ROLE_ZH.get(role, role),
        "communication_goal": slide.teaching_goal,
        "primary_message": _primary_message(slide),
        "secondary_message": _secondary_message(slide),
        "attention_anchor": _attention_anchor(role, formula_density, has_asset, slide.type),
        "visual_priority": "high" if (has_asset or diagram_need == "required" or slide.type in ("title", "definition")) else "normal",
        "visual_asset_need": asset_plan,
        "content_density": density,
        "interaction_mode": "teacher-asks" if slide.type in ("practice", "inquiry") else "teacher-explains",
        "formula_density": formula_density,
        "diagram_need": diagram_need,
        "example_need": slide.type == "worked-example",
        "comparison_need": slide.type == "comparison",
        "teacher_talk_support": (slide.speaker_note or "")[:80],
        "student_attention_target": _attention_anchor(role, formula_density, has_asset, slide.type),
        "layout_family_candidates": candidates,
        "avoid_patterns": avoid,
        "continuity_with_previous": "",   # sequence 阶段回填
        "contrast_with_previous": "",     # sequence 阶段回填
        "narrative_phase": phase,
        "source_refs": list(slide.source_refs or []),
        "_prev_role": prev_role,
        "_next_role": next_role,
        "_type": slide.type,
        "_content": slide.content or "",
        "_asset_ids": list(slide.assets or []),
    }


def spec_math_char_re() -> re.Pattern[str]:
    return re.compile("[" + re.escape("".join(MATH_CHARS)) + "]")
