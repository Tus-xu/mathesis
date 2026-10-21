"""RV11 · ExperimentRun 域单元测试（先红后绿）。

覆盖任务书要求：
- 资源/网络/env 策略门：模型（或调用方）**不能**提高资源与网络权限；
- manifest 记录完备性：imageDigest/dependencyLock/seed/codeHash/command/envAllowlist/limits；
- exit 0 ≠ 结论成立：claim_holds 语义不在这里被翻转；
- 复现对比：同 manifest → EXACT_MATCH；容差内 → METRICS_TOLERANCE；否则 MISMATCH；
- exec_status → 真实 FAILED/CANCELLED/PARTIAL 映射（绝不返回绿色预览）。
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# 策略门
# --------------------------------------------------------------------------


class TestPolicyGate:
    def test_defaults_are_safe_and_complete(self):
        from research_vnext.core.domain.experiment_run import apply_policy_gate

        decision = apply_policy_gate({})
        eff = decision.effective
        assert eff["cpuLimit"] > 0
        assert eff["memoryLimitMb"] > 0
        assert eff["pidsLimit"] > 0
        assert eff["diskLimitMb"] > 0
        assert eff["wallTimeMs"] > 0
        assert eff["outputLimitBytes"] > 0
        assert decision.facts["network"] == "deny"

    def test_reasonable_request_passes_through(self):
        from research_vnext.core.domain.experiment_run import apply_policy_gate

        decision = apply_policy_gate({"cpuLimit": 1.0, "memoryLimitMb": 256})
        assert decision.effective["cpuLimit"] == 1.0
        assert decision.effective["memoryLimitMb"] == 256

    def test_unknown_limit_field_rejected(self):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, apply_policy_gate

        with pytest.raises(ExperimentPolicyRejected) as ei:
            apply_policy_gate({"gpuCount": 8})
        assert ei.value.code == "LIMIT_FIELD_UNKNOWN"

    def test_invalid_value_rejected(self):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, apply_policy_gate

        with pytest.raises(ExperimentPolicyRejected) as ei:
            apply_policy_gate({"memoryLimitMb": -5})
        assert ei.value.code == "LIMIT_VALUE_INVALID"
        with pytest.raises(ExperimentPolicyRejected):
            apply_policy_gate({"cpuLimit": "many"})

    @pytest.mark.parametrize(
        "field,value",
        [
            ("cpuLimit", 64.0),
            ("memoryLimitMb", 65536),
            ("pidsLimit", 100000),
            ("diskLimitMb", 8192),
            ("wallTimeMs", 10_800_000),
            ("outputLimitBytes", 1 << 30),
        ],
    )
    def test_escalation_above_ceiling_rejected(self, field, value):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, apply_policy_gate

        with pytest.raises(ExperimentPolicyRejected) as ei:
            apply_policy_gate({field: value})
        assert ei.value.code == "LIMIT_ESCALATION_REJECTED"
        assert field in str(ei.value.message)

    def test_network_allow_rejected(self):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, apply_policy_gate

        with pytest.raises(ExperimentPolicyRejected) as ei:
            apply_policy_gate({}, network="allow")
        assert ei.value.code == "NETWORK_ESCALATION_REJECTED"

    def test_unknown_env_key_rejected(self):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, apply_policy_gate

        with pytest.raises(ExperimentPolicyRejected) as ei:
            apply_policy_gate({}, environment={"MODEL_API_KEY": "sk-anything"})
        assert ei.value.code == "ENV_KEY_NOT_ALLOWED"

    def test_env_allowlist_value_constrained(self):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, apply_policy_gate

        with pytest.raises(ExperimentPolicyRejected) as ei:
            apply_policy_gate({}, environment={"PYTHONHASHSEED": "random"})
        assert ei.value.code == "ENV_VALUE_INVALID"

    def test_wall_time_must_fit_stage_budget(self):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, apply_policy_gate

        # stage 配置小于 ceiling+余量时：sandbox 墙钟若超出 worker 预算，
        # 必须在 dispatch 就拒绝，而不是让 worker 稍后把 handler 杀进无意义重试。
        with pytest.raises(ExperimentPolicyRejected) as ei:
            apply_policy_gate({"wallTimeMs": 240_000}, stage_timeout_seconds=180)
        assert ei.value.code == "WALL_TIME_EXCEEDS_STAGE_BUDGET"

    def test_ceiling_wall_fits_default_stage_budget(self):
        from research_vnext.core.domain.experiment_run import apply_policy_gate

        # 默认 stage 300s - 60s 余量 = 240s = ceiling ⇒ 恰好放行
        decision = apply_policy_gate({"wallTimeMs": 240_000})
        assert decision.effective["wallTimeMs"] == 240_000

    def test_facts_record_ceilings_and_allowlist(self):
        from research_vnext.core.domain.experiment_run import ENV_ALLOWLIST, apply_policy_gate

        decision = apply_policy_gate({"memoryLimitMb": 512})
        assert decision.facts["ceilings"]["memoryLimitMb"] >= 512
        assert set(decision.facts["envAllowlist"]) == set(ENV_ALLOWLIST)


# --------------------------------------------------------------------------
# manifest 记录
# --------------------------------------------------------------------------


def _full_manifest() -> dict:
    return {
        "run_id": "rv11-test-run",
        "code_hash": "a" * 64,
        "input_hash": "b" * 64,
        "output_hash": "c" * 64,
        "artifact_hash": "d" * 64,
        "dependency_lock_hash": "e" * 64,
        "image": "math-arena-sandbox:0.2.0",
        "image_digest": "sha256:" + "f" * 64,
        "seed": 42,
        "exec_status": "pass",
        "exit_code": 0,
        "duration_ms": 1200,
        "backend": "container",
        "isolation_mode": "container",
        "isolation_controls_enforced": {"network_isolation": True, "non_root_user": True},
        "unenforced_reasons": {"tmpfs_workdir": "PARTIAL by design"},
        "effective_limits": {"cpu_limit": 1.0, "memory_limit_mb": 256},
        "warnings": [],
        "truncation": {"truncated": False},
        "sandbox_version": "0.2.0",
        "platform": "linux (container)",
    }


class TestReproRecord:
    def test_record_has_all_mandatory_keys(self):
        from research_vnext.core.domain.experiment_run import build_repro_record

        rec = build_repro_record(
            _full_manifest(),
            requested_limits={"cpuLimit": 1.0},
            command=["docker", "run", "--rm"],
            env_allowlist=["PYTHONHASHSEED"],
        )
        for key in (
            "codeHash",
            "inputHash",
            "outputHash",
            "artifactHash",
            "imageDigest",
            "dependencyLockHash",
            "seed",
            "command",
            "envAllowlist",
            "resourceLimits",
            "isolationMode",
            "isolationControls",
        ):
            assert key in rec, f"缺少 {key}"
        assert rec["resourceLimits"]["requested"] == {"cpuLimit": 1.0}
        assert rec["command"] == ["docker", "run", "--rm"]

    def test_incomplete_manifest_rejected(self):
        from research_vnext.core.domain.experiment_run import ExperimentPolicyRejected, build_repro_record

        manifest = _full_manifest()
        manifest.pop("image_digest")
        with pytest.raises(ExperimentPolicyRejected) as ei:
            build_repro_record(manifest, requested_limits={}, command=[], env_allowlist=[])
        assert ei.value.code == "REPRO_MANIFEST_INCOMPLETE"

    def test_reproducibility_missing_reports_gaps(self):
        from research_vnext.core.domain.experiment_run import build_repro_record, reproducibility_missing

        rec = build_repro_record(_full_manifest(), requested_limits={}, command=[], env_allowlist=[])
        assert reproducibility_missing(rec) == []
        broken = dict(rec)
        broken.pop("imageDigest")
        assert "imageDigest" in reproducibility_missing(broken)


# --------------------------------------------------------------------------
# 复现对比
# --------------------------------------------------------------------------


class TestCompare:
    def _pair(self, *, same_output=True, same_seed=True):
        from research_vnext.core.domain.experiment_run import build_repro_record

        orig = build_repro_record(_full_manifest(), requested_limits={}, command=[], env_allowlist=[])
        replica_manifest = _full_manifest()
        if not same_output:
            replica_manifest["output_hash"] = "9" * 64
        if not same_seed:
            replica_manifest["seed"] = 7
        replica = build_repro_record(replica_manifest, requested_limits={}, command=[], env_allowlist=[])
        return orig, replica

    def test_exact_match_when_hashes_identical(self):
        from research_vnext.core.domain.experiment_run import compare_with_original

        orig, replica = self._pair()
        verdict = compare_with_original(orig, replica, metrics_orig={"x": 1.0}, metrics_replica={"x": 1.0})
        assert verdict["verdict"] == "EXACT_MATCH"
        assert verdict["outputHashMatch"] is True

    def test_insufficient_data_when_manifest_broken(self):
        from research_vnext.core.domain.experiment_run import compare_with_original

        orig, replica = self._pair()
        replica.pop("outputHash")
        verdict = compare_with_original(orig, replica, metrics_orig={}, metrics_replica={})
        assert verdict["verdict"] == "INSUFFICIENT_DATA"

    def test_metrics_tolerance_when_output_differs_but_metrics_close(self):
        from research_vnext.core.domain.experiment_run import compare_with_original

        orig, replica = self._pair(same_output=False)
        verdict = compare_with_original(
            orig, replica, metrics_orig={"pi": 3.14159}, metrics_replica={"pi": 3.14160}, tolerance=1e-3
        )
        assert verdict["verdict"] == "METRICS_TOLERANCE"
        assert verdict["outputHashMatch"] is False
        assert verdict["metricDeltas"][0]["withinTolerance"] is True

    def test_mismatch_when_metrics_diverge(self):
        from research_vnext.core.domain.experiment_run import compare_with_original

        orig, replica = self._pair(same_output=False)
        verdict = compare_with_original(
            orig, replica, metrics_orig={"pi": 3.14}, metrics_replica={"pi": 2.72}, tolerance=1e-3
        )
        assert verdict["verdict"] == "MISMATCH"

    def test_mismatch_when_seed_differs(self):
        from research_vnext.core.domain.experiment_run import compare_with_original

        orig, replica = self._pair(same_seed=False)
        verdict = compare_with_original(orig, replica, metrics_orig={}, metrics_replica={})
        assert verdict["verdict"] == "MISMATCH"
        assert verdict["seedMatch"] is False


# --------------------------------------------------------------------------
# exec_status → 真实结果映射（绝不绿色预览）
# --------------------------------------------------------------------------


class TestOutcomeMapping:
    def test_pass_maps_succeeded_with_no_claim(self):
        from research_vnext.core.domain.experiment_run import exec_status_to_outcome

        outcome = exec_status_to_outcome("pass")
        assert outcome.run_status == "SUCCEEDED"
        assert outcome.error_code == ""
        assert outcome.retryable is False

    @pytest.mark.parametrize(
        "status,code",
        [("fail", "SANDBOX_FAILED"), ("timeout", "SANDBOX_TIMEOUT"), ("refused", "SANDBOX_REFUSED")],
    )
    def test_user_level_failures_are_terminal(self, status, code):
        from research_vnext.core.domain.experiment_run import exec_status_to_outcome

        outcome = exec_status_to_outcome(status)
        assert outcome.run_status == "FAILED"
        assert outcome.error_code == code
        assert outcome.retryable is False

    def test_error_is_retryable_infrastructure_failure(self):
        from research_vnext.core.domain.experiment_run import exec_status_to_outcome

        outcome = exec_status_to_outcome("error")
        assert outcome.run_status == "FAILED"
        assert outcome.retryable is True

    def test_guard_violation_overrides_to_permanent(self):
        from research_vnext.core.domain.experiment_run import exec_status_to_outcome

        outcome = exec_status_to_outcome("fail", guard_violation={"code": "BLOCKED_IMPORT"})
        assert outcome.error_code == "SANDBOX_GUARD_VIOLATION"
        assert outcome.retryable is False

    def test_cancelled_is_not_success(self):
        from research_vnext.core.domain.experiment_run import exec_status_to_outcome

        outcome = exec_status_to_outcome("pass", cancelled=True)
        assert outcome.run_status == "CANCELLED"
