"""Typed job-handler interface for the durable Job Runtime (WP-H, research-vnext).

This module is intentionally standalone: it imports **nothing** from
``services/research-vnext`` and assumes nothing about its internals. It exposes a
description (``JobHandlerSpec``) that a runtime can register, plus an envelope
(``JobEnvelope``) that matches the queue conventions frozen in
``deploy/registry.yml`` (``research-vnext-8300``: Valkey Stream on port 6380,
stream prefix ``strm:rv8300``, consumer group ``rv8300-workers``, object prefix
``rvs/``, dedupe scope ``rv8300``).

``idempotency_key`` is the *dedupe* contract: the same key must never produce two
executions. For a sandbox run the natural key is
``sha256(code_hash | input_hash | seed | dependency_lock_hash | effective limits)``
-- see :meth:`SandboxRunHandler.idempotency_key`, which derives it from the
manifest hashes so two identical submissions collapse to one execution.

Side effects are declared, not implied: this handler writes nothing outside its
per-run workspace (which is deleted), so ``side_effects`` is empty. That is a
statement a reviewer can check against the code, which is the point of declaring it.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .constants import (
    JOB_STREAM,
    QUEUE_DEDUPE_SCOPE,
    QUEUE_OBJECT_PREFIX,
    RESULT_STREAM,
    SANDBOX_RUN_JOB_KIND,
)
from .hashing import sha256_canonical_json
from .models import ExecutionRequest, ExecutionResult, ResourceLimits, SandboxPolicy

JobStatus = Literal["ok", "failed", "timeout", "refused", "error"]


class JobEnvelope(BaseModel):
    """Wire envelope. Field names are fixed here so WP-H can pin them on its side."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    kind: str = SANDBOX_RUN_JOB_KIND
    attempt: int = Field(default=1, ge=1)
    dedupe_scope: str = QUEUE_DEDUPE_SCOPE
    idempotency_key: str
    lease_token: str | None = None
    fencing_token: int | None = None
    trace_id: str | None = None
    payload_ref: str | None = Field(
        default=None, description=f"object key under '{QUEUE_OBJECT_PREFIX}' holding the request"
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    enqueued_at: str | None = None

    def request(self) -> ExecutionRequest:
        """Parse the payload into an :class:`ExecutionRequest` (raises on bad input)."""
        return ExecutionRequest.model_validate(self.payload)


class JobResult(BaseModel):
    """What a handler returns to the runtime. Always carries the manifest."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    kind: str = SANDBOX_RUN_JOB_KIND
    status: JobStatus
    idempotency_key: str
    manifest: dict[str, Any]
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    #: Declared so the runtime knows whether re-delivery is safe.
    idempotent_replay: bool = True
    produced_objects: list[str] = Field(default_factory=list)

    @property
    def retryable(self) -> bool:
        """Only backend/infrastructure errors are retryable; refusals never are."""
        return self.status == "error"


class JobHandlerSpec(BaseModel):
    """Registration descriptor. A runtime can consume this without importing our code."""

    model_config = ConfigDict(extra="forbid")

    kind: str = SANDBOX_RUN_JOB_KIND
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    #: Hard ceiling the runtime must honour in addition to the per-run limit.
    timeout_ms: int
    max_attempts: int = 2
    idempotent: bool = True
    idempotency_key_fields: list[str]
    side_effects: list[str] = Field(default_factory=list)
    concurrency: int = 1
    stream: str = JOB_STREAM
    result_stream: str = RESULT_STREAM
    required_isolation: bool = True
    description: str = ""


@runtime_checkable
class JobHandler(Protocol):
    """What the runtime calls. Deliberately minimal and transport-free."""

    spec: JobHandlerSpec

    def handle(self, envelope: JobEnvelope) -> JobResult:
        """Execute one job. Must never raise for a job-level failure -- return a
        ``failed``/``refused``/``timeout`` result instead. Raising means "the
        runtime should treat this as an infrastructure error"."""


class SandboxRunHandler:
    """The sandbox's implementation of :class:`JobHandler`."""

    kind = SANDBOX_RUN_JOB_KIND

    def __init__(self) -> None:
        self.spec = JobHandlerSpec(
            kind=self.kind,
            input_schema=_request_json_schema(),
            output_schema=_result_json_schema(),
            timeout_ms=600_000,
            max_attempts=2,
            idempotent=True,
            idempotency_key_fields=[
                "code_hash",
                "input_hash",
                "seed",
                "dependency_lock_hash",
                "limits",
                "backend",
            ],
            side_effects=[],
            concurrency=1,
            required_isolation=True,
            description=(
                "Execute one research experiment inside the isolation sandbox. Writes nothing "
                "outside its per-run workspace; returns the full provenance manifest. Refuses "
                "to run when real isolation is unavailable."
            ),
        )

    # -- idempotency --------------------------------------------------------------------

    def idempotency_key(self, req: ExecutionRequest) -> str:
        """Deterministic key over content only (never timestamps, never run_id).

        Two submissions of the same code, inputs, seed, lock and limits collapse to
        one execution -- which is the whole point of a dedupe key. ``run_id`` is
        deliberately excluded: including it would defeat deduplication.
        """
        return sha256_canonical_json(
            {
                "kind": self.kind,
                "code": req.code if req.code is not None else (req.code_ref.sha256 if req.code_ref else ""),
                "inputs": sorted(
                    (
                        {
                            "name": item.name,
                            "sha256": item.sha256
                            or (sha256_canonical_json(item.inline) if item.inline is not None else None),
                            "object_ref": item.object_ref,
                        }
                        for item in req.inputs
                    ),
                    key=lambda item: item["name"],
                ),
                "seed": req.seed,
                "params": req.params,
                "dependency_lock": req.dependency_lock.model_dump(),
                "limits": req.limits.model_dump(),
                "backend": req.backend,
                "network": req.policy.network.value,
            }
        )

    # -- execution ----------------------------------------------------------------------

    def handle(self, envelope: JobEnvelope) -> JobResult:
        from .orchestrator import run_experiment

        try:
            req = envelope.request()
        except Exception as exc:  # noqa: BLE001 - schema-invalid input is not retryable
            return JobResult(
                job_id=envelope.job_id,
                status="failed",
                idempotency_key=envelope.idempotency_key,
                manifest={},
                error=f"SCHEMA_INVALID: {type(exc).__name__}: {exc}",
            )

        result: ExecutionResult = run_experiment(req)
        status = _status_for(result)
        return JobResult(
            job_id=envelope.job_id,
            status=status,
            idempotency_key=envelope.idempotency_key or self.idempotency_key(req),
            manifest=result.manifest.model_dump(mode="json"),
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.error,
            idempotent_replay=True,
            produced_objects=[],
        )


def _status_for(result: ExecutionResult) -> JobStatus:
    mapping: dict[str, JobStatus] = {
        "pass": "ok",
        "fail": "failed",
        "timeout": "timeout",
        "refused": "refused",
        "error": "error",
    }
    return mapping[result.manifest.exec_status]


def _request_json_schema() -> dict[str, Any]:
    return ExecutionRequest.model_json_schema()


def _result_json_schema() -> dict[str, Any]:
    return JobResult.model_json_schema()


def new_envelope(req: ExecutionRequest, *, job_id: str | None = None) -> JobEnvelope:
    """Convenience for tests and for callers that want to enqueue a sandbox job."""
    handler = SandboxRunHandler()
    return JobEnvelope(
        job_id=job_id or f"sandbox-{uuid.uuid4().hex[:16]}",
        idempotency_key=handler.idempotency_key(req),
        payload=req.model_dump(mode="json"),
    )


def policy_from_env(default: SandboxPolicy | None = None) -> SandboxPolicy:
    """Building a policy from environment is explicit and documented in one place.

    ``SANDBOX_REQUIRE_REAL_ISOLATION=0`` is the *only* way to accept the local
    backend, and the worker logs a warning when it sees it.
    """
    import os

    from .constants import ENV_REQUIRE_REAL_ISOLATION

    policy = default or SandboxPolicy()
    raw = os.environ.get(ENV_REQUIRE_REAL_ISOLATION)
    if raw is None:
        return policy
    truthy = raw.strip().lower() in {"1", "true", "yes", "on"}
    return policy.model_copy(update={"require_real_isolation": truthy})


def default_limits_from_env(base: ResourceLimits | None = None) -> ResourceLimits:
    return base or ResourceLimits()


__all__ = [
    "JobEnvelope",
    "JobHandler",
    "JobHandlerSpec",
    "JobResult",
    "JobStatus",
    "SandboxRunHandler",
    "new_envelope",
    "policy_from_env",
]
