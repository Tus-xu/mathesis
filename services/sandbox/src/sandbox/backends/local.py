"""Local/process backend -- for developer machines with no container runtime.

THIS BACKEND PROVIDES NO OPERATING-SYSTEM ISOLATION. It runs the submitted code as
a child process of the caller, on the host, with the caller's kernel, filesystem and
network namespace. It is honest about that:

* enforced: wall-clock timeout (kills the process tree), output cap, a stripped
  environment that contains no host variables, a per-run workspace directory,
  the static/runtime module guard, and (as a partial measure) a path guard that
  rejects ``open()`` on absolute host paths and workspace escapes.
* NOT enforced: non-root user, network isolation, memory limit, CPU limit, PID
  limit, disk quota, read-only rootfs, tmpfs. Every one of those is reported
  ``False`` in ``isolation_controls_enforced`` with a reason.

Consequences a caller must accept before using it (and which the capability report
repeats):

* the code can reach the network, so ``socket.create_connection`` succeeding here
  means the backend is *not* a network boundary. Verified on this host: see
  ``docs/ISOLATION-MATRIX.md`` and the ``local-unisolated`` evidence section.
* the code can allocate unbounded memory and spawn unlimited processes. A fork
  bomb on Linux would affect the host; on Windows the timeout is the only stop.
* the code runs as the invoking user, so it can read anything that user can read --
  the stripped environment removes *environment-variable* secrets but not files.

For those reasons :class:`sandbox.models.SandboxPolicy` defaults
``require_real_isolation=True`` and the orchestrator refuses to use this backend
unless the caller explicitly relaxes the policy. Choosing it always emits a loud
warning and marks the manifest ``isolation_mode: "local-unisolated"``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from ..constants import UNBOUNDED
from ..models import (
    CapabilityReport,
    EffectiveLimits,
    ExecutionRequest,
    IsolationControls,
    IsolationMode,
    ResourceLimits,
    SandboxPolicy,
    utc_now_iso,
)
from ..processutil import kill_tree, popen_kwargs_new_group
from ..workspace import RunWorkspace
from .base import BackendRun, IsolationBackend

#: Environment handed to the child. Deliberately NOT built from ``os.environ``:
#: inheriting the parent environment is how ``MODEL_API_KEY``-style secrets leak
#: into executed code. ``PYTHONHASHSEED`` is pinned for reproducibility.
LOCAL_ENV: dict[str, str] = {
    "PATH": "",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
}

#: Controls this backend can name a mechanism for.
ENFORCED: tuple[str, ...] = (
    "wall_clock_timeout",
    "output_cap",
    "no_host_env_secrets",
    "module_allowlist",
)

MECHANISMS_WHEN_ENFORCED = {
    "wall_clock_timeout": (
        "subprocess.Popen + polling deadline; kill_tree() (taskkill /F /T on Windows, "
        "killpg on POSIX) terminates the process and its descendants"
    ),
    "output_cap": (
        "child writes to files; the host reads at most limits.output_limit_bytes per "
        "stream and reports the true file size in the manifest"
    ),
    "no_host_env_secrets": (
        "child environment is a fixed literal dict (LOCAL_ENV); os.environ is never "
        "inherited, so no .env / API key variable reaches the code"
    ),
    "module_allowlist": (
        "runner/runner.py static AST guard + builtins.__import__ hook; verified by "
        "tests/test_module_allowlist.py"
    ),
}

#: Why each remaining control is absent. These strings end up verbatim in the
#: manifest and the capability report.
REASONS = {
    "non_root_user": (
        "NOT enforced: code runs as the invoking host user ("
        "{} on this machine); there is no uid namespace outside a container"
    ),
    "read_only_rootfs": "NOT enforced: no mount namespace; the host rootfs is the code's rootfs",
    "tmpfs_workdir": (
        "PARTIAL: the run workspace is a fresh host directory under the temp dir, which "
        "removes host secrets by construction but is neither tmpfs nor memory-backed"
    ),
    "network_isolation": (
        "NOT enforced: no network namespace. Verified counter-example on this host: with "
        "the module guard disabled, socket.create_connection('example.com', 80) SUCCEEDS "
        "from this backend. Treat every local run as network-capable."
    ),
    "memory_limit": "NOT enforced: no cgroup; a memory hog can exhaust host RAM",
    "cpu_limit": "NOT enforced: no cgroup cpu.max / no CPU affinity applied",
    "pids_limit": "NOT enforced: no pid namespace; a fork bomb can exhaust host PIDs",
    "disk_limit": (
        "PARTIAL: workspace growth is measured after the fact and reported, but nothing "
        "stops the write mid-flight"
    ),
    "no_host_mount": (
        "NOT enforced: the code runs on the host, so every host path the user can read is "
        "readable. The module guard removes builtins.open, which is friction, not a boundary"
    ),
    "no_docker_socket": (
        "NOT enforced as a control: nothing prevents the code from opening the socket path "
        "if the invoking user can; the module guard blocks import socket/os which raises the "
        "cost but is not a boundary"
    ),
    "image_by_digest": "NOT enforced: there is no image; the host interpreter is used as-is",
}


class LocalBackend(IsolationBackend):
    name = "local"

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = python_executable or sys.executable or "python"

    # -- availability -------------------------------------------------------------------

    def is_available(self) -> tuple[bool, str | None]:
        exe = shutil.which(self.python_executable) or (
            self.python_executable if Path(self.python_executable).is_file() else None
        )
        if not exe:
            return False, f"no usable python interpreter found at {self.python_executable!r}"
        return True, None

    # -- capabilities -------------------------------------------------------------------

    def _controls(self, policy: SandboxPolicy | None = None) -> IsolationControls:
        data = {name: False for name in IsolationControls.model_fields}
        for name in ENFORCED:
            data[name] = True
        if policy is not None and not policy.enforce_module_allowlist:
            # The guard is a policy, not a backend property: if the caller turned it
            # off, the manifest must not claim it ran.
            data["module_allowlist"] = False
        return IsolationControls(**data)

    def mechanisms(self) -> dict[str, str]:
        return dict(REASONS) | MECHANISMS_WHEN_ENFORCED

    def _reasons(self, policy: SandboxPolicy | None = None) -> dict[str, str]:
        mechanisms = dict(self.mechanisms())
        if policy is not None and not policy.enforce_module_allowlist:
            mechanisms["module_allowlist"] = (
                "DISABLED BY POLICY: policy.enforce_module_allowlist=False, so neither the "
                "static AST guard nor the import hook ran for this request"
            )
        return {
            name: mechanisms[name]
            for name, value in self._controls(policy).model_dump().items()
            if not value
        }

    def capabilities(self, policy: SandboxPolicy | None = None) -> CapabilityReport:
        policy = policy or SandboxPolicy()
        available, reason = self.is_available()
        controls = self._controls(policy)
        mechanisms = self.mechanisms()
        if not policy.enforce_module_allowlist:
            mechanisms = dict(mechanisms) | {
                "module_allowlist": "DISABLED BY POLICY (policy.enforce_module_allowlist=False)"
            }
        missing = [name for name, value in controls.model_dump().items() if not value]
        refuse = policy.require_real_isolation
        return CapabilityReport(
            generated_at=utc_now_iso(),
            sandbox_version=_version(),
            backend_requested="local",
            backend_selected="local" if available else None,
            backend_available=available,
            backend_unavailable_reason=reason,
            isolation_mode_if_run_now=(
                IsolationMode.REFUSED
                if refuse
                else (IsolationMode.LOCAL_UNISOLATED if available else IsolationMode.ERROR)
            ),
            controls_enforceable=controls.model_dump(),
            control_mechanisms=mechanisms,
            unenforced_reasons=self._reasons(policy),
            effective_limits_if_run_now=_effective(),
            policy=policy,
            would_refuse_current_policy=refuse,
            refusal_reason=(
                "policy.require_real_isolation=True: the local backend provides no "
                f"operating-system isolation and enforces only {len(ENFORCED)}/"
                f"{len(IsolationControls.model_fields)} controls; missing: " + ", ".join(missing)
            )
            if refuse
            else None,
            runtime_facts={
                "python_executable": self.python_executable,
                "host_platform": sys.platform,
                "child_env_keys": sorted(LOCAL_ENV),
                "env_inherited_from_parent": False,
                "network_boundary": False,
                "os_isolation": "NONE",
            },
            warnings=[
                "LOCAL BACKEND: NO operating-system isolation. Code runs on this host as "
                f"{_host_user()}. Network, memory, CPU, PID and filesystem are NOT bounded. "
                "Never use for untrusted or LLM-generated code; never use in production.",
            ],
        )

    # -- execution ----------------------------------------------------------------------

    def run(self, req: ExecutionRequest, payload) -> BackendRun:  # noqa: ANN001
        limits: ResourceLimits = req.limits
        ws = RunWorkspace.create(
            base=req_work_root(req), run_id=req.run_id, disk_limit_mb=limits.disk_limit_mb
        )
        runner = _materialise_runner(ws.python_dir)
        payload.materialise(ws.root)

        params_json = _params_json(req)
        argv = [
            self.python_executable,
            str(runner),
            "--code",
            str(ws.root / "experiment.py"),
            "--seed",
            str(req.seed),
            "--guard",
            "strict-math" if req.policy.enforce_module_allowlist else "none",
            "--stdout",
            str(ws.stdout_path()),
            "--stderr",
            str(ws.stderr_path()),
            "--meta",
            str(ws.meta_path()),
            "--params-json",
            params_json,
            "--artifacts-dir",
            str(ws.artifacts_dir),
        ]

        started = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        warnings = [
            "LOCAL BACKEND SELECTED: isolation_mode=local-unisolated. Network, memory, CPU, "
            "PID, disk and filesystem isolation are NOT enforced for this run.",
        ]
        spawn_error: str | None = None
        try:
            with open(os.devnull, "wb") as devnull:  # noqa: PTH123
                proc = subprocess.Popen(  # noqa: S603
                    argv,
                    cwd=str(ws.python_dir),
                    env=LOCAL_ENV,
                    stdin=subprocess.DEVNULL,
                    stdout=devnull,
                    stderr=devnull,
                    **popen_kwargs_new_group(),
                )
            deadline = started + limits.wall_time_ms / 1000.0
            while True:
                exit_code = proc.poll()
                if exit_code is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    kill_tree(proc)
                    exit_code = proc.poll()
                    break
                time.sleep(0.02)
        except Exception as exc:  # noqa: BLE001 - a spawn failure is a backend error
            spawn_error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.monotonic() - started) * 1000)

        stdout, stderr, truncation, cap_by = ws.read_capture(limits.output_limit_bytes)
        truncation.cap_enforced_by = cap_by
        artifacts, artifacts_bytes = ws.collect_artifacts()
        workspace_bytes = ws.workspace_bytes()

        if workspace_bytes > ws.max_total_bytes:
            warnings.append(
                f"disk limit EXCEEDED after the fact: workspace holds {workspace_bytes} bytes "
                f"vs declared {limits.disk_limit_mb} MiB. Nothing stopped the write; the local "
                "backend only measures."
            )
        if spawn_error:
            warnings.append(f"process spawn failed: {spawn_error}")

        os_text = _os_release_text(ws)
        ws.cleanup(keep=False)

        controls = self._controls(req.policy)
        return BackendRun(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            controls=controls,
            unenforced_reasons=self._reasons(req.policy),
            effective_limits=_effective(limits),
            isolation_mode=IsolationMode.LOCAL_UNISOLATED,
            truncation=truncation,
            artifacts=artifacts,
            backend_name=self.name,
            image=None,
            image_digest=None,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            platform=sys.platform,
            argv=_redact_argv(argv),
            warnings=warnings,
            runtime_facts={
                "workspace_strategy": "fresh host temp dir, deleted after the run",
                "workspace_bytes_before_cleanup": workspace_bytes,
                "artifacts_bytes": artifacts_bytes,
                "duration_ms": duration_ms,
                "child_env_keys": sorted(LOCAL_ENV),
                "network_boundary": False,
                "os_isolation": "NONE",
                "kernel": sys.platform,
                "os_release_first_line": os_text,
                "host_user": _host_user(),
                "disabled_controls": sorted(controls.missing()),
            },
        )


# --------------------------------------------------------------------------------------


def _version() -> str:
    from .. import __version__

    return __version__


def _effective(limits: ResourceLimits | None = None) -> EffectiveLimits:
    """The local backend bounds exactly two dimensions; the rest are UNBOUNDED."""
    return EffectiveLimits(
        cpu_limit=UNBOUNDED,
        memory_limit_mb=UNBOUNDED,
        pids_limit=UNBOUNDED,
        disk_limit_mb=(f"measured-only (declared {limits.disk_limit_mb} MiB)" if limits else "measured-only"),
        wall_time_ms=limits.wall_time_ms if limits else UNBOUNDED,
        output_limit_bytes=limits.output_limit_bytes if limits else UNBOUNDED,
        network="UNBOUNDED (no network namespace; egress is possible)",
    )


def _materialise_runner(target_dir: Path) -> Path:
    """Copy the packaged runner into the workspace so the child has a real file path."""
    source = resources.files("sandbox._data").joinpath("runner.py")
    destination = target_dir / "runner.py"
    destination.write_bytes(source.read_bytes())
    return destination


def _params_json(req: ExecutionRequest) -> str:
    import json

    return json.dumps(req.params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def req_work_root(req: ExecutionRequest) -> Path | None:
    """Allow tests/operators to pin the workspace root; otherwise use the temp dir."""
    pinned = os.environ.get("SANDBOX_WORK_ROOT", "").strip()
    return Path(pinned) if pinned else None


def _redact_argv(argv: list[str]) -> list[str]:
    """The runner receives paths only -- no secrets -- but redact defensively."""
    return [part if len(part) < 512 else part[:64] + "...<truncated>" for part in argv]


def _host_user() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def _os_release_text(ws: RunWorkspace) -> str:
    """First line of the host OS release, for the manifest's runtime facts.

    Recorded so a reviewer can tell whether two "identical" manifests were produced on
    the same host build. It describes the *host*, which is exactly why the local
    backend is the unisolated one.
    """
    del ws
    if sys.platform == "win32":
        if hasattr(sys, "getwindowsversion"):
            return f"Windows build {sys.getwindowsversion().build}"
        return "Windows (build unknown)"
    try:
        return Path("/etc/os-release").read_text(encoding="utf-8").splitlines()[0]  # noqa: PTH123
    except OSError:
        return "unknown"


__all__ = ["ENFORCED", "LOCAL_ENV", "MECHANISMS_WHEN_ENFORCED", "REASONS", "LocalBackend"]
