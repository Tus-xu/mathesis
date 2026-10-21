"""R21.1 Visual Planning Layer 单元测试（规则规划器 + 多样性控制 + deck QA）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.visual_planning import VERSION, build_visual_plan
from app.visual_planning.qa_deck import run_deck_qa
from app.visual_planning.sequence import PlanningFailure, plan_sequence

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden-deck-spec.json").read_text(encoding="utf-8")
)


@pytest.fixture()
def golden_spec():
    from app.contracts.deck import TeachingDeckSpec

    return TeachingDeckSpec.model_validate(GOLDEN)


def test_plan_golden_shape_and_diversity(golden_spec) -> None:
    plan = build_visual_plan(golden_spec)
    assert plan["version"] == VERSION
    assert len(plan["slides"]) == 10
    fams = [s["layout_family"] for s in plan["slides"]]
    assert len(set(fams)) >= 4, f"layout diversity {len(set(fams))} < 4: {fams}"
    # 同族连续 ≤2
    run = 1
    for i in range(1, len(fams)):
        run = run + 1 if fams[i] == fams[i - 1] else 1
        assert run <= 2, f"同族 {fams[i]} 连续 >2 @page {i + 1}"
    qa = run_deck_qa(plan)
    assert qa["visual"]["pass"], qa["visual"]["issues"]
    assert qa["narrative"]["pass"], qa["narrative"]["issues"]


def test_intent_fields_complete(golden_spec) -> None:
    plan = build_visual_plan(golden_spec)
    required = {
        "teaching_role", "communication_goal", "primary_message", "attention_anchor",
        "visual_priority", "visual_asset_need", "content_density", "interaction_mode",
        "formula_density", "diagram_need", "example_need", "comparison_need",
        "layout_family_candidates", "avoid_patterns", "continuity_with_previous",
        "contrast_with_previous", "source_refs", "narrative_phase",
    }
    for s in plan["slides"]:
        missing = required - set(s["intent"].keys())
        assert not missing, f"{s['slide_id']} 缺字段: {missing}"


def test_definition_slot_mapping_banner(golden_spec) -> None:
    """slide-05 定义句（含"叫做列举法"）必须进 definition-banner，示例进 supporting。"""
    plan = build_visual_plan(golden_spec)
    s5 = next(s for s in plan["slides"] if s["slide_id"] == "slide-05")
    slots = {sl["slot"]: sl for sl in s5["slots"]}
    banner = slots.get("definition-banner", {}).get("text", "")
    assert "列举法" in banner
    supporting = " ".join(slots.get("supporting", {}).get("items", []))
    assert "太平洋" in supporting and "太平洋" not in banner


def test_no_geometry_keys_in_plan(golden_spec) -> None:
    """ResolvedVisualPlan 禁止携带坐标（引擎中立铁律）。"""
    plan = build_visual_plan(golden_spec)
    qa = run_deck_qa(plan)
    assert not [i for i in qa["visual"]["issues"] if i.startswith("geometry-key-leak")]


def test_definition_banner_has_no_trailing_punct(golden_spec) -> None:
    """F3 回归：定义横幅（标题式元素）尾部句末标点被剔除 → 不再被换行甩出孤立「。」。"""
    plan = build_visual_plan(golden_spec)
    for s in plan["slides"]:
        for sl in s["slots"]:
            if sl["slot"] == "definition-banner":
                assert not sl["text"].endswith(("。", "．", ".", "！", "？")), sl["text"]


def test_hero_cover_has_no_internal_jargon(golden_spec) -> None:
    """F2 回归：封面可见文本不得出现工程黑话（SourceLocator/教材直连/溯源见备注）。"""
    plan = build_visual_plan(golden_spec)
    cover = plan["slides"][0]
    visible: list[str] = []
    for sl in cover["slots"]:
        if sl.get("text"):
            visible.append(sl["text"])
        visible.extend(sl.get("items", []))
    joined = " ".join(visible)
    assert "SourceLocator" not in joined, joined
    assert "教材直连" not in joined, joined
    assert "溯源见备注" not in joined, joined


def test_diversity_controller_blocks_monotony(golden_spec) -> None:
    """8 连同 role 页必须被 controller 分散到多个 family。"""
    for s in golden_spec.slides:
        s.type = "concept"
    plan = build_visual_plan(golden_spec)
    fams = [s["layout_family"] for s in plan["slides"]]
    assert len(set(fams)) >= 2, "controller 未打破同 role 单调"


def test_unjustified_text_only_flagged(golden_spec) -> None:
    """image-focus 页无资产 → visual QA 失败（unjustified-text-only）。"""
    golden_spec.slides[7].assets = []  # slide-08 数轴页去图
    plan = build_visual_plan(golden_spec)
    qa = run_deck_qa(plan)
    assert not qa["visual"]["pass"]
    assert any(i.startswith("unjustified-text-only") for i in qa["visual"]["issues"])


def test_sequence_failure_raises() -> None:
    """候选被约束清空 → PlanningFailure（不静默退化为重复布局）。"""
    intents = []
    for i in range(3):
        intents.append({
            "teaching_role": "MODEL_EXAMPLE",
            "narrative_phase": "WORKED_EXAMPLE",
            "layout_family_candidates": ["worked-example"],
            "visual_asset_need": {"bound_asset_ids": ["a"] if i % 2 else []},
        })
    # worked-example 是唯一候选且连续 3 页 → 无可用候选 → 失败
    with pytest.raises(PlanningFailure):
        plan_sequence(intents)


def _intent(role: str, phase: str, cands: list[str], bound: list[str] | None = None) -> dict:
    return {
        "teaching_role": role,
        "narrative_phase": phase,
        "layout_family_candidates": list(cands),
        "visual_asset_need": {"bound_asset_ids": list(bound or [])},
    }


def test_backtracking_recovers_from_tail_phase_switch() -> None:
    """F4 回归：单候选页（worked-example）紧随 text-only streak，须靠回溯换出可行解。

    复现 v2new deck（bdc417b8）真实 role/phase/candidate 序列：旧实现 tried 状态被永久
    污染，8 轮内找不到解 → PlanningFailure；修复后回溯会清空下游 tried，找到可行解。
    """
    intents = [
        _intent("TITLE_OPENER", "HOOK", ["hero-context"]),
        _intent("CONCEPT_BUILD", "CONCEPT_FORMATION", ["concept-focus", "definition-emphasis"]),
        _intent("CONTEXT_HOOK", "HOOK", ["question-prompt", "observe-and-infer"]),
        _intent("CONCEPT_BUILD", "CONCEPT_FORMATION", ["concept-focus", "definition-emphasis"], ["__math_object__"]),
        _intent("REASONING_CHAIN", "DERIVATION", ["progressive-derivation", "equation-focus"]),
        _intent("QUESTION_RAISE", "QUESTION", ["question-prompt", "checkpoint"]),
        _intent("GUIDED_PRACTICE", "PRACTICE", ["practice-prompt", "worked-example"]),
        _intent("MODEL_EXAMPLE", "WORKED_EXAMPLE", ["worked-example"]),
        _intent("GUIDED_PRACTICE", "PRACTICE", ["practice-prompt", "worked-example"]),
        _intent("SYNTHESIS_MAP", "SYNTHESIS", ["summary-map"]),
    ]
    out = plan_sequence(intents)  # 不得抛 PlanningFailure
    fams = [it["_chosen_family"] for it in out]
    assert all(fams), fams
    # 同族连续 ≤2
    run = 1
    for i in range(1, len(fams)):
        run = run + 1 if fams[i] == fams[i - 1] else 1
        assert run <= 2, fams
    # 阶段切换处必须换族
    for i in range(1, len(fams)):
        if out[i]["narrative_phase"] != out[i - 1]["narrative_phase"]:
            assert fams[i] != fams[i - 1], fams
    # 族必须落在该页候选内
    for it, fam in zip(out, fams):
        assert fam in it["layout_family_candidates"]
    # 尾部单候选页（worked-example）确实被排入
    assert fams[7] == "worked-example"


def test_plan_sequence_is_deterministic() -> None:
    """回溯搜索必须确定性（同输入 → 同输出）。"""
    def build():
        return [
            _intent("TITLE_OPENER", "HOOK", ["hero-context"]),
            _intent("CONCEPT_BUILD", "CONCEPT_FORMATION", ["concept-focus", "definition-emphasis"]),
            _intent("CONTEXT_HOOK", "HOOK", ["question-prompt", "observe-and-infer"]),
            _intent("CONCEPT_BUILD", "CONCEPT_FORMATION", ["concept-focus", "definition-emphasis"]),
            _intent("REASONING_CHAIN", "DERIVATION", ["progressive-derivation", "equation-focus"]),
            _intent("QUESTION_RAISE", "QUESTION", ["question-prompt", "checkpoint"]),
            _intent("GUIDED_PRACTICE", "PRACTICE", ["practice-prompt", "worked-example"]),
            _intent("MODEL_EXAMPLE", "WORKED_EXAMPLE", ["worked-example"]),
            _intent("GUIDED_PRACTICE", "PRACTICE", ["practice-prompt", "worked-example"]),
            _intent("SYNTHESIS_MAP", "SYNTHESIS", ["summary-map"]),
        ]
    a = [it["_chosen_family"] for it in plan_sequence(build())]
    b = [it["_chosen_family"] for it in plan_sequence(build())]
    assert a == b


def test_phase_switch_forces_family_change(golden_spec) -> None:
    plan = build_visual_plan(golden_spec)
    slides = plan["slides"]
    for i in range(1, len(slides)):
        if slides[i]["narrative_phase"] != slides[i - 1]["narrative_phase"]:
            assert slides[i]["layout_family"] != slides[i - 1]["layout_family"], \
                f"阶段切换页 {i + 1} 未换族"
