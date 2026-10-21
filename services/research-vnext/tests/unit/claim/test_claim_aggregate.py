"""RV12 · Claim 聚合与 AI proposal 边界测试（任务书 §必须完成 1 与 §4）。

两条不变量：
1. Claim 必须携带**全部** 13 个必需字段（statement / scope / assumptions /
   status / confidence basis / evidence links / experiment links / counterevidence /
   limitations / version / owner / ACL / audit）。
2. **AI 只能 propose claim/status** —— `ClaimProposal` 在类型上就没有权威 `status`，
   落库状态下永远由服务端 Gate 决定。
"""

from __future__ import annotations

import pytest

from research_vnext.core.claim.aggregate import (
    REQUIRED_CLAIM_FIELDS,
    ClaimAggregate,
    ClaimAssumption,
    ClaimProposal,
    ClaimScope,
    materialize_claim,
)
from research_vnext.core.claim.types import ClaimStatus


def _scope() -> ClaimScope:
    return ClaimScope(
        domain="常微分方程",
        population="一阶线性 ODE 初值问题",
        parameters=("a", "b"),
        boundary="a != 0 且 t >= 0",
    )


def _proposal(**over: object) -> ClaimProposal:
    base = dict(
        statement="当 a>0 时解在 t→∞ 单调收敛到 b/a。",
        scope=_scope(),
        assumptions=(ClaimAssumption(statement="系数连续", basis="题目给定", falsifiable=True),),
        claim_kind="PARAPHRASE",
        limitations=("仅覆盖一维",),
        evidence_refs=("ev-1",),
        experiment_refs=("run-1",),
        proposed_status="SOLVED",
        rationale="模型认为自己已经解决",
    )
    base.update(over)
    return ClaimProposal(**base)  # type: ignore[arg-type]


def _materialize(**over: object) -> ClaimAggregate:
    kw = dict(
        claim_id="11111111-1111-1111-1111-111111111111",
        session_id="22222222-2222-2222-2222-222222222222",
        revision=1,
        owner_id="33333333-3333-3333-3333-333333333333",
        acl={
            "tenant_id": "44444444-4444-4444-4444-444444444444",
            "project_id": "55555555-5555-5555-5555-555555555555",
            "session_id": "22222222-2222-2222-2222-222222222222",
            "owner_id": "33333333-3333-3333-3333-333333333333",
            "visibility": "project",
            "allowed_roles": [],
        },
    )
    kw.update(over)
    return materialize_claim(_proposal(), **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 1. 必需字段
# --------------------------------------------------------------------------


def test_required_field_set_is_frozen_and_covers_the_13_task_fields() -> None:
    assert REQUIRED_CLAIM_FIELDS == frozenset(
        {
            "statement",
            "scope",
            "assumptions",
            "status",
            "confidenceBasis",
            "evidenceLinks",
            "experimentLinks",
            "counterEvidence",
            "limitations",
            "version",
            "owner",
            "acl",
            "audit",
        }
    )


def test_claim_dict_carries_every_required_field() -> None:
    payload = _materialize().to_dict()
    missing = REQUIRED_CLAIM_FIELDS - set(payload)
    assert not missing, f"Claim 缺少必需字段: {sorted(missing)}"


def test_claim_carries_owner_acl_and_version() -> None:
    payload = _materialize(revision=3).to_dict()
    assert payload["version"] == 3
    assert payload["owner"] == "33333333-3333-3333-3333-333333333333"
    assert payload["acl"]["tenant_id"] == "44444444-4444-4444-4444-444444444444"
    assert payload["acl"]["visibility"] == "project"


def test_confidence_basis_is_computed_not_asserted() -> None:
    basis = _materialize().to_dict()["confidenceBasis"]
    assert basis["method"] == "rubric-v1"
    assert basis["band"] == "NONE"
    assert basis["score"] == 0.0
    assert basis["factors"] == {} or isinstance(basis["factors"], dict)


def test_audit_records_the_ai_proposed_status_separately() -> None:
    audit = _materialize().to_dict()["audit"]
    assert audit["aiProposedStatus"] == "SOLVED"
    assert audit["origin"] == "AI_PROPOSAL"


# --------------------------------------------------------------------------
# 2. AI 只能 propose
# --------------------------------------------------------------------------


def test_proposal_has_no_authoritative_status_field() -> None:
    p = _proposal()
    assert not hasattr(p, "status"), "AI proposal 不得携带权威 status（服务端生成）"
    assert p.proposed_status == "SOLVED"


def test_materializing_forces_inconclusive_regardless_of_model_claim() -> None:
    agg = _materialize()
    assert agg.status == ClaimStatus.INCONCLUSIVE.value
    assert agg.requires_review is True
    assert "AI_PROPOSED_NOT_VERIFIED" in agg.review_reasons


def test_empty_statement_is_rejected() -> None:
    with pytest.raises(Exception) as ei:
        _proposal(statement="   ")
    assert getattr(ei.value, "code", "") in {"CLAIM_STATEMENT_REQUIRED", "CLAIM_STATEMENT_EMPTY"}


def test_scope_is_mandatory() -> None:
    with pytest.raises(Exception) as ei:
        ClaimProposal(
            statement="某个结论",
            scope=None,  # type: ignore[arg-type]
            assumptions=(),
            claim_kind="PARAPHRASE",
            limitations=(),
        )
    assert getattr(ei.value, "code", "") == "CLAIM_SCOPE_REQUIRED"


def test_proposal_cannot_supply_server_owned_fields() -> None:
    """模型不得自带 confidenceBasis / counterEvidence / audit —— 那些是服务端事实。"""
    for banned in ("confidence_basis", "counter_evidence", "audit", "requires_review"):
        with pytest.raises(TypeError):
            ClaimProposal(  # type: ignore[call-arg]
                statement="x",
                scope=_scope(),
                assumptions=(),
                claim_kind="PARAPHRASE",
                limitations=(),
                **{banned: {}},
            )


def test_assumptions_must_declare_falsifiability() -> None:
    with pytest.raises(Exception) as ei:
        ClaimAssumption(statement="不可检验的信念", basis="直觉", falsifiable=False)
    assert getattr(ei.value, "code", "") == "ASSUMPTION_NOT_FALSIFIABLE"


def test_aggregate_rejects_unknown_status() -> None:
    with pytest.raises(Exception) as ei:
        _materialize(status="PROBABLY")
    assert getattr(ei.value, "code", "") == "CLAIM_STATUS_UNKNOWN"
