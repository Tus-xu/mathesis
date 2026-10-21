"""RV07 · Citation 往返：CSL-JSON 权威 + BibTeX/RIS/DOI 受测 round-trip。

任务书要求 4：canonical metadata 优先 CSL-JSON；支持 BibTeX/RIS/DOI 受测 round-trip；
**不为缺失字段编造值**；引用往返无静默丢失。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.citation import formats as FM  # noqa: E402
from research_vnext.core.citation import roundtrip as RT  # noqa: E402
from research_vnext.core.citation.csl import (  # noqa: E402
    CslDate,
    CslItem,
    CslName,
    from_csl_json,
    normalize_doi,
    to_csl_json,
)
from research_vnext.core.citation.errors import CitationContractError  # noqa: E402

#: 真实 Crossref 记录（RV04 已实测抓取同一条）。
REAL = CslItem(
    csl_type="article-journal",
    id="10.1038/nature14539",
    title="Deep learning",
    authors=(CslName(family="LeCun", given="Yann"),),
    issued=CslDate(2015, 5, 27),
    container_title="Nature",
    doi="10.1038/nature14539",
    url="https://doi.org/10.1038/nature14539",
    publisher="Springer Science and Business Media LLC",
    page="436-444",
    volume="521",
    issue="7553",
    issn="0028-0836",
    language="en",
    abstract="Deep learning allows computational models ...",
)

#: 中文作者 + 中文日期 + 中文标题（教师平台/CNKI 形态）。
CJK = CslItem(
    csl_type="article-journal",
    id="cnki-2026-0001",
    title="导数概念的二阶认知障碍与教学干预",
    authors=(
        CslName(family="王", given="小明"),
        CslName(family="李", given="华"),
    ),
    issued=CslDate(2026, 6, 5),
    container_title="数学教育学报",
    doi="",
    url="",
    publisher="",
    page="12-18",
    volume="35",
    issue="2",
    issn="",
    language="zh-CN",
    abstract="",
)


# --------------------------------------------------------------------------
# CSL-JSON canonical
# --------------------------------------------------------------------------


def test_csl_json_is_the_canonical_round_trip() -> None:
    assert from_csl_json(to_csl_json(REAL)) == REAL
    assert from_csl_json(to_csl_json(CJK)) == CJK


def test_csl_json_shape_uses_date_parts_and_name_objects() -> None:
    payload = to_csl_json(REAL)
    assert payload["issued"] == {"date-parts": [[2015, 5, 27]]}
    assert payload["author"] == [{"family": "LeCun", "given": "Yann"}]
    assert payload["type"] == "article-journal"
    assert payload["DOI"] == "10.1038/nature14539"


def test_missing_doi_is_never_fabricated() -> None:
    payload = to_csl_json(CJK)
    assert "DOI" not in payload or payload["DOI"] == ""
    item = from_csl_json({"type": "article-journal", "title": "无 DOI 条目"})
    assert item.doi == ""
    assert item.issued is None


def test_csl_item_rejects_unknown_type() -> None:
    with pytest.raises(CitationContractError):
        CslItem(csl_type="not-a-csl-type", id="x", title="t")


# --------------------------------------------------------------------------
# DOI 往返
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1038/NATURE14539", "10.1038/nature14539"),
        ("https://doi.org/10.1038/nature14539", "10.1038/nature14539"),
        ("doi:10.1038/nature14539", "10.1038/nature14539"),
        ("http://dx.doi.org/10.1038/nature14539", "10.1038/nature14539"),
    ],
)
def test_doi_normalization_is_deterministic(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


def test_doi_round_trip_is_exact() -> None:
    item = FM.from_doi("https://doi.org/10.1038/NATURE14539")
    assert item.doi == "10.1038/nature14539"
    assert FM.to_doi(item) == "10.1038/nature14539"
    assert FM.from_doi(FM.to_doi(item)).doi == item.doi


def test_doi_round_trip_of_item_without_doi_does_not_invent_one() -> None:
    with pytest.raises(CitationContractError):
        FM.to_doi(CJK)


# --------------------------------------------------------------------------
# BibTeX round-trip
# --------------------------------------------------------------------------


def test_bibtex_round_trip_preserves_supported_fields() -> None:
    text = FM.to_bibtex(REAL)
    assert "@article{" in text
    back, report = RT.roundtrip(REAL, "bibtex")
    RT.assert_no_silent_loss(report)
    assert back.title == REAL.title
    assert back.authors == REAL.authors
    assert back.issued == REAL.issued
    assert back.doi == REAL.doi
    assert back.container_title == REAL.container_title
    assert back.page == REAL.page
    assert back.volume == REAL.volume
    assert back.issue == REAL.issue


def test_bibtex_round_trip_keeps_chinese_author_and_date() -> None:
    text = FM.to_bibtex(CJK)
    assert "王" in text and "小明" in text
    back, report = RT.roundtrip(CJK, "bibtex")
    RT.assert_no_silent_loss(report)
    assert back.authors == CJK.authors
    assert back.issued == CJK.issued
    assert back.title == CJK.title
    assert back.language == "zh-CN"


def test_bibtex_omits_missing_fields_instead_of_inventing() -> None:
    text = FM.to_bibtex(CJK)
    assert "doi" not in text.lower()
    back, _ = RT.roundtrip(CJK, "bibtex")
    assert back.doi == ""


def test_bibtex_report_declares_unrepresentable_fields() -> None:
    item = CslItem(
        csl_type="article-journal",
        id="x",
        title="t",
        extra=(("note", "只存在于 CSL"),),
    )
    _, report = RT.roundtrip(item, "bibtex")
    assert "extra.note" in report.unsupported_by_format
    RT.assert_no_silent_loss(report)


# --------------------------------------------------------------------------
# RIS round-trip
# --------------------------------------------------------------------------


def test_ris_round_trip_preserves_supported_fields() -> None:
    text = FM.to_ris(REAL)
    assert "TY  - JOUR" in text
    assert text.rstrip().endswith("ER  -")
    back, report = RT.roundtrip(REAL, "ris")
    RT.assert_no_silent_loss(report)
    assert back.title == REAL.title
    assert back.authors == REAL.authors
    assert back.issued == REAL.issued
    assert back.doi == REAL.doi
    assert back.container_title == REAL.container_title


def test_ris_round_trip_keeps_chinese_author_and_date() -> None:
    back, report = RT.roundtrip(CJK, "ris")
    RT.assert_no_silent_loss(report)
    assert back.authors == CJK.authors
    assert back.issued == CJK.issued
    assert back.title == CJK.title


def test_ris_omits_missing_doi() -> None:
    text = FM.to_ris(CJK)
    assert "DO  -" not in text
    back, _ = RT.roundtrip(CJK, "ris")
    assert back.doi == ""


# --------------------------------------------------------------------------
# 静默丢失检测
# --------------------------------------------------------------------------


def test_report_detects_silent_loss() -> None:
    report = RT.RoundTripReport(
        fmt="bibtex",
        preserved=("title",),
        lost=("volume",),
        unsupported_by_format=(),
        fabricated=(),
    )
    with pytest.raises(CitationContractError):
        RT.assert_no_silent_loss(report)


def test_report_detects_fabricated_fields() -> None:
    report = RT.RoundTripReport(
        fmt="ris",
        preserved=("title",),
        lost=(),
        unsupported_by_format=(),
        fabricated=("doi",),
    )
    with pytest.raises(CitationContractError):
        RT.assert_no_silent_loss(report)


def test_all_three_formats_report_no_loss_for_real_metadata() -> None:
    for fmt in ("bibtex", "ris", "doi"):
        _, report = RT.roundtrip(REAL, fmt)
        RT.assert_no_silent_loss(report)


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(CitationContractError):
        RT.roundtrip(REAL, "endnote-xml")
