"""Capability report: the machine-readable answer to "what is actually enforced?".

The failure this package exists to fix is a harness that reports safety it does not
have. So the capability report has one rule, enforced by tests:

    **Every ``True`` in ``controls_enforceable`` must name a mechanism, and every
    ``False`` must name a reason. Nothing is reported as enforced on the strength
    of a comment.**

:func:`capability_report` with ``backend="all"`` reports both backends side by side,
which is what an operator should read before trusting any run. ``sandbox capabilities``
(CLI) prints it; ``sandbox capabilities --json`` emits it for machines.
"""

from __future__ import annotations

import sys
from typing import Any

from . import __version__
from .backends import ContainerBackend, LocalBackend
from .models import CapabilityReport, IsolationControls, ResourceLimits, SandboxPolicy

#: Controls that make a run "really isolated" -- the ones the user mandate names.
MANDATE_CONTROLS: tuple[str, ...] = (
    "non_root_user",
    "cpu_limit",
    "memory_limit",
    "wall_clock_timeout",
    "disk_limit",
    "output_cap",
    "network_isolation",
    "no_host_mount",
    "no_docker_socket",
    "image_by_digest",
)


def capability_report(
    backend: str = "auto",
    policy: SandboxPolicy | None = None,
    limits: ResourceLimits | None = None,
) -> CapabilityReport:
    """Report on one backend (``container`` / ``local`` / ``auto``)."""
    policy = policy or SandboxPolicy()
    limits = limits or ResourceLimits()
    container = ContainerBackend()
    local = LocalBackend()

    if backend == "container":
        return container.capabilities(policy, limits)
    if backend == "local":
        return local.capabilities(policy)
    # auto: report the container (the production target) but state the fallback.
    report = container.capabilities(policy, limits)
    available, _reason = container.is_available()
    local_report = local.capabilities(policy)
    report.backend_requested = "auto"
    report.runtime_facts["fallback_backend"] = "local"
    report.runtime_facts["fallback_controls_enforceable"] = local_report.controls_enforceable
    report.runtime_facts["fallback_isolation_mode"] = local_report.isolation_mode_if_run_now.value
    if not available:
        report.warnings.append(
            "container runtime unavailable: 'auto' would REFUSE to run under the default policy "
            "(require_real_isolation=True) rather than silently degrade to the local backend."
        )
    return report


def all_backends(
    policy: SandboxPolicy | None = None, limits: ResourceLimits | None = None
) -> dict[str, Any]:
    """Both backends, side by side, plus the resolved selection for the policy."""
    policy = policy or SandboxPolicy()
    limits = limits or ResourceLimits()
    container = ContainerBackend()
    container_available, container_reason = container.is_available()
    reports = {
        "container": container.capabilities(policy, limits),
        "local": LocalBackend().capabilities(policy),
    }
    selection = _resolve_selection(policy, container_available, container_reason)
    return {
        "generated_at": reports["container"].generated_at,
        "sandbox_version": __version__,
        "host": {"platform": sys.platform, "python": sys.version.split()[0]},
        "policy": policy.model_dump(),
        "limits_requested": limits.model_dump(),
        "selection_for_policy": selection,
        "backends": {name: report.model_dump() for name, report in reports.items()},
        "honesty_check": honesty_findings(reports),
    }


def _resolve_selection(
    policy: SandboxPolicy, container_available: bool, container_reason: str | None
) -> dict[str, Any]:
    if container_available:
        return {
            "selected": "container",
            "isolation_mode": "container",
            "would_execute": True,
            "reason": "container runtime available; policy satisfied",
        }
    if policy.require_real_isolation:
        return {
            "selected": None,
            "isolation_mode": "refused",
            "would_execute": False,
            "reason": (
                f"container runtime unavailable ({container_reason}); policy.require_real_isolation"
                "=True so the local backend is refused. No code would execute."
            ),
        }
    return {
        "selected": "local",
        "isolation_mode": "local-unisolated",
        "would_execute": True,
        "reason": (
            "container runtime unavailable and policy.require_real_isolation=False: the run would "
            "proceed with NO operating-system isolation."
        ),
    }


def honesty_findings(reports: dict[str, CapabilityReport]) -> list[dict[str, str]]:
    """Assert the invariants the capability report claims about itself.

    Any finding here is a self-reported defect, not a warning to be ignored.
    """
    findings: list[dict[str, str]] = []
    for name, report in reports.items():
        for control, enforced in report.controls_enforceable.items():
            mechanism = report.control_mechanisms.get(control, "")
            if enforced and not mechanism.strip():
                findings.append(
                    {
                        "backend": name,
                        "control": control,
                        "severity": "DEFECT",
                        "detail": "reported enforced but names no mechanism",
                    }
                )
            if not enforced and control not in report.unenforced_reasons:
                findings.append(
                    {
                        "backend": name,
                        "control": control,
                        "severity": "DEFECT",
                        "detail": "reported unenforced without a stated reason",
                    }
                )
        if report.backend_available and report.backend_selected is None:
            findings.append(
                {
                    "backend": name,
                    "control": "-",
                    "severity": "DEFECT",
                    "detail": "backend available but none selected",
                }
            )
    return findings


def mandate_matrix(reports: dict[str, CapabilityReport]) -> dict[str, dict[str, str]]:
    """Compress the mandate's control list into enforced / partial / NOT enforced.

    ``partial`` is derived from the mechanism text, which is where a backend is
    required to disclose a partial implementation (for example "PARTIAL: the /work
    workspace is a bind mount, not tmpfs"). Anything not enforced is reported as
    ``NOT enforced`` -- the word "partial" is never substituted for it.
    """
    matrix: dict[str, dict[str, str]] = {}
    for name, report in reports.items():
        row: dict[str, str] = {}
        for control in MANDATE_CONTROLS:
            enforced = report.controls_enforceable.get(control, False)
            mechanism = report.control_mechanisms.get(control, "")
            if enforced and mechanism.upper().startswith("PARTIAL"):
                row[control] = "PARTIAL"
            elif enforced:
                row[control] = "ENFORCED"
            else:
                row[control] = "NOT enforced"
        matrix[name] = row
    return matrix


def required_controls_present(controls: IsolationControls) -> tuple[bool, list[str]]:
    missing = [name for name in MANDATE_CONTROLS if not getattr(controls, name)]
    return (not missing), missing


__all__ = [
    "MANDATE_CONTROLS",
    "all_backends",
    "capability_report",
    "honesty_findings",
    "mandate_matrix",
    "required_controls_present",
]
