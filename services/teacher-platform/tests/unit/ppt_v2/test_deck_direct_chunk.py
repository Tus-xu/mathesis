"""R12 TeachingDeckSpec direct-chunk new writer / legacy reader 测试。

- 新 writer（TextbookSourceRef.from_locator）：page_refs 必空、chunk_refs 携带
  canonical v1 locator ref（零假页码）；
- legacy reader（DeckSpecReader）：旧 deck（page_refs 非空）可打开且保持可读；
- 契约：schema/TS 重新生成与提交文件一致（字段漂移拦截）。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ppt_v2.domain.gen.gen_schema import generate_schema
from app.ppt_v2.domain.gen.gen_ts import generate_ts
from app.ppt_v2.domain.reader import DeckSpecReader
from app.ppt_v2.domain.spec import TeachingDeckSpec, TextbookSourceRef

_SID = "50000000-0000-0000-0000-000000000001"
_SEC = "50000000-0000-0000-0000-000000000010"
_CH = "50000000-0000-0000-0000-0000000000a1"

_DOMAIN_SCHEMA = Path(__file__).resolve().parents[3] / "app" / "ppt_v2" / "domain" / "schema"


def test_from_locator_new_writer_zero_page_refs() -> None:
    locator = f"textbook_v2/locator/v1/source/{_SID}/section/{_SEC}/chunk/{_CH}"
    ref = TextbookSourceRef.from_locator(locator)
    assert ref.page_refs == []  # 验收：新 artifact 的 page_refs=0
    assert ref.chunk_refs == [locator]
    assert ref.locator == locator
    assert ref.textbook_id == _SID
    assert ref.section_id == _SEC


def test_from_locator_rejects_non_section_or_invalid() -> None:
    import pytest

    with pytest.raises(ValueError):
        TextbookSourceRef.from_locator(f"textbook_v2/locator/v1/source/{_SID}")  # 缺 section
    with pytest.raises(ValueError):
        TextbookSourceRef.from_locator("web://http/search/result")


def test_legacy_deck_with_page_refs_opens_readable() -> None:
    raw = {
        "schema_version": "2.0.0",
        "id": "deck-legacy-1",
        "metadata": {"title": "旧 deck（含假页码）"},
        "textbook_sources": [
            {
                "textbook_id": _SID,
                "section_id": _SEC,
                "page_refs": ["12", "13"],  # legacy 页码：只读兼容
            }
        ],
        "outline": [],
        "slides": [],
        "assets": [],
        "speaker_notes": {},
        "change_log": [],
    }
    spec, warnings = DeckSpecReader.read_dict(raw)
    assert isinstance(spec, TeachingDeckSpec)
    assert spec.textbook_sources[0].page_refs == ["12", "13"]  # 旧页码仍可读
    assert spec.textbook_sources[0].chunk_refs == []  # 旧 deck 无 chunk_refs（默认空，不伪造）


def test_new_deck_spec_page_refs_zero_roundtrip() -> None:
    """新 writer 产出的 deck 经 reader 读回 page_refs 恒为 0。"""
    locator = f"textbook_v2/locator/v1/source/{_SID}/section/{_SEC}/chunk/{_CH}"
    spec = TeachingDeckSpec(
        id="deck-new-1",
        metadata={"title": "新 deck（direct-chunk）"},
        textbook_sources=[TextbookSourceRef.from_locator(locator)],
    )
    raw = spec.model_dump(mode="json")
    reopened, _ = DeckSpecReader.read_dict(raw)
    assert reopened.textbook_sources[0].page_refs == []


def test_schema_regeneration_includes_chunk_refs() -> None:
    """TextbookSourceRef 增补字段必须出现在重新生成的 schema/TS（漂移拦截）。"""
    schema = generate_schema()
    props = schema["$defs"]["TextbookSourceRef"]["properties"]
    assert "chunk_refs" in props and "locator" in props and "page_refs" in props

    ts = generate_ts(schema)
    assert "chunk_refs?: string[]" in ts
    assert "locator?: string | null" in ts or "locator?: string" in ts

    committed_schema = (_DOMAIN_SCHEMA / "deck-spec.schema.json").read_text(encoding="utf-8")
    regenerated_schema = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    assert regenerated_schema == committed_schema, "schema 漂移：请重新生成并提交"

    committed_ts = (_DOMAIN_SCHEMA / "deck-spec.ts").read_text(encoding="utf-8")
    assert ts == committed_ts, "TS 漂移：请重新生成并提交"
