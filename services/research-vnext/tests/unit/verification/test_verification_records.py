"""RV12 · VerificationRecord 结构与 verifier AI run 审计测试（任务书 §3 与 §8）。

要求：**每类结果都有结构化 VerificationRecord 和 artifact refs**，
且**每次 verifier AI run 有 PromptRef / ContextManifest / tool policy / cost / trace**。
"""

from __future__ import annotations

import pytest

from research_vnext.core.verification.service import (
    VERIFIER_PROMPT_ID,
    build_verifier_context_manifest,
    verification_prompt_ref,
)
from research_vnext.core.verification.types import (
    DEFAULT_TOOL_POLICY,
    VerificationCriterion,
    VerificationKind,
    VerificationRecord,
)


def _record(**over: object) -> VerificationRecord:
    base = dict(
        record_id="55555555-5555-5555-5555-555555555555",
        claim_id="11111111-1111-1111-1111-111111111111",
        session_id="22222222-2222-2222-2222-222222222222",
        tenant_id="44444444-4444-4444-4444-444444444444",
        verifier_id="math.check",
        verifier_version="1",
        kind=VerificationKind.MATH_CHECK,
        result="PASS",
        criteria=(
            VerificationCriterion(code="ALGEBRAIC_IDENTITY", passed=True, detail="残差 0"),
        ),
        artifact_refs=("artifact:verification/55555555/math.json",),
        detail={"maxResidual": 0.0},
        independent=True,
        prompt_ref="rv.verifier.audit@v1#deadbeefdeadbeef",
        context_manifest_ref="ctx-1",
        tool_policy=DEFAULT_TOOL_POLICY,
        cost_usd=None,
        cost_basis="deterministic-no-llm",
        ai_run_id="",
        trace_id="trace-1",
        created_at="2026-09-14T09:00:00+00:00",
    )
    base.update(over)
    return VerificationRecord(**base)  # type: ignore[arg-type]


def test_record_carries_every_audit_dimension() -> None:
    payload = _record().to_dict()
    for key in (
        "recordId",
        "claimId",
        "sessionId",
        "tenantId",
        "verifierId",
        "verifierVersion",
        "kind",
        "result",
        "criteria",
        "artifactRefs",
        "detail",
        "independent",
        "promptRef",
        "contextManifestRef",
        "toolPolicy",
        "costUsd",
        "costBasis",
        "aiRunId",
        "traceId",
        "createdAt",
    ):
        assert key in payload, key
    assert payload["artifactRefs"], "必须给出 artifact refs"


@pytest.mark.parametrize("kind", [k.value for k in VerificationKind])
def test_every_kind_can_produce_a_structured_record(kind: str) -> None:
    rec = _record(kind=kind)
    assert rec.to_dict()["kind"] == kind


def test_unknown_result_is_rejected() -> None:
    with pytest.raises(Exception) as ei:
        _record(result="MAYBE")
    assert getattr(ei.value, "code", "") == "VERIFICATION_RESULT_UNKNOWN"


def test_unknown_cost_is_null_not_zero() -> None:
    rec = _record()
    assert rec.cost_usd is None
    assert rec.cost_basis == "deterministic-no-llm"


def test_record_is_observation_only_by_construction() -> None:
    """Verifier 只**观察**：记录不得携带任何"已改状态"的语义。"""
    payload = _record().to_dict()
    assert payload["observationOnly"] is True
    for banned in ("appliedStatus", "mutatedClaim", "deletedEvidence", "patchedExperiment"):
        assert banned not in payload


def test_tool_policy_is_embedded_so_permissions_are_auditable() -> None:
    policy = _record().to_dict()["toolPolicy"]
    assert policy["network"] == "deny"
    assert "MUTATE_EXPERIMENT" in policy["forbiddenOperations"]
    assert "DELETE_COUNTEREVIDENCE" in policy["forbiddenOperations"]


# --------------------------------------------------------------------------
# PromptRef / ContextManifest
# --------------------------------------------------------------------------


def test_verifier_prompt_is_registered_and_published() -> None:
    from research_vnext.core.llm.prompts import registry as R

    R.reset_releases()
    assert VERIFIER_PROMPT_ID in R.REGISTRY or VERIFIER_PROMPT_ID.replace("@v1", "") in str(R.REGISTRY)
    ref = verification_prompt_ref()
    assert "@v" in ref and "#" in ref


def test_context_manifest_has_refs_and_no_body_text() -> None:
    manifest = build_verifier_context_manifest(
        claim={"claimId": "c1", "statement": "SECRET-CLAIM-BODY", "claimKind": "PARAPHRASE"},
        evidence=[{"evidenceId": "ev-1", "evidenceType": "PAPER_FULL_TEXT"}],
        run_refs=["run-1"],
        namespace={"tenant": "t1", "session": "s1"},
    )
    record = manifest.to_record()
    assert record["contentHash"]
    assert manifest.section_refs(), "必须给出引用"
    blob = str(record)
    assert "SECRET-CLAIM-BODY" not in blob, "ContextManifest 不得携带正文"
