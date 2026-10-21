"""R4 参数化数学对象（math_object）与页操作 patch 测试。

验收锚点（ACCEPTANCE-MATRIX 4.3/4.4/4.6 的领域层）：
- math_object 只存独立参数 a/b，c 恒派生且数学正确（c=√(a²−b²)）；
- a > b > 0 数学红线：非法参数的 spec/patch 一律拒绝；
- set_slide.math_object patch：revision+1、影响面只含目标页；
- move_slide：页序真实调整、影响面=全部页、越界拒绝；
- add_slide + move_slide 组合提案（AI 新增页）：同提案内 add 的页可立即 move。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ppt_v2.domain.errors import PatchValidationError
from app.ppt_v2.domain.patch import DeckPatchProposal, PatchOp, apply_proposal
from app.ppt_v2.domain.spec import MathObjectSpec, TeachingDeckSpec

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _deck(name: str = "deck_golden_a.json") -> TeachingDeckSpec:
    return TeachingDeckSpec.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _proposal(deck_id: str, expected_revision: int, ops: list[PatchOp], summary: str = "test") -> DeckPatchProposal:
    return DeckPatchProposal(
        deck_id=deck_id, expected_revision=expected_revision, summary=summary, source="human", operations=ops
    )


# ── math_object 领域校验 ─────────────────────────────────────────────────────


def test_math_object_focal_distance_is_derived_and_correct() -> None:
    mo = MathObjectSpec(kind="ellipse", params={"a": 5.0, "b": 3.0})
    assert mo.focal_distance == pytest.approx(4.0)  # c=√(25−9)=4
    mo6 = MathObjectSpec(kind="ellipse", params={"a": 6.0, "b": 3.0})
    assert mo6.focal_distance == pytest.approx((36 - 9) ** 0.5)


def test_math_object_rejects_invalid_math() -> None:
    with pytest.raises(ValueError, match="a > b > 0"):
        MathObjectSpec(kind="ellipse", params={"a": 3.0, "b": 5.0})
    with pytest.raises(ValueError, match="a > b > 0"):
        MathObjectSpec(kind="ellipse", params={"a": 4.0, "b": 4.0})  # 退化为圆，不是椭圆
    with pytest.raises(ValueError, match="未知参数"):
        MathObjectSpec(kind="ellipse", params={"a": 5.0, "b": 3.0, "c": 4.0})  # c 是派生值禁止写入
    with pytest.raises(ValueError, match="数值参数"):
        MathObjectSpec(kind="ellipse", params={"a": 5.0})


def test_math_object_defaults_match_visual_spec() -> None:
    """黄金图 4 开关：前三项开、数值标注默认关。"""
    mo = MathObjectSpec(kind="ellipse", params={"a": 5, "b": 3})
    assert mo.toggles.show_foci is True
    assert mo.toggles.show_axes is True
    assert mo.toggles.show_aux_lines is True
    assert mo.toggles.show_value_labels is False
    assert mo.style.stroke_px == 2
    assert mo.style.dash == "solid"


# ── patch 通道：set_slide.math_object ───────────────────────────────────────


def test_patch_updates_math_object_params() -> None:
    spec = _deck()
    spec.slides[2] = spec.slides[2].model_copy(
        update={"math_object": MathObjectSpec(kind="ellipse", params={"a": 5.0, "b": 3.0})}
    )
    new_mo = MathObjectSpec(kind="ellipse", params={"a": 6.0, "b": 3.0}).model_dump(mode="json")
    proposal = _proposal(
        spec.id,
        spec.revision,
        [PatchOp(op="set_slide", target=spec.slides[2].id, field="math_object", value=new_mo)],
        summary="长半轴 5→6",
    )
    new_spec, patch, _ = apply_proposal(spec, proposal)
    assert new_spec.revision == spec.revision + 1
    updated = next(s for s in new_spec.slides if s.id == spec.slides[2].id)
    assert updated.math_object is not None and updated.math_object.semi_major == 6.0
    assert updated.math_object.focal_distance == pytest.approx((36 - 9) ** 0.5)
    assert updated.revision == spec.slides[2].revision + 1
    # math_object 属渲染元数据（不触发内容/版式重生成，只触发图形重渲）——
    # 与 speaker_note 同一影响面语义：不进入 content/layout 集合
    assert patch.affected.content_affected == frozenset()
    assert patch.affected.layout_affected == frozenset()


def test_patch_rejects_mathematically_wrong_params() -> None:
    spec = _deck()
    bad = {"kind": "ellipse", "params": {"a": 2.0, "b": 3.0}}
    proposal = _proposal(
        spec.id,
        spec.revision,
        [PatchOp(op="set_slide", target=spec.slides[0].id, field="math_object", value=bad)],
    )
    with pytest.raises(PatchValidationError, match="a > b > 0"):
        apply_proposal(spec, proposal)
    assert spec.revision == 3  # 原 spec 无副作用


# ── move_slide（页操作：排序）────────────────────────────────────────────────


def test_move_slide_reorders_and_affects_all() -> None:
    spec = _deck("deck_golden_b.json")
    order_before = [s.id for s in spec.slides]
    proposal = _proposal(
        spec.id,
        spec.revision,
        [PatchOp(op="move_slide", target=order_before[1], value={"index": 3})],
        summary="第 2 页后移",
    )
    new_spec, patch, _ = apply_proposal(spec, proposal)
    order_after = [s.id for s in new_spec.slides]
    assert order_after[3] == order_before[1]
    assert len(order_after) == len(order_before)
    assert patch.affected.slide_ids == frozenset(s.id for s in spec.slides)


def test_move_slide_out_of_range_rejected() -> None:
    spec = _deck("deck_golden_b.json")
    proposal = _proposal(
        spec.id,
        spec.revision,
        [PatchOp(op="move_slide", target=spec.slides[0].id, value={"index": 99})],
    )
    with pytest.raises(PatchValidationError, match="越界"):
        apply_proposal(spec, proposal)


def test_add_slide_then_move_in_same_proposal() -> None:
    """AI 新增页组合：add_slide 目标在 validate 阶段尚不存在，但同提案内可见。"""
    spec = _deck("deck_golden_b.json")
    value = {
        "type": "concept",
        "teaching_goal": "随堂巩固",
        "title": "新页",
        "content": "新页正文（合成）",
        "source_refs": [],
    }
    ops = [
        PatchOp(op="add_slide", target="slide-new-01", value=value),
        PatchOp(op="move_slide", target="slide-new-01", value={"index": 1}),
    ]
    new_spec, _patch, _log = apply_proposal(spec, _proposal(spec.id, spec.revision, ops))
    assert [s.id for s in new_spec.slides][1] == "slide-new-01"
    assert len(new_spec.slides) == len(spec.slides) + 1
