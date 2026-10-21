"""RV04 · literature title-author-year fingerprint."""

from __future__ import annotations

from research_vnext.core.literature.fingerprint import title_author_year_fingerprint


def test_same_paper_same_fingerprint():
    fp1 = title_author_year_fingerprint(
        title="Attention Is All You Need",
        authors=[{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
        published_date="2017-06-12",
    )
    fp2 = title_author_year_fingerprint(
        title="attention is all you need",
        authors=[{"name": "noam shazeer"}, {"name": "ashish vaswani"}],
        published_date="2017",
    )
    assert fp1 == fp2


def test_different_year_different_fingerprint():
    a = title_author_year_fingerprint(title="X", authors=[], published_date="2017")
    b = title_author_year_fingerprint(title="X", authors=[], published_date="2018")
    assert a != b
