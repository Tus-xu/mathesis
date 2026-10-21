"""RV07 → RV08 前端契约投影：字段形状、诚实降级、不臆造。

对应 `RV08-RV07-INTERFACE/INTERFACE-GAP-ANALYSIS.md` 的 G3/G4。
前端消费方（已存在，本轮未改）：
- `artifacts/evidence/EvidenceCard.vue`（`data-testid="rv07-evidence-card"`）
- `artifacts/reader/ResearchReaderFocus.vue`（`data-testid="rv07-reader-focus"`）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence.contracts import rights_policy_for  # noqa: E402
from research_vnext.core.evidence.frontend_projection import (  # noqa: E402
    FALLBACK_ANCHOR_TYPE,
    locator_summary,
    to_frontend_evidence_item,
    to_frontend_reader_anchor,
)
from research_vnext.core.evidence.types import StaleState  # noqa: E402
from tests.unit.evidence import factories as F  # noqa: E402

#: 前端 `types/research.ts::ResearchEvidenceItem` 声明的 7 个字段。
REQUIRED_EVIDENCE_FIELDS = {
    "id",
    "source",
    "label",
    "locator",
    "snippet",
    "assetVersion",
    "authorized",
}


def _public(card) -> dict:
    """复刻 `service.card_public_dict()` 的输出形状（不碰 DB）。"""
    payload = card.to_dict()
    payload["snippet_redacted"] = False
    payload["rights_policy"] = rights_policy_for(card.rights).as_dict()
    payload["updated_at"] = ""
    return payload


def _resolved(**over) -> dict:
    """`ResolvedLocator.to_dict()` 形状。"""
    base = {
        "kind": "ANCHOR",
        "anchor_type": "section",
        "page": 4,
        "bbox": [72.0, 120.0, 480.0, 300.0],
        "section_path": ["第三章", "3.2 导数"],
        "external": {},
        "verifiable": True,
        "stale": False,
        "stale_state": "FRESH",
        "stale_reason": "",
        "text_hash": "t" * 64,
        "region_hash": "r" * 64,
        "confidence": 0.9,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# 字段形状
# --------------------------------------------------------------------------


def test_evidence_item_covers_every_required_frontend_field() -> None:
    item = to_frontend_evidence_item(_public(F.paper_card()))
    assert REQUIRED_EVIDENCE_FIELDS <= set(item)


def test_evidence_item_derives_source_and_label() -> None:
    item = to_frontend_evidence_item(_public(F.paper_card()))
    assert item["source"] == "crossref"  # source_ref.provider
    assert item["label"] == "PAPER_FULL_TEXT"  # labels 为空 → 退回 type


def test_evidence_item_prefers_first_label_when_present() -> None:
    card = F.paper_card(labels=("TEXTBOOK_CHUNK", "第三章"))
    item = to_frontend_evidence_item(_public(card))
    assert item["label"] == "TEXTBOOK_CHUNK"


def test_evidence_item_locator_is_a_string_not_an_object() -> None:
    """前端 `locator` 是 `string`，而 RV07 侧是对象 —— 必须压平。"""
    item = to_frontend_evidence_item(_public(F.paper_card()), resolved=_resolved())
    assert isinstance(item["locator"], str)
    assert item["locator"] == "page 4 · bbox 72.0,120.0,480.0,300.0 · 第三章/3.2 导数"


def test_evidence_item_asset_version_comes_from_locator_payload() -> None:
    item = to_frontend_evidence_item(_public(F.paper_card()))
    assert item["assetVersion"] == "1"


# --------------------------------------------------------------------------
# 诚实降级
# --------------------------------------------------------------------------


def test_authorized_true_for_active_fresh_card() -> None:
    item = to_frontend_evidence_item(_public(F.paper_card()))
    assert item["authorized"] is True


def test_authorized_false_when_stale() -> None:
    card = F.paper_card(stale_state=StaleState.STALE)
    item = to_frontend_evidence_item(_public(card))
    assert item["authorized"] is False


def test_authorized_false_when_not_active() -> None:
    card = F.paper_card(status="INVALIDATED")
    item = to_frontend_evidence_item(_public(card))
    assert item["authorized"] is False


def test_rights_redaction_empties_snippet_without_flipping_authorized() -> None:
    """摘录被 rights 清空 ≠ 该来源未授权 —— 两种诚实态不能混为一谈。

    复刻 `service.card_public_dict()` 对"全文不可见"卡的处理：卡片本体存有 snippet，
    但对外投影把 snippet 置空并标 `snippet_redacted=True`。
    """
    card = F.paper_card(rights=F.rights(fulltext_allowed=False))
    public = _public(card)
    public["snippet"] = ""
    public["snippet_redacted"] = True

    item = to_frontend_evidence_item(public)
    assert item["snippet"] == ""
    assert item["snippetRedacted"] is True
    assert item["authorized"] is True
    assert item["readerFulltextVisible"] is False


# --------------------------------------------------------------------------
# 不臆造
# --------------------------------------------------------------------------


def test_empty_input_yields_empty_strings_never_invented_values() -> None:
    item = to_frontend_evidence_item({})
    assert item["id"] == ""
    assert item["source"] == ""
    assert item["locator"] == ""
    assert item["snippet"] == ""
    assert item["assetVersion"] == ""
    assert item["authorized"] is False


def test_locator_summary_of_nothing_is_empty() -> None:
    assert locator_summary(None) == ""
    assert locator_summary({}) == ""


def test_anchor_without_resolved_falls_back_to_anchor_identity() -> None:
    """卡片 locator 只有 anchor_id/anchor_type 时，不得编造页码。"""
    summary = locator_summary(F.anchor_locator().as_dict())
    assert summary == "section · anchor anchor-0001"
    assert "page" not in summary


def test_external_locator_summary_uses_dimension_prefix() -> None:
    summary = locator_summary({"kind": "EXTERNAL", "dimension": "DOI", "doi": "10.1/x"})
    assert summary == "DOI: 10.1/x"


# --------------------------------------------------------------------------
# ReaderAnchor
# --------------------------------------------------------------------------


def test_reader_anchor_maps_every_field() -> None:
    anchor = to_frontend_reader_anchor(_public(F.paper_card()), _resolved())
    assert anchor == {
        "type": "section",
        "assetId": "22222222-2222-2222-2222-222222222222",
        "assetVersion": "1",
        "parserVersion": "teacher-direct-chunk@1.0.0",
        "assetHash": "a" * 64,
        "page": 4,
        "bbox": [72.0, 120.0, 480.0, 300.0],
        "section": "第三章/3.2 导数",
        "assetAvailable": True,
    }


def test_reader_anchor_type_outside_enum_falls_back() -> None:
    anchor = to_frontend_reader_anchor(_public(F.paper_card()), _resolved(anchor_type="WEIRD"))
    assert anchor["type"] == FALLBACK_ANCHOR_TYPE


def test_reader_anchor_asset_available_follows_verifiable_not_stale_flag() -> None:
    """stale 即不可回源：`assetAvailable` 必须为 False，前端据此拒绝打开。"""
    stale = _resolved(verifiable=False, stale=True, stale_state="STALE", stale_reason="版本已变")
    anchor = to_frontend_reader_anchor(_public(F.paper_card()), stale)
    assert anchor["assetAvailable"] is False


def test_reader_anchor_bbox_ignored_unless_exactly_four_numbers() -> None:
    anchor = to_frontend_reader_anchor(_public(F.paper_card()), _resolved(bbox=[1.0, 2.0]))
    assert anchor["bbox"] is None


def test_reader_anchor_section_is_none_when_path_empty() -> None:
    anchor = to_frontend_reader_anchor(_public(F.paper_card()), _resolved(section_path=[]))
    assert anchor["section"] is None


# --------------------------------------------------------------------------
# 跨仓契约漂移守卫
# --------------------------------------------------------------------------
#
# 前端 `types/research.ts` 是**消费方声明**，本模块是**生产方**。
# 两者一旦漂移（前端加字段、后端没跟上），投影会静默产出 `undefined`，
# 而前端 `adapters/researchAdapter.ts:137` 对 evidence 是**直通无 allowlist**，
# 不会报错 —— 所以必须由本测试兜住。
#
# 前端仓库不在本机时跳过（CI 上不做跨仓断言）。

FRONTEND_TYPE_PATHS = (
    Path("D:/frontend/.worktrees/research-home-chat/src/features/research-vnext/types/research.ts"),
    Path("D:/frontend/src/features/research-vnext/types/research.ts"),
)


def _frontend_interface_fields(name: str) -> set[str] | None:
    for path in FRONTEND_TYPE_PATHS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        match = re.search(rf"export interface {name}\s*\{{(.*?)\n\}}", text, re.S)
        if not match:
            continue
        fields: set[str] = set()
        for line in match.group(1).splitlines():
            hit = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", line)
            if hit:
                fields.add(hit.group(1))
        return fields
    return None


def test_projection_covers_frontend_evidence_item_contract() -> None:
    fields = _frontend_interface_fields("ResearchEvidenceItem")
    if fields is None:
        pytest.skip("前端 types/research.ts 不在本机 —— 跨仓契约守卫仅在开发机生效")
    item = to_frontend_evidence_item(_public(F.paper_card()))
    missing = sorted(fields - set(item))
    assert not missing, f"投影缺前端已声明字段（契约漂移）: {missing}"


def test_projection_covers_frontend_reader_anchor_contract() -> None:
    fields = _frontend_interface_fields("ReaderAnchor")
    if fields is None:
        pytest.skip("前端 types/research.ts 不在本机 —— 跨仓契约守卫仅在开发机生效")
    anchor = to_frontend_reader_anchor(_public(F.paper_card()), _resolved())
    missing = sorted(fields - set(anchor))
    assert not missing, f"投影缺前端已声明字段（契约漂移）: {missing}"


def test_frontend_contract_guard_actually_found_the_file() -> None:
    """守卫自检：本机应能找到前端类型文件，否则上面两条会静默 skip 而失去意义。"""
    assert _frontend_interface_fields("ResearchEvidenceItem") is not None, (
        "未找到前端 types/research.ts —— 契约守卫形同虚设，请检查路径"
    )
