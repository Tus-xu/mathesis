"""The one interface both isolation backends implement.

A backend's only job is: given verified payload bytes and limits, run them inside
whatever isolation it can actually provide, and report *truthfully* what that was.
It does not decide policy, it does not build the manifest -- :mod:`sandbox.orchestrator`
does both, from the backend's returned :class:`BackendRun`.

The honesty contract lives in :class:`BackendRun.controls`: a backend may only set
a control ``True`` when it can name the mechanism, and every ``False`` must come
with a reason in ``unenforced_reasons``. The capability report and the manifest are
both derived from those two fields, so a false claim here is the single place a lie
could enter -- which is why every backend is tested for it.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from ..models import (
    Artifact,
    CapabilityReport,
    EffectiveLimits,
    ExecutionRequest,
    IsolationControls,
    IsolationMode,
    OutputTruncation,
)


@dataclass
class BackendRun:
    """Raw outcome of one backend invocation, before the manifest is assembled."""

    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    controls: IsolationControls
    unenforced_reasons: dict[str, str]
    effective_limits: EffectiveLimits
    isolation_mode: IsolationMode
    truncation: OutputTruncation = field(default_factory=OutputTruncation)
    artifacts: list[Artifact] = field(default_factory=list)
    backend_name: str = ""
    image: str | None = None
    image_digest: str | None = None
    python_version: str = ""
    platform: str = ""
    argv: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    runtime_facts: dict[str, Any] = field(default_factory=dict)
    #: Set when the outcome is not derivable from the exit code -- specifically when
    #: the run never started (missing image, launch failure). Without this the
    #: orchestrator would report a pre-execution abort as ``fail``, which reads like
    #: "your code failed" when in truth nothing ran.
    exec_status_override: str | None = None

    @property
    def exec_status(self) -> str:
        if self.exec_status_override:
            return self.exec_status_override
        if self.timed_out:
            return "timeout"
        return "pass" if self.exit_code == 0 else "fail"


class IsolationBackend(abc.ABC):
    """Isolation backend contract.

    ``capabilities()`` must be *side-effect free enough* to call from a health
    endpoint: it may probe for a container runtime but must not start workloads.
    """

    name: str = "abstract"

    @abc.abstractmethod
    def is_available(self) -> tuple[bool, str | None]:
        """Return ``(available, reason_if_not)``."""

    @abc.abstractmethod
    def capabilities(self, policy) -> CapabilityReport:  # noqa: ANN001 - SandboxPolicy
        """Machine-readable answer to "what isolation would be in force right now?"."""

    @abc.abstractmethod
    def run(self, req: ExecutionRequest, payload) -> BackendRun:  # noqa: ANN001 - ResolvedPayload
        """Execute and return a truthful :class:`BackendRun`. Must not raise for
        user-code failure -- only for backend failure, which the orchestrator turns
        into ``isolation_mode == ERROR`` (fail closed)."""

    # -- shared helpers -----------------------------------------------------------------

    @staticmethod
    def _reasons_for_false(controls: IsolationControls, mechanisms: dict[str, str]) -> dict[str, str]:
        """Every unenforced control must carry a reason that names the missing mechanism."""
        return {
            name: mechanisms.get(name, "no mechanism available in this backend/environment")
            for name, value in controls.model_dump().items()
            if not value
        }
