"""DeckPatch 应用与乐观并发测试（R10）。

覆盖验收门：revision conflict、只改一页、换主题影响全部 layout 但不改教学内容、
add/remove slide、asset rights 变更、preserve_original 违规、change_log 追加。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ppt_v2.domain.errors import (
    PatchValidationError,
    PreserveOriginalViolationError,
    RevisionConflictError,
    UnknownSlideError,
)
from app.ppt_v2.domain.patch import DeckPatchProposal, PatchOp, apply_proposal
from app.ppt_v2.domain.spec import TeachingDeckSpec

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _deck(name: str = "deck_golden_a.json") -> TeachingDeckSpec:
    return TeachingDeckSpec.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _proposal(deck_id: str, expected_revision: int, ops: list[PatchOp], summary: str = "test") -> DeckPatchProposal:
    return DeckPatchProposal(
        deck_id=deck_id, expected_revision=expected_revision, summary=summary, source="human", operations=ops
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. 乐观并发与原子性
# ─────────────────────────────────────────────────────────────────────────────


def test_revision_conflict_rejected_without_side_effect() -> None:
    spec = _deck()  # revision=3
    stale = _proposal("deck-golden-a", 2, [PatchOp(op="set_slide", target="s1", field="title", value="新标题")])
    with pytest.raises(RevisionConflictError) as exc:
        apply_proposal(spec, stale)
    assert exc.value.expected == 2 and exc.value.actual == 3
    # 原 spec 未变（apply 在副本上进行）
    assert spec.revision == 3


def test_deck_id_mismatch_rejected() -> None:
    spec = _deck()
    proposal = _proposal("other-deck", 3, [PatchOp(op="set_slide", target="s1", field="title", value="x")])
    with pytest.raises(PatchValidationError, match="deck_id"):
        apply_proposal(spec, proposal)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 只改一页：revision+1、change_log、slide revision、影响面
# ─────────────────────────────────────────────────────────────────────────────


def test_change_single_slide_content() -> None:
    spec = _deck("deck_golden_a.json")
    before = {s.model_dump_json() for s in spec.slides if s.id != "s3"}
    _rev_before = {s.id: s.revision for s in spec.slides}

    proposal = _proposal(
        "deck-golden-a",
        3,
        [PatchOp(op="set_slide", target="s3", field="content", value="新定义文本（合成）")],
        summary="只改 s3 内容",
    )
    new_spec, patch, log = apply_proposal(spec, proposal, patch_id="patch-only-s3")

    assert new_spec.revision == 4
    assert patch.base_revision == 3 and patch.result_revision == 4
    assert log.revision == 4 and log.patch_ids == ["patch-only-s3"]
    assert patch.affected.slide_ids == frozenset({"s3"})
    assert patch.affected.content_affected == frozenset({"s3"})

    # 其他页内容与 revision 一律未变
    after = {s.model_dump_json() for s in new_spec.slides if s.id != "s3"}
    assert after == before
    for s in new_spec.slides:
        if s.id != "s3":
            assert s.revision == _rev_before[s.id]
    s3 = next(s for s in new_spec.slides if s.id == "s3")
    assert s3.revision == _rev_before["s3"] + 1
    assert s3.content == "新定义文本（合成）"
    assert new_spec.change_log[-1].summary == "只改 s3 内容"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 换主题：全部 layout 受影响，教学内容不变
# ─────────────────────────────────────────────────────────────────────────────


def test_theme_change_layout_all_content_unchanged() -> None:
    spec = _deck("deck_golden_a.json")
    before_content = {s.id: s.content for s in spec.slides}

    proposal = _proposal(
        "deck-golden-a",
        3,
        [PatchOp(op="set_theme", value={"theme_id": "synth-theme-blackboard", "version": "0.9.0"})],
        summary="换主题",
    )
    new_spec, patch, _ = apply_proposal(spec, proposal)

    assert new_spec.revision == 4
    assert patch.affected.whole_deck_layout is True
    assert patch.affected.layout_affected == frozenset(s.id for s in spec.slides)
    assert not patch.affected.content_affected
    assert new_spec.theme is not None and new_spec.theme.theme_id == "synth-theme-blackboard"
    # 教学内容逐字不变 + slide revision 不因主题而自增
    assert {s.id: s.content for s in new_spec.slides} == before_content
    assert {s.id: s.revision for s in new_spec.slides} == {s.id: s.revision for s in spec.slides}


# ─────────────────────────────────────────────────────────────────────────────
# 4. add/remove slide 与 asset
# ─────────────────────────────────────────────────────────────────────────────


def test_add_and_remove_slide() -> None:
    spec = _deck("deck_golden_b.json")  # revision=0
    new_slide = {
        "type": "practice",
        "teaching_goal": "补充练习",
        "title": "练习 3",
        "content": "写出 {a,b,c} 的子集个数。",
        "layout_hint": "practice",
    }
    add = _proposal("deck-golden-b", 0, [PatchOp(op="add_slide", target="b9", value=new_slide)], "加练习页")
    with_add, patch_add, _ = apply_proposal(spec, add)
    assert with_add.revision == 1
    assert patch_add.affected.slide_ids == frozenset({"b9"})
    assert [s.id for s in with_add.slides] == ["b1", "b2", "b3", "b4", "b9"]
    assert patch_add.affected.content_affected == frozenset({"b9"})

    remove = _proposal(
        "deck-golden-b", 1, [PatchOp(op="remove_slide", target="b9")], "删除练习页"
    )
    after_remove, patch_rm, _ = apply_proposal(with_add, remove)
    assert after_remove.revision == 2
    assert patch_rm.affected.slide_ids == frozenset({"b9"})
    assert [s.id for s in after_remove.slides] == ["b1", "b2", "b3", "b4"]


def test_add_asset_then_reference_from_slide() -> None:
    spec = _deck("deck_golden_b.json")
    asset_payload = {
        "kind": "TEACHER_UPLOAD",
        "object_key": "ppt_v2/decks/deck-golden-b/assets/upload-ab.png",
        "sha256": "1" * 64,
        "mime_type": "image/png",
        "size_bytes": 4096,
        "rights_status": "STAGED",
        "preserve_original": True,
    }
    add_asset = _proposal(
        "deck-golden-b",
        0,
        [PatchOp(op="add_asset", target="asset-upload-1", value=asset_payload)],
        "上传原题图",
    )
    with_asset, _, _ = apply_proposal(spec, add_asset)
    assert with_asset.revision == 1
    assert len(with_asset.assets) == 1
    assert with_asset.assets[0].kind == "TEACHER_UPLOAD"

    # 引用新资产
    link = _proposal(
        "deck-golden-b",
        1,
        [PatchOp(op="set_slide", target="b3", field="assets", value=["asset-upload-1"])],
        "例题引用原图",
    )
    linked, _, _ = apply_proposal(with_asset, link)
    s3 = next(s for s in linked.slides if s.id == "b3")
    assert s3.assets == ["asset-upload-1"]
    assert s3.revision == 1


def test_remove_slide_clears_speaker_notes() -> None:
    spec = _deck("deck_golden_a.json")
    assert "s1" in spec.speaker_notes
    remove = _proposal("deck-golden-a", 3, [PatchOp(op="remove_slide", target="s1")], "删除标题页")
    after, _, _ = apply_proposal(spec, remove)
    assert "s1" not in after.speaker_notes
    assert all(s.id != "s1" for s in after.slides)


# ─────────────────────────────────────────────────────────────────────────────
# 5. preserve_original 与不可变字段
# ─────────────────────────────────────────────────────────────────────────────


def test_teacher_upload_preserve_original_violation_mapped() -> None:
    spec = _deck("deck_golden_b.json")
    bad = {
        "kind": "TEACHER_UPLOAD",
        "object_key": "k/bad.png",
        "sha256": "2" * 64,
        "mime_type": "image/png",
        "size_bytes": 1,
        "preserve_original": False,
    }
    proposal = _proposal("deck-golden-b", 0, [PatchOp(op="add_asset", target="a-bad", value=bad)])
    with pytest.raises(PreserveOriginalViolationError):
        apply_proposal(spec, proposal)


def test_asset_immutable_fields_rejected() -> None:
    spec = _deck("deck_golden_a.json")
    proposal = _proposal(
        "deck-golden-a",
        3,
        [PatchOp(op="set_asset", target="asset-fig-1", field="object_key", value="hacked/key")],
    )
    with pytest.raises(PatchValidationError, match="不可变"):
        apply_proposal(spec, proposal)


def test_slide_id_immutable_rejected() -> None:
    spec = _deck("deck_golden_a.json")
    proposal = _proposal("deck-golden-a", 3, [PatchOp(op="set_slide", target="s1", field="id", value="s-other")])
    with pytest.raises(PatchValidationError):
        apply_proposal(spec, proposal)


def test_unknown_slide_target_rejected() -> None:
    spec = _deck("deck_golden_a.json")
    proposal = _proposal("deck-golden-a", 3, [PatchOp(op="set_slide", target="ghost", field="title", value="x")])
    with pytest.raises(UnknownSlideError):
        apply_proposal(spec, proposal)


# ─────────────────────────────────────────────────────────────────────────────
# 6. 元数据 / 策略 / 大纲 patch
# ─────────────────────────────────────────────────────────────────────────────


def test_set_metadata_title() -> None:
    spec = _deck("deck_golden_a.json")
    proposal = _proposal(
        "deck-golden-a", 3, [PatchOp(op="set_metadata", field="title", value="1.1 合成集合的概念（修订）")]
    )
    new_spec, patch, _ = apply_proposal(spec, proposal)
    assert new_spec.metadata.title == "1.1 合成集合的概念（修订）"
    assert patch.affected.layout_affected == frozenset(s.id for s in spec.slides)
    assert not patch.affected.content_affected


def test_set_metadata_unknown_field_rejected() -> None:
    spec = _deck("deck_golden_a.json")
    proposal = _proposal("deck-golden-a", 3, [PatchOp(op="set_metadata", field="pixels", value=1)])
    with pytest.raises(PatchValidationError, match="白名单"):
        apply_proposal(spec, proposal)


def test_set_strategy_merges_partial() -> None:
    spec = _deck("deck_golden_a.json")
    proposal = _proposal(
        "deck-golden-a",
        3,
        [PatchOp(op="set_strategy", value={"intro": "改为从班级口算热身引入"})],
        "调整引入",
    )
    new_spec, patch, _ = apply_proposal(spec, proposal)
    assert new_spec.teaching_strategy.intro == "改为从班级口算热身引入"
    assert new_spec.teaching_strategy.concept_progression == spec.teaching_strategy.concept_progression  # 其余保留
    assert patch.affected.slide_ids == frozenset({"s2", "s4", "s8"})


def test_metadata_type_validation() -> None:
    spec = _deck("deck_golden_a.json")
    proposal = _proposal("deck-golden-a", 3, [PatchOp(op="set_metadata", field="duration_minutes", value=-5)])
    with pytest.raises(PatchValidationError):
        apply_proposal(spec, proposal)
