"""RV12 · Verifier 适配器测试：数学检查 / 数据代码复现 / 敏感性 / robustness / 反例 / 边界。

反例与失败路径是重点：一个"永远 PASS"的 verifier 比没有 verifier 更危险。
"""

from __future__ import annotations

import pytest

from research_vnext.core.verification.adapters import (
    boundary_check,
    code_repro_check,
    counterexample_check,
    data_repro_check,
    math_check,
    robustness_check,
    sensitivity_check,
)

# --------------------------------------------------------------------------
# 数学检查
# --------------------------------------------------------------------------


def test_math_identity_passes_on_all_sample_points() -> None:
    out = math_check(
        {
            "checks": [
                {
                    "name": "代数恒等 (a-b)^2 = a^2-2ab+b^2",
                    "kind": "IDENTITY",
                    "lhs": "(a-b)**2",
                    "rhs": "a**2-2*a*b+b**2",
                    "points": [{"a": 1, "b": 1}, {"a": 3, "b": 0.5}, {"a": -2, "b": 7}],
                    "tolerance": 1e-9,
                }
            ]
        }
    )
    assert out.result == "PASS"
    assert out.criteria[0]["passed"] is True
    assert out.criteria[0]["maxResidual"] <= 1e-9


def test_math_identity_fails_when_wrong() -> None:
    out = math_check(
        {
            "checks": [
                {
                    "name": "错误恒等",
                    "kind": "IDENTITY",
                    "lhs": "(a-b)**2",
                    "rhs": "a**2+b**2",
                    "points": [{"a": 3, "b": 1}],
                    "tolerance": 1e-9,
                }
            ]
        }
    )
    assert out.result == "FAIL"
    assert out.criteria[0]["passed"] is False


def test_math_dimension_mismatch_fails() -> None:
    out = math_check(
        {
            "checks": [
                {"name": "量纲", "kind": "DIMENSION", "lhs_dim": "m/s", "rhs_dim": "m/s^2"}
            ]
        }
    )
    assert out.result == "FAIL"
    assert out.criteria[0]["reason"] == "DIMENSION_MISMATCH"


def test_math_without_any_evaluable_check_is_unknown_not_pass() -> None:
    out = math_check({"checks": []})
    assert out.result == "UNKNOWN"
    assert "NO_CHECKS" in out.unknowns


def test_math_rejects_unsafe_expression() -> None:
    out = math_check(
        {
            "checks": [
                {
                    "name": "注入尝试",
                    "kind": "IDENTITY",
                    "lhs": "__import__('os').system('id')",
                    "rhs": "0",
                    "points": [{}],
                }
            ]
        }
    )
    assert out.result == "UNKNOWN"
    assert any("EXPRESSION_REJECTED" in u for u in out.unknowns)


# --------------------------------------------------------------------------
# 复现检查
# --------------------------------------------------------------------------


def _repro(**over: object) -> dict:
    base = {
        "imageDigest": "sha256:abc",
        "dependencyLockHash": "lock-1",
        "seed": 42,
        "codeHash": "code-1",
        "outputHash": "out-1",
    }
    base.update(over)
    return base


def test_code_repro_exact_match_passes() -> None:
    out = code_repro_check({"original": _repro(), "replica": _repro()})
    assert out.result == "PASS"
    assert out.detail["verdict"] == "EXACT_MATCH"


def test_code_repro_mismatch_fails() -> None:
    out = code_repro_check({"original": _repro(), "replica": _repro(outputHash="out-2")})
    assert out.result == "FAIL"
    assert out.detail["verdict"] == "MISMATCH"


def test_code_repro_missing_metadata_is_unknown() -> None:
    out = code_repro_check({"original": _repro(), "replica": {"seed": 42}})
    assert out.result == "UNKNOWN"
    assert "dependencyLockHash" in out.unknowns or "INSUFFICIENT_DATA" in out.unknowns


def test_data_repro_hash_match_passes_and_mismatch_fails() -> None:
    ok = data_repro_check(
        {
            "original": {"datasetHash": "d1", "rows": 1000, "checksum": "c1"},
            "replica": {"datasetHash": "d1", "rows": 1000, "checksum": "c1"},
        }
    )
    assert ok.result == "PASS"
    bad = data_repro_check(
        {
            "original": {"datasetHash": "d1", "rows": 1000},
            "replica": {"datasetHash": "d2", "rows": 1000},
        }
    )
    assert bad.result == "FAIL"


def test_data_repro_missing_input_is_unknown() -> None:
    out = data_repro_check({"original": {}, "replica": {}})
    assert out.result == "UNKNOWN"


# --------------------------------------------------------------------------
# 敏感性 / robustness
# --------------------------------------------------------------------------


def test_sensitivity_within_tolerance_passes() -> None:
    out = sensitivity_check(
        {
            "baseline": 1.0,
            "tolerance": 0.05,
            "perturbations": [
                {"parameter": "a", "delta": 0.01, "metric": 1.01},
                {"parameter": "a", "delta": -0.01, "metric": 0.99},
            ],
        }
    )
    assert out.result == "PASS"


def test_sensitivity_exceeding_tolerance_fails_and_names_the_parameter() -> None:
    out = sensitivity_check(
        {
            "baseline": 1.0,
            "tolerance": 0.05,
            "perturbations": [{"parameter": "a", "delta": 0.2, "metric": 1.6}],
        }
    )
    assert out.result == "FAIL"
    assert out.criteria[0]["blocking"] == "SENSITIVITY_UNSTABLE"
    assert out.detail["worst"]["parameter"] == "a"


def test_sensitivity_without_perturbations_is_unknown() -> None:
    out = sensitivity_check({"baseline": 1.0, "tolerance": 0.05, "perturbations": []})
    assert out.result == "UNKNOWN"


def test_robustness_across_seeds_passes_when_dispersion_small() -> None:
    out = robustness_check(
        {
            "tolerance": 0.01,
            "samples": [{"seed": 1, "metric": 1.0}, {"seed": 2, "metric": 1.005}],
        }
    )
    assert out.result == "PASS"


def test_robustness_fails_when_seed_dispersion_large() -> None:
    out = robustness_check(
        {
            "tolerance": 0.01,
            "samples": [{"seed": 1, "metric": 1.0}, {"seed": 2, "metric": 1.8}],
        }
    )
    assert out.result == "FAIL"
    assert out.criteria[0]["blocking"] == "ROBUSTNESS_SEED_SENSITIVE"


def test_robustness_with_single_seed_is_unknown() -> None:
    out = robustness_check({"tolerance": 0.01, "samples": [{"seed": 1, "metric": 1.0}]})
    assert out.result == "UNKNOWN"


# --------------------------------------------------------------------------
# 反例 / 边界
# --------------------------------------------------------------------------


def test_counterexample_reproduced_means_fail_with_witness_kept() -> None:
    out = counterexample_check(
        {
            "candidates": [
                {
                    "name": "a=0.5 时 f(a)<0",
                    "expression": "a**2-a-0.5",
                    "variables": {"a": 0.5},
                    "violates_when": "negative",
                    "tolerance": 1e-9,
                }
            ]
        }
    )
    assert out.result == "FAIL"
    witness = out.detail["witness"]
    assert witness["name"] == "a=0.5 时 f(a)<0"
    assert witness["value"] == pytest.approx(-0.75)
    # 反证必须被保留在 artifact refs 里（不得"因为不好看就丢掉"）
    assert out.artifacts


def test_counterexample_not_reproduced_passes() -> None:
    out = counterexample_check(
        {
            "candidates": [
                {
                    "name": "a=2 时 f(a)<0",
                    "expression": "a**2-a-0.5",
                    "variables": {"a": 2},
                    "violates_when": "negative",
                    "tolerance": 1e-9,
                }
            ]
        }
    )
    assert out.result == "PASS"
    assert out.detail["witness"] is None


def test_counterexample_without_candidates_is_unknown() -> None:
    out = counterexample_check({"candidates": []})
    assert out.result == "UNKNOWN"


def test_boundary_case_violation_fails() -> None:
    out = boundary_check(
        {
            "cases": [
                {
                    "name": "a->0+ 时 1/a 应当有限",
                    "expression": "1/a",
                    "variables": {"a": 0.0},
                    "expect": "finite",
                }
            ]
        }
    )
    assert out.result in {"FAIL", "UNKNOWN"}
    if out.result == "FAIL":
        assert out.criteria[0]["blocking"] == "BOUNDARY_VIOLATION"


def test_boundary_case_satisfied_passes() -> None:
    out = boundary_check(
        {
            "cases": [
                {"name": "a=1 时 1/a>0", "expression": "1/a", "variables": {"a": 1.0}, "expect": "positive"}
            ]
        }
    )
    assert out.result == "PASS"


def test_boundary_without_cases_is_unknown() -> None:
    out = boundary_check({"cases": []})
    assert out.result == "UNKNOWN"


# --------------------------------------------------------------------------
# 通用：结果永远落在封闭三值
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn,params",
    [
        (math_check, {"checks": [{"name": "x", "kind": "IDENTITY", "lhs": "?", "rhs": "0", "points": [{}]}]}),
        (code_repro_check, {"original": {}, "replica": {}}),
        (data_repro_check, {}),
        (sensitivity_check, {}),
        (robustness_check, {}),
        (counterexample_check, {}),
        (boundary_check, {}),
    ],
)
def test_result_is_always_in_closed_three_valued_set(fn, params) -> None:  # noqa: ANN001
    out = fn(params)
    assert out.result in {"PASS", "FAIL", "UNKNOWN"}
    assert out.criteria is not None
