"""VisualAssetPlan —— 数学视觉规划器（任务书 §11）。

对每页显式判定视觉形态（TEXT_ONLY/FORMULA/DIAGRAM/...）。
教学目标明显需要图形理解却选纯文字页 → 必须给出理由，否则 deck QA 失败。

资产优先级（任务书）：
1. 教材/教师原始图  2. deterministic math render  3. JSXGraph/程序化 SVG→PNG
4. 合成示意图  5. AI image 仅限非数学装饰（数学图禁止 AI 生成）
"""
from __future__ import annotations

from typing import Any

from app.visual_planning._meta import MATH_OBJECT_ASSET_ID
from app.visual_planning.intent import DIAGRAM_HINT_WORDS, _is_math_line

ASSET_KIND_TO_VISUAL: dict[str, str] = {
    "TEXTBOOK": "ORIGINAL_TEXTBOOK_IMAGE",
    "TEACHER_UPLOAD": "TEACHER_UPLOAD",
    "MATH_RENDER": "MATH_RENDER_RECIPE",   # deterministic render（recipe/来源随 AssetRef）
    "AI_IMAGE": "AI_DECORATIVE_ONLY",
}


def build_asset_plan(slide: Any, assets_by_id: dict[str, Any]) -> dict[str, Any]:
    lines = [ln for ln in (slide.content or "").split("\n") if ln.strip()]
    math_ratio = (sum(1 for ln in lines if _is_math_line(ln)) / len(lines)) if lines else 0.0
    text_mentions_diagram = any(w in (slide.content or "") for w in DIAGRAM_HINT_WORDS)
    goal_mentions_diagram = any(w in (slide.teaching_goal or "") for w in DIAGRAM_HINT_WORDS)
    has_math_object = getattr(slide, "math_object", None) is not None

    if slide.assets:
        asset = assets_by_id.get(slide.assets[0])
        visual_kind = ASSET_KIND_TO_VISUAL.get(asset.kind, "DIAGRAM") if asset else "DIAGRAM"
        diagram_need = "required"          # spec 已配图 → 图示是明确教学意图
        rationale = f"slide 引用资产 {slide.assets[0]}（kind={asset.kind if asset else 'unknown'}）"
    elif has_math_object:
        # R8-U：页承载参数化数学对象 → 图示需求为 required，由引擎确定性渲染为 PNG
        # （非贴图）；不算 text_only_risk（图形确实会出现在产物里）。
        visual_kind, diagram_need = "MATH_RENDER_RECIPE", "required"
        rationale = "slide 承载参数化 math_object（kind=ellipse），引擎确定性渲染为 PNG 内嵌"
    elif slide.type == "image-focus":
        visual_kind, diagram_need = "DIAGRAM", "required"
        rationale = "type=image-focus 教学意图即图示；无资产 → 规划缺口（QA 失败项）"
    elif text_mentions_diagram or goal_mentions_diagram:
        visual_kind, diagram_need = "DIAGRAM", "suggested"
        rationale = "内容/教学目标提及图形工具（数轴/图象等），建议配图"
    elif math_ratio >= 0.5:
        visual_kind, diagram_need = "FORMULA", "none"
        rationale = f"数学行占比 {math_ratio:.0%} → 公式焦点表达"
    elif math_ratio > 0:
        visual_kind, diagram_need = "FORMULA", "none"
        rationale = f"含数学符号（行占比 {math_ratio:.0%}）→ 数学行强调表达"
    else:
        visual_kind, diagram_need = "TEXT_ONLY", "none"
        rationale = "无图示信号 → 纯文字表达"

    bound = list(slide.assets or [])
    if has_math_object and not slide.assets:
        bound.append(MATH_OBJECT_ASSET_ID)
    # 需要图示却无资产 → 缺口页：justification 留空 = 无理由纯文字（deck QA 拦截）
    # math_object 页的图形由引擎真实产出，故不算缺口。
    text_only_risk = diagram_need == "required" and not slide.assets and not has_math_object
    return {
        "visual_kind": visual_kind,
        "diagram_need": diagram_need,
        "math_ratio": round(math_ratio, 3),
        "bound_asset_ids": bound,
        "text_only_risk": text_only_risk,
        "justification": "" if text_only_risk else rationale,
        "priority_chain": _priority_chain(visual_kind),
    }


def _priority_chain(visual_kind: str) -> list[str]:
    if visual_kind in ("ORIGINAL_TEXTBOOK_IMAGE", "TEACHER_UPLOAD"):
        return ["original-asset"]
    if visual_kind == "MATH_RENDER_RECIPE":
        return ["deterministic-math-render", "programmatic-svg-png", "composed-diagram"]
    if visual_kind == "FORMULA":
        return ["native-text-equation", "deterministic-render-fallback"]
    return ["text"]
