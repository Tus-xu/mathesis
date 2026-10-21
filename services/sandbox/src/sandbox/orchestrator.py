"""Orchestrator: policy gate -> verified payload -> backend -> mandatory manifest.

Every public entry point in this package funnels through :func:`run_experiment`, so
there is exactly one place that can produce an execution result -- and exactly one
place that can lie about it.

Order of operations (each step fails closed):

1. normalise the dependency lock (an unspecified image is filled in from the
   backend's real image, so ``dependency_lock_hash`` cannot quietly hash an empty
   lock and read as "no dependencies");
2. select a backend -- refuses when isolation is demanded but unavailable;
3. resolve + digest-verify the payload; a mismatch aborts before execution;
4. execute;
5. build the manifest from *observed* values only;
6. return.

``execution_mode=REFUSED`` results carry ``exec_status="refused"``, all-False
controls, and never a ``pass``.
"""

from __future__ import annotations

import platform
import time

from . import __version__
from .backends import BackendRun, IsolationBackend, enforce_policy, select_backend
from .errors import IsolationRefusedError, PayloadError, SandboxError
from .hashing import (
    artifact_hash,
    code_hash,
    dependency_lock_hash,
    input_hash,
    output_hash,
)
from .models import (
    Artifact,
    ExecutionManifest,
    ExecutionRequest,
    ExecutionResult,
    IsolationControls,
    IsolationMode,
    utc_now_iso,
)
from .payloads import ResolvedPayload, inputs_hash_payload, resolve

#: Workspace-internal files that are harness bookkeeping, never user artifacts.
_INTERNAL_ARTIFACT_PREFIXES = ("python/", "out/", "inputs/")


def run_experiment(
    req: ExecutionRequest,
    *,
    backend: IsolationBackend | None = None,
) -> ExecutionResult:
    """Execute ``req`` and return a result carrying a complete manifest.

    Raises nothing for user-code failure (that is a normal ``fail``/``timeout``
    result) and nothing for policy refusal (that is a ``refused`` result). Raises
    :class:`~sandbox.errors.PayloadError` for an unverified payload, because
    executing unverified bytes would invalidate every hash in the manifest.
    """
    lock = _normalise_lock(req, backend)
    # The policy gate runs unconditionally, even when a caller injects a backend
    # instance directly. Otherwise a caller holding a LocalBackend could bypass the
    # refusal by passing it in -- the gate must not depend on how selection happened.
    try:
        if backend is not None:
            enforce_policy(backend, req.policy)
            selected = backend
        else:
            selected = select_backend(req.backend, policy=req.policy)
    except IsolationRefusedError as exc:
        return _refused_result(req, exc, lock)
    except SandboxError as exc:
        return _error_result(req, str(exc), lock)

    payload = resolve(req)  # raises PayloadError -- deliberately not caught here

    started = time.monotonic()
    executed_at = utc_now_iso()
    try:
        run = selected.run(req, payload)
    except SandboxError as exc:
        return _error_result(req, f"{type(exc).__name__}: {exc}", lock)
    except Exception as exc:  # noqa: BLE001 - a backend crash must still yield a manifest
        return _error_result(
            req,
            f"backend {selected.name} raised {type(exc).__name__}: {exc}",
            lock,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    duration_ms = int((time.monotonic() - started) * 1000)

    if run.image and lock.image in (None, ""):
        lock = lock.model_copy(update={"image": run.image, "format": "image-pin"})

    artifacts = _user_artifacts(run.artifacts)
    manifest = _build_manifest(
        req,
        run,
        payload,
        lock_hash=dependency_lock_hash(lock),
        artifacts=artifacts,
        executed_at=executed_at,
        duration_ms=duration_ms,
    )
    return ExecutionResult(
        manifest=manifest,
        stdout=run.stdout,
        stderr=run.stderr,
        artifacts=artifacts,
        result_repr=run.stdout.strip()[-200:] if run.stdout.strip() else "",
        error=None if run.exit_code == 0 else (run.stderr.strip()[-400:] or None),
        argv=run.argv,
    )


# --------------------------------------------------------------------------------------


def _normalise_lock(req: ExecutionRequest, backend: IsolationBackend | None):
    """Fill the lock's image from the backend default when the caller gave none.

    Only the dependency-lock *model* is touched here; the manifest's recorded value
    is re-derived from the backend's actual image after the run when possible.
    """
    lock = req.dependency_lock
    if lock.image or lock.content:
        return lock
    image = req.sandbox_image
    if image is None and backend is not None:
        image = getattr(backend, "image", None)
    if image is None and req.backend in {"auto", "container"}:
        from .constants import DEFAULT_SANDBOX_IMAGE

        image = DEFAULT_SANDBOX_IMAGE
    if image is None:
        return lock
    return lock.model_copy(update={"image": image, "format": "image-pin"})


def _user_artifacts(artifacts: list[Artifact]) -> list[Artifact]:
    """Drop harness bookkeeping so ``artifact_hash`` describes caller-visible output."""
    kept = [
        item
        for item in artifacts
        if not any(item.path.startswith(prefix) for prefix in _INTERNAL_ARTIFACT_PREFIXES)
        and item.path not in {"experiment.py"}
    ]
    return sorted(kept, key=lambda item: item.path)


def _build_manifest(
    req: ExecutionRequest,
    run: BackendRun,
    payload: ResolvedPayload,
    *,
    lock_hash: str,
    artifacts: list[Artifact],
    executed_at: str,
    duration_ms: int,
) -> ExecutionManifest:
    controls = run.controls
    summary = controls.model_dump()
    reasons = dict(run.unenforced_reasons)
    # Every unenforced control must have a reason; if a backend forgot one, say so
    # rather than emitting an unexplained False.
    for name, value in summary.items():
        if not value and name not in reasons:
            reasons[name] = "backend reported this control as not enforced without a stated reason"
    if req.policy.require_real_isolation and not controls.fully_enforced:
        run.warnings.append(
            "policy.require_real_isolation=True yet "
            f"{len(controls.missing())} controls are not enforced for this run: "
            + ", ".join(sorted(controls.missing()))
        )

    return ExecutionManifest(
        run_id=req.run_id,
        experiment_id=req.experiment_id,
        code_hash=code_hash(payload.code),
        input_hash=input_hash(inputs_hash_payload(payload)),
        output_hash=output_hash(run.stdout, run.stderr),
        dependency_lock_hash=lock_hash,
        artifact_hash=artifact_hash(artifacts),
        image=run.image,
        image_digest=run.image_digest,
        python_version=run.python_version or platform.python_version(),
        sandbox_version=__version__,
        platform=run.platform or platform.platform(),
        seed=req.seed,
        executed_at=executed_at,
        duration_ms=duration_ms,
        exit_code=run.exit_code,
        exec_status=run.exec_status,  # type: ignore[arg-type]
        timed_out=run.timed_out,
        backend=run.backend_name,
        isolation_mode=run.isolation_mode,
        isolation_controls_enforced=controls,
        controls_enforced_summary=summary,
        unenforced_reasons=reasons,
        effective_limits=run.effective_limits,
        truncation=run.truncation,
        warnings=list(run.warnings),
        labels=req.labels,
        runtime_facts=dict(run.runtime_facts),
    )


def _refused_result(req: ExecutionRequest, exc: IsolationRefusedError, lock) -> ExecutionResult:  # noqa: ANN001
    """No code ran. Nothing is claimed. ``exec_status`` is ``refused``, not ``pass``."""
    controls = IsolationControls()
    return ExecutionResult(
        manifest=ExecutionManifest(
            run_id=req.run_id,
            experiment_id=req.experiment_id,
            code_hash=code_hash(req.code or ""),
            input_hash=input_hash([item.model_dump() for item in req.inputs]),
            output_hash=output_hash("", ""),
            dependency_lock_hash=dependency_lock_hash(lock),
            artifact_hash=artifact_hash([]),
            image=None,
            image_digest=None,
            python_version=platform.python_version(),
            sandbox_version=__version__,
            platform=platform.platform(),
            seed=req.seed,
            executed_at=utc_now_iso(),
            duration_ms=0,
            exit_code=None,
            exec_status="refused",
            timed_out=False,
            backend=exc.backend,
            isolation_mode=IsolationMode.REFUSED,
            isolation_controls_enforced=controls,
            controls_enforced_summary=controls.model_dump(),
            unenforced_reasons={
                name: f"nothing executed: {exc}" for name in IsolationControls.model_fields
            },
            effective_limits=_no_limits(),
            warnings=[
                "REFUSED: policy demanded real isolation and the selected backend cannot provide "
                "it. No code was executed and no output was produced.",
                str(exc),
            ],
            labels=req.labels,
        ),
        stdout="",
        stderr="",
        artifacts=[],
        error=str(exc),
        argv=[],
    )


def _error_result(
    req: ExecutionRequest,
    message: str,
    lock,  # noqa: ANN001
    duration_ms: int = 0,
) -> ExecutionResult:
    """Backend failure. Fail closed: no execution claim, no pass."""
    controls = IsolationControls()
    return ExecutionResult(
        manifest=ExecutionManifest(
            run_id=req.run_id,
            experiment_id=req.experiment_id,
            code_hash=code_hash(req.code or ""),
            input_hash=input_hash([item.model_dump() for item in req.inputs]),
            output_hash=output_hash("", ""),
            dependency_lock_hash=dependency_lock_hash(lock),
            artifact_hash=artifact_hash([]),
            image=getattr(lock, "image", None),
            image_digest=None,
            python_version=platform.python_version(),
            sandbox_version=__version__,
            platform=platform.platform(),
            seed=req.seed,
            executed_at=utc_now_iso(),
            duration_ms=duration_ms,
            exit_code=None,
            exec_status="error",
            timed_out=False,
            backend=req.backend,
            isolation_mode=IsolationMode.ERROR,
            isolation_controls_enforced=controls,
            controls_enforced_summary=controls.model_dump(),
            unenforced_reasons={name: f"nothing executed: {message}" for name in controls.model_fields},
            effective_limits=_no_limits(),
            warnings=["ERROR: the sandbox backend failed. No isolation claim is made.", message],
            labels=req.labels,
        ),
        stdout="",
        stderr="",
        artifacts=[],
        error=message,
        argv=[],
    )


def _no_limits():
    from .models import EffectiveLimits

    return EffectiveLimits()


__all__ = ["PayloadError", "run_experiment"]
