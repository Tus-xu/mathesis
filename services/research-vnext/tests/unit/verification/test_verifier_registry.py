"""RV12 · Verifier 注册表与权限边界测试。

两条 fail-closed 铁律：
1. **未知 verifier 一律拒绝**（不认识的方法不能"默认通过"）；
2. **Verifier 不拥有修改原实验与删除反证的权限** —— 这必须写成
   可被机器断言的工具策略，而不是注释里的君子协定。
"""

from __future__ import annotations

import pytest

from research_vnext.core.verification.registry import (
    VERIFIER_SPECS,
    UnknownVerifier,
    get_verifier_spec,
    require_verifier,
)
from research_vnext.core.verification.types import (
    DEFAULT_TOOL_POLICY,
    DELETE_COUNTEREVIDENCE,
    FORBIDDEN_OPERATIONS,
    MODIFY_EVIDENCE,
    MUTATE_EXPERIMENT,
    PUBLISH_CLAIM,
    WRITE_DOMAIN_STATE,
    ToolPolicyViolation,
    VerificationKind,
    assert_operation_allowed,
)


def test_every_verification_kind_has_exactly_one_default_verifier() -> None:
    kinds = {spec.kind for spec in VERIFIER_SPECS.values()}
    assert kinds == {k.value for k in VerificationKind}
    for kind in VerificationKind:
        matching = [s for s in VERIFIER_SPECS.values() if s.kind == kind.value]
        assert len(matching) >= 1, f"{kind.value} 没有可用 verifier"


def test_specs_are_versioned_and_described() -> None:
    for vid, spec in VERIFIER_SPECS.items():
        assert spec.verifier_id == vid
        assert spec.version, f"{vid} 缺少版本"
        assert spec.description, f"{vid} 缺少说明"
        assert spec.cost_basis_default in {"deterministic-no-llm", "table", "unknown-model"}


def test_unknown_verifier_is_rejected_fail_closed() -> None:
    with pytest.raises(UnknownVerifier) as ei:
        require_verifier("magic.verifier")
    assert ei.value.code == "UNKNOWN_VERIFIER"
    assert get_verifier_spec("magic.verifier") is None


@pytest.mark.parametrize(
    "op",
    [MUTATE_EXPERIMENT, DELETE_COUNTEREVIDENCE, MODIFY_EVIDENCE, WRITE_DOMAIN_STATE, PUBLISH_CLAIM],
)
def test_forbidden_operations_are_all_rejected(op: str) -> None:
    assert op in FORBIDDEN_OPERATIONS
    with pytest.raises(ToolPolicyViolation) as ei:
        assert_operation_allowed(DEFAULT_TOOL_POLICY, op)
    assert ei.value.code == "TOOL_POLICY_VIOLATION"


def test_read_and_verify_operations_are_allowed() -> None:
    for op in ("READ_EVIDENCE", "READ_EXPERIMENT_RESULT", "RUN_CHECK", "WRITE_VERIFICATION_RECORD"):
        assert_operation_allowed(DEFAULT_TOOL_POLICY, op)


def test_policy_has_no_network_and_bounded_cost() -> None:
    assert DEFAULT_TOOL_POLICY.network == "deny"
    assert DEFAULT_TOOL_POLICY.max_cost_usd <= 1.0
    assert DEFAULT_TOOL_POLICY.max_artifact_refs > 0


def test_unknown_operation_is_rejected_not_silently_allowed() -> None:
    with pytest.raises(ToolPolicyViolation):
        assert_operation_allowed(DEFAULT_TOOL_POLICY, "SOMETHING_NEW")
