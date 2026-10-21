"""RV06 · scope 权威与跨维隔离测试。

失败先行。钉死：
- Query 必填 actor/scope/session/purpose/allowlist/filters/budget/deadline/trace；
- ResolvedScope 一次性冻结，provider **只能收窄不能放宽**；
- 跨 tenant / 跨 project / 跨 school（班级）的命中一律剔除并记 SCOPE_VIOLATION。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from research_vnext.core.federation.contracts import (
    AccessRights,
    Budget,
    FederatedQuery,
    HitScope,
    LiteratureLocator,
    Provenance,
    ProviderHit,
    ScoreComponents,
)
from research_vnext.core.federation.errors import QueryInvalid
from research_vnext.core.federation.scope import (
    ViolationCode,
    check_hit_scope,
    resolve_scope,
)
from research_vnext.core.federation.types import EvidenceType


def _hit(
    *,
    provider: str = "crossref",
    tenant_id: str = "t1",
    project_id: str | None = "p1",
    school_id: str | None = None,
) -> ProviderHit:
    return ProviderHit(
        evidence_type=EvidenceType.LITERATURE,
        provider=provider,
        provider_version="1.0.0",
        locator=LiteratureLocator(doi="10.1/x"),
        snippet="snippet",
        rights=AccessRights(rights_status="UNKNOWN", rights_evidence_ref="e1", redistributable=False),
        provenance=Provenance(retrieved_at="2026-09-13T00:00:00Z", parser_version="rv06", source_hash="a" * 64),
        retrieved_at="2026-09-13T00:00:00Z",
        score_components=ScoreComponents(lexical=0.5, vector=0.0, rights_penalty=0.0, recency=1.0),
        scope_meta=HitScope(tenant_id=tenant_id, project_id=project_id, school_id=school_id),
    )


def _scope(**over: object):
    base = {
        "actor_id": "actor-1",
        "tenant_id": "t1",
        "project_ids": frozenset({"p1"}),
        "session_id": "sess-1",
        "school_id": "school-A",
        "capabilities": frozenset({"workspace.research.enter", "research.session.write"}),
        "provider_allowlist": ("crossref", "project_asset", "foundational_math"),
    }
    base.update(over)
    return resolve_scope(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 1. Query 必填契约
# --------------------------------------------------------------------------


def test_query_requires_every_declared_field() -> None:
    q = FederatedQuery(
        actor_id="a1",
        tenant_id="t1",
        project_id="p1",
        session_id="s1",
        purpose="literature_review",
        provider_allowlist=("crossref",),
        filters={"evidence_types": [EvidenceType.LITERATURE]},
        budget=Budget(max_results=10, max_provider_calls=3, cost_ceiling=0.0),
        deadline_ms=2000,
        trace_id="trace-1",
        query_text="graph neural networks",
    )
    assert q.query_hash and len(q.query_hash) == 64
    assert q.purpose == "literature_review"


@pytest.mark.parametrize(
    "missing",
    ["actor_id", "tenant_id", "purpose", "trace_id", "deadline_ms"],
)
def test_query_missing_required_field_is_rejected(missing: str) -> None:
    kwargs = {
        "actor_id": "a1",
        "tenant_id": "t1",
        "project_id": "p1",
        "session_id": "s1",
        "purpose": "literature_review",
        "provider_allowlist": ("crossref",),
        "filters": {},
        "budget": Budget(max_results=10, max_provider_calls=3, cost_ceiling=0.0),
        "deadline_ms": 2000,
        "trace_id": "trace-1",
        "query_text": "q",
    }
    kwargs[missing] = "" if missing not in ("deadline_ms",) else 0
    with pytest.raises(QueryInvalid):
        FederatedQuery(**kwargs)  # type: ignore[arg-type]


def test_query_requires_provider_allowlist() -> None:
    with pytest.raises(QueryInvalid):
        FederatedQuery(
            actor_id="a1",
            tenant_id="t1",
            project_id="p1",
            session_id="s1",
            purpose="p",
            provider_allowlist=(),
            filters={},
            budget=Budget(max_results=10, max_provider_calls=3, cost_ceiling=0.0),
            deadline_ms=1000,
            trace_id="trace-1",
            query_text="q",
        )


# --------------------------------------------------------------------------
# 2. Scope 冻结：provider 不能放宽
# --------------------------------------------------------------------------


def test_resolved_scope_is_immutable() -> None:
    sc = _scope()
    with pytest.raises(FrozenInstanceError):
        sc.tenant_id = "t2"  # type: ignore[misc]


def test_scope_has_no_widen_api() -> None:
    """provider 侧不得存在任何放宽 scope 的入口。"""
    sc = _scope()
    for name in ("widen", "add_project", "add_tenant", "extend", "allow_more"):
        assert not hasattr(sc, name), f"ResolvedScope 不得提供 {name}()"


# --------------------------------------------------------------------------
# 3. 跨 tenant / project / school 负例
# --------------------------------------------------------------------------


def test_cross_tenant_hit_is_violation() -> None:
    code = check_hit_scope(_hit(tenant_id="t-other"), _scope())
    assert code is ViolationCode.TENANT_MISMATCH


def test_cross_project_hit_is_violation() -> None:
    code = check_hit_scope(_hit(project_id="p-other"), _scope())
    assert code is ViolationCode.PROJECT_MISMATCH


def test_cross_school_hit_is_violation() -> None:
    code = check_hit_scope(_hit(school_id="school-B"), _scope())
    assert code is ViolationCode.SCHOOL_MISMATCH


def test_same_scope_hit_is_allowed() -> None:
    assert check_hit_scope(_hit(project_id="p1", school_id="school-A"), _scope()) is None


def test_platform_seed_visible_to_every_school() -> None:
    """school_id 为 NULL 的平台级种子对全部 school 可见（但 tenant/project 仍要匹配）。"""
    assert check_hit_scope(_hit(school_id=None), _scope()) is None


def test_provider_not_in_allowlist_is_violation() -> None:
    """provider 不得自行扩展自己被调用的范围（防冒充注册）。"""
    code = check_hit_scope(_hit(provider="web_external"), _scope())
    assert code is ViolationCode.PROVIDER_NOT_ALLOWED


def test_project_scoped_scope_rejects_projectless_hit() -> None:
    """查询限定在 p1 时，无 project 归属的命中不得混入。"""
    code = check_hit_scope(_hit(project_id=None), _scope())
    assert code is ViolationCode.PROJECT_MISMATCH


def test_scope_without_school_restriction_allows_any_school() -> None:
    """未限定 school 的 scope（例如纯文献查询）不应用 school 维度误杀。"""
    sc = _scope(school_id=None)
    assert check_hit_scope(_hit(school_id="school-Z"), sc) is None
