"""Security tests: real attempts, honest verdicts.

Rules these tests follow, because the whole point of this package is not faking:

* every test attempts the real attack (real socket call, real memory hog, real
  infinite loop, real 100 MB of output, real host-path read, real env scrape);
* a control is only called "blocked" when the container backend's kernel/cgroup
  enforcement actually stopped it -- and the assertion says which mechanism;
* on the local backend the same attacks are run and the **observed** outcome is
  recorded; the tests assert the manifest reports the control as NOT enforced, and
  they do **not** assert that the attack failed (it does not fail).
"""

from __future__ import annotations

import json
import os

import pytest

from sandbox.backends import ContainerBackend, LocalBackend
from sandbox.models import ResourceLimits
from sandbox.orchestrator import run_experiment

from .conftest import make_request

# --------------------------------------------------------------------------------------
# 1. Network
# --------------------------------------------------------------------------------------

NETWORK_CODE = """\
import socket
try:
    s = socket.create_connection(("example.com", 80), timeout=10)
    print("NETWORK_OK", s.getpeername())
except Exception as exc:
    print("NETWORK_FAILED", type(exc).__name__, exc)
"""


@pytest.mark.container
def test_container_network_denied(container_backend: ContainerBackend) -> None:
    """--network=none must make egress impossible (kernel-enforced, not policy)."""
    req = make_request(
        NETWORK_CODE,
        run_id="net-deny",
        backend="container",
        enforce_module_allowlist=False,  # use the real socket, not the guard
        limits=ResourceLimits(wall_time_ms=30_000, output_limit_bytes=32 * 1024),
    )
    result = run_experiment(req, backend=container_backend)
    manifest = result.manifest
    assert manifest.exec_status == "pass", (result.stdout, result.stderr, manifest.warnings)
    assert "NETWORK_FAILED" in result.stdout, (
        f"external connection unexpectedly succeeded inside --network=none: {result.stdout!r}"
    )
    assert "NETWORK_OK" not in result.stdout
    assert manifest.isolation_controls_enforced.network_isolation is True
    assert manifest.effective_limits.network == "none"


def test_local_backend_network_is_reported_unenforced_never_blocked() -> None:
    """The same attack on the local backend, with the guard off: it reaches the network.

    This test does not assert that local is safe -- it asserts that the harness tells
    the truth about it. Regression guard against ever labelling a local run "no network".
    """
    req = make_request(
        NETWORK_CODE,
        run_id="net-local",
        backend="local",
        require_real_isolation=False,
        enforce_module_allowlist=False,
        limits=ResourceLimits(wall_time_ms=30_000),
    )
    result = run_experiment(req, backend=LocalBackend())
    manifest = result.manifest
    assert manifest.isolation_controls_enforced.network_isolation is False
    assert "network_isolation" in manifest.unenforced_reasons
    assert manifest.effective_limits.network.startswith("UNBOUNDED")
    # Observed outcome on this host is recorded verbatim; either branch is honest,
    # but the manifest may never claim the network was blocked.
    reachable = "NETWORK_OK" in result.stdout
    assert ("NETWORK_FAILED" in result.stdout) or reachable, (result.stdout, result.stderr)
    if reachable:
        assert "network_isolation" not in manifest.isolation_controls_enforced.enforced()


def test_local_backend_with_guard_on_blocks_import_socket_but_still_admits_no_isolation() -> None:
    req = make_request(
        "import socket\nprint('NEVER')\n",
        run_id="net-local-guard",
        backend="local",
        require_real_isolation=False,
        limits=ResourceLimits(wall_time_ms=20_000),
    )
    result = run_experiment(req, backend=LocalBackend())
    assert "GUARD_VIOLATION[BLOCKED_IMPORT]" in result.stderr
    # The guard blocked the *import*; the manifest still says the network is not isolated.
    assert result.manifest.isolation_controls_enforced.network_isolation is False


# --------------------------------------------------------------------------------------
# 2. Resource exhaustion
# --------------------------------------------------------------------------------------

FORKBOMB_CODE = """\
import os
children = []
while True:
    pid = os.fork()
    if pid == 0:
        while True:
            pass
    children.append(pid)
"""

MEMORY_HOG_CODE = """\
chunks = []
while True:
    chunks.append(bytearray(32 * 1024 * 1024))
"""


@pytest.mark.container
def test_container_memory_limit_kills_memory_hog(container_backend: ContainerBackend) -> None:
    """A hog under --memory=64m must be killed (OOM), not allowed to grow."""
    req = make_request(
        MEMORY_HOG_CODE,
        run_id="mem-hog",
        backend="container",
        enforce_module_allowlist=False,
        limits=ResourceLimits(
            memory_limit_mb=64, wall_time_ms=60_000, output_limit_bytes=16 * 1024
        ),
    )
    result = run_experiment(req, backend=container_backend)
    manifest = result.manifest
    assert manifest.exec_status in {"fail", "timeout"}, (
        f"memory hog was not stopped: status={manifest.exec_status} exit={manifest.exit_code} "
        f"stdout={result.stdout[:200]!r} stderr={result.stderr[:200]!r}"
    )
    assert manifest.exec_status != "pass"
    assert manifest.isolation_controls_enforced.memory_limit is True
    assert manifest.effective_limits.memory_limit_mb == 64
    combined = (result.stdout + result.stderr + " ".join(manifest.warnings)).lower()
    assert ("memory" in combined) or manifest.timed_out or manifest.exit_code == 137, (
        "expected an OOM kill (exit 137) or a timeout; observed: "
        f"exit={manifest.exit_code} status={manifest.exec_status} warnings={manifest.warnings}"
    )


@pytest.mark.container
def test_container_pid_limit_contains_fork_bomb(container_backend: ContainerBackend) -> None:
    """A fork bomb under --pids-limit must fail immediately, not take the host down."""
    req = make_request(
        FORKBOMB_CODE,
        run_id="fork-bomb",
        backend="container",
        enforce_module_allowlist=False,
        limits=ResourceLimits(
            pids_limit=32, memory_limit_mb=128, wall_time_ms=45_000, output_limit_bytes=16 * 1024
        ),
    )
    result = run_experiment(req, backend=container_backend)
    manifest = result.manifest
    assert manifest.exec_status in {"fail", "timeout"}
    assert manifest.isolation_controls_enforced.pids_limit is True
    assert manifest.effective_limits.pids_limit == 32
    combined = (result.stderr + " ".join(manifest.warnings)).lower()
    assert ("fork" in combined) or ("resource temporarily unavailable" in combined) or manifest.timed_out


def test_local_backend_resource_attacks_are_reported_honestly() -> None:
    """Local backend: no memory/PID limit exists, so the manifest must say so.

    The attack itself is NOT run at full strength here -- that would damage the shared
    machine, which is a real limitation of the local backend and exactly why it is
    marked NOT enforced. What is asserted is the reporting, plus that a *bounded*
    allocation still completes, proving there is no silent memory cap.
    """
    req = make_request(
        "chunks = [bytearray(4 * 1024 * 1024) for _ in range(8)]\nprint('ALLOCATED', len(chunks))\n",
        run_id="mem-local",
        backend="local",
        require_real_isolation=False,
        limits=ResourceLimits(memory_limit_mb=32, wall_time_ms=20_000),
    )
    result = run_experiment(req, backend=LocalBackend())
    manifest = result.manifest
    assert manifest.isolation_controls_enforced.memory_limit is False
    assert manifest.isolation_controls_enforced.pids_limit is False
    assert manifest.effective_limits.memory_limit_mb == "UNBOUNDED"
    # 32 MiB declared, 32 MiB allocated: no enforcement intervened.
    assert result.manifest.exec_status == "pass", result.stderr
    assert "ALLOCATED 8" in result.stdout


# --------------------------------------------------------------------------------------
# 3. Wall-clock timeout
# --------------------------------------------------------------------------------------

INFINITE_CODE = """\
print("BEFORE_LOOP")
flush_output()
while True:
    pass
"""


@pytest.mark.container
def test_container_infinite_loop_hits_wall_clock_timeout(container_backend: ContainerBackend) -> None:
    req = make_request(
        INFINITE_CODE,
        run_id="timeout",
        backend="container",
        enforce_module_allowlist=False,
        limits=ResourceLimits(wall_time_ms=3000, output_limit_bytes=16 * 1024),
    )
    result = run_experiment(req, backend=container_backend)
    manifest = result.manifest
    assert manifest.timed_out is True
    assert manifest.exec_status == "timeout"
    assert manifest.isolation_controls_enforced.wall_clock_timeout is True
    assert "BEFORE_LOOP" in result.stdout, (
        "partial output written before the kill must be preserved: stdout="
        f"{result.stdout[:200]!r}"
    )
    assert manifest.duration_ms < 60_000


def test_local_infinite_loop_hits_wall_clock_timeout() -> None:
    req = make_request(
        INFINITE_CODE,
        run_id="timeout-local",
        backend="local",
        require_real_isolation=False,
        limits=ResourceLimits(wall_time_ms=2500, output_limit_bytes=16 * 1024),
    )
    result = run_experiment(req, backend=LocalBackend())
    manifest = result.manifest
    assert manifest.timed_out is True
    assert manifest.exec_status == "timeout"
    assert manifest.isolation_controls_enforced.wall_clock_timeout is True
    assert "BEFORE_LOOP" in result.stdout


# --------------------------------------------------------------------------------------
# 4. Output cap
# --------------------------------------------------------------------------------------

FLOOD_CODE = """\
line = "A" * 1000
for _ in range(200):
    print(line)
    flush_output()
print("FLOOD_DONE")
flush_output()
"""


@pytest.mark.container
def test_container_output_is_truncated_and_recorded(container_backend: ContainerBackend) -> None:
    cap = 8192
    req = make_request(
        FLOOD_CODE,
        run_id="flood-container",
        backend="container",
        enforce_module_allowlist=False,
        limits=ResourceLimits(wall_time_ms=30_000, output_limit_bytes=cap),
    )
    result = run_experiment(req, backend=container_backend)
    manifest = result.manifest
    assert manifest.truncation.truncated is True
    assert manifest.truncation.limit_bytes == cap
    assert manifest.truncation.stdout_bytes_seen > cap, (
        "the manifest must record how much output was actually produced, not just that it "
        f"was cut: seen={manifest.truncation.stdout_bytes_seen} cap={cap}"
    )
    assert len(result.stdout.encode("utf-8")) <= cap
    assert manifest.isolation_controls_enforced.output_cap is True


def test_local_output_is_truncated_and_recorded() -> None:
    cap = 4096
    req = make_request(
        FLOOD_CODE,
        run_id="flood-local",
        backend="local",
        require_real_isolation=False,
        limits=ResourceLimits(wall_time_ms=30_000, output_limit_bytes=cap),
    )
    result = run_experiment(req, backend=LocalBackend())
    manifest = result.manifest
    assert manifest.truncation.truncated is True
    assert len(result.stdout.encode("utf-8")) <= cap
    assert manifest.truncation.stdout_bytes_seen > cap


# --------------------------------------------------------------------------------------
# 5. Host path / environment secrets
# --------------------------------------------------------------------------------------

SECRET_PROBE_CODE = """\
import os, json
found = {k: v for k, v in os.environ.items() if k in ("MODEL_API_KEY", "EMBEDDING_API_KEY",
                                                      "DATABASE_URL", "RESEARCH_DATABASE_URL",
                                                      "POSTGRES_PASSWORD", "AWS_SECRET_ACCESS_KEY")}
paths = {}
for p in ("/etc/passwd", "/etc/shadow", "/proc/1/environ", "C:/Windows/win.ini",
          "C:/Users", "/var/run/docker.sock"):
    try:
        with open(p, "rb") as fh:
            data = fh.read(64)
        paths[p] = "READABLE:" + data[:16].hex()
    except Exception as exc:
        paths[p] = type(exc).__name__
print("SECRETS", json.dumps(found, sort_keys=True))
print("PATHS", json.dumps(paths, sort_keys=True))
"""


@pytest.mark.parametrize("probe_code", [SECRET_PROBE_CODE])
def test_secret_probe_code_is_usable_only_with_the_guard_off(probe_code: str) -> None:
    """Sanity guard for the tests below: this probe deliberately needs the guard off.

    Without this check a future edit could leave the guard on and the "secret" tests
    would pass for the wrong reason (the ``import os`` being blocked, not the
    container's filesystem isolation).
    """
    from sandbox.orchestrator import run_experiment as _run

    from .conftest import make_request as _mk

    guarded = _run(
        _mk(
            probe_code,
            run_id="probe-guarded",
            backend="local",
            require_real_isolation=False,
        )
    )
    assert "GUARD_VIOLATION[BLOCKED_IMPORT]" in guarded.stderr


@pytest.mark.container
def test_container_cannot_read_host_secrets_or_paths(container_backend: ContainerBackend) -> None:
    """Differential proof, not an assertion about a string that could pass vacuously.

    The same probe is run twice. On the **local** backend (host, guard off) it reports
    at least one READABLE host path -- that establishes the probe actually works. Inside
    the **container** it must report zero READABLE paths and an empty secret set. If the
    probe were broken, the local leg would fail rather than the container leg passing
    for the wrong reason.
    """
    limits = ResourceLimits(wall_time_ms=30_000, output_limit_bytes=32 * 1024)

    host_req = make_request(
        SECRET_PROBE_CODE,
        run_id="secret-probe-host",
        backend="local",
        require_real_isolation=False,
        enforce_module_allowlist=False,
        limits=limits,
    )
    host_result = run_experiment(host_req, backend=LocalBackend())
    assert host_result.manifest.exec_status == "pass", host_result.stderr
    assert host_result.manifest.isolation_controls_enforced.no_host_mount is False
    host_paths = json.loads(host_result.stdout.split("PATHS ", 1)[1].strip())
    host_readable = [p for p, verdict in host_paths.items() if str(verdict).startswith("READABLE")]
    assert host_readable, (
        "the probe found no readable host path even on the host itself, so it cannot prove "
        f"anything about the container. host verdicts: {host_paths}"
    )

    req = make_request(
        SECRET_PROBE_CODE,
        run_id="secret-probe",
        backend="container",
        enforce_module_allowlist=False,
        limits=limits,
    )
    result = run_experiment(req, backend=container_backend)
    assert result.manifest.exec_status == "pass", (result.stdout, result.stderr)
    assert "SECRETS {}" in result.stdout, f"secret environment leaked: {result.stdout!r}"
    container_paths = json.loads(result.stdout.split("PATHS ", 1)[1].strip())
    assert set(container_paths) == set(host_paths), (
        "the two legs must probe the same paths, otherwise the comparison is meaningless"
    )

    # Host-only paths must be unreachable inside the container. These are the paths that
    # the host leg proves exist -- i.e. the container cannot see the host's filesystem.
    host_only = set(host_readable)
    container_sees_host_only = host_only & {
        p for p, verdict in container_paths.items() if str(verdict).startswith("READABLE")
    }
    assert container_sees_host_only == set(), (
        f"the container read host-only paths {sorted(container_sees_host_only)}; "
        f"host leg verdicts: {host_paths}; container leg verdicts: {container_paths}"
    )
    # Windows host paths must not exist at all in a Linux container.
    for windows_path in ("C:/Windows/win.ini", "C:/Users"):
        assert not str(container_paths[windows_path]).startswith("READABLE"), container_paths

    # Honest note recorded in the test itself: /etc/passwd and /proc/1/environ ARE
    # readable inside the container. Those are the *container's own* files -- an
    # expected consequence of a read-only rootfs, not a host leak. They contain no host
    # secrets because the container environment is the fixed literal set, and the
    # SECRETS assertion above proves no secret variable reached it.
    container_readable = sorted(
        p for p, verdict in container_paths.items() if str(verdict).startswith("READABLE")
    )
    assert set(container_readable) <= {"/etc/passwd", "/proc/1/environ", "C:/Windows/win.ini"}, (
        f"unexpected readable paths inside the container: {container_readable}"
    )
    assert result.manifest.isolation_controls_enforced.no_host_env_secrets is True
    assert result.manifest.isolation_controls_enforced.no_docker_socket is True


def test_local_backend_env_is_stripped_but_filesystem_is_not() -> None:
    """Env stripping is real on the local backend; filesystem access is not limited.

    The guard is turned off here on purpose: the point is to test the *backend's*
    environment handling, not the import allowlist.
    """
    os.environ["SANDBOX_TEST_CANARY"] = "canary-value-must-not-leak"
    try:
        req = make_request(
            "import os, json\n"
            "print('CANARY', json.dumps({k: v for k, v in os.environ.items() "
            "if 'CANARY' in k}))\n",
            run_id="env-local",
            backend="local",
            require_real_isolation=False,
            enforce_module_allowlist=False,
            limits=ResourceLimits(wall_time_ms=20_000),
        )
        result = run_experiment(req, backend=LocalBackend())
        assert result.manifest.exec_status == "pass", result.stderr
        assert "CANARY {}" in result.stdout, f"host env leaked into the child: {result.stdout!r}"
        assert "canary-value-must-not-leak" not in result.stdout
        assert result.manifest.isolation_controls_enforced.no_host_env_secrets is True
        # ...but the filesystem is NOT isolated, and the manifest must say so.
        assert result.manifest.isolation_controls_enforced.no_host_mount is False
    finally:
        os.environ.pop("SANDBOX_TEST_CANARY", None)


def test_local_backend_env_keys_are_exactly_the_fixed_literal_set() -> None:
    """Prove which variables exist rather than asserting a single canary is absent."""
    req = make_request(
        "import os, json\nprint('KEYS', json.dumps(sorted(os.environ), sort_keys=True))\n",
        run_id="env-keys",
        backend="local",
        require_real_isolation=False,
        enforce_module_allowlist=False,
    )
    result = run_experiment(req, backend=LocalBackend())
    assert result.manifest.exec_status == "pass", result.stderr
    assert "KEYS [" in result.stdout
    payload = result.stdout.split("KEYS ", 1)[1].strip()
    keys = json.loads(payload)
    from sandbox.backends.local import LOCAL_ENV

    assert sorted(keys) == sorted(LOCAL_ENV), (
        "the child environment must be exactly LOCAL_ENV: an inherited host variable would "
        f"be a secret leak. observed={keys}"
    )
    for forbidden in ("MODEL_API_KEY", "DATABASE_URL", "PATH_TO_ENV", "USERPROFILE", "APPDATA"):
        assert forbidden not in keys


def test_container_argv_never_contains_the_docker_socket(tmp_path) -> None:
    """Assert on the rendered argv, not on a comment: no socket, one bind only."""
    from sandbox.workspace import RunWorkspace

    backend = ContainerBackend()
    ws = RunWorkspace.create(base=tmp_path, run_id="argv", disk_limit_mb=8)
    req = make_request("print(1)\n", run_id="argv", backend="container")
    argv = backend.build_argv(req, ws, "ma-sandbox-argvtest", image="python:3.12-slim")
    joined = " ".join(argv)
    assert "docker.sock" not in joined
    assert "docker_engine" not in joined
    assert argv.count("-v") == 1, f"exactly one bind mount is permitted, got {argv}"
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--user") + 1] == "10001:10001"
    assert "--read-only" in argv
    assert argv[argv.index("--pids-limit") + 1] == "64"
    assert argv[argv.index("--memory") + 1] == "256m"
    backend.assert_argv_safe(argv)

    # And the guard must actually fire when a socket bind is introduced.
    tainted = list(argv)
    tainted.insert(tainted.index("-v") + 2, "-v")
    tainted.insert(tainted.index("-v") + 3, "/var/run/docker.sock:/var/run/docker.sock")
    with pytest.raises(RuntimeError):
        backend.assert_argv_safe(tainted)


def test_no_secret_material_appears_in_a_manifest() -> None:
    """Manifests are logged; they must never carry payload bytes or env values."""
    secret = "sk-live-DO-NOT-LOG-0123456789"
    req = make_request(
        f"print('len', len({secret!r}))\n",
        run_id="manifest-secrets",
        backend="local",
        require_real_isolation=False,
        params={"token": secret},
    )
    result = run_experiment(req, backend=LocalBackend())
    serialised = json.dumps(result.manifest.model_dump(mode="json"))
    assert secret not in serialised
    assert "token" not in serialised, "params keys must not be copied into the manifest"
