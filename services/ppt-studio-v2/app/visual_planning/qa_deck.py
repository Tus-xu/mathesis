"""deck-level Visual QA + Narrative QA（任务书 §23）。

与页级确定性 QA（overflow/overlap/截断，8201 qa.py）互补：
- 本模块在 planner 层对 ResolvedVisualPlan 做 deck 级检查；
- 全部检查可解释、可定位到 narrative/planning/layout 层；
- 图形坐标扫描：ResolvedVisualPlan 禁止携带 x/y/w/h（引擎中立铁律）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.visual_planning.families import REGISTRY

MAX_SAME_FAMILY_RUN = 2
MAX_TEXT_ONLY_RUN = 2

INTERNAL_LABEL_RE = re.compile(
    r"\b(layout_hint|layoutHint|slide_type|concept|definition|derivation|"
    r"worked-example|image-focus|inquiry|summary|overview)\b", re.IGNORECASE,
)

GEOMETRY_KEY_RE = re.compile(r"^(x|y|w|h|width|height|left|top|cx|cy)$", re.IGNORECASE)


def _runs(values: list[str]) -> list[tuple[str, int]]:
    runs: list[tuple[str, int]] = []
    for v in values:
        if runs and runs[-1][0] == v:
            runs[-1] = (v, runs[-1][1] + 1)
        else:
            runs.append((v, 1))
    return runs


def run_deck_qa(plan: dict[str, Any]) -> dict[str, Any]:
    slides = plan["slides"]
    visual_issues: list[str] = []
    narrative_issues: list[str] = []

    # ── 坐标零容忍 ──
    def _scan_geometry(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if GEOMETRY_KEY_RE.match(str(k)):
                    visual_issues.append(f"geometry-key-leak:{path}.{k}")
                _scan_geometry(v, f"{path}.{k}")
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                _scan_geometry(v, f"{path}[{idx}]")

    _scan_geometry(plan, "plan")

    # ── Visual QA ──
    fams = [s["layout_family"] for s in slides]
    for fam, run in _runs(fams):
        if run > MAX_SAME_FAMILY_RUN:
            visual_issues.append(f"layout-repetition:{fam}x{run}")

    text_only_run = 0
    for s in slides:
        text_only_run = text_only_run + 1 if REGISTRY[s["layout_family"]].is_text_only else 0
        if text_only_run > MAX_TEXT_ONLY_RUN:
            visual_issues.append(f"text-only-streak:{s['slide_id']}")
            text_only_run = 0

    # 视觉干旱：连续 ≥4 页无视觉锚（无图族 + 无绑定资产 + 无公式焦点）
    drought = 0
    for s in slides:
        has_visual = (
            REGISTRY[s["layout_family"]].has_visual_slot
            or s["asset_bindings"]
            or s["intent"]["formula_density"] in ("medium", "high")
        )
        drought = drought + 1 if not has_visual else 0
        if drought >= 4:
            visual_issues.append(f"visual-drought:{s['slide_id']}")
            drought = 0

    # 密度单调（warning 级）：density 由 spec 内容决定（事实源不可篡改），
    # planner 只能通过 family 形态调节视觉重量 → 同 density 连续 ≥4 页给 warning 不 fail
    warnings: list[str] = []
    dens = [s["intent"]["content_density"] for s in slides]
    for d, run in _runs(dens):
        if run >= 4:
            warnings.append(f"density-uniform:{d}x{run}(用 family 形态调节视觉重量)")

    # card 骨架滥用：全篇 card 骨架族占比 > 40%
    card_fams = sum(1 for s in slides if REGISTRY[s["layout_family"]].card_skeleton)
    if card_fams > len(slides) * 0.4:
        visual_issues.append(f"card-overuse:{card_fams}/{len(slides)}")

    # role-family 适配：family 必须在该 role 候选内（防止任选）
    for s in slides:
        if s["layout_family"] not in s["intent"]["layout_family_candidates"]:
            visual_issues.append(f"role-family-mismatch:{s['slide_id']}:{s['layout_family']}")

    # 需图示页无理由纯文字
    for s in slides:
        need = s["intent"]["visual_asset_need"]
        if need.get("text_only_risk") and not need.get("justification"):
            visual_issues.append(f"unjustified-text-only:{s['slide_id']}")

    # 内部标签泄漏（版面可见文本 = slots 文本 + role_label_zh；kicker 用 phase_zh）
    for s in slides:
        visible = [s.get("role_label_zh", ""), s.get("phase_zh", ""),
                   s.get("family_purpose_zh", "")]
        for slot in s["slots"]:
            if slot.get("text"):
                visible.append(slot["text"])
            visible.extend(slot.get("items", []))
        joined = " ".join(v for v in visible if v)
        if INTERNAL_LABEL_RE.search(joined):
            hit = INTERNAL_LABEL_RE.search(joined).group(0)
            visual_issues.append(f"internal-label-leak:{s['slide_id']}:{hit}")

    # ── Narrative QA ──
    phases = [s["narrative_phase"] for s in slides]
    first_idx = {p: i for i, p in enumerate(phases)}
    if "HOOK" not in phases and "OBSERVE" not in phases:
        narrative_issues.append("missing-introduction-phase")
    if "CONCEPT_FORMATION" in phases:
        cf = first_idx["CONCEPT_FORMATION"]
        if not any(p in ("HOOK", "OBSERVE", "QUESTION") for p in phases[:cf]):
            narrative_issues.append("concept-before-evidence")
    if "WORKED_EXAMPLE" in phases:
        we = first_idx["WORKED_EXAMPLE"]
        if not any(p in ("CONCEPT_FORMATION", "OBSERVE") for p in phases[:we]):
            narrative_issues.append("example-before-concept")
    if "PRACTICE" in phases and "WORKED_EXAMPLE" not in phases and "CONCEPT_FORMATION" not in phases[:first_idx["PRACTICE"]]:
        narrative_issues.append("practice-detects-nothing")
    if phases[-1] != "SYNTHESIS":
        narrative_issues.append("missing-synthesis-closing")
    # summary 页必须综合（有结构化 items，不是复读标题）
    last = slides[-1]
    if last["narrative_phase"] == "SYNTHESIS":
        items = next((sl.get("items") for sl in last["slots"] if sl["slot"] in ("map-2col", "supporting")), None)
        if not items or len(items) < 3:
            narrative_issues.append("summary-too-thin")

    visual_pass = not visual_issues
    narrative_pass = not narrative_issues
    return {
        "visual": {"pass": visual_pass, "issues": visual_issues, "warnings": warnings},
        "narrative": {"pass": narrative_pass, "issues": narrative_issues},
        "checks_version": "r21.1-deckqa-1.0.0",
        "summary": {
            "families_used": sorted(set(fams)),
            "family_run_max": max(run for _, run in _runs(fams)),
            "layout_diversity": len(set(fams)),
        },
    }


def plan_to_json(plan: dict[str, Any]) -> bytes:
    return json.dumps(plan, ensure_ascii=False, sort_keys=False, separators=(",", ":")).encode("utf-8")
