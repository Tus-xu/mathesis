"""The honesty tests: the capability report and the manifest may not overclaim.

These are the tests that would have caught the original defect -- a docstring
claiming ``setrlimit`` and a Windows job object that did not exist. They assert the
*structure* of the claims, and (for the local backend) assert the specific
not-enforced verdicts that make the backend honest.
"""

from __future__ import annotations

from sandbox.backends.local import ENFORCED as LOCAL_ENFORCED
from sandbox.capabilities import MANDATE_CONTROLS, all_backends, capability_report, mandate_matrix
from sandbox.constants import UNBOUNDED
from sandbox.models import IsolationControls, IsolationMode, ResourceLimits, SandboxPolicy
from sandbox.orchestrator import run_experiment

from .conftest import make_request

TRIVIAL = "print('honesty')\n"


def test_capability_report_has_no_self_reported_defects() -> None:
    payload = all_backends()
    assert payload["honesty_check"] == [], (
        "every enforced control must name a mechanism and every unenforced control a reason; "
        f"defects: {payload['honesty_check']}"
    )


def test_local_backend_reports_specific_controls_as_not_enforced() -> None:
    policy = SandboxPolicy(require_real_isolation=False)
    report = capability_report("local", policy)
    for control in (
        "non_root_user",
        "network_isolation",
        "memory_limit",
        "cpu_limit",
        "pids_limit",
        "read_only_rootfs",
        "no_host_mount",
        "no_docker_socket",
        "image_by_digest",
    ):
        assert report.controls_enforceable[control] is False, (
            f"local backend must not claim {control}: it has no mechanism for it"
        )
        assert control in report.unenforced_reasons, f"{control} must carry a stated reason"
        assert report.unenforced_reasons[control].strip()

    # With an isolation-requiring policy the report must predict a refusal; with an
    # explicitly relaxed policy it must describe the local-unisolated run instead.
    assert report.isolation_mode_if_run_now is IsolationMode.LOCAL_UNISOLATED
    assert report.would_refuse_current_policy is False
    refusing = capability_report("local", SandboxPolicy())
    assert refusing.isolation_mode_if_run_now is IsolationMode.REFUSED
    assert refusing.would_refuse_current_policy is True


def test_local_backend_only_claims_what_it_can_name() -> None:
    report = capability_report("local", SandboxPolicy(require_real_isolation=False))
    claimed = {name for name, value in report.controls_enforceable.items() if value}
    assert claimed == set(LOCAL_ENFORCED)
    for control in claimed:
        assert report.control_mechanisms[control].strip(), f"{control} needs a named mechanism"


def test_local_backend_manifest_reports_unbounded_limits_and_unisolated_mode() -> None:
    result = run_experiment(
        make_request(TRIVIAL, backend="local", require_real_isolation=False, run_id="honest-local")
    )
    manifest = result.manifest
    assert manifest.exec_status == "pass"
    assert manifest.isolation_mode.value == "local-unisolated"
    assert manifest.effective_limits.memory_limit_mb == UNBOUNDED
    assert manifest.effective_limits.cpu_limit == UNBOUNDED
    assert manifest.effective_limits.pids_limit == UNBOUNDED
    missing = manifest.isolation_controls_enforced.missing()
    assert "network_isolation" in missing
    assert "memory_limit" in missing
    assert manifest.unenforced_reasons["network_isolation"].startswith("NOT enforced")
    assert any("local-unisolated" in warning for warning in manifest.warnings)


def test_capability_report_matrix_never_calls_unenforced_controls_partial() -> None:
    """'partial' must never be used to soften a control that is simply absent."""
    payload = all_backends()
    from sandbox.models import CapabilityReport

    reports = {
        name: CapabilityReport.model_validate(data) for name, data in payload["backends"].items()
    }
    matrix = mandate_matrix(reports)
    for backend, row in matrix.items():
        for control in MANDATE_CONTROLS:
            verdict = row[control]
            assert verdict in {"ENFORCED", "PARTIAL", "NOT enforced"}
            if verdict == "PARTIAL":
                # PARTIAL is only legitimate when the mechanism text discloses why.
                mechanism = reports[backend].control_mechanisms[control]
                assert mechanism.upper().startswith("PARTIAL"), (
                    f"{backend}.{control} is PARTIAL in the matrix but its mechanism text does "
                    "not disclose the partial nature"
                )


def test_manifest_always_carries_every_mandated_field() -> None:
    result = run_experiment(
        make_request(TRIVIAL, backend="local", require_real_isolation=False, run_id="fields")
    )
    manifest = result.manifest.model_dump()
    for field in (
        "code_hash",
        "input_hash",
        "output_hash",
        "image_digest",
        "dependency_lock_hash",
        "seed",
        "python_version",
        "executed_at",
        "duration_ms",
        "exit_code",
        "isolation_mode",
        "isolation_controls_enforced",
    ):
        assert field in manifest, f"manifest is missing mandated field {field!r}"
    assert set(manifest["isolation_controls_enforced"]) == set(IsolationControls.model_fields)


def test_image_digest_is_recorded_for_the_container_path(tmp_path) -> None:
    """Even a refused/aborted container run must not invent a digest."""
    from sandbox.backends.container import ContainerBackend

    backend = ContainerBackend(image="math-arena-sandbox:does-not-exist-local")
    available, reason = backend.is_available()
    if not available:
        import pytest

        pytest.skip(f"container runtime unavailable: {reason}")
    report = backend.capabilities()
    assert report.runtime_facts["image_digest"] is None
    assert report.runtime_facts["image_present_locally"] is False
    assert any("not present locally" in w for w in report.warnings)


def test_requested_limits_are_echoed_but_defaults_do_not_imply_enforcement() -> None:
    limits = ResourceLimits(memory_limit_mb=321, cpu_limit=2.0, pids_limit=11)
    report = capability_report("local", SandboxPolicy(require_real_isolation=False), limits)
    assert report.effective_limits_if_run_now.memory_limit_mb == UNBOUNDED, (
        "a requested limit that the backend cannot enforce must be reported UNBOUNDED"
    )
