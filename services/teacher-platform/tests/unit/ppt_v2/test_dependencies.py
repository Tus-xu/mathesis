"""显式依赖规则测试（R10）：影响面必须由规则计算，禁止默认全 deck 重生成。"""

from __future__ import annotations

import json
from pathlib import Path

from app.ppt_v2.domain.dependencies import compute_affected
from app.ppt_v2.domain.spec import TeachingDeckSpec

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _deck(name: str = "deck_golden_a.json") -> TeachingDeckSpec:
    return TeachingDeckSpec.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def test_metadata_title_affects_all_layout_only() -> None:
    spec = _deck()
    affected = compute_affected(spec, op="set_metadata", target=None, field="title")
    assert affected.slide_ids == frozenset(s.id for s in spec.slides)
    assert affected.layout_affected == affected.slide_ids
    assert not affected.content_affected  # 教学内容零变化
    assert not affected.whole_deck_layout  # 与换主题区分（标题带 vs 全套版式）


def test_theme_change_affects_all_layout_but_not_content() -> None:
    spec = _deck()
    affected = compute_affected(spec, op="set_theme", target=None, field=None)
    assert affected.whole_deck_layout is True
    assert affected.layout_affected == frozenset(s.id for s in spec.slides)
    assert not affected.content_affected


def test_single_slide_content_change_only_that_slide() -> None:
    spec = _deck()
    affected = compute_affected(spec, op="set_slide", target="s3", field="content")
    assert affected.slide_ids == frozenset({"s3"})
    assert affected.content_affected == frozenset({"s3"})
    assert not affected.layout_affected


def test_speaker_note_is_metadata_only() -> None:
    spec = _deck()
    affected = compute_affected(spec, op="set_slide", target="s1", field="speaker_note")
    assert affected.slide_ids == frozenset({"s1"})
    assert not affected.content_affected
    assert not affected.layout_affected


def test_lesson_strategy_affects_intro_concept_summary() -> None:
    spec = _deck()
    affected = compute_affected(spec, op="set_strategy", target=None, field=None)
    # golden_a 中：s2=introduction、s4=concept、s8=summary（s3=definition 不敏感）
    expected = frozenset({"s2", "s4", "s8"})
    assert affected.slide_ids == expected
    assert affected.content_affected == expected


def test_remove_referenced_asset_affects_referencing_slides() -> None:
    spec = _deck()
    # asset-fig-1 被 s5 引用；asset-svg-1 被 s6 引用
    affected = compute_affected(spec, op="asset_remove", target="asset-fig-1", field=None)
    assert affected.slide_ids == frozenset({"s5"})
    assert affected.content_affected == frozenset({"s5"})


def test_set_asset_rights_affects_referencing_slides() -> None:
    spec = _deck()
    affected = compute_affected(spec, op="asset_set", target="asset-svg-1", field="rights_status")
    assert affected.slide_ids == frozenset({"s6"})
    assert affected.assets_affected == frozenset({"asset-svg-1"})


def test_add_unreferenced_asset_no_slide_affected() -> None:
    spec = _deck()
    affected = compute_affected(spec, op="asset_add", target="asset-new", field=None)
    assert affected.slide_ids == frozenset()


def test_selected_examples_affect_source_match_slides() -> None:
    spec = _deck()
    # golden_a：s5 的 source_refs 含 problem://synth-prob-1-1
    affected = compute_affected(spec, op="set_selected_examples", target=None, field=None)
    assert affected.slide_ids == frozenset({"s5"})
