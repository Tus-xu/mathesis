"""ResolvedVisualPlan —— 引擎中立的可序列化计划（进入实际引擎前的最后一层）。

- 不含任何 x/y/w/h（qa_deck 扫描强制）；坐标在 engine compiler/layout resolver 产生；
- slots 携带**语义槽位 + 具体内容文本**（内容→槽位映射在 planner 完成，引擎只管布局）；
- 不是新的业务事实源：TeachingDeckSpec 仍是内容事实源，本计划只表达"如何呈现"。
"""
from __future__ import annotations

import re
from typing import Any

from app.visual_planning._meta import MATH_OBJECT_ASSET_ID, VERSION
from app.visual_planning.assets import build_asset_plan
from app.visual_planning.families import REGISTRY, family_manifest
from app.visual_planning.intent import build_slide_intent
from app.visual_planning.narrative import PHASE_ZH, build_narrative_plan
from app.visual_planning.sequence import plan_sequence
from app.visual_planning.tokens import tokens_for_engine

GEOMETRY_KEY_RE = re.compile(r"^(x|y|w|h|width|height|left|top|cx|cy)$", re.IGNORECASE)

# 参数化数学对象（slide.math_object）在 figure 槽位中的资产占位 id（与 8201 引擎同字面量）。
# 语义：该页承载 math_object → 引擎确定性渲染为 PNG 内嵌（非贴图、非空框）。
MATH_OBJECT_ASSET_ID = "__math_object__"


def _has_math_object(slide: Any) -> bool:
    return getattr(slide, "math_object", None) is not None


# ─────────── 内容 → 槽位映射（确定性规则，按 family content_mapper 分派） ───────────

# 强定义词：命中即可作为定义横幅（归纳式教材文本定义句通常在示例之后）
DEFINITION_STRONG = ("叫做", "称为", "记作", "统称", "定义")
DEFINITION_WEAK = ("是", "组成")

# 定义横幅/标题类槽位尾部的句末标点（R8-F F3）：banner 是**标题式**展示元素（大号加粗、
# 居中），句末标点在标题里是多余的；且当横幅文本接近换行边界时，句末「。」会被 CJK 换行
# 甩到单独一行、渲染成孤立的「。」（R8-U/slide4-rendered.png 实测）。这里确定性剔除 banner
# 尾部的句末标点，消除该孤立标点观感（不改动正文/其它槽位内容）。
_BANNER_TRAILING_PUNCT_RE = re.compile(r"[。．.！!？?；;：:，,、]+$")


def _strip_banner_trailing_punct(text: str) -> str:
    """剔除标题式横幅文本尾部的句末标点（清空则原样返回，绝不产出空横幅）。"""
    trimmed = _BANNER_TRAILING_PUNCT_RE.sub("", str(text or "")).strip()
    return trimmed or text


def _norm_key(s: str) -> str:
    """归一化键：去空白 + 去全角标点 + 去首尾；用于跨条目等价判定，避开 lead/body 重复。

    例："一般地，我们把研究对象统称为元素。" 与 "- 一般地，我们把研究对象统称为元素。"
    归一化后相等 → mapper 视为同一句，去重。
    """
    s = s.strip().lstrip("-• ").strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，。；：、？！.,;:!?]+$", "", s)
    return s


def _lines(content: str) -> list[str]:
    return [ln.strip() for ln in (content or "").split("\n") if ln.strip()]


def _bullets(content: str) -> list[str]:
    return [ln.lstrip("-• ").strip() for ln in _lines(content) if ln.lstrip("-• ").strip()]


def _prose(content: str) -> list[str]:
    return [ln for ln in _lines(content) if not ln.startswith(("-", "•"))]


def map_slots(family_id: str, slide: Any) -> list[dict[str, Any]]:
    """把 slide 内容映射进 family 槽位。返回 [{slot, text?, items?}, ...]。

    TP-H1：lead/body 重复防护通用化 —— 用 _norm_key 做归一化键，
    不依赖"特定词集合"或"特定页型"特殊判断（任务书第 1 条要求）。

    R8-U：页承载参数化 math_object → 保证 figure 槽位存在（用占位 id 表达），
    使图形进入导出链路（引擎把占位 id 换成确定性渲染的 PNG）。
    """
    slots_out = _map_slots_core(family_id, slide)
    if _has_math_object(slide) and not any(s.get("slot") == "figure" for s in slots_out):
        slots_out = [*slots_out, {"slot": "figure", "asset_ids": [MATH_OBJECT_ASSET_ID]}]
    return slots_out


def _map_slots_core(family_id: str, slide: Any) -> list[dict[str, Any]]:
    """family content_mapper 分派（不含 math_object 兜底注入）。"""
    mapper = REGISTRY[family_id].content_mapper
    lines = _lines(slide.content or "")
    bullets = _bullets(slide.content or "")
    prose = _prose(slide.content or "")

    if mapper == "hero":
        # R8-F：旧的硬编码 meta「教材直连 · SourceLocator 溯源见备注」是工程黑话
        # （SourceLocator/溯源见备注不得进入教师可见正文），且与 subtitle 语义重复
        # （subtitle 已承载教师可读教材标识，来自投影的 context.source，如
        # 「人教A版2019 · 选择性必修第一册」）→ 不再输出 meta 槽。
        # 溯源能力不删除：SourceLocator 继续写在 PPTX 备注（notes）里，不上可见正文。
        return [{"slot": "kicker", "text": "高中数学 · 课堂教学"},
                {"slot": "title", "text": slide.title},
                {"slot": "subtitle", "text": " ".join(prose) if prose else ""}]

    if mapper == "numbered":
        # 教学目标 / 提问：直接编号并列；空行/不同前缀不再制造重复
        return [{"slot": "title", "text": slide.title},
                {"slot": "numbered-items", "items": bullets or lines}]

    if mapper == "observe":
        return [{"slot": "title", "text": slide.title},
                {"slot": "figure", "asset_ids": list(slide.assets or [])},
                {"slot": "observations", "items": bullets or lines}]

    if mapper == "concept":
        stmt = prose[0] if prose else (lines[0] if lines else "")
        stmt_key = _norm_key(stmt or "")
        # 通用去重：归一化键相等即视为同句，全部剔除
        rest = [b for b in (bullets or prose[1:])
                if stmt_key and _norm_key(b) != stmt_key]
        slots_out: list[dict[str, Any]] = [
            {"slot": "title", "text": slide.title},
            {"slot": "concept-statement", "text": stmt},
            {"slot": "points-2col", "items": rest}]
        if slide.assets:
            slots_out.append({"slot": "figure", "asset_ids": list(slide.assets or [])})
        return slots_out

    if mapper == "definition":
        def_line = next((ln for ln in lines if any(k in ln for k in DEFINITION_STRONG)), None)
        if def_line is None:
            def_line = next((ln for ln in lines if any(k in ln for k in DEFINITION_WEAK)),
                            prose[0] if prose else (lines[0] if lines else ""))
        banner = _strip_banner_trailing_punct(def_line.lstrip("-• ").strip())
        banner_key = _norm_key(banner)
        rest = [ln.lstrip("-• ").strip() for ln in lines if _norm_key(ln) != banner_key]
        return [{"slot": "title", "text": slide.title},
                {"slot": "definition-banner", "text": banner},
                {"slot": "supporting", "items": rest}]

    if mapper == "equation":
        eq = max(lines, key=lambda ln: sum(ch in "∈∉≤≥≠±×÷²³∑∫√∞{}" for ch in ln), default="")
        eq_key = _norm_key(eq)
        ann = [ln for ln in lines if _norm_key(ln) != eq_key]
        return [{"slot": "title", "text": slide.title},
                {"slot": "equation", "text": eq},
                {"slot": "annotations", "items": [ln.lstrip("-• ").strip() for ln in ann]}]

    if mapper == "steps":
        return [{"slot": "title", "text": slide.title},
                {"slot": "steps", "items": lines}]

    if mapper == "worked":
        problem = next((ln for ln in lines if ln.startswith(("例", "问题"))), prose[0] if prose else "")
        problem_key = _norm_key(problem)
        steps = [ln for ln in lines if _norm_key(ln) != problem_key]
        return [{"slot": "title", "text": slide.title},
                {"slot": "problem", "text": problem},
                {"slot": "solution-steps", "items": steps}]

    if mapper == "practice":
        prompt = prose[0] if prose else (lines[0] if lines else "")
        prompt_key = _norm_key(prompt)
        hints = [b for b in bullets if _norm_key(b) != prompt_key] if prompt in prose \
            else [ln for ln in lines[1:] if _norm_key(ln) != prompt_key]
        return [{"slot": "title", "text": slide.title},
                {"slot": "prompt", "text": prompt},
                {"slot": "hints", "items": hints}]

    if mapper == "twocol":
        half = (len(lines) + 1) // 2
        return [{"slot": "title", "text": slide.title},
                {"slot": "column-left", "items": lines[:half]},
                {"slot": "column-right", "items": lines[half:]}]

    if mapper == "checkpoint":
        return [{"slot": "title", "text": slide.title},
                {"slot": "question", "text": prose[0] if prose else ""},
                {"slot": "check-items", "items": bullets or lines[1:]}]

    if mapper == "summary":
        return [{"slot": "title", "text": slide.title},
                {"slot": "map-2col", "items": bullets or lines}]

    return [{"slot": "title", "text": slide.title},
            {"slot": "body", "items": lines}]


# ─────────── 主入口 ───────────


def build_visual_plan(spec: Any) -> dict[str, Any]:
    """TeachingDeckSpec → ResolvedVisualPlan（可序列化 dict，无坐标）。"""
    assets_by_id = {a.id: a for a in spec.assets}
    narrative = build_narrative_plan(spec)

    intents: list[dict[str, Any]] = []
    phase_of = {p["slide_id"]: p["phase"] for p in narrative["narrative_phases"]}
    for slide in spec.slides:
        prev_role = None
        next_role = None
        asset_plan = build_asset_plan(slide, assets_by_id)
        intent = build_slide_intent(slide, prev_role, next_role, asset_plan, phase_of.get(slide.id, "CONCEPT_FORMATION"))
        intents.append(intent)
    # prev/next role 回填（三页联合决策输入）
    for i, intent in enumerate(intents):
        intent["_prev_role"] = intents[i - 1]["teaching_role"] if i > 0 else None
        intent["_next_role"] = intents[i + 1]["teaching_role"] if i + 1 < len(intents) else None

    plan_sequence(intents)

    slides_out: list[dict[str, Any]] = []
    for i, intent in enumerate(intents):
        slide = spec.slides[i]
        fam_id = intent["_chosen_family"]
        slides_out.append({
            "slide_id": slide.id,
            "page_no": i + 1,
            "teaching_role": intent["teaching_role"],
            "role_label_zh": intent["role_label_zh"],
            "narrative_phase": intent["narrative_phase"],
            "phase_zh": PHASE_ZH.get(intent["narrative_phase"], intent["narrative_phase"]),
            "layout_family": fam_id,
            "family_purpose_zh": REGISTRY[fam_id].teaching_purpose,
            "intent": {k: v for k, v in intent.items() if not k.startswith("_")},
            "slots": map_slots(fam_id, slide),
            "asset_bindings": [
                {"slot": "figure", "asset_id": aid, "asset_kind": assets_by_id[aid].kind if aid in assets_by_id else None}
                for aid in slide.assets
            ] + ([{"slot": "figure", "asset_id": MATH_OBJECT_ASSET_ID, "asset_kind": "MATH_RENDER"}]
                 if _has_math_object(slide) else []),
            "source_refs": intent["source_refs"],
        })

    plan = {
        "version": VERSION,
        "deck": {"id": spec.id, "title": spec.metadata.title, "revision": spec.revision,
                 "page_count": len(spec.slides)},
        "deck_narrative": narrative,
        "slide_order": [s.id for s in spec.slides],
        "slides": slides_out,
        "layout_family_manifest": family_manifest(),
        "style_tokens": tokens_for_engine(),
        "fallback_policy": {
            "visual_plan_missing": "引擎回退 legacy basic path（feature-disable 安全）",
            "family_unsupported": "引擎降级到最接近的已支持族并记录 warning",
            "overflow": "planner density 分流优先；禁止缩字号/裁内容（引擎 QA 复核）",
        },
        "engine_capability_requirements": {
            "editable_text": True, "editable_shapes": True, "replaceable_images": True,
            "notes": True, "cjk_font_stack": ["Microsoft YaHei", "SimSun"],
            "math": "unicode-equation + recipe-backed images；OMML 留引擎轮",
        },
        "source": {
            "spec_sha256": "",  # resolve 调用方填（steps 持有 spec_sha）
            "planner": VERSION,
            "llm_free": True,   # 本版为确定性规则规划；LLM 增强留后续轮
        },
    }
    return plan
