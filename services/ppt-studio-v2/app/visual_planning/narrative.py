"""DeckNarrativePlan —— deck 级教学叙事规划（不得每页独立规划）。

lesson_arc 由 approved Blueprint 的教学逻辑驱动（outline 阶段 goal + slide type 校准），
不强制所有课程走完全相同的阶段顺序。
"""
from __future__ import annotations

from typing import Any

# slide.type → 默认叙事阶段（fallback；outline stage goal 优先校准）
TYPE_DEFAULT_PHASE: dict[str, str] = {
    "title": "HOOK",
    "introduction": "HOOK",
    "inquiry": "QUESTION",
    "image-focus": "OBSERVE",
    "comparison": "OBSERVE",
    "concept": "CONCEPT_FORMATION",
    "definition": "CONCEPT_FORMATION",
    "derivation": "DERIVATION",
    "worked-example": "WORKED_EXAMPLE",
    "practice": "PRACTICE",
    "summary": "SYNTHESIS",
}

# outline stage goal 中文关键词 → phase
STAGE_GOAL_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("导入", "情境", "引入"), "HOOK"),
    (("观察",), "OBSERVE"),
    (("问题", "提问"), "QUESTION"),
    (("概念", "含义", "新授", "理解"), "CONCEPT_FORMATION"),
    (("推导", "证明", "推导链"), "DERIVATION"),
    (("例题", "精讲", "示范"), "WORKED_EXAMPLE"),
    (("练习", "检测", "巩固"), "PRACTICE"),
    (("小结", "总结", "收束"), "SYNTHESIS"),
]

PHASE_ZH: dict[str, str] = {
    "HOOK": "情境导入", "OBSERVE": "观察发现", "QUESTION": "提出问题",
    "CONCEPT_FORMATION": "概念形成", "DERIVATION": "推理推导",
    "VERIFY": "验证辨析", "WORKED_EXAMPLE": "例题精讲", "PRACTICE": "练习巩固",
    "SYNTHESIS": "总结提升",
}


def _stage_goal_phase(goal: str) -> str | None:
    for keys, phase in STAGE_GOAL_KEYWORDS:
        if any(k in goal for k in keys):
            return phase
    return None


def build_narrative_plan(spec: Any, slide_phase: dict[str, str] | None = None) -> dict[str, Any]:
    """slide_phase：调用方可传入校准结果（outline 阶段映射），此处按 spec 自行推导。"""
    slides = spec.slides
    # 1) outline stage → slide_id 集合 + stage goal 关键词映射
    stage_of_slide: dict[str, tuple[str, str]] = {}  # slide_id -> (stage_id, phase)
    for entry in spec.outline:
        phase = _stage_goal_phase(entry.goal or "")
        for sid in entry.slide_ids:
            stage_of_slide[sid] = (entry.id, phase or "")

    phases: list[str] = []
    stage_labels: list[str] = []
    for s in slides:
        stage_id, goal_phase = stage_of_slide.get(s.id, ("", ""))
        phase = goal_phase or TYPE_DEFAULT_PHASE.get(s.type, "CONCEPT_FORMATION")
        # image-focus 页在概念阶段内仍标注观察属性（phase 保持所属 stage，避免假切换）
        phases.append(phase)
        stage_labels.append(stage_id)

    densities = [_content_density(s) for s in slides]
    return {
        "lesson_arc": _lesson_arc(phases),
        "audience": spec.metadata.grade or "高中",
        "duration_minutes": spec.metadata.duration_minutes,
        "presentation_context": "课堂教学投影（教师主讲 + 学生跟练）",
        "visual_style_intent": "浅色现代教育风：白底、冷灰蓝文字、主蓝强调、克制的局部卡片、大留白",
        "narrative_phases": [
            {"slide_id": s.id, "page_no": i + 1, "phase": phases[i],
             "phase_zh": PHASE_ZH.get(phases[i], phases[i]), "stage_id": stage_labels[i]}
            for i, s in enumerate(slides)
        ],
        "emphasis_points": [
            s.id for i, s in enumerate(slides)
            if s.type in ("definition", "worked-example", "summary")
        ],
        "pacing": _pacing(densities, phases),
        "density_curve": [
            {"slide_id": s.id, "page_no": i + 1, "density": densities[i]}
            for i, s in enumerate(slides)
        ],
        "visual_rhythm": _visual_rhythm(slides),
        "recurring_motifs": ["accent-bar", "kicker-phase-tag", "numbered-dot", "definition-banner"],
        "transition_strategy": "教学阶段切换页使用 kicker 阶段标签 + family 切换形成视觉转场；同阶段内以构图变化推进",
        "source": {
            "blueprint_ref": (spec.projection or {}).get("approved_blueprint_id")
            if hasattr(spec, "projection") else None,
            "deck_revision": spec.revision,
        },
    }


def _lesson_arc(phases: list[str]) -> list[str]:
    arc: list[str] = []
    for p in phases:
        if not arc or arc[-1] != p:
            arc.append(p)
    return arc


def _content_density(slide: Any) -> str:
    lines = [ln for ln in (slide.content or "").split("\n") if ln.strip()]
    chars = sum(len(ln) for ln in lines)
    if chars <= 60 and len(lines) <= 4:
        return "light"
    if chars <= 160 and len(lines) <= 8:
        return "moderate"
    return "dense"


def _pacing(densities: list[str], phases: list[str]) -> dict[str, Any]:
    dense_run = max(
        (sum(1 for d in densities[i:] if d == "dense") for i in range(len(densities))),
        default=0,
    )
    return {
        "avg_density": _mode(densities) or "moderate",
        "max_consecutive_dense": dense_run,
        "phase_switches": sum(1 for i in range(1, len(phases)) if phases[i] != phases[i - 1]),
        "note": "概念形成与例题页放慢（fixture 学情引用页已标注），导入/小结轻快",
    }


def _mode(values: list[str]) -> str | None:
    if not values:
        return None
    return max(set(values), key=values.count)


def _visual_rhythm(slides: list[Any]) -> list[dict[str, Any]]:
    return [
        {"slide_id": s.id, "page_no": i + 1,
         "has_asset": bool(s.assets), "type": s.type}
        for i, s in enumerate(slides)
    ]
