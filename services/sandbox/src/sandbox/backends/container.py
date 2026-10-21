"""Container isolation backend (Docker/Podman CLI).

This is the production path. The exact ``docker run`` contract it builds, stated
here so a reviewer can diff intent against code:

============================  ==================================================
control                       mechanism
============================  ==================================================
non-root user                 ``--user 10001:10001`` plus the image's own ``USER``
                              directive (Dockerfile.sandbox). A caller-supplied
                              image without USER cannot silently run as root
read-only rootfs              ``--read-only``
network isolation             ``--network=none`` when ``policy.network == DENY``
memory limit                  ``--memory <n>m --memory-swap <n>m`` (cgroup)
CPU limit                     ``--cpus <x>`` (cgroup cpu.max)
PID limit                     ``--pids-limit <n>`` (cgroup pids.max; fork bombs)
disk limit                    PARTIAL -- ``--tmpfs /tmp:...,size=<n>m`` bounds
                              scratch; the /work bind mount has NO quota, so its
                              growth is measured after the run and reported
tmpfs workdir                 PARTIAL -- /tmp is tmpfs; /work is a bind mount to a
                              fresh host temp dir. Reported ``False`` on purpose so
                              nobody can claim the whole workdir is memory-backed
output cap                    the runner writes stdout/stderr to files; the host
                              reads at most ``limits.output_limit_bytes`` per stream
                              and reports the true byte counts (so truncation is
                              observable, not silent)
host mounts                   exactly one bind: the per-run workspace -> /work.
                              ``assert_argv_safe`` rejects any other ``-v``/``--mount``
docker socket                 never added; ``assert_argv_safe`` rejects argv that
                              would expose any FORBIDDEN_HOST_MOUNTS path
env secrets                   only a fixed literal key set via ``-e``; no
                              ``--env-file`` and no inherited host environment
wall-clock timeout            host deadline + ``docker stop -t 1`` then ``docker
                              kill`` then ``docker rm -f``; the container's PID 1 is
                              the runner, so the workload dies with the container
image by digest               the reference is resolved to its ``sha256:`` image ID
                              before launch and recorded in the manifest

HONESTY NOTES -- the parts most likely to be overstated:

1. ``--network=none`` is enforced by the kernel, and is verified by a real test
   (``tests/test_security.py::test_container_network_denied``), not asserted here.
2. ``no_host_mount`` means "no host path other than the per-run workspace is
   visible". A stricter reading would call it PARTIAL; the manifest says exactly
   which bind exists (``runtime_facts.bind_mounts``).
3. Unlimited-write protection on the workspace bind does not exist. A payload that
   fills the host disk will fill the host disk. Reported as ``disk_limit: False``.
4. A rootful Docker daemon means the daemon (not the workload) is root. Container
   escape via daemon bugs is out of scope for this package; it is listed in
   docs/RUNBOOK.md under KNOWN GAPS rather than quietly ignored.
5. The image must already be present locally. This backend never pulls implicitly:
   a pull would make ``image_by_digest`` a statement about a moving tag.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from ..constants import (
    CONTAINER_WORKDIR,
    DEFAULT_SANDBOX_IMAGE,
    FORBIDDEN_HOST_MOUNTS,
    SANDBOX_GID,
    SANDBOX_UID,
)
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
from ..processutil import kill_tree
from ..workspace import RunWorkspace
from .base import BackendRun, IsolationBackend
from .local import _version

MECHANISMS = {
    "non_root_user": f"docker run --user {SANDBOX_UID}:{SANDBOX_GID} + image USER directive",
    "read_only_rootfs": "docker run --read-only",
    "tmpfs_workdir": (
        "PARTIAL: --tmpfs /tmp:rw,noexec,nosuid,size=<disk_limit>m is memory-backed; "
        "the /work workspace is a bind mount to a fresh host temp dir, so the workdir as a "
        "whole is NOT tmpfs -- reported unenforced deliberately"
    ),
    "network_isolation": "docker run --network=none (policy.network=deny); kernel-enforced",
    "memory_limit": "docker run --memory <n>m --memory-swap <n>m (cgroup memory.max)",
    "cpu_limit": "docker run --cpus <x> (cgroup cpu.max)",
    "pids_limit": "docker run --pids-limit <n> (cgroup pids.max; caps fork bombs)",
    "disk_limit": (
        "PARTIAL: --tmpfs /tmp size=<n>m bounds scratch; the bind-mounted workspace has no "
        "filesystem quota, so growth there is measured after the run and reported, never "
        "prevented -- reported unenforced deliberately"
    ),
    "wall_clock_timeout": "host deadline + docker stop -t 1 + docker kill + docker rm -f",
    "output_cap": (
        "runner writes stdout/stderr to files; the host reads at most "
        "limits.output_limit_bytes per stream and records the true byte counts"
    ),
    "no_host_mount": (
        "exactly one bind (per-run workspace -> /work); assert_argv_safe() rejects any other "
        "-v/--mount token before launch"
    ),
    "no_docker_socket": (
        "the socket is never added to argv; assert_argv_safe() rejects argv containing any "
        "FORBIDDEN_HOST_MOUNTS path"
    ),
    "no_host_env_secrets": (
        "only a fixed literal key set is passed with -e; no --env-file, no inherited host env"
    ),
    "image_by_digest": (
        "the image reference is resolved to its sha256 image ID (docker image inspect) before "
        "launch and that ID is recorded in the manifest; the backend never pulls implicitly"
    ),
    "module_allowlist": "runner/runner.py AST guard + import hook inside the container",
}

#: Fixed, literal environment passed with ``-e``. Never derived from ``os.environ``.
CONTAINER_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONHASHSEED": "0",
    "HOME": CONTAINER_WORKDIR,
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}

#: Where the packaged runner is placed inside the workspace (so /work/python/runner.py
#: exists in-container without any shell escaping).
RUNNER_REL = "python/runner.py"


def build_inline_script(
    *,
    workspace: str,
    seed: int,
    guard: str,
    code_path: str = "experiment.py",
    params_literal: str | None = None,
    out_dir: str = "out",
    artifacts_dir: str = "artifacts",
) -> str:
    """The exact ``sh -c`` script executed inside the container.

    Written with ``shlex.quote`` for every interpolated value, and every interpolated
    value is either an int or a path/JSON produced by this package -- never raw caller
    text. This is what lets the whole workload be launched as argv with no shell
    heredocs, no entrypoint file and no in-container file writes outside /work.
    """
    import shlex

    params = params_literal if params_literal is not None else "{}"
    return (
        f'exec python3 {shlex.quote(workspace + "/" + RUNNER_REL)}'
        f" --code {shlex.quote(workspace + '/' + code_path)}"
        f" --seed {int(seed)}"
        f" --guard {shlex.quote(guard)}"
        f" --stdout {shlex.quote(workspace + '/' + out_dir + '/stdout.bin')}"
        f" --stderr {shlex.quote(workspace + '/' + out_dir + '/stderr.bin')}"
        f" --meta {shlex.quote(workspace + '/' + out_dir + '/meta.json')}"
        f" --params-json {shlex.quote(params)}"
        f" --artifacts-dir {shlex.quote(workspace + '/' + artifacts_dir)}"
    )


class ContainerBackend(IsolationBackend):
    """Docker/Podman CLI-based isolation backend."""

    name = "container"

    def __init__(self, runtime: str | None = None, image: str | None = None) -> None:
        self.runtime = runtime or _detect_runtime() or "docker"
        self.image = image or DEFAULT_SANDBOX_IMAGE

    # -- availability -------------------------------------------------------------------

    def is_available(self) -> tuple[bool, str | None]:
        if not shutil.which(self.runtime):
            return False, f"container runtime {self.runtime!r} not found on PATH"
        try:
            probe = subprocess.run(  # noqa: S603
                [self.runtime, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"{self.runtime} probe failed: {type(exc).__name__}: {exc}"
        if probe.returncode != 0:
            return False, (
                f"{self.runtime} daemon not reachable (exit {probe.returncode}): "
                f"{(probe.stderr or probe.stdout).strip()[:200]}"
            )
        return True, None

    def server_version(self) -> str:
        try:
            probe = subprocess.run(  # noqa: S603
                [self.runtime, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
            return probe.stdout.strip() or "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    def resolve_image_digest(self, image: str) -> str | None:
        """Resolve ``image`` to an immutable ``sha256:`` image ID, or ``None``.

        Local inspection only. A ``docker pull`` here would make the recorded
        "digest" a statement about a tag at pull time, which is exactly the kind of
        moving-target claim this package exists to remove.
        """
        try:
            probe = subprocess.run(  # noqa: S603
                [self.runtime, "image", "inspect", "--format", "{{.Id}}", image],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except Exception:  # noqa: BLE001
            return None
        digest = probe.stdout.strip()
        return digest if digest.startswith("sha256:") else None

    def image_python_version(self, image: str) -> str | None:
        try:
            probe = subprocess.run(  # noqa: S603
                [self.runtime, "run", "--rm", "--network", "none", "--entrypoint", "python3", image, "-c",
                 "import sys;print(sys.version.split()[0])"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            version = probe.stdout.strip()
            return version or None
        except Exception:  # noqa: BLE001
            return None

    # -- capabilities -------------------------------------------------------------------

    def _controls(self, policy: SandboxPolicy, limits: ResourceLimits) -> IsolationControls:  # noqa: ARG002
        return IsolationControls(
            non_root_user=True,
            read_only_rootfs=True,
            tmpfs_workdir=False,  # PARTIAL by design: /tmp only, not /work
            network_isolation=policy.network.value == "deny",
            memory_limit=True,
            cpu_limit=True,
            pids_limit=True,
            disk_limit=False,  # PARTIAL by design: no quota on the workspace bind
            wall_clock_timeout=True,
            output_cap=True,
            no_host_mount=True,
            no_docker_socket=True,
            no_host_env_secrets=True,
            image_by_digest=True,
            module_allowlist=policy.enforce_module_allowlist,
        )

    def capabilities(
        self,
        policy: SandboxPolicy | None = None,
        limits: ResourceLimits | None = None,
    ) -> CapabilityReport:
        policy = policy or SandboxPolicy()
        limits = limits or ResourceLimits()
        available, reason = self.is_available()
        controls = self._controls(policy, limits)
        missing = controls.missing()
        digest = self.resolve_image_digest(self.image) if available else None
        warnings = [
            "PARTIAL controls on this backend, by design and not by omission: "
            "tmpfs_workdir=False (/work is a bind mount, not tmpfs) and disk_limit=False "
            "(no filesystem quota on the workspace bind; growth is measured, not prevented).",
        ]
        if policy.network.value != "deny":
            warnings.append(
                "policy.network=allow: --network=none will NOT be applied and "
                "network_isolation is reported as unenforced for the run."
            )
        if available and digest is None:
            warnings.append(
                f"image {self.image!r} is not present locally; a run would be refused because "
                "image_by_digest could not be honoured. Build Dockerfile.sandbox or pre-pull."
            )
        if not available and reason:
            warnings.append(f"container backend unavailable: {reason}")
        return CapabilityReport(
            generated_at=utc_now_iso(),
            sandbox_version=_version(),
            backend_requested="container",
            backend_selected="container" if available else None,
            backend_available=available,
            backend_unavailable_reason=reason,
            isolation_mode_if_run_now=IsolationMode.CONTAINER if available else IsolationMode.ERROR,
            controls_enforceable=controls.model_dump(),
            control_mechanisms=MECHANISMS,
            unenforced_reasons={name: MECHANISMS[name] for name in missing},
            effective_limits_if_run_now=_effective(limits, policy),
            policy=policy,
            would_refuse_current_policy=False,
            refusal_reason=None,
            runtime_facts={
                "runtime": self.runtime,
                "runtime_server_version": self.server_version() if available else None,
                "image": self.image,
                "image_digest": digest,
                "image_present_locally": digest is not None,
                "workdir_in_container": CONTAINER_WORKDIR,
                "forbidden_mounts_asserted": list(FORBIDDEN_HOST_MOUNTS),
                "container_env_keys": sorted(CONTAINER_ENV),
                "os_isolation": "container namespaces + cgroups",
                "implicit_pull": False,
            },
            warnings=warnings,
        )

    # -- argv construction --------------------------------------------------------------

    def build_argv(
        self,
        req: ExecutionRequest,
        ws: RunWorkspace,
        container_name: str,
        *,
        image: str,
    ) -> list[str]:
        limits = req.limits
        argv: list[str] = [
            self.runtime,
            "run",
            "--rm",
            "--name",
            container_name,
            "--user",
            f"{SANDBOX_UID}:{SANDBOX_GID}",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={limits.disk_limit_mb}m",
            "--workdir",
            CONTAINER_WORKDIR,
            "--stop-timeout",
            "1",
            "--memory",
            f"{limits.memory_limit_mb}m",
            "--memory-swap",
            f"{limits.memory_limit_mb}m",
            "--cpus",
            str(limits.cpu_limit),
            "--pids-limit",
            str(limits.pids_limit),
            "--network",
            "none" if req.policy.network.value == "deny" else "bridge",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
        ]
        for key, value in sorted(CONTAINER_ENV.items()):
            argv += ["-e", f"{key}={value}"]
        argv += ["-v", f"{_docker_path(ws.root)}:{CONTAINER_WORKDIR}"]
        for key, value in sorted(req.labels.items()):
            argv += ["--label", f"math-arena.{key}={value}"]
        argv += ["--label", f"math-arena.run-id={req.run_id}"]
        script = build_inline_script(
            workspace=CONTAINER_WORKDIR,
            seed=req.seed,
            guard="strict-math" if req.policy.enforce_module_allowlist else "none",
            params_literal=_params_json(req),
        )
        argv += [image, "/bin/sh", "-c", script]
        return argv

    def assert_argv_safe(self, argv: list[str], settings: object | None = None) -> None:
        """Refuse to launch argv that carries a forbidden mount or an unexpected bind.

        This is what makes ``no_docker_socket`` and ``no_host_mount`` code-enforced
        statements instead of comments: the check runs on the rendered argv, and the
        backend calls it immediately before every launch.
        """
        del settings
        joined = " ".join(argv).replace("\\", "/")
        for forbidden in FORBIDDEN_HOST_MOUNTS:
            if forbidden in joined:
                raise RuntimeError(f"refusing to launch container: argv would expose {forbidden}")
        for index, token in enumerate(argv):
            if token in {"-v", "--volume", "--mount"}:
                mount = argv[index + 1] if index + 1 < len(argv) else ""
                if CONTAINER_WORKDIR not in mount:
                    raise RuntimeError(
                        f"refusing to launch container: unexpected host mount {mount!r}; "
                        f"only a '{CONTAINER_WORKDIR}' bind is permitted"
                    )

    # -- execution ----------------------------------------------------------------------

    def run(self, req: ExecutionRequest, payload) -> BackendRun:  # noqa: ANN001
        limits = req.limits
        image = req.sandbox_image or self.image
        ws = RunWorkspace.create(base=None, run_id=req.run_id, disk_limit_mb=limits.disk_limit_mb)
        _materialise_container_workspace(ws, req, payload)

        digest = self.resolve_image_digest(image)
        if digest is None:
            ws.cleanup()
            return BackendRun(
                stdout="",
                stderr=(
                    f"sandbox image {image!r} is not present locally, so it could not be resolved "
                    "to an image ID; refusing to run because image_by_digest cannot be honoured"
                ),
                exit_code=None,
                timed_out=False,
                controls=IsolationControls(),  # all False: nothing was executed
                unenforced_reasons={
                    name: "run aborted before launch: image digest unresolvable (image not present "
                    "locally; this backend never pulls implicitly)"
                    for name in IsolationControls.model_fields
                },
                effective_limits=_effective(limits, req.policy),
                isolation_mode=IsolationMode.ERROR,
                backend_name=self.name,
                image=image,
                image_digest=None,
                argv=[],
                exec_status_override="error",
                warnings=[
                    f"image {image!r} unresolvable; run aborted. Build Dockerfile.sandbox or "
                    "pre-pull the image, then retry. No fallback to the local backend was taken."
                ],
            )

        container_name = f"ma-sandbox-{uuid.uuid4().hex[:12]}"
        argv = self.build_argv(req, ws, container_name, image=image)
        self.assert_argv_safe(argv)

        started = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        warnings: list[str] = []
        runtime_stderr = ""
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = started + limits.wall_time_ms / 1000.0
            while True:
                exit_code = proc.poll()
                if exit_code is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    _docker_stop_kill(self.runtime, container_name, proc)
                    exit_code = proc.poll()
                    break
                time.sleep(0.05)
            try:
                runtime_stderr = proc.stderr.read() if proc.stderr else ""
            except Exception:  # noqa: BLE001
                runtime_stderr = ""
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"container launch failed: {type(exc).__name__}: {exc}")
            _force_remove(self.runtime, container_name)
        duration_ms = int((time.monotonic() - started) * 1000)
        if runtime_stderr.strip():
            warnings.append(f"container runtime stderr: {runtime_stderr.strip()[:400]}")

        stdout, stderr, truncation, cap_by = ws.read_capture(limits.output_limit_bytes)
        truncation.cap_enforced_by = cap_by
        artifacts, artifacts_bytes = ws.collect_artifacts()
        workspace_bytes = ws.workspace_bytes()
        runner_meta = ws.read_meta()
        ws.cleanup()

        if timed_out:
            warnings.append(
                f"wall-clock timeout at {limits.wall_time_ms}ms: container {container_name} was "
                "stopped then killed; the captured output below is whatever the code flushed "
                "before it died"
            )
        if workspace_bytes > ws.max_total_bytes:
            warnings.append(
                f"workspace grew to {workspace_bytes} bytes, beyond the declared "
                f"{limits.disk_limit_mb} MiB. There is no filesystem quota on the bind-mounted "
                "workspace: this is measured, not prevented."
            )
        if not runner_meta:
            warnings.append(
                "runner sidecar (out/meta.json) is missing: the code never reached the runner, or "
                "was killed before it could write it. Treated as a failure, never as a pass."
            )

        controls = self._controls(req.policy, limits)
        return BackendRun(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            controls=controls,
            unenforced_reasons={name: MECHANISMS[name] for name in controls.missing()},
            effective_limits=_effective(limits, req.policy),
            isolation_mode=IsolationMode.CONTAINER,
            truncation=truncation,
            artifacts=artifacts,
            backend_name=self.name,
            image=image,
            image_digest=digest,
            python_version=(_meta_value(runner_meta, "python_version") or "unknown"),
            platform="linux (container)",
            argv=argv,
            warnings=warnings,
            runtime_facts={
                "runtime": self.runtime,
                "container_name": container_name,
                "duration_ms_including_runtime_overhead": duration_ms,
                "workspace_bytes": workspace_bytes,
                "artifacts_bytes": artifacts_bytes,
                "runner_meta": _meta_json(runner_meta),
                "bind_mounts": [f"{ws.root} -> {CONTAINER_WORKDIR}"],
                "network_mode": "none" if req.policy.network.value == "deny" else "bridge",
                "tmpfs": f"/tmp:rw,noexec,nosuid,size={limits.disk_limit_mb}m",
                "cap_drop": "ALL",
                "security_opt": ["no-new-privileges"],
                "image_digest": digest,
                "os_isolation": "container namespaces + cgroups",
            },
        )


# --------------------------------------------------------------------------------------


def _effective(limits: ResourceLimits, policy: SandboxPolicy) -> EffectiveLimits:
    return EffectiveLimits(
        cpu_limit=limits.cpu_limit,
        memory_limit_mb=limits.memory_limit_mb,
        pids_limit=limits.pids_limit,
        disk_limit_mb=(
            f"tmpfs /tmp only ({limits.disk_limit_mb} MiB); workspace bind measured-only"
        ),
        wall_time_ms=limits.wall_time_ms,
        output_limit_bytes=limits.output_limit_bytes,
        network="none" if policy.network.value == "deny" else "bridge (NOT isolated)",
    )


def _detect_runtime() -> str | None:
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return candidate
    return None


def _materialise_container_workspace(ws: RunWorkspace, req: ExecutionRequest, payload) -> None:  # noqa: ANN001
    """Write code, inputs, params and the runner into the bind-mounted workspace.

    The runner is copied as a real file rather than embedded in the command line:
    that removes every shell-escaping question from the launch path, and it means the
    code that executes in-container is byte-identical to the file the tests exercise.
    """
    payload.materialise(ws.root)
    runner_source = (Path(__file__).resolve().parents[1] / "_data" / "runner.py").read_bytes()
    runner_path = ws.root / RUNNER_REL
    runner_path.parent.mkdir(parents=True, exist_ok=True)
    runner_path.write_bytes(runner_source)
    (ws.root / "python" / "params.json").write_text(
        _params_json(req), encoding="utf-8", newline="\n"
    )
    out_dir = ws.results_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    _make_writable(ws.root)


def _make_writable(root: Path) -> None:
    """Make the workspace writable by the container's non-root uid.

    Needed because the bind mount carries host ownership: on Docker Desktop for
    Windows the mapping is permissive, on Linux it is not. Directories get 0777 and
    files 0666, which is acceptable *because the workspace is fresh, per-run, holds
    nothing but the submitted payload, and is deleted afterwards*.
    """
    try:
        root.chmod(0o777)
        for path in root.rglob("*"):
            if path.is_dir():
                path.chmod(0o777)
            elif path.is_file():
                path.chmod(0o666)
    except OSError:
        pass  # NTFS / bind-mount quirks: the container entrypoint retries with its own perms


def _docker_path(path: Path) -> str:
    """Render a host path for ``-v``. MSYS/Git-Bash path mangling is a real footgun."""
    text = str(path).replace("\\", "/")
    if len(text) > 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _docker_stop_kill(runtime: str, name: str, proc: subprocess.Popen) -> None:
    for command in (
        [runtime, "stop", "-t", "1", name],
        [runtime, "kill", name],
        [runtime, "rm", "-f", name],
    ):
        try:
            subprocess.run(command, capture_output=True, timeout=30, check=False)  # noqa: S603
        except Exception:  # noqa: BLE001
            pass
        try:
            if proc.poll() is not None:
                break
        except Exception:  # noqa: BLE001
            break
    kill_tree(proc)


def _force_remove(runtime: str, name: str) -> None:
    try:
        subprocess.run(  # noqa: S603
            [runtime, "rm", "-f", name], capture_output=True, timeout=30, check=False
        )
    except Exception:  # noqa: BLE001
        pass


def _params_json(req: ExecutionRequest) -> str:
    return json.dumps(req.params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _meta_json(raw: str) -> dict | None:
    if not raw.strip().startswith("{"):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _meta_value(raw: str, key: str) -> str | None:
    data = _meta_json(raw)
    if not data:
        return None
    value = data.get(key)
    return str(value) if value is not None else None


__all__ = ["CONTAINER_ENV", "MECHANISMS", "ContainerBackend", "build_inline_script"]
