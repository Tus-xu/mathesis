"""RV06 · 去重 / rerank 保留来源维度测试。

失败先行。钉死：
- 去重键含 evidence_type，**不跨维度合并**；
- locator 相同但 rights/provenance 不同 → 保留多份并互记 warning，不无损合并；
- rerank 权重是模块常量，评分路径不含 query 特例分支。
"""

from __future__ import annotations

import inspect

from research_vnext.core.federation import dedupe as dedupe_mod
from research_vnext.core.federation.contracts import (
    AccessRights,
    AssetLocator,
    FederatedResult,
    LiteratureLocator,
    Provenance,
    ScoreComponents,
    TextbookLocator,
)
from research_vnext.core.federation.dedupe import (
    dedupe_and_rank,
    dedupe_results,
    rerank,
)
from research_vnext.core.federation.types import EvidenceType, WarnCode


def _mk(
    *,
    result_id: str,
    evidence_type: EvidenceType,
    locator,
    rights_status: str = "UNKNOWN",
    source_hash: str = "a" * 64,
    snippet: str = "s",
    score: float = 0.5,
) -> FederatedResult:
    import hashlib

    return FederatedResult(
        result_id=result_id,
        evidence_type=evidence_type,
        provider="p",
        provider_version="1.0.0",
        locator=locator,
        snippet=snippet,
        snippet_hash=hashlib.sha256(snippet.encode()).hexdigest(),
        rights=AccessRights(
            rights_status=rights_status, rights_evidence_ref="e", redistributable=False
        ),
        provenance=Provenance(
            retrieved_at="2026-09-13T00:00:00Z", parser_version="rv06", source_hash=source_hash
        ),
        retrieved_at="2026-09-13T00:00:00Z",
        score_components=ScoreComponents(lexical=score, vector=0.0, rights_penalty=0.0, recency=1.0),
        # 教材结果不得声明 citation_eligible=True（契约硬约束）
        citation_eligible=evidence_type is not EvidenceType.FOUNDATIONAL_MATH,
    )


# --------------------------------------------------------------------------
# 1. 精确重复才折叠
# --------------------------------------------------------------------------


def test_exact_duplicate_is_collapsed() -> None:
    a = _mk(result_id="r1", evidence_type=EvidenceType.LITERATURE, locator=LiteratureLocator(doi="10.1/x"))
    b = _mk(result_id="r2", evidence_type=EvidenceType.LITERATURE, locator=LiteratureLocator(doi="10.1/x"))
    out = dedupe_results([a, b])
    assert len(out.results) == 1
    assert out.collapsed == 1
    assert out.conflicts_kept == 0


def test_same_locator_different_rights_are_not_merged() -> None:
    a = _mk(
        result_id="r1",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/x"),
        rights_status="PUBLIC",
    )
    b = _mk(
        result_id="r2",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/x"),
        rights_status="RESTRICTED",
    )
    out = dedupe_results([a, b])
    assert len(out.results) == 2
    assert out.conflicts_kept == 1
    for r in out.results:
        assert WarnCode.DUPLICATE_LOCATOR_DISTINCT_SOURCE.value in r.warnings


def test_same_locator_different_provenance_is_not_merged() -> None:
    a = _mk(
        result_id="r1",
        evidence_type=EvidenceType.PROJECT_ASSET,
        locator=AssetLocator(asset_id="a1", asset_version=1, content_hash="b" * 64),
    )
    b = _mk(
        result_id="r2",
        evidence_type=EvidenceType.PROJECT_ASSET,
        locator=AssetLocator(asset_id="a1", asset_version=1, content_hash="b" * 64),
        source_hash="c" * 64,
    )
    out = dedupe_results([a, b])
    assert len(out.results) == 2
    assert out.conflicts_kept == 1


# --------------------------------------------------------------------------
# 2. 跨维度绝不合并
# --------------------------------------------------------------------------


def test_different_evidence_type_is_never_merged() -> None:
    """教材 chunk 与同名文献即使标识巧合相近，也不得合并成一条。"""
    t = _mk(
        result_id="r1",
        evidence_type=EvidenceType.FOUNDATIONAL_MATH,
        locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id="c1"),
    )
    lit = _mk(result_id="r2", evidence_type=EvidenceType.LITERATURE, locator=LiteratureLocator(doi="10.1/x"))
    out = dedupe_results([t, lit])
    assert len(out.results) == 2
    assert out.collapsed == 0
    kinds = {r.evidence_type for r in out.results}
    assert kinds == {EvidenceType.FOUNDATIONAL_MATH, EvidenceType.LITERATURE}


def test_dedupe_preserves_dimension_label_on_every_result() -> None:
    t = _mk(
        result_id="r1",
        evidence_type=EvidenceType.FOUNDATIONAL_MATH,
        locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id="c1"),
    )
    out = dedupe_results([t])
    assert out.results[0].evidence_type is EvidenceType.FOUNDATIONAL_MATH
    assert isinstance(out.results[0].locator, TextbookLocator)


# --------------------------------------------------------------------------
# 3. rerank 权重恒定，无 query 特例
# --------------------------------------------------------------------------


def test_rerank_uses_module_constant_weights() -> None:
    assert hasattr(dedupe_mod, "DEFAULT_WEIGHTS")
    sig = inspect.signature(rerank)
    assert "query" not in sig.parameters
    assert "query_text" not in sig.parameters


def test_rerank_orders_by_score_components() -> None:
    low = _mk(
        result_id="r1",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/low"),
        score=0.1,
    )
    high = _mk(
        result_id="r2",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/high"),
        score=0.9,
    )
    ordered = rerank([low, high])
    assert [r.result_id for r in ordered] == ["r2", "r1"]


def test_rerank_never_rewrites_locator_or_rights() -> None:
    a = _mk(
        result_id="r1",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/x", venue="Nature"),
        rights_status="RESTRICTED",
    )
    out = rerank([a])[0]
    assert out.locator.doi == "10.1/x"
    assert out.locator.venue == "Nature"
    assert out.rights.rights_status == "RESTRICTED"


def test_source_code_has_no_query_literal_branch() -> None:
    """静态守卫：评分路径不得出现按查询字面量特判的分支。"""
    src = inspect.getsource(dedupe_mod)
    for banned in ("if query ==", 'if query_text ==', "hardcode", "SpecialCase"):
        assert banned not in src


# --------------------------------------------------------------------------
# 4. 维度交织：候选数量不均时不得让单一维度占满 top-k
# --------------------------------------------------------------------------


def _bulk_foundational(n: int) -> list[FederatedResult]:
    return [
        _mk(
            result_id=f"t{i}",
            evidence_type=EvidenceType.FOUNDATIONAL_MATH,
            locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id=f"c{i}"),
            score=0.9,
        )
        for i in range(n)
    ]


def test_balance_keeps_every_dimension_visible() -> None:
    """实测场景：教材 300 条候选 vs 资产/文献各 1 条 —— 必须三类都出现。"""
    many = _bulk_foundational(20)
    asset = _mk(
        result_id="a1",
        evidence_type=EvidenceType.PROJECT_ASSET,
        locator=AssetLocator(asset_id="a1", asset_version=1, content_hash="b" * 64),
        score=0.05,
    )
    lit = _mk(
        result_id="l1",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/x"),
        score=0.01,
    )
    out = dedupe_and_rank([*many, asset, lit], limit=6)
    kinds = {r.evidence_type for r in out.results}
    assert kinds == {
        EvidenceType.FOUNDATIONAL_MATH,
        EvidenceType.PROJECT_ASSET,
        EvidenceType.LITERATURE,
    }


def test_balance_preserves_score_order_within_dimension() -> None:
    a = _mk(
        result_id="t-hi",
        evidence_type=EvidenceType.FOUNDATIONAL_MATH,
        locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id="c-hi"),
        score=0.9,
    )
    b = _mk(
        result_id="t-lo",
        evidence_type=EvidenceType.FOUNDATIONAL_MATH,
        locator=TextbookLocator(source_id="s1", section_id="sec1", chunk_id="c-lo"),
        score=0.2,
    )
    out = dedupe_and_rank([b, a], limit=10)
    assert [r.result_id for r in out.results] == ["t-hi", "t-lo"]


def test_balance_can_be_disabled() -> None:
    many = _bulk_foundational(5)
    lit = _mk(
        result_id="l1",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/x"),
        score=0.01,
    )
    out = dedupe_and_rank([*many, lit], limit=3, balance_by_dimension=False)
    assert {r.evidence_type for r in out.results} == {EvidenceType.FOUNDATIONAL_MATH}


def test_balance_does_not_merge_or_rewrite() -> None:
    many = _bulk_foundational(3)
    lit = _mk(
        result_id="l1",
        evidence_type=EvidenceType.LITERATURE,
        locator=LiteratureLocator(doi="10.1/keep", venue="Nature"),
        rights_status="RESTRICTED",
    )
    out = dedupe_and_rank([*many, lit], limit=10).results
    kept = [r for r in out if r.evidence_type is EvidenceType.LITERATURE]
    assert len(kept) == 1
    assert kept[0].locator.doi == "10.1/keep"
    assert kept[0].rights.rights_status == "RESTRICTED"
