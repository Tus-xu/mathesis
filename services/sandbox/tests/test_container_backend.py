"""Container-backend contract tests (real Docker, no fakes).

Everything here is marked ``container`` and skips with a printed reason when no
runtime/image is available. When it does run, it runs real containers -- there is no
mock anywhere in this file, and no test falls back to the local backend while still
claiming container behaviour.
"""

from __future__ import annotations

import pytest

from sandbox.backends import ContainerBackend
from sandbox.models import ResourceLimits
from sandbox.orchestrator import run_experiment

from .conftest import TEST_IMAGE, make_request

pytestmark = pytest.mark.container

#: Deliberately uses ONLY allowlisted imports (``json``, ``math``). ``sys``/``os`` are
#: blocked by the module guard -- that the guard blocks them is asserted separately
#: below, because a test should not blur "the guard blocked my probe" with "the
#: container works".
ARGS_CODE = """\
import json, math
print("JSON", json.dumps({"seeded": True}, sort_keys=True))
print("MATH", math.comb(10, 3))
print("SEED_SEEN", SEED)
print("PARAMS_KEYS", sorted(PARAMS))
flush_output()
"""

ARTIFACT_CODE = """\
path = "artifacts/res" + "ult.txt"
handle = open(path, "w")
handle.write("artifact-body\\n")
handle.close()
print("WROTE_ARTIFACT")
flush_output()
"""

GUARD_BLOCKED_CODE = """\
import os
print("NEVER")
"""


def test_container_run_records_the_full_manifest(
    container_backend: ContainerBackend, image_digest: str
) -> None:
    req = make_request(
        ARGS_CODE,
        run_id="container-manifest",
        backend="container",
        seed=4242,
        params={"alpha": 1},
        limits=ResourceLimits(wall_time_ms=30_000, output_limit_bytes=32 * 1024),
    )
    result = run_experiment(req, backend=container_backend)
    manifest = result.manifest

    assert manifest.exec_status == "pass", (result.stdout, result.stderr, manifest.warnings)
    assert manifest.image_digest == image_digest
    assert manifest.image == TEST_IMAGE
    assert manifest.python_version.startswith("3."), manifest.python_version
    assert manifest.seed == 4242
    assert manifest.exit_code == 0
    assert manifest.isolation_mode.value == "container"
    assert manifest.code_hash and manifest.input_hash and manifest.output_hash
    assert manifest.dependency_lock_hash

    # Controls genuinely enforced by this backend.
    enforced = manifest.isolation_controls_enforced
    for control in (
        "non_root_user",
        "read_only_rootfs",
        "network_isolation",
        "memory_limit",
        "cpu_limit",
        "pids_limit",
        "wall_clock_timeout",
        "output_cap",
        "no_host_mount",
        "no_docker_socket",
        "no_host_env_secrets",
        "image_by_digest",
        "module_allowlist",
    ):
        assert getattr(enforced, control) is True, f"{control} should be enforced in a container"

    # Controls that are honestly NOT enforced on this backend.
    assert enforced.tmpfs_workdir is False
    assert enforced.disk_limit is False
    assert "tmpfs_workdir" in manifest.unenforced_reasons
    assert "disk_limit" in manifest.unenforced_reasons
    assert manifest.unenforced_reasons["disk_limit"].startswith("PARTIAL")
    # ...and that gap is surfaced as a warning on an isolation-requiring policy.
    assert any("controls are not enforced" in w for w in manifest.warnings)

    # Workload facts from inside the container.
    assert "MATH 120" in result.stdout
    assert "SEED_SEEN 4242" in result.stdout, result.stdout
    assert "PARAMS_KEYS ['alpha']" in result.stdout, result.stdout
    meta = manifest.runtime_facts["runner_meta"]
    assert meta["python_version"].startswith("3.")
    assert meta["seed"] == 4242
    assert meta["guard"] == "strict-math"

    # The exact argv is preserved for audit.
    assert "--network" in result.argv and "--read-only" in result.argv
    assert result.argv[result.argv.index("--user") + 1] == "10001:10001"
    assert manifest.runtime_facts["network_mode"] == "none"
    assert manifest.runtime_facts["cap_drop"] == "ALL"


def test_container_collects_and_hashes_artifacts(container_backend: ContainerBackend) -> None:
    """Artifact collection needs ``open``, so this run uses the guard-off path."""
    req = make_request(
        ARTIFACT_CODE,
        run_id="container-artifacts",
        backend="container",
        enforce_module_allowlist=False,
        limits=ResourceLimits(wall_time_ms=30_000),
    )
    result = run_experiment(req, backend=container_backend)
    assert result.manifest.exec_status == "pass", (result.stdout, result.stderr)
    assert result.manifest.isolation_controls_enforced.module_allowlist is False
    paths = [artifact.path for artifact in result.artifacts]
    assert "result.txt" in paths, paths
    assert all(len(artifact.sha256) == 64 for artifact in result.artifacts)


def test_container_guard_blocks_forbidden_imports_in_container(container_backend: ContainerBackend) -> None:
    """The guard runs inside the container too, not just on the dev machine."""
    req = make_request(
        GUARD_BLOCKED_CODE,
        run_id="container-guard",
        backend="container",
        limits=ResourceLimits(wall_time_ms=30_000),
    )
    result = run_experiment(req, backend=container_backend)
    assert result.manifest.exit_code == 3, (result.stdout, result.stderr)
    assert "GUARD_VIOLATION[BLOCKED_IMPORT]" in result.stderr
    assert "NEVER" not in result.stdout
    assert result.manifest.isolation_controls_enforced.module_allowlist is True


def test_container_numpy_free_environment_is_still_deterministic(container_backend: ContainerBackend) -> None:
    """The image is a plain python image: assert what it can and cannot do."""
    req = make_request(
        "import math\nprint('ok', math.comb(10, 3))\n",
        run_id="container-math",
        backend="container",
        seed=1234,
    )
    result = run_experiment(req, backend=container_backend)
    assert result.manifest.exec_status == "pass", (result.stdout, result.stderr)
    assert "ok 120" in result.stdout
    assert result.manifest.seed == 1234
    meta = result.manifest.runtime_facts["runner_meta"]
    assert meta["seed"] == 1234
    assert meta["guard"] == "strict-math"


def test_container_refuses_a_missing_image_instead_of_pulling(container_backend: ContainerBackend) -> None:
    req = make_request(
        "print('NEVER')\n",
        run_id="container-missing-image",
        backend="container",
        sandbox_image="math-arena-sandbox:definitely-not-present-xyz",
    )
    result = run_experiment(req, backend=container_backend)
    manifest = result.manifest
    assert manifest.exec_status == "error"
    assert manifest.image_digest is None
    assert manifest.isolation_controls_enforced == type(manifest.isolation_controls_enforced)()
    assert any("unresolvable" in warning for warning in manifest.warnings)
    assert "NEVER" not in result.stdout


def test_container_cleanup_leaves_no_containers_behind(container_backend: ContainerBackend) -> None:

    before = _container_ids()
    req = make_request("print('cleanup')\n", run_id="container-cleanup", backend="container")
    result = run_experiment(req, backend=container_backend)
    assert result.manifest.exec_status == "pass", result.stderr
    after = _container_ids()
    assert after - before == set(), f"leaked containers: {after - before}"


def _container_ids() -> set[str]:
    import subprocess

    probe = subprocess.run(  # noqa: S603
        ["docker", "ps", "-a", "--filter", "name=ma-sandbox-", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return {line.strip() for line in probe.stdout.splitlines() if line.strip()}
