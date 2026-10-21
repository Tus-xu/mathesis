"""RV07 · locator 回源往返：点击 Evidence → RV05 anchor 或可验证 external locator。

覆盖任务书失败测试：
- locator 失效（anchor 缺失 / 版本变化 / 撤权）；
- source hash 变化 → 证据标 stale；
- 教材只用 direct-chunk locator，不生成 PDF 页码/DOI。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence import locator as L  # noqa: E402
from research_vnext.core.evidence.errors import EvidenceLocatorError  # noqa: E402
from research_vnext.core.evidence.types import LocatorKind, StaleState  # noqa: E402
from tests.unit.evidence import factories as F  # noqa: E402


def _anchor(**over) -> SimpleNamespace:
    base = dict(
        anchor_id="anchor-0001",
        asset_id="22222222-2222-2222-2222-222222222222",
        asset_version=1,
        content_hash="a" * 64,
        parse_run_id="run-1",
        parser_version="pypdf-structural@1.0.0",
        type="section",
        page=4,
        bbox=[72.0, 120.0, 480.0, 300.0],
        section_path=["第三章", "3.2 导数"],
        text_preview="导数的定义是……",
        text_hash="t" * 64,
        region_hash="r" * 64,
        page_width=595.0,
        page_height=842.0,
        confidence=0.97,
        warnings=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_anchor_resolution_returns_page_bbox_section() -> None:
    resolved = L.resolve_from_anchor(
        F.anchor_locator(),
        _anchor(),
        current_asset_version=1,
        current_content_hash="a" * 64,
    )
    assert resolved.kind is LocatorKind.ANCHOR
    assert resolved.anchor_type == "section"
    assert resolved.page == 4
    assert resolved.bbox == [72.0, 120.0, 480.0, 300.0]
    assert resolved.section_path == ["第三章", "3.2 导数"]
    assert resolved.verifiable is True
    assert resolved.stale is False


@pytest.mark.parametrize("kind", ["page", "bbox", "section", "table", "figure", "formula"])
def test_every_rv05_anchor_kind_roundtrips(kind: str) -> None:
    resolved = L.resolve_from_anchor(
        F.anchor_locator(anchor_type=kind),
        _anchor(type=kind),
        current_asset_version=1,
        current_content_hash="a" * 64,
    )
    assert resolved.anchor_type == kind


def test_missing_anchor_is_a_locator_failure_not_silent_success() -> None:
    with pytest.raises(EvidenceLocatorError):
        L.resolve_from_anchor(
            F.anchor_locator(),
            None,
            current_asset_version=1,
            current_content_hash="a" * 64,
        )


def test_source_hash_change_marks_stale() -> None:
    resolved = L.resolve_from_anchor(
        F.anchor_locator(),
        _anchor(),
        current_asset_version=1,
        current_content_hash="b" * 64,
    )
    assert resolved.stale is True
    assert resolved.stale_state is StaleState.STALE
    assert "hash" in resolved.stale_reason
    assert resolved.verifiable is False


def test_asset_version_change_marks_stale() -> None:
    resolved = L.resolve_from_anchor(
        F.anchor_locator(),
        _anchor(),
        current_asset_version=2,
        current_content_hash="a" * 64,
    )
    assert resolved.stale is True
    assert "version" in resolved.stale_reason


def test_revoked_asset_marks_stale_and_unverifiable() -> None:
    resolved = L.resolve_from_anchor(
        F.anchor_locator(),
        _anchor(),
        current_asset_version=None,
        current_content_hash=None,
    )
    assert resolved.stale is True
    assert resolved.stale_reason == "asset_revoked"


def test_anchor_of_other_tenant_is_rejected() -> None:
    with pytest.raises(EvidenceLocatorError):
        L.resolve_from_anchor(
            F.anchor_locator(asset_id="33333333-3333-3333-3333-333333333333"),
            _anchor(asset_id="44444444-4444-4444-4444-444444444444"),
            current_asset_version=1,
            current_content_hash="a" * 64,
        )


def test_external_doi_locator_is_verifiable() -> None:
    resolved = L.resolve_external(F.external_locator())
    assert resolved.kind is LocatorKind.EXTERNAL
    assert resolved.verifiable is True
    assert resolved.external["doi"] == "10.1038/nature14539"


def test_external_url_without_hash_and_fetched_at_is_not_verifiable() -> None:
    """裸 URL 会漂移 → 没有内容指纹与抓取时间就不算可验证。"""
    resolved = L.resolve_external(
        F.external_locator(doi="", url="https://example.org/paper", content_hash="", fetched_at="")
    )
    assert resolved.verifiable is False
    assert resolved.stale is True


def test_external_doi_alone_is_verifiable() -> None:
    """DOI 是稳定标识符 → 不需要内容指纹也可验证。"""
    resolved = L.resolve_external(
        F.external_locator(url="", content_hash="", fetched_at="")
    )
    assert resolved.verifiable is True


def test_textbook_locator_resolves_to_direct_chunk_triple_only() -> None:
    resolved = L.resolve_external(F.textbook_locator())
    assert resolved.external == {"source_id": "src-1", "section_id": "sec-1", "chunk_id": "chunk-1"}
    assert "page" not in resolved.external
    assert "doi" not in resolved.external
    assert resolved.verifiable is True


def test_resolved_locator_public_dict_has_no_storage_keys() -> None:
    resolved = L.resolve_from_anchor(
        F.anchor_locator(),
        _anchor(),
        current_asset_version=1,
        current_content_hash="a" * 64,
    )
    blob = repr(resolved.to_dict()).lower()
    for forbidden in ("storage_key", "minio", "endpoint", "bucket"):
        assert forbidden not in blob
