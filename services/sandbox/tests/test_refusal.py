"""Acceptance condition #5: a caller can refuse to run when isolation is not real.

The default policy is the refusing one. These tests pin that default and prove the
refusal is a first-class outcome -- ``exec_status="refused"`` with all-False controls
-- and not an exception that a caller might swallow into a "pass".
"""

from __future__ import annotations

import pytest

from sandbox.backends import ContainerBackend, LocalBackend, select_backend
from sandbox.cli import app
from sandbox.errors import BackendUnavailableError, IsolationRefusedError
from sandbox.models import IsolationControls, IsolationMode, SandboxPolicy
from sandbox.orchestrator import run_experiment

from .conftest import make_request


def test_default_policy_requires_real_isolation() -> None:
    assert SandboxPolicy().require_real_isolation is True
    assert SandboxPolicy().network.value == "deny"
    assert SandboxPolicy().enforce_module_allowlist is True


def test_local_backend_refused_under_default_policy() -> None:
    with pytest.raises(IsolationRefusedError) as excinfo:
        select_backend("local", policy=SandboxPolicy())
    message = str(excinfo.value)
    assert "no operating-system isolation" in message
    assert "require_real_isolation=False" in message
    missing = excinfo.value.missing_controls
    assert "network_isolation" in missing and "memory_limit" in missing
    assert "non_root_user" in missing


def test_refusal_is_a_result_not_an_exception_and_never_a_pass() -> None:
    req = make_request(
        "print('THIS MUST NOT RUN')\n",
        run_id="refusal",
        backend="local",
        require_real_isolation=True,
        enforce_module_allowlist=False,
    )
    result = run_experiment(req, backend=LocalBackend())
    manifest = result.manifest
    assert manifest.exec_status == "refused"
    assert manifest.exec_status != "pass"
    assert manifest.isolation_mode is IsolationMode.REFUSED
    assert manifest.exit_code is None
    assert result.stdout == "" and result.stderr == ""
    assert "THIS MUST NOT RUN" not in result.stdout
    assert manifest.isolation_controls_enforced == IsolationControls()  # every control False
    assert all("nothing executed" in reason for reason in manifest.unenforced_reasons.values())
    assert any("REFUSED" in warning for warning in manifest.warnings)


def test_auto_backend_refuses_when_no_container_runtime_exists() -> None:
    """With no usable runtime, 'auto' must refuse rather than silently go local."""

    class DeadContainer(ContainerBackend):
        def is_available(self) -> tuple[bool, str | None]:
            return False, "simulated: no docker/podman on this host"

    with pytest.raises(IsolationRefusedError) as excinfo:
        select_backend("auto", policy=SandboxPolicy(), container=DeadContainer())
    assert "no container runtime is available" in str(excinfo.value)


def test_auto_backend_degrades_to_local_only_when_policy_allows() -> None:
    class DeadContainer(ContainerBackend):
        def is_available(self) -> tuple[bool, str | None]:
            return False, "simulated"

    backend = select_backend(
        "auto",
        policy=SandboxPolicy(require_real_isolation=False),
        container=DeadContainer(),
    )
    assert isinstance(backend, LocalBackend)


def test_explicit_container_request_raises_when_unavailable() -> None:
    class DeadContainer(ContainerBackend):
        def is_available(self) -> tuple[bool, str | None]:
            return False, "simulated"

    with pytest.raises(BackendUnavailableError):
        select_backend("container", policy=SandboxPolicy(), container=DeadContainer())


def test_container_backend_is_not_refused_by_the_default_policy() -> None:
    backend = select_backend(
        "container", policy=SandboxPolicy(), container=_FakeContainer()
    )
    assert backend.name == "container"


def test_capability_report_says_what_would_happen_now() -> None:
    from sandbox.capabilities import capability_report

    report = capability_report("local", SandboxPolicy())
    assert report.would_refuse_current_policy is True
    assert report.isolation_mode_if_run_now is IsolationMode.REFUSED
    assert report.refusal_reason and "require_real_isolation=True" in report.refusal_reason


def test_cli_refuses_local_without_the_flag_and_runs_with_it(tmp_path) -> None:
    """The switch is wired end to end, not just present in the model."""
    from typer.testing import CliRunner

    code = tmp_path / "job.py"
    code.write_text("print('CLI_OK')\n", encoding="utf-8")
    runner = CliRunner()

    refused = runner.invoke(app, ["run", str(code), "--backend", "local"])
    assert refused.exit_code == 1, refused.output
    assert "refused" in refused.output
    assert "CLI_OK" not in refused.output

    allowed = runner.invoke(
        app, ["run", str(code), "--backend", "local", "--allow-unisolated"]
    )
    assert allowed.exit_code == 0, allowed.output
    assert "CLI_OK" in allowed.output
    assert "local-unisolated" in allowed.output
    assert "controls NOT enforced" in allowed.output


def test_cli_capabilities_reports_not_enforced_for_local() -> None:
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["capabilities", "--backend", "local"])
    assert result.exit_code == 0, result.output
    assert "NOT enforced" in result.output
    assert "network_isolation" in result.output


def _FakeContainer() -> ContainerBackend:
    """A container backend that claims availability (for selection tests only)."""

    class _Fake(ContainerBackend):
        def is_available(self) -> tuple[bool, str | None]:
            return True, None

    return _Fake()
