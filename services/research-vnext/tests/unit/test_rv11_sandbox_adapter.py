"""RV11 · sandbox adapter 单元测试（纯映射/解析，不需要 Docker）。

被测对象：`research_vnext/core/execution/sandbox_adapter.py`
- research ExecutionRequest → sandbox ExecutionRequest 的映射（含 limits 防御性再钳制）；
- sandbox 结果 → research ExecutionResult 的映射（repro 记录 / claim_holds=None 不变量）；
- `RESULT_JSON:` 信封解析与 artifact 物化计划（metrics.json / table.csv / figure.svg）。
"""

from __future__ import annotations

import pytest


DEMO_STDOUT = (
    "[sandbox-runner] python 3.12.8\n"
    "[sandbox-runner] seed=42\n"
    'RESULT_JSON: {"metrics": {"pi_estimate": 3.141592}, '
    '"table": {"columns": ["n", "estimate"], "rows": [[100, 3.12], [1000, 3.141]]}, '
    '"figure": {"title": "estimate", "points": [[100, 3.12], [1000, 3.141]]}}\n'
)


class TestResultEnvelope:
    def test_parse_last_result_json_line(self):
        from research_vnext.core.execution.sandbox_adapter import parse_result_envelope

        env = parse_result_envelope(DEMO_STDOUT)
        assert env["metrics"]["pi_estimate"] == pytest.approx(3.141592)
        assert env["table"]["columns"] == ["n", "estimate"]

    def test_missing_envelope_is_empty_not_error(self):
        from research_vnext.core.execution.sandbox_adapter import parse_result_envelope

        assert parse_result_envelope("no envelope here") == {}

    def test_corrupt_envelope_is_empty_not_error(self):
        from research_vnext.core.execution.sandbox_adapter import parse_result_envelope

        assert parse_result_envelope("RESULT_JSON: {not json") == {}


class TestArtifactPlan:
    def test_plan_materialises_metrics_table_figure(self):
        from research_vnext.core.execution.sandbox_adapter import parse_result_envelope, plan_artifacts

        env = parse_result_envelope(DEMO_STDOUT)
        plan = plan_artifacts("run-1", env)
        paths = [item["path"] for item in plan]
        assert "artifacts/metrics.json" in paths
        assert "artifacts/table.csv" in paths
        assert "artifacts/figure.svg" in paths
        for item in plan:
            assert len(item["sha256"]) == 64
            assert item["derivedFrom"] == "outputHash"
            assert item["content"]  # non-empty payload bytes/text

    def test_table_csv_escapes_and_headers(self):
        import json as _json

        from research_vnext.core.execution.sandbox_adapter import parse_result_envelope

        line = "RESULT_JSON: " + _json.dumps(
            {"table": {"columns": ["a", "b"], "rows": [[1, "x,y"], [2, 'q"z']]}}
        )
        env = parse_result_envelope(line)
        from research_vnext.core.execution.sandbox_adapter import plan_artifacts

        plan = {item["path"]: item for item in plan_artifacts("r", env)}
        csv_text = plan["artifacts/table.csv"]["content"]
        lines = csv_text.strip().splitlines()
        assert lines[0] == "a,b"
        assert '"x,y"' in lines[1]
        assert '"q""z"' in lines[2]

    def test_empty_envelope_yields_no_artifacts(self):
        from research_vnext.core.execution.sandbox_adapter import plan_artifacts

        assert plan_artifacts("r", {}) == []


class TestRequestMapping:
    def _research_request(self, **over):
        from research_vnext.core.execution.executor import ExecutionRequest

        base = dict(
            job_id="job-1",
            session_id="sess-1",
            plan_id=None,
            code_ref="plan:experiment",
            dataset_ref="inline:demo",
            seed=42,
            timeout_seconds=120,
        )
        base.update(over)
        return ExecutionRequest(**base)

    def test_maps_to_sandbox_request_with_defensive_clamps(self):
        from research_vnext.core.execution.sandbox_adapter import to_sandbox_request

        req = self._research_request(
            code="print(1)",
            inputs=[{"name": "data", "text": "1,2,3"}],
            limits={"cpuLimit": 999.0, "memoryLimitMb": 999999},
        )
        sreq = to_sandbox_request(req, attempt=1)
        assert sreq.code == "print(1)"
        assert sreq.seed == 42
        assert sreq.policy.network.value == "deny"
        assert sreq.policy.require_real_isolation is True
        # 防御性再钳制：即使上游漏了策略门，adapter 也不放行超限值
        assert sreq.limits.cpu_limit <= 2.0
        assert sreq.limits.memory_limit_mb <= 2048

    def test_run_id_is_deterministic_per_job_and_attempt(self):
        from research_vnext.core.execution.sandbox_adapter import to_sandbox_request

        req = self._research_request(code="print(1)")
        a = to_sandbox_request(req, attempt=1)
        b = to_sandbox_request(req, attempt=1)
        c = to_sandbox_request(req, attempt=2)
        assert a.run_id == b.run_id
        assert a.run_id != c.run_id


class TestResultMapping:
    def test_manifest_to_execution_result(self):
        from research_vnext.core.execution.sandbox_adapter import result_from_sandbox

        class _Manifest(dict):
            def model_dump(self, mode="json"):
                return dict(self)

        manifest = _Manifest(
            run_id="rv11-job-1-1",
            code_hash="a" * 64,
            input_hash="b" * 64,
            output_hash="c" * 64,
            artifact_hash="d" * 64,
            dependency_lock_hash="e" * 64,
            image="math-arena-sandbox:0.2.0",
            image_digest="sha256:" + "f" * 64,
            seed=42,
            exec_status="pass",
            exit_code=0,
            duration_ms=900,
            backend="container",
            isolation_mode="container",
            isolation_controls_enforced={"network_isolation": True},
            unenforced_reasons={"tmpfs_workdir": "PARTIAL by design"},
            effective_limits={"cpu_limit": 1.0},
            warnings=[],
            truncation={"truncated": False},
            sandbox_version="0.2.0",
            platform="linux (container)",
            runtime_facts={},
        )
        res = result_from_sandbox(
            job_id="job-1",
            attempt=1,
            manifest=manifest,
            stdout=DEMO_STDOUT,
            stderr="",
            argv=["docker", "run"],
            requested_limits={"cpuLimit": 1.0},
            env_allowlist=["PYTHONHASHSEED"],
        )
        assert res.exit_code == 0
        # 不变量：执行器永远不宣布结论成立
        assert res.claim_holds is None
        assert res.metrics["pi_estimate"] == pytest.approx(3.141592)
        assert res.repro["imageDigest"] == "sha256:" + "f" * 64
        assert res.repro["command"] == ["docker", "run"]
        assert res.repro["resourceLimits"]["requested"] == {"cpuLimit": 1.0}
        assert res.figures and res.figures[0]["path"] == "artifacts/figure.svg"

    def test_guard_violation_detected_from_stderr(self):
        from research_vnext.core.execution.sandbox_adapter import detect_guard_violation

        assert detect_guard_violation("GUARD_VIOLATION[BLOCKED_IMPORT]: module 'socket' is blocked")
        assert not detect_guard_violation("plain traceback")


class TestSandboxSourceResolution:
    def test_default_source_points_at_services_sandbox_src(self):
        from research_vnext.core.execution.sandbox_adapter import sandbox_source

        src = sandbox_source()
        assert (src / "sandbox" / "orchestrator.py").is_file()

    def test_executor_importable_module(self):
        from research_vnext.core.execution.sandbox_adapter import SandboxExecutor

        ex = SandboxExecutor()
        assert ex.name == "sandbox"
        assert ex.configured is True
