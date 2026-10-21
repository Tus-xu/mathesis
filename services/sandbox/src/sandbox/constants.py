"""Frozen identifiers shared by every sandbox surface.

Values marked FROZEN come from ``deploy/registry.yml`` (``research-vnext-8300``)
and must not be re-invented here: WP-H owns the queue, this package only consumes
the convention.
"""

from __future__ import annotations

# --- Queue conventions, read from deploy/registry.yml:265-274 (research-vnext-8300) ---
# FROZEN: valkey_stream, port 6380, prefix strm:rv8300, group rv8300-workers,
# object prefix rvs/, dedupe scope rv8300.
QUEUE_KIND = "valkey_stream"
QUEUE_PORT = 6380
QUEUE_STREAM_PREFIX = "strm:rv8300"
QUEUE_CONSUMER_GROUP = "rv8300-workers"
QUEUE_DEDUPE_SCOPE = "rv8300"
QUEUE_OBJECT_PREFIX = "rvs/"

#: Stream key this worker consumes. WP-H's runtime appends the job kind.
JOB_STREAM = f"{QUEUE_STREAM_PREFIX}:jobs"
#: Stream key this worker writes results/acks to (WP-H owns final consumption).
RESULT_STREAM = f"{QUEUE_STREAM_PREFIX}:results"

#: The only job kind this handler answers to.
SANDBOX_RUN_JOB_KIND = "sandbox.run_experiment"

#: Environment variable names the worker reads. Any of them may be absent -- absence
#: is a hard failure, never a silent in-process fallback.
ENV_VALKEY_URL = "SANDBOX_VALKEY_URL"
ENV_REQUIRE_REAL_ISOLATION = "SANDBOX_REQUIRE_REAL_ISOLATION"
ENV_BACKEND = "SANDBOX_BACKEND"
ENV_CONTAINER_RUNTIME = "SANDBOX_CONTAINER_RUNTIME"
ENV_SANDBOX_IMAGE = "SANDBOX_IMAGE"

#: Image used by the container backend when the caller does not pin one.
#: The daocloud mirror tag is the one already present on the dev machine; the
#: Dockerfile.sandbox in this directory builds the production image (non-root).
DEFAULT_SANDBOX_IMAGE = "docker.m.daocloud.io/library/python:3.12-slim"

#: Non-root identity baked into Dockerfile.sandbox.
SANDBOX_UID = 10001
SANDBOX_GID = 10001
SANDBOX_USER = "sandbox"

#: Path inside the container where the per-run workspace is mounted (tmpfs only
#: when the backend can guarantee it; see docs/ISOLATION-MATRIX.md).
CONTAINER_WORKDIR = "/work"

#: Fixed, sanitised environment handed to executed code. No host variable is
#: inherited -- in particular no MODEL_API_KEY / DATABASE_URL / .env content.
MINIMAL_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/work",
    "TMPDIR": "/work/tmp",
    "TMP": "/work/tmp",
    "TEMP": "/work/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
}

#: Host directory names that must never be visible inside a run. Documented here
#: so the capability report can quote the actual list rather than a vague claim.
FORBIDDEN_HOST_MOUNTS: tuple[str, ...] = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "//./pipe/docker_engine",
    "//./pipe/dockerDesktopLinuxEngine",
)

#: Sentinel written to the manifest's ``limits.effective`` when a backend cannot
#: bound a dimension at all. Chosen over ``None`` so a reader cannot confuse
#: "unbounded" with "not applicable".
UNBOUNDED = "UNBOUNDED"
