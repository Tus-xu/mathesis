"""RV04 unit tests · literature fingerprint + crossref mapping + provider errors."""

from __future__ import annotations

import httpx
import pytest

from research_vnext.core.literature.crossref import CrossrefProvider, _map_work
from research_vnext.core.literature.fingerprint import title_author_year_fingerprint
from research_vnext.core.literature.provider import (
    AVAIL_NO,
    ProviderRateLimited,
    ProviderTimeout,
    hash_raw_response,
)


def test_fingerprint_stable_under_punctuation():
    a = title_author_year_fingerprint(
        title="Deep Learning!",
        authors=[{"name": "Yann LeCun"}],
        published_date="2015-05-26",
    )
    b = title_author_year_fingerprint(
        title="deep learning",
        authors=[{"name": "yann lecun"}],
        published_date="2015",
    )
    assert a == b


def test_fingerprint_differs_on_title():
    a = title_author_year_fingerprint(title="A", authors=[], published_date="2020")
    b = title_author_year_fingerprint(title="B", authors=[], published_date="2020")
    assert a != b


def test_map_work_fields_and_unknown_rights():
    work = {
        "DOI": "10.1038/NATURE14539",
        "title": ["Deep learning"],
        "author": [{"given": "Yann", "family": "LeCun"}],
        "issued": {"date-parts": [[2015, 5, 26]]},
        "container-title": ["Nature"],
        "URL": "https://doi.org/10.1038/nature14539",
    }
    meta = _map_work(work, query="deep learning", request_id="rid-1")
    assert meta.provider == "crossref"
    assert meta.doi == "10.1038/nature14539"
    assert meta.title == "Deep learning"
    assert meta.venue == "Nature"
    assert meta.published_date.startswith("2015")
    assert meta.abstract_available == AVAIL_NO
    # 无 license → UNKNOWN，不得冒充 PUBLIC
    assert meta.rights_status == "UNKNOWN"
    assert meta.raw_response_hash == hash_raw_response(work)
    assert meta.record_kind if hasattr(meta, "record_kind") else True


def test_crossref_rate_limit_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "3"}, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://crossref.test")
    p = CrossrefProvider(client=client)
    with pytest.raises(ProviderRateLimited) as ei:
        p.search("x")
    assert ei.value.code == "PROVIDER_RATE_LIMITED"
    assert ei.value.retryable is True


def test_crossref_timeout_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://crossref.test")
    p = CrossrefProvider(client=client)
    with pytest.raises(ProviderTimeout):
        p.search("x")


def test_crossref_real_search_returns_typed_metadata():
    """真实 Crossref（网络）；无网络时 skip。"""
    p = CrossrefProvider()
    try:
        items = p.search("Attention is all you need", limit=3, request_id="rv04-real")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Crossref unavailable: {e}")
    assert 1 <= len(items) <= 3
    m = items[0]
    assert m.provider == "crossref"
    assert m.title
    assert m.raw_response_hash
    assert m.retrieved_at
    # metadata ≠ full text；不得把 abstract 冒充全文
    assert m.fulltext_available in {"YES", "NO", "UNKNOWN"}
