"""Job-handler interface and worker integration (fails closed).

WP-H owns the Job Runtime; this package owns only the handler side of the contract.
These tests assert the interface is typed and complete, and -- importantly -- that the
standalone worker **refuses to run** when Valkey is unreachable instead of quietly
executing jobs in-process.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from sandbox.constants import (
    JOB_STREAM,
    QUEUE_CONSUMER_GROUP,
    QUEUE_DEDUPE_SCOPE,
    QUEUE_PORT,
    QUEUE_STREAM_PREFIX,
    SANDBOX_RUN_JOB_KIND,
)
from sandbox.errors import RuntimeUnavailableError
from sandbox.jobhandler import JobEnvelope, SandboxRunHandler, new_envelope, policy_from_env

from .conftest import make_request

CODE = "print('job-ok')\n"


def test_queue_conventions_match_deploy_registry() -> None:
    """These values are frozen by deploy/registry.yml; re-inventing them is a defect."""
    assert QUEUE_PORT == 6380
    assert QUEUE_STREAM_PREFIX == "strm:rv8300"
    assert QUEUE_CONSUMER_GROUP == "rv8300-workers"
    assert QUEUE_DEDUPE_SCOPE == "rv8300"
    assert JOB_STREAM == "strm:rv8300:jobs"


def test_handler_spec_is_complete_and_declares_no_side_effects() -> None:
    handler = SandboxRunHandler()
    spec = handler.spec
    assert spec.kind == SANDBOX_RUN_JOB_KIND
    assert spec.timeout_ms > 0
    assert spec.idempotent is True
    assert spec.max_attempts == 2
    assert spec.required_isolation is True
    assert spec.side_effects == [], "this handler must declare no side effects it cannot prove"
    assert spec.input_schema["title"] == "ExecutionRequest"
    assert spec.output_schema["title"] == "JobResult"
    assert "code_hash" in spec.idempotency_key_fields
    assert spec.stream == JOB_STREAM


def test_idempotency_key_is_content_addressed_and_ignores_run_id() -> None:
    handler = SandboxRunHandler()
    req_a = make_request(CODE, run_id="job-a", backend="local")
    req_b = make_request(CODE, run_id="job-b", backend="local")
    assert handler.idempotency_key(req_a) == handler.idempotency_key(req_b), (
        "two submissions of identical content must collapse to one execution"
    )
    req_c = make_request(CODE + "print('more')\n", run_id="job-a", backend="local")
    assert handler.idempotency_key(req_a) != handler.idempotency_key(req_c)
    seeded = make_request(CODE, run_id="job-a", backend="local", seed=7)
    assert handler.idempotency_key(req_a) != handler.idempotency_key(seeded)


def test_envelope_round_trips_into_an_execution_request() -> None:
    req = make_request(CODE, run_id="job-envelope", backend="local", require_real_isolation=False)
    envelope = new_envelope(req)
    assert envelope.kind == SANDBOX_RUN_JOB_KIND
    assert envelope.dedupe_scope == QUEUE_DEDUPE_SCOPE
    parsed = envelope.request()
    assert parsed.run_id == "job-envelope"


def test_handler_returns_a_result_with_the_manifest_and_never_raises() -> None:
    handler = SandboxRunHandler()
    req = make_request("@#$ not python\n", run_id="job-bad", backend="local", require_real_isolation=False)
    envelope = JobEnvelope(
        job_id="j1", idempotency_key="k1", payload=req.model_dump(mode="json")
    )
    result = handler.handle(envelope)
    assert result.status == "failed"
    assert result.manifest["code_hash"]
    assert result.retryable is False

    broken = JobEnvelope(job_id="j2", idempotency_key="k2", payload={"run_id": "x"})
    invalid = handler.handle(broken)
    assert invalid.status == "failed"
    assert "SCHEMA_INVALID" in (invalid.error or "")


def test_refusal_propagates_as_a_non_retryable_status() -> None:
    handler = SandboxRunHandler()
    req = make_request(CODE, run_id="job-refused", backend="local", require_real_isolation=True)
    result = handler.handle(
        JobEnvelope(job_id="j3", idempotency_key="k3", payload=req.model_dump(mode="json"))
    )
    assert result.status == "refused"
    assert result.retryable is False
    assert result.manifest["isolation_mode"] == "refused"


def test_policy_from_env_only_relaxes_on_an_explicit_falsy_value(monkeypatch) -> None:
    monkeypatch.delenv("SANDBOX_REQUIRE_REAL_ISOLATION", raising=False)
    assert policy_from_env().require_real_isolation is True
    monkeypatch.setenv("SANDBOX_REQUIRE_REAL_ISOLATION", "0")
    assert policy_from_env().require_real_isolation is False
    monkeypatch.setenv("SANDBOX_REQUIRE_REAL_ISOLATION", "banana")
    assert policy_from_env().require_real_isolation is False, (
        "an unparseable value must not be treated as 'yes' silently; it is logged as a warning"
    )


def test_worker_fails_closed_when_valkey_is_unreachable() -> None:
    """No silent in-process fallback: the worker must raise and say why."""
    from sandbox.worker import ValkeyStreamConsumer

    consumer = ValkeyStreamConsumer(
        url="redis://127.0.0.1:6399/0",  # deliberately nothing listening here
        stream=JOB_STREAM,
        group=QUEUE_CONSUMER_GROUP,
        consumer="test-consumer",
        handler=SandboxRunHandler(),
    )
    with pytest.raises(RuntimeUnavailableError) as excinfo:
        consumer.fail_if_unavailable(timeout_s=2)
    assert "Valkey" in str(excinfo.value) or "redis" in str(excinfo.value)


def test_worker_cli_reports_failure_when_queue_is_down() -> None:
    """End-to-end: `python -m sandbox.worker --once` must exit non-zero, not run jobs."""
    probe = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "sandbox.worker",
            "--once",
            "--url",
            "redis://127.0.0.1:6399/0",
            "--stream",
            JOB_STREAM,
            "--group",
            QUEUE_CONSUMER_GROUP,
            "--consumer",
            "cli-test",
            "--connect-timeout",
            "2",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
    )
    assert probe.returncode != 0, f"worker should fail closed; stdout={probe.stdout!r}"
    combined = probe.stdout + probe.stderr
    payload = _extract_json(combined)
    assert payload is not None, f"worker must emit a machine-readable failure report: {combined!r}"
    assert payload["status"] == "failed"
    assert payload["reason"] == "queue_unavailable"
    assert payload["executed_jobs"] == 0, (
        "the worker must not have executed any job without a queue"
    )
    assert payload["in_process_fallback"] is False
    assert payload["stream"] == JOB_STREAM and payload["group"] == QUEUE_CONSUMER_GROUP


def _extract_json(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None
