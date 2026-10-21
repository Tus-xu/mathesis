"""Isolation backends. One interface, two implementations, zero shared lies.

* :class:`~sandbox.backends.container.ContainerBackend` -- the production path.
  Enforces namespace/cgroup isolation, non-root, read-only rootfs, ``--network=none``
  by default and image-digest pinning. Two controls are deliberately reported as
  NOT enforced (``tmpfs_workdir``, ``disk_limit``) because they are only partially
  true; see ``docs/ISOLATION-MATRIX.md``.
* :class:`~sandbox.backends.local.LocalBackend` -- the developer path. Enforces a
  timeout, an output cap, a stripped environment, a fresh workspace and the module
  guard, and reports everything else as NOT enforced. It never claims otherwise.

Selection is explicit (:func:`select_backend`) and *refuses* rather than degrades:
with ``SandboxPolicy.require_real_isolation`` (the default) the local backend is
never selected silently.
"""

from __future__ import annotations

from ..errors import BackendUnavailableError, IsolationRefusedError
from ..models import IsolationControls, IsolationMode, SandboxPolicy
from .base import BackendRun, IsolationBackend
from .container import ContainerBackend
from .local import LocalBackend

#: Controls a backend must enforce for a run to count as "real isolation".
REAL_ISOLATION_REQUIRED: tuple[str, ...] = (
    "non_root_user",
    "network_isolation",
    "memory_limit",
    "cpu_limit",
    "pids_limit",
    "read_only_rootfs",
    "no_docker_socket",
    "image_by_digest",
    "wall_clock_timeout",
    "output_cap",
)


def select_backend(
    requested: str,
    *,
    policy: SandboxPolicy,
    container: ContainerBackend | None = None,
    local: LocalBackend | None = None,
) -> IsolationBackend:
    """Pick a backend, or raise. Never silently downgrades.

    * ``container`` -- raise :class:`BackendUnavailableError` if unusable.
    * ``local`` -- raise :class:`IsolationRefusedError` when the policy requires
      real isolation (the default), so a caller must *say* it accepts no isolation.
    * ``auto`` -- prefer container; fall back to local only when the policy allows
      unisolated execution, otherwise raise :class:`IsolationRefusedError` naming
      the missing controls.

    Refusal is decided from the backend's own reported control set (never from its
    class name), so the same check protects an explicitly injected backend instance.
    """
    if requested == "local":
        selected_local = local or LocalBackend()
        if policy.require_real_isolation:
            _refuse(selected_local, policy)
        return selected_local

    if requested == "container":
        selected = container or ContainerBackend()
        available, reason = selected.is_available()
        if not available:
            raise BackendUnavailableError(f"container backend unavailable: {reason}")
        return selected

    if requested == "auto":
        selected = container or ContainerBackend()
        available, _reason = selected.is_available()
        if available:
            return selected
        if policy.require_real_isolation:
            raise IsolationRefusedError(
                "refusing to execute: no container runtime is available and "
                "policy.require_real_isolation is True (default), so the only remaining "
                "backend ('local') cannot satisfy the isolation requirement.",
                missing_controls=list(REAL_ISOLATION_REQUIRED),
                backend="local",
            )
        return local or LocalBackend()

    raise BackendUnavailableError(f"unknown backend {requested!r}")


def enforce_policy(backend: IsolationBackend, policy: SandboxPolicy) -> None:
    """Raise :class:`IsolationRefusedError` if ``backend`` cannot satisfy ``policy``.

    Called by the orchestrator on *every* run, including runs whose backend was
    injected directly, so the refusal cannot be bypassed by constructing a backend.
    """
    if policy.require_real_isolation:
        _refuse(backend, policy)


def _refuse(backend: IsolationBackend, policy: SandboxPolicy) -> None:
    """Raise unless ``backend`` really provides the controls the mandate names.

    The check reads the backend's own capability report -- never its class name -- so
    a backend that claims availability but enforces nothing is still refused.
    """
    report = backend.capabilities(policy)
    if report.isolation_mode_if_run_now is IsolationMode.CONTAINER:
        return  # the container backend enforces the namespace/cgroup controls
    missing = [name for name in REAL_ISOLATION_REQUIRED if not report.controls_enforceable.get(name, False)]
    if not missing:
        return
    raise IsolationRefusedError(
        "refusing to execute: backend "
        f"{getattr(backend, 'name', '?')!r} provides no operating-system isolation and "
        "does not enforce the controls required for real isolation, while "
        "policy.require_real_isolation is True (default). Missing: "
        + ", ".join(missing)
        + ". Pass SandboxPolicy(require_real_isolation=False) to accept local-unisolated "
        "execution explicitly, or use the container backend.",
        missing_controls=missing,
        backend=getattr(backend, "name", "unknown"),
    )


def controls_for(backend: IsolationBackend, policy: SandboxPolicy) -> IsolationControls:
    """Convenience: the control set a backend reports for a policy."""
    report = backend.capabilities(policy)
    return IsolationControls(**report.controls_enforceable)


__all__ = [
    "REAL_ISOLATION_REQUIRED",
    "BackendRun",
    "ContainerBackend",
    "IsolationBackend",
    "LocalBackend",
    "controls_for",
    "enforce_policy",
    "select_backend",
]
