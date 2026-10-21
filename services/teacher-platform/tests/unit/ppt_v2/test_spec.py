"""TeachingDeckSpec 领域契约测试（R10，P0）。

覆盖验收门：合法 golden spec、缺 source ref、非法 asset rights、preserve_original、
序列化 round-trip、未知字段策略 / 版本闸门、禁止引擎坐标（schema 层）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ppt_v2.domain.errors import DeckSpecError, UnsupportedSchemaVersion
from app.ppt_v2.domain.reader import DeckSpecReader
from app.ppt_v2.domain.spec import (
    ENGINE_GEOMETRY_FIELD_NAMES,
    SCHEMA_VERSION,
    AssetRef,
    SlideSpec,
    TeachingDeckSpec,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# 1. 合法 golden spec
# ─────────────────────────────────────────────────────────────────────────────


def test_golden_a_valid_full_deck() -> None:
    spec = TeachingDeckSpec.model_validate(_load("deck_golden_a.json"))
    assert spec.schema_version == SCHEMA_VERSION
    assert len(spec.slides) == 8
    assert len(spec.assets) == 2
    assert spec.revision == 3
    # 引用完整性：speaker_notes / outline.slide_ids / slide.assets 全部可解析
    assert set(spec.speaker_notes) <= {s.id for s in spec.slides}
    slide_by_id = {s.id: s for s in spec.slides}
    for s in spec.slides:
        assert set(s.assets) <= {a.id for a in spec.assets}
    outline_slides = {sid for e in spec.outline for sid in e.slide_ids}
    assert outline_slides <= set(slide_by_id)


def test_golden_b_valid_small_deck() -> None:
    spec = TeachingDeckSpec.model_validate(_load("deck_golden_b.json"))
    assert len(spec.slides) == 4
    assert spec.theme is not None and spec.theme.theme_id == "synth-theme-blackboard"
    assert spec.assets == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. 非法 src/资产权利
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_missing_textbook_source_rejected() -> None:
    """教材源铁律：textbook_sources 为空 → 非法（教学事实无法追溯）。"""
    data = _load("deck_invalid_missing_source_ref.json")
    with pytest.raises(ValidationError) as exc:
        TeachingDeckSpec.model_validate(data)
    msgs = json.dumps(json.loads(exc.value.json()), ensure_ascii=False)
    assert "textbook_sources" in msgs


def test_invalid_asset_rights_rejected() -> None:
    """非法 rights_status 值 → 契约拒绝（未授权/未知状态不得进入渲染。"""
    data = _load("deck_golden_a.json")
    data["assets"][0]["rights_status"] = "LEAKED"
    with pytest.raises(ValidationError):
        TeachingDeckSpec.model_validate(data)


def test_asset_sha256_must_be_hex64() -> None:
    """AssetRef.sha256 必须为 64 位 hex（可审计指纹）。"""
    with pytest.raises(ValidationError):
        AssetRef.model_validate(
            {
                "id": "a1",
                "kind": "TEXTBOOK",
                "object_key": "k",
                "sha256": "not-a-hash",
                "mime_type": "image/png",
                "size_bytes": 1,
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. preserve_original（TEACHER_UPLOAD 原件保护）
# ─────────────────────────────────────────────────────────────────────────────


def test_teacher_upload_preserve_original_default_true() -> None:
    a: AssetRef = AssetRef.model_validate(
        {
            "id": "a-up",
            "kind": "TEACHER_UPLOAD",
            "object_key": "k",
            "sha256": "0" * 64,
            "mime_type": "image/jpeg",
            "size_bytes": 10,
        }
    )
    assert a.preserve_original is True


def test_teacher_upload_cannot_disable_preserve_original() -> None:
    with pytest.raises(ValidationError, match="preserve_original"):
        AssetRef.model_validate(
            {
                "id": "a-up",
                "kind": "TEACHER_UPLOAD",
                "object_key": "k",
                "sha256": "0" * 64,
                "mime_type": "image/jpeg",
                "size_bytes": 10,
                "preserve_original": False,
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. schema 层：禁止引擎坐标 x/y/w/h
# ─────────────────────────────────────────────────────────────────────────────


def test_generated_schema_has_no_engine_geometry_fields() -> None:
    """V2 Iron Law：生成 JSON Schema 中任何对象属性都不得名为 x/y/w/h。"""
    schema = TeachingDeckSpec.model_json_schema()
    seen: set[str] = set()

    def walk(node: dict) -> None:
        if isinstance(node, dict):
            if "properties" in node:
                for prop in node["properties"]:
                    seen.add(prop)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert seen.isdisjoint(set(ENGINE_GEOMETRY_FIELD_NAMES)), f"引擎坐标字段出现在契约中: {sorted(seen & set(ENGINE_GEOMETRY_FIELD_NAMES))}"


def test_slide_ignores_engine_coordinates_on_read() -> None:
    """extra=ignore：携带 x/y/w/h 的 payload 可被旧 reader 容忍（丢弃），不崩溃。"""
    data = _load("deck_golden_a.json")
    data["slides"][0]["x"] = 100
    data["slides"][0]["y"] = 200
    spec = TeachingDeckSpec.model_validate(data)
    assert not hasattr(spec.slides[0], "x")


# ─────────────────────────────────────────────────────────────────────────────
# 5. 序列化 round-trip + 向前兼容 reader
# ─────────────────────────────────────────────────────────────────────────────


def test_python_round_trip_is_lossless() -> None:
    """同版本 round-trip：Pydantic → JSON → Pydantic 语义不变。"""
    spec = TeachingDeckSpec.model_validate(_load("deck_golden_a.json"))
    dumped = json.loads(spec.model_dump_json())
    spec2 = DeckSpecReader.read(dumped)[0]
    assert spec2.model_dump() == spec.model_dump()


def test_reader_unknown_fields_tolerated_with_warning() -> None:
    """同一 major 内未知字段：reader 容忍并返回 warning（向前兼容）。"""
    data = _load("deck_golden_b.json")
    data["future_field_unknown"] = {"anything": 1}
    spec, warnings = DeckSpecReader.read_dict(data)
    assert spec.id == "deck-golden-b"
    assert warnings == []


def test_reader_newer_minor_version_accepted_with_warning() -> None:
    """未来 minor（同 major）：兼容读取并记录 warning。"""
    data = _load("deck_golden_b.json")
    data["schema_version"] = "2.1.0"
    spec, warnings = DeckSpecReader.read_dict(data)
    assert spec.id == "deck-golden-b"
    assert any("2.1.0" in w for w in warnings)


def test_reader_unknown_major_fails_closed() -> None:
    """未来 major：fail closed，拒绝语义猜测。"""
    data = _load("deck_golden_b.json")
    data["schema_version"] = "3.0.0"
    with pytest.raises(UnsupportedSchemaVersion):
        DeckSpecReader.read_dict(data)


def test_reader_missing_version_rejected() -> None:
    data = _load("deck_golden_a.json")
    del data["schema_version"]
    with pytest.raises(DeckSpecError, match="schema_version"):
        DeckSpecReader.read_dict(data)


# ─────────────────────────────────────────────────────────────────────────────
# 6. slide 字段契约
# ─────────────────────────────────────────────────────────────────────────────


def test_slide_layout_hint_must_be_semantic_enum() -> None:
    """layout_hint 只能是语义枚举；任何像素值（如 'x:0,y:0'）被拒绝。"""
    with pytest.raises(ValidationError):
        SlideSpec.model_validate(
            {
                "id": "s-x",
                "type": "concept",
                "teaching_goal": "g",
                "title": "t",
                "layout_hint": "rect@0,0,400,300",
            }
        )


def test_slide_negative_revision_rejected() -> None:
    with pytest.raises(ValidationError):
        SlideSpec.model_validate(
            {"id": "s-x", "type": "concept", "teaching_goal": "g", "title": "t", "revision": -1}
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. 引用完整性错误路径
# ─────────────────────────────────────────────────────────────────────────────


def test_duplicate_slide_ids_rejected() -> None:
    data = _load("deck_golden_b.json")
    data["slides"].append(dict(data["slides"][0]))
    with pytest.raises(ValidationError, match="slide id 重复"):
        TeachingDeckSpec.model_validate(data)


def test_slide_assets_referencing_unknown_asset_rejected() -> None:
    data = _load("deck_golden_b.json")
    data["slides"][1]["assets"] = ["no-such-asset"]
    with pytest.raises(ValidationError, match="不存在的 id"):
        TeachingDeckSpec.model_validate(data)


def test_speaker_notes_unknown_slide_rejected() -> None:
    data = _load("deck_golden_b.json")
    data["speaker_notes"] = {"ghost": "x"}
    with pytest.raises(ValidationError, match="speaker_notes"):
        TeachingDeckSpec.model_validate(data)
