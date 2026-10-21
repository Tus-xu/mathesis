"""SlideSpec.elements 语义元素契约测试（FROZEN-CONTRACT §A，P0）。

覆盖验收：
- `elements` 可选默认 `[]`（旧 spec 只有 content → 完全兼容读取）；
- §A.2 role 枚举冻结（越界即拒绝）；
- §A.3 字段约束（text ≤400 / title ≤60 / items ≤10 且每项 ≤200 / figure_ref 仅 math_object）；
- 严禁引擎坐标（x/y/w/h 不进 schema；携带即被静默丢弃）；
- patch 通道可改 `elements`（教师主权）且影响面归入内容。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ppt_v2.domain.dependencies import compute_affected
from app.ppt_v2.domain.patch import (
    DeckPatchProposal,
    PatchOp,
    apply_proposal,
)
from app.ppt_v2.domain.spec import (
    ENGINE_GEOMETRY_FIELD_NAMES,
    SCHEMA_VERSION,
    SlideElement,
    SlideSpec,
    TeachingDeckSpec,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ── 1. 向后兼容 + 默认空 ─────────────────────────────────────────────────────


def test_existing_spec_without_elements_reads_with_empty_default() -> None:
    """旧 spec（只有 content，无 elements）读取后 elements == []（§A.1 向后兼容）。"""
    spec = TeachingDeckSpec.model_validate(_load("deck_golden_a.json"))
    assert spec.schema_version == SCHEMA_VERSION
    assert all(s.elements == [] for s in spec.slides)


def test_roundtrip_lossless_with_elements() -> None:
    """携带 elements 的 spec：model_dump → 再校验 → 语义不变。"""
    data = _load("deck_golden_a.json")
    data["slides"][1]["elements"] = [
        {"role": "definition", "text": "平面内与两定点距离之和为常数 $2a$ 的点的轨迹。"},
        {"role": "formula", "latex": r"\frac{x^2}{a^2}+\frac{y^2}{b^2}=1"},
        {"role": "figure", "figure_ref": "math_object"},
        {"role": "knowledge_card", "title": "标准方程", "latex": r"x^2/a^2+y^2/b^2=1", "items": ["2a 为长轴长"]},
        {"role": "bullets", "items": ["探究范围", "探究对称性"]},
        {"role": "note", "text": "用数学的眼光观察世界"},
    ]
    spec = TeachingDeckSpec.model_validate(data)
    assert len(spec.slides[1].elements) == 6
    assert spec.slides[1].elements[0].role == "definition"
    assert spec.slides[1].elements[2].figure_ref == "math_object"
    reloaded = TeachingDeckSpec.model_validate(spec.model_dump(mode="json"))
    assert reloaded.model_dump() == spec.model_dump()


# ── 2. §A.2 role 枚举冻结 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role",
    ["definition", "formula", "knowledge_card", "figure", "bullets", "note", "steps", "table"],
)
def test_role_enum_accepts_contract_values(role: str) -> None:
    el = SlideElement.model_validate({"role": role})
    assert el.role == role


def test_role_enum_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        SlideElement.model_validate({"role": "banner"})


def test_role_is_required() -> None:
    with pytest.raises(ValidationError):
        SlideElement.model_validate({"text": "x"})


# ── 3. §A.3 字段约束 ─────────────────────────────────────────────────────────


def test_text_length_bounded() -> None:
    with pytest.raises(ValidationError):
        SlideElement.model_validate({"role": "note", "text": "字" * 401})


def test_title_length_bounded() -> None:
    with pytest.raises(ValidationError):
        SlideElement.model_validate({"role": "knowledge_card", "title": "字" * 61})


def test_items_count_bounded() -> None:
    with pytest.raises(ValidationError):
        SlideElement.model_validate({"role": "bullets", "items": ["x"] * 11})


def test_each_item_length_bounded() -> None:
    with pytest.raises(ValidationError):
        SlideElement.model_validate({"role": "bullets", "items": ["字" * 201]})


def test_figure_ref_only_math_object() -> None:
    with pytest.raises(ValidationError):
        SlideElement.model_validate({"role": "figure", "figure_ref": "image-42"})
    ok = SlideElement.model_validate({"role": "figure", "figure_ref": "math_object"})
    assert ok.figure_ref == "math_object"


# ── 4. 严禁引擎坐标 ──────────────────────────────────────────────────────────


def test_element_model_has_no_geometry_fields() -> None:
    assert set(SlideElement.model_fields).isdisjoint(set(ENGINE_GEOMETRY_FIELD_NAMES))


def test_schema_has_no_geometry_property_in_elements() -> None:
    schema = json.loads(json.dumps(TeachingDeckSpec.model_json_schema(), ensure_ascii=False))
    elem = schema["$defs"]["SlideElement"]["properties"]
    assert set(elem).isdisjoint(set(ENGINE_GEOMETRY_FIELD_NAMES))


def test_geometry_fields_on_element_are_ignored() -> None:
    """extra=ignore：元素 payload 携带 x/y/w/h 被静默丢弃（reader 策略），不崩溃。"""
    el = SlideElement.model_validate({"role": "note", "text": "x", "x": 10, "y": 20})
    assert not hasattr(el, "x")


# ── 5. patch 通道（教师主权）：elements 可改且归入内容影响面 ──────────────────


def test_elements_is_mutable_slide_field() -> None:
    from app.ppt_v2.domain.spec import SLIDE_MUTABLE_FIELDS

    assert "elements" in SLIDE_MUTABLE_FIELDS


def test_set_slide_elements_applies_and_marks_content_affected() -> None:
    spec = TeachingDeckSpec.model_validate(_load("deck_golden_b.json"))
    target = spec.slides[1].id
    affected = compute_affected(spec, op="set_slide", target=target, field="elements")
    assert target in affected.content_affected

    proposal = DeckPatchProposal(
        deck_id=spec.id,
        expected_revision=spec.revision,
        summary="补充定义带",
        operations=[
            PatchOp(
                op="set_slide",
                target=target,
                field="elements",
                value=[{"role": "definition", "text": "集合的定义。"}],
            )
        ],
    )
    new_spec, _patch, _log = apply_proposal(spec, proposal)
    updated = next(s for s in new_spec.slides if s.id == target)
    assert updated.elements[0].role == "definition"
    # 其它页的 elements 不受影响（局部性不变量）
    assert all(s.elements == [] for s in new_spec.slides if s.id != target)


def test_slide_spec_helper_accepts_elements() -> None:
    s = SlideSpec.model_validate(
        {
            "id": "s1",
            "type": "concept",
            "teaching_goal": "g",
            "title": "t",
            "elements": [{"role": "steps", "items": ["第一步", "第二步"]}],
        }
    )
    assert s.elements[0].role == "steps"
