"""Shared test fixtures.

Test tiers, stated plainly so a reader always knows what was actually exercised:

* unmarked tests -- no container needed; they exercise the contract, hashing, the
  guard, and the refusal path. These run everywhere.
* ``@pytest.mark.container`` -- require a reachable container runtime AND the
  sandbox image present locally. When either is missing they **skip with a printed
  reason**. They never fake a container run and never fall back to the local
  backend while claiming container evidence.

``SANDBOX_TEST_IMAGE`` overrides the image; the default is the ``python:3.12-slim``
mirror tag already present on this machine, because the backend never pulls
implicitly (a pull would turn ``image_by_digest`` into a claim about a moving tag).
"""

from __future__ import annotations

import os

import pytest

from sandbox.backends import ContainerBackend
from sandbox.hashing import sha256_text
from sandbox.models import (
    DependencyLock,
    ExecutionRequest,
    NetworkPolicy,
    ResourceLimits,
    SandboxPolicy,
)

TEST_IMAGE = os.environ.get("SANDBOX_TEST_IMAGE", "docker.m.daocloud.io/library/python:3.12-slim")
TEST_LOCK = "pydantic==2.9.2\n"


def make_request(
    code: str,
    *,
    run_id: str = "test-run",
    backend: str = "container",
    seed: int = 0,
    limits: ResourceLimits | None = None,
    network: NetworkPolicy = NetworkPolicy.DENY,
    require_real_isolation: bool = True,
    inputs: list | None = None,
    params: dict | None = None,
    enforce_module_allowlist: bool = True,
    sandbox_image: str | None = TEST_IMAGE,
) -> ExecutionRequest:
    return ExecutionRequest(
        run_id=run_id,
        code=code,
        inputs=inputs or [],
        params=params or {},
        seed=seed,
        dependency_lock=DependencyLock(format="requirements.txt", content=TEST_LOCK),
        limits=limits or ResourceLimits(
            cpu_limit=1.0,
            memory_limit_mb=256,
            pids_limit=64,
            disk_limit_mb=32,
            wall_time_ms=20_000,
            output_limit_bytes=64 * 1024,
        ),
        policy=SandboxPolicy(
            require_real_isolation=require_real_isolation,
            network=network,
            enforce_module_allowlist=enforce_module_allowlist,
        ),
        backend=backend,  # type: ignore[arg-type]
        sandbox_image=sandbox_image,
    )


@pytest.fixture(scope="session")
def container_backend() -> ContainerBackend:
    backend = ContainerBackend(image=TEST_IMAGE)
    available, reason = backend.is_available()
    if not available:
        pytest.skip(f"container runtime unavailable, container tests skipped: {reason}")
    digest = backend.resolve_image_digest(TEST_IMAGE)
    if digest is None:
        pytest.skip(
            f"sandbox image {TEST_IMAGE!r} is not present locally, container tests skipped. "
            "Build Dockerfile.sandbox or set SANDBOX_TEST_IMAGE to a local image."
        )
    return backend


@pytest.fixture(scope="session")
def image_digest(container_backend: ContainerBackend) -> str:
    digest = container_backend.resolve_image_digest(TEST_IMAGE)
    assert digest is not None
    return digest


def code_hash_of(code: str) -> str:
    return sha256_text(code)
