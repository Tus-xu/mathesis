"""Standalone Valkey Stream consumer for sandbox jobs.

Run with::

    python -m sandbox.worker --once
    python -m sandbox.worker            # loop until interrupted

Contract (frozen by ``deploy/registry.yml``, owned by WP-H -- this file only consumes
it): Valkey Stream on port 6380, stream prefix ``strm:rv8300``, consumer group
``rv8300-workers``, dedupe scope ``rv8300``, object prefix ``rvs/``.

FAIL-CLOSED RULE, and the reason this module exists as its own entry point:

    If the stream/consumer group cannot be reached, the worker raises
    :class:`~sandbox.errors.RuntimeUnavailableError` and exits non-zero. It never
    falls back to executing jobs in-process, and it never reports success. A queue
    outage must look like an outage.

Claims are acknowledged only after the handler returns, so a crash mid-job leaves the
entry pending for redelivery (at-least-once). Because every sandbox job is
content-addressed and produces no external side effects, redelivery is safe -- which is
what lets ``JobHandlerSpec.idempotent`` be ``True`` rather than aspirational.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    ENV_VALKEY_URL,
    JOB_STREAM,
    QUEUE_CONSUMER_GROUP,
    QUEUE_DEDUPE_SCOPE,
    QUEUE_PORT,
    RESULT_STREAM,
)
from .errors import RuntimeUnavailableError
from .jobhandler import JobEnvelope, JobResult, SandboxRunHandler

DEFAULT_URL = f"redis://127.0.0.1:{QUEUE_PORT}/0"


@dataclass
class WorkerStats:
    read: int = 0
    executed: int = 0
    acked: int = 0
    failed: int = 0
    refused: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "read": self.read,
            "executed_jobs": self.executed,
            "acked": self.acked,
            "failed": self.failed,
            "refused": self.refused,
            "error_count": len(self.errors),
            "errors": self.errors[-5:],
        }


class ValkeyStreamConsumer:
    """Minimal durable consumer. Deliberately small: WP-H owns the real runtime."""

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        stream: str = JOB_STREAM,
        group: str = QUEUE_CONSUMER_GROUP,
        consumer: str = "sandbox-worker-1",
        handler: SandboxRunHandler | None = None,
        dedupe_scope: str = QUEUE_DEDUPE_SCOPE,
        result_stream: str = RESULT_STREAM,
    ) -> None:
        self.url = url
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.handler = handler or SandboxRunHandler()
        self.dedupe_scope = dedupe_scope
        self.result_stream = result_stream
        self._client: Any = None

    # -- connection ---------------------------------------------------------------------

    def client(self):  # noqa: ANN201
        if self._client is not None:
            return self._client
        try:
            import redis  # imported lazily so `import sandbox` never needs a server
        except ImportError as exc:  # pragma: no cover
            raise RuntimeUnavailableError(
                "the 'redis' client package is not installed; the worker cannot connect to "
                "Valkey. Install math-arena-sandbox with its declared dependencies."
            ) from exc
        try:
            self._client = redis.Redis.from_url(
                self.url,
                socket_connect_timeout=float(os.environ.get("SANDBOX_VALKEY_CONNECT_TIMEOUT", "5")),
                socket_timeout=float(os.environ.get("SANDBOX_VALKEY_SOCKET_TIMEOUT", "30")),
                decode_responses=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeUnavailableError(
                f"could not construct a Valkey client for {self.url!r}: {exc}"
            ) from exc
        return self._client

    def fail_if_unavailable(self, timeout_s: float = 5.0) -> None:
        """Ping and ensure the group exists. Raises rather than degrading."""
        client = self.client()
        try:
            client.ping()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeUnavailableError(
                f"Valkey at {self.url!r} is unreachable ({type(exc).__name__}: {exc}). "
                "The worker refuses to execute jobs without a queue and will not fall back "
                "to in-process execution."
            ) from exc
        try:
            client.xgroup_create(name=self.stream, groupname=self.group, id="$", mkstream=True)
        except Exception as exc:  # noqa: BLE001
            # BUSYGROUP means the group already exists -- that is the normal case.
            if "BUSYGROUP" not in str(exc):
                raise RuntimeUnavailableError(
                    f"could not ensure consumer group {self.group!r} on stream {self.stream!r}: {exc}"
                ) from exc

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    # -- consumption --------------------------------------------------------------------

    def read_batch(self, *, count: int = 1, block_ms: int = 5000) -> list[tuple[str, dict]]:
        client = self.client()
        try:
            response = client.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={self.stream: ">"},
                count=count,
                block=block_ms,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeUnavailableError(
                f"reading from stream {self.stream!r} failed: {type(exc).__name__}: {exc}. "
                "Worker stops here rather than executing anything without a queue."
            ) from exc
        entries: list[tuple[str, dict]] = []
        for _stream, messages in response or []:
            for message_id, fields in messages:
                entries.append((_decode(message_id), _decode_fields(fields)))
        return entries

    def ack(self, message_id: str) -> None:
        self.client().xack(self.stream, self.group, message_id)

    def publish_result(self, result: JobResult) -> None:
        """Best-effort result publish. Failure is recorded, never silently dropped.

        The authoritative delivery is the stream entry staying unacked; this is a
        convenience channel for WP-H.
        """
        try:
            self.client().xadd(
                self.result_stream,
                {
                    "job_id": result.job_id,
                    "kind": result.kind,
                    "status": result.status,
                    "idempotency_key": result.idempotency_key,
                    "manifest": json.dumps(result.manifest, sort_keys=True, default=str),
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeUnavailableError(
                f"could not publish the result for job {result.job_id!r} to "
                f"{self.result_stream!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def process_one(self, message_id: str, fields: dict) -> JobResult:
        envelope = _envelope_from_fields(fields, message_id)
        result = self.handler.handle(envelope)
        self.publish_result(result)
        return result

    def run(self, *, once: bool = False, count: int = 1, block_ms: int = 5000) -> WorkerStats:
        """Consume until stopped. Raises :class:`RuntimeUnavailableError` on queue loss."""
        stats = WorkerStats()
        self.fail_if_unavailable()
        stopping = {"flag": False}

        def _stop(_signum, _frame):  # noqa: ANN001
            stopping["flag"] = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass

        while not stopping["flag"]:
            entries = self.read_batch(count=count, block_ms=block_ms)
            stats.read += len(entries)
            for message_id, fields in entries:
                result = self.process_one(message_id, fields)
                stats.executed += 1
                if result.status == "ok":
                    self.ack(message_id)
                    stats.acked += 1
                elif result.status == "refused":
                    # A refusal is a terminal verdict, not a transient failure: ack it so
                    # it is not redelivered forever, and let the caller see the manifest.
                    self.ack(message_id)
                    stats.refused += 1
                elif result.retryable:
                    stats.failed += 1
                    stats.errors.append(f"{result.job_id}: {result.error}")
                else:
                    self.ack(message_id)
                    stats.failed += 1
                    stats.errors.append(f"{result.job_id}: {result.error}")
            if once:
                break
            if not entries:
                time.sleep(0.2)
        return stats


def _decode(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _decode_fields(fields: dict) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in fields.items():
        name = _decode(key)
        text = _decode(value)
        try:
            decoded[name] = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            decoded[name] = text
    return decoded


def _envelope_from_fields(fields: dict, message_id: str) -> JobEnvelope:
    """Accept either a flattened envelope or a single JSON blob under ``payload``.

    The dual form is deliberate: WP-H may store the request in the object store
    (``rvs/``) and pass only a reference, in which case ``payload`` is the request
    JSON and ``payload_ref`` names the object.
    """
    payload = fields.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeUnavailableError(
                f"job {message_id} carries an unparseable payload: {exc}"
            ) from exc
    if not isinstance(payload, dict):
        payload = {}
    return JobEnvelope(
        job_id=str(fields.get("job_id") or message_id),
        kind=str(fields.get("kind") or fields.get("type") or "sandbox.run_experiment"),
        attempt=int(fields.get("attempt") or 1),
        dedupe_scope=str(fields.get("dedupe_scope") or QUEUE_DEDUPE_SCOPE),
        idempotency_key=str(fields.get("idempotency_key") or ""),
        lease_token=fields.get("lease_token"),
        fencing_token=fields.get("fencing_token"),
        trace_id=fields.get("trace_id"),
        payload_ref=fields.get("payload_ref"),
        payload=payload,
        enqueued_at=fields.get("enqueued_at"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sandbox.worker",
        description=(
            "Sandbox worker: consume experiment jobs from the research-vnext Valkey stream. "
            "Fails closed (non-zero exit, no execution) when the queue is unreachable."
        ),
    )
    parser.add_argument("--url", default=os.environ.get(ENV_VALKEY_URL, DEFAULT_URL))
    parser.add_argument("--stream", default=JOB_STREAM)
    parser.add_argument("--group", default=QUEUE_CONSUMER_GROUP)
    parser.add_argument("--consumer", default=f"sandbox-worker-{os.getpid()}")
    parser.add_argument("--once", action="store_true", help="process available messages then exit")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--block-ms", type=int, default=5000)
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true", help="print a JSON status line")
    args = parser.parse_args(argv)

    consumer = ValkeyStreamConsumer(
        url=args.url,
        stream=args.stream,
        group=args.group,
        consumer=args.consumer,
    )
    try:
        consumer.fail_if_unavailable(timeout_s=args.connect_timeout)
        stats = consumer.run(once=args.once, count=args.count, block_ms=args.block_ms)
    except RuntimeUnavailableError as exc:
        report = {
            "status": "failed",
            "reason": "queue_unavailable",
            "detail": str(exc),
            "stream": args.stream,
            "group": args.group,
            "executed_jobs": 0,
            "in_process_fallback": False,
        }
        print(json.dumps(report, sort_keys=True) if args.json else str(exc), file=sys.stderr)
        consumer.close()
        return 2
    except Exception as exc:  # noqa: BLE001
        report = {
            "status": "failed",
            "reason": "worker_error",
            "detail": f"{type(exc).__name__}: {exc}",
            "executed_jobs": 0,
            "in_process_fallback": False,
        }
        print(json.dumps(report, sort_keys=True) if args.json else report["detail"], file=sys.stderr)
        consumer.close()
        return 3
    finally:
        pass

    report = {"status": "ok", "stream": args.stream, "group": args.group, **stats.as_dict()}
    print(json.dumps(report, sort_keys=True) if args.json else json.dumps(report, sort_keys=True))
    consumer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["DEFAULT_URL", "ValkeyStreamConsumer", "WorkerStats", "main"]
