"""RV06 · FoundationalMath adapter 单元测试。

因为 Teacher 库里 `page_range` 目前全为 NULL，端到端用例覆盖不到
"页码字段被丢弃" 这条分支 —— 而它恰是教材铁律最关键的一条，
所以这里直接对 `hit_from_row()` 注入含 `page_range` 的行来钉死它。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from research_vnext.core.federation.providers import foundational_math as fm
from research_vnext.core.federation.providers.base import ProviderError
from research_vnext.core.federation.providers.foundational_math import (
    READONLY_CONNECT_TIMEOUT_S,
    READONLY_STATEMENT_TIMEOUT_MS,
    VISIBLE_RIGHTS,
    build_readonly_engine,
    foundational_database_url,
    hit_from_row,
)
from research_vnext.core.federation.scope import resolve_scope
from research_vnext.core.federation.types import EvidenceType, WarnCode

CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Row:
    """Teacher direct-chunk 查询结果的轻量替身（字段名与 SQL 别名一致）。"""

    def __init__(self, **over: object) -> None:
        base: dict[str, object] = {
            "chunk_id": "c1",
            "section_id": "sec1",
            "source_id": "src1",
            "chunk_text": "本节讨论函数 y=ax^2+bx+c 的顶点公式。",
            "content_hash": "a" * 64,
            "index_version": "1",
            "has_page_range": False,
            "section_title": "2.1 等式性质与不等式性质",
            "section_path": "第二章/2.1",
            "section_created_at": CREATED,
            "rights_status": "STAGED",
            "school_id": None,
            "publisher": "人教社",
            "grade": "高中",
            "volume": "必修一",
        }
        base.update(over)
        for k, v in base.items():
            setattr(self, k, v)


def _scope():
    return resolve_scope(
        actor_id="a1",
        tenant_id="t1",
        project_ids=frozenset({"p1"}),
        session_id="s1",
        school_id=None,
        capabilities=frozenset({"workspace.research.enter"}),
        provider_allowlist=("foundational_math",),
    )


def test_locator_is_source_section_chunk_only() -> None:
    hit = hit_from_row(_Row(), _scope(), score=0.5)
    assert hit.locator.as_dict() == {"source_id": "src1", "section_id": "sec1", "chunk_id": "c1"}


def test_page_range_is_dropped_and_warned() -> None:
    """Teacher 即使有页码，adapter 也必须丢弃并留痕。"""
    hit = hit_from_row(_Row(has_page_range=True), _scope(), score=0.5)
    assert WarnCode.DROPPED_PAGE_RANGE.value in hit.warnings
    assert "page" not in hit.locator.as_dict()
    assert "page_range" not in hit.locator.as_dict()


def test_no_page_keys_surface_even_without_page_range() -> None:
    hit = hit_from_row(_Row(), _scope(), score=0.5)
    banned = {"page", "page_range", "page_ref", "page_refs", "pnn", "bbox", "doi", "venue", "journal"}
    assert banned.isdisjoint(set(hit.locator.as_dict()))


def test_hit_is_labelled_textbook_and_not_citation_eligible() -> None:
    hit = hit_from_row(_Row(), _scope(), score=0.5)
    assert hit.evidence_type is EvidenceType.FOUNDATIONAL_MATH
    assert "TEXTBOOK" in hit.labels
    assert "FOUNDATIONAL_MATH" in hit.labels
    assert hit.citation_eligible is False


def test_hit_never_offers_fulltext() -> None:
    hit = hit_from_row(_Row(rights_status="ACTIVE"), _scope(), score=0.5)
    assert hit.rights.fulltext_allowed is False
    assert WarnCode.NO_RIGHTS_FULLTEXT_BLOCKED.value in hit.warnings
    assert hit.rights.redistributable is False


def test_rights_evidence_ref_is_traceable() -> None:
    hit = hit_from_row(_Row(rights_status="STAGED"), _scope(), score=0.5)
    assert "STAGED" in hit.rights.rights_evidence_ref


def test_visible_rights_excludes_quarantined() -> None:
    assert "QUARANTINED" not in VISIBLE_RIGHTS
    assert set(VISIBLE_RIGHTS) == {"STAGED", "ACTIVE"}


def test_readonly_engine_requires_dsn() -> None:
    with pytest.raises(ProviderError):
        build_readonly_engine("")


def test_foundational_dsn_defaults_to_empty() -> None:
    import os

    original = os.environ.pop("RESEARCH_FOUNDATIONAL_DATABASE_URL", None)
    try:
        assert foundational_database_url() == ""
    finally:
        if original is not None:
            os.environ["RESEARCH_FOUNDATIONAL_DATABASE_URL"] = original


def test_snippet_is_truncated_and_has_no_storage_paths() -> None:
    hit = hit_from_row(_Row(chunk_text="x" * 2000), _scope(), score=0.5)
    assert len(hit.snippet) <= 400
    assert "rvs/" not in hit.snippet


# --------------------------------------------------------------------------
# 有界阻塞：跨库读取必须带驱动级超时
# --------------------------------------------------------------------------


def _capture_engine_kwargs(monkeypatch) -> dict:
    captured: dict = {}
    sentinel = object()

    def _fake_create_engine(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(fm, "create_engine", _fake_create_engine)
    return captured


def test_readonly_engine_bounds_blocking_io_with_driver_timeouts(monkeypatch) -> None:
    """建连与语句都必须带驱动级超时。

    没有 `connect_timeout` 时，对端被防火墙**丢包**（而非 RST 拒绝）会让
    `engine.connect()` 一直等到 OS 级 TCP 超时 —— 本机实测约 **130 秒**；
    而编排层的 `deadline_ms` 只是 provider 之间的循环检查，**无法抢占**它。
    """
    captured = _capture_engine_kwargs(monkeypatch)
    build_readonly_engine("postgresql+psycopg://u:p@example.invalid:5432/db")

    args = captured["connect_args"]
    assert args["connect_timeout"] == READONLY_CONNECT_TIMEOUT_S
    # 超时必须"足够短"，否则等于没设 —— 它要能在 deadline 之内收敛。
    assert READONLY_CONNECT_TIMEOUT_S <= 5
    assert READONLY_STATEMENT_TIMEOUT_MS <= 30_000

    options = args["options"]
    assert "default_transaction_read_only=on" in options
    assert f"statement_timeout={READONLY_STATEMENT_TIMEOUT_MS}" in options


def test_readonly_engine_keeps_readonly_guarantee_alongside_timeouts(monkeypatch) -> None:
    """加超时不能把只读保证挤掉（两条约束必须在同一个 options 串里）。"""
    captured = _capture_engine_kwargs(monkeypatch)
    build_readonly_engine("postgresql+psycopg://u:p@example.invalid:5432/db")

    options = captured["connect_args"]["options"]
    assert options.startswith("-c default_transaction_read_only=on")
    assert "pool_pre_ping" not in options  # options 只承载 -c 参数
    assert captured["pool_pre_ping"] is True
