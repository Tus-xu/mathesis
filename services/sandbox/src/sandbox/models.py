"""Typed execution contract: request -> result(+manifest).

Nothing in this module performs I/O. It is the shared vocabulary that every
isolation backend, the CLI, the worker and the tests all speak.

Honesty contract (see docs/ISOLATION-MATRIX.md):
``IsolationControls`` is a *runtime observation*, not a declaration. A backend
may only set a flag to ``True`` when it can name the mechanism that enforced it;
otherwise the flag is ``False`` and ``unenforced_reasons`` carries the reason.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------------------
# Policy / limits
# --------------------------------------------------------------------------------------


class NetworkPolicy(str, enum.Enum):
    """Whether the executed code is allowed to reach the network."""

    DENY = "deny"  #: default. Container backend: ``--network=none``. Local: NOT enforced.
    ALLOW = "allow"  #: caller explicitly opts in; recorded in the manifest.


class ResourceLimits(BaseModel):
    """Requested hard limits. A backend either enforces a limit or reports it as absent.

    ``effective`` on :class:`ExecutionManifest` echoes the value that was actually
    applied, so a caller can never mistake a request for an enforcement.
    """

    model_config = ConfigDict(extra="forbid")

    cpu_limit: float = Field(default=1.0, gt=0, le=64, description="CPU cores")
    memory_limit_mb: int = Field(default=512, ge=32, le=32768)
    pids_limit: int = Field(default=64, ge=1, le=4096)
    disk_limit_mb: int = Field(default=64, ge=1, le=4096)
    wall_time_ms: int = Field(default=30_000, ge=100, le=600_000)
    output_limit_bytes: int = Field(default=256 * 1024, ge=1024, le=16 * 1024 * 1024)


class SandboxPolicy(BaseModel):
    """Policy gates a caller can flip. Defaults are the *safe* direction:

    * ``require_real_isolation=True`` -- refuse to run when isolation is not real.
      Backends/tests that want the local backend must opt out explicitly.
    * ``network=DENY`` -- no network unless asked for.
    * ``enforce_module_allowlist=True`` -- defence in depth (AST guard + runtime hook).
    """

    model_config = ConfigDict(extra="forbid")

    require_real_isolation: bool = True
    network: NetworkPolicy = NetworkPolicy.DENY
    enforce_module_allowlist: bool = True
    fail_closed_on_unknown_backend: bool = True

    @property
    def allows_unisolated(self) -> bool:
        return not self.require_real_isolation


# --------------------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------------------


class InputRef(BaseModel):
    """A named input. Either inline bytes/text or a content-addressed reference.

    ``sha256`` is REQUIRED when ``inline`` is given: an unverified inline payload
    would make ``input_hash`` meaningless for reproducibility.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    inline: str | None = None
    object_ref: str | None = None  #: e.g. "rvs/inputs/ab12..." (MinIO, object_prefix rvs/)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    media_type: str = "text/plain"

    @model_validator(mode="after")
    def _exactly_one_source(self) -> InputRef:
        if (self.inline is None) == (self.object_ref is None):
            raise ValueError("InputRef requires exactly one of inline / object_ref")
        if self.object_ref is not None and self.sha256 is None:
            raise ValueError("object_ref inputs must carry the expected sha256")
        return self


class CodeRef(BaseModel):
    """Reference to code stored outside the request body.

    Used for the queue path: WP-H stores the payload as an object under ``rvs/``
    and passes the digest, so the worker can verify it did not change in flight.
    ``inline`` is the fallback for callers without object storage.
    """

    model_config = ConfigDict(extra="forbid")

    object_ref: str | None = None
    inline: str | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _exactly_one_source(self) -> CodeRef:
        if (self.inline is None) == (self.object_ref is None):
            raise ValueError("CodeRef requires exactly one of inline / object_ref")
        return self


class DependencyLock(BaseModel):
    """The dependency lock recorded in the manifest.

    ``lock_hash`` is always populated: when no explicit lock text is supplied it
    falls back to the sha256 of the canonical JSON of the pinned ``image``, which
    is the real dependency identity of a container run.
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["requirements.txt", "uv.lock", "pip-freeze", "image-pin", "none"] = "none"
    content: str | None = None
    image: str | None = None


class ExecutionRequest(BaseModel):
    """What a caller (research-vnext, the CLI, or a test) hands to a backend."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    experiment_id: str | None = None
    code: str | None = None
    code_ref: CodeRef | None = None
    inputs: list[InputRef] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int = Field(default=0, ge=0, lt=2**32)
    dependency_lock: DependencyLock = Field(default_factory=DependencyLock)
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    policy: SandboxPolicy = Field(default_factory=SandboxPolicy)
    backend: Literal["auto", "container", "local"] = "auto"
    sandbox_image: str | None = None
    entrypoint_module: str = "runner.py"
    labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _exactly_one_code_source(self) -> ExecutionRequest:
        if (self.code is None) == (self.code_ref is None):
            raise ValueError("ExecutionRequest requires exactly one of code / code_ref")
        return self

    @field_validator("labels")
    @classmethod
    def _label_keys_safe(cls, value: dict[str, str]) -> dict[str, str]:
        # Labels are echoed into the container argv; keep them inert.
        for key, val in value.items():
            if not key or len(key) > 64 or len(val) > 256:
                raise ValueError("label key/value too long")
            if any(ch in key or ch in val for ch in ("\n", "\r", "\x00")):
                raise ValueError("labels must not contain control characters")
        return value


# --------------------------------------------------------------------------------------
# Isolation controls -- the heart of the honesty contract
# --------------------------------------------------------------------------------------


class IsolationControls(BaseModel):
    """Per-control enforcement observation for one concrete execution.

    Every field answers: *was this control actually in force for this run?*
    ``True`` requires a named mechanism; the capability report prints it.
    """

    model_config = ConfigDict(extra="forbid")

    non_root_user: bool = False
    read_only_rootfs: bool = False
    tmpfs_workdir: bool = False
    network_isolation: bool = False
    memory_limit: bool = False
    cpu_limit: bool = False
    pids_limit: bool = False
    disk_limit: bool = False
    wall_clock_timeout: bool = False
    output_cap: bool = False
    no_host_mount: bool = False
    no_docker_socket: bool = False
    no_host_env_secrets: bool = False
    image_by_digest: bool = False
    module_allowlist: bool = False

    def enforced(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value]

    def missing(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if not value]

    @property
    def fully_enforced(self) -> bool:
        return all(self.model_dump().values())


class IsolationMode(str, enum.Enum):
    """Machine-readable verdict on the *strength* of isolation for a run."""

    CONTAINER = "container"  #: container runtime applied; controls per observation
    LOCAL_UNISOLATED = "local-unisolated"  #: process on the host; NO OS-level isolation
    REFUSED = "refused"  #: policy demanded real isolation; nothing was executed
    ERROR = "error"  #: backend failure before/around execution (fail closed)


# --------------------------------------------------------------------------------------
# Manifest / result
# --------------------------------------------------------------------------------------


class Artifact(BaseModel):
    """A file the executed code produced inside the run workspace."""

    model_config = ConfigDict(extra="forbid")

    path: str  #: workspace-relative, POSIX separators
    size_bytes: int
    sha256: str


class OutputTruncation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    truncated: bool = False
    limit_bytes: int = 0
    stdout_bytes_seen: int = 0
    stderr_bytes_seen: int = 0
    cap_enforced_by: str = ""

    @property
    def any_truncated(self) -> bool:
        return self.truncated


class EffectiveLimits(BaseModel):
    """What was really applied. ``UNBOUNDED`` means the backend could not bound it."""

    model_config = ConfigDict(extra="forbid")

    cpu_limit: float | str = "UNBOUNDED"
    memory_limit_mb: int | str = "UNBOUNDED"
    pids_limit: int | str = "UNBOUNDED"
    disk_limit_mb: int | str = "UNBOUNDED"
    wall_time_ms: int | str = "UNBOUNDED"
    output_limit_bytes: int | str = "UNBOUNDED"
    network: str = "UNBOUNDED"


class ExecutionManifest(BaseModel):
    """Mandatory provenance record attached to every sandbox result.

    Content hashes are computed from content only -- never from timestamps -- so
    the same (code, inputs, seed, lock) yields identical hashes across runs.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_version: str = "1.0"
    run_id: str
    experiment_id: str | None = None

    # content identity
    code_hash: str
    input_hash: str
    output_hash: str
    dependency_lock_hash: str
    artifact_hash: str

    # environment identity
    image: str | None = None
    image_digest: str | None = None
    python_version: str
    sandbox_version: str
    platform: str

    # execution facts
    seed: int
    executed_at: str = Field(description="ISO-8601 UTC, deliberately NOT part of any hash")
    duration_ms: int
    exit_code: int | None
    exec_status: Literal["pass", "fail", "timeout", "refused", "error"]
    timed_out: bool = False

    # isolation facts
    backend: str
    isolation_mode: IsolationMode
    isolation_controls_enforced: IsolationControls
    controls_enforced_summary: dict[str, bool] = Field(
        default_factory=dict,
        description="flattened view of isolation_controls_enforced for queue consumers",
    )
    unenforced_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="control name -> why it could not be enforced",
    )
    effective_limits: EffectiveLimits
    truncation: OutputTruncation = Field(default_factory=OutputTruncation)
    warnings: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    runtime_facts: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "backend observations for this run: container name, bind mounts, network mode, "
            "tmpfs args, runner sidecar contents. Diagnostics only -- it never carries payload "
            "bytes or environment values."
        ),
    )

    def public_summary(self) -> dict[str, Any]:
        """Small dict for logs/queue payloads. Never includes code or secrets."""
        return {
            "run_id": self.run_id,
            "exec_status": self.exec_status,
            "exit_code": self.exit_code,
            "code_hash": self.code_hash,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "dependency_lock_hash": self.dependency_lock_hash,
            "image_digest": self.image_digest,
            "isolation_mode": self.isolation_mode.value,
            "isolation_fully_enforced": self.isolation_controls_enforced.fully_enforced,
            "unenforced_controls": sorted(self.unenforced_reasons),
            "duration_ms": self.duration_ms,
            "executed_at": self.executed_at,
        }


class ExecutionResult(BaseModel):
    """Backend return value. ``manifest`` is never optional -- that is the point."""

    model_config = ConfigDict(extra="forbid")

    manifest: ExecutionManifest
    stdout: str
    stderr: str
    artifacts: list[Artifact] = Field(default_factory=list)
    result_repr: str = ""
    error: str | None = None
    argv: list[str] = Field(default_factory=list, description="exact command executed")

    @property
    def ok(self) -> bool:
        return self.manifest.exec_status == "pass"


class CapabilityReport(BaseModel):
    """Answer to: "what isolation is actually in force right now?".

    ``controls_enforceable`` is what the backend *can* do in this environment;
    ``controls_actually_in_force_for_policy`` is what a given policy would get.
    Both are booleans with named mechanisms -- no adjectives.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    sandbox_version: str
    backend_requested: str
    backend_selected: str | None
    backend_available: bool
    backend_unavailable_reason: str | None = None
    isolation_mode_if_run_now: IsolationMode
    controls_enforceable: dict[str, bool]
    control_mechanisms: dict[str, str]
    unenforced_reasons: dict[str, str]
    effective_limits_if_run_now: EffectiveLimits
    policy: SandboxPolicy
    would_refuse_current_policy: bool
    refusal_reason: str | None = None
    runtime_facts: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
