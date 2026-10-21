"""Raw capability + isolation evidence collector.

Run from services/sandbox::

    .venv\\Scripts\\python.exe tools\\capability_evidence.py

Prints, for the current machine, exactly what isolation is in force -- and, for each
mandated control, which backend enforces it and by what mechanism. It also runs four
attack probes for real (network, memory hog, fork bomb, infinite loop, output flood)
and prints the observed outcome plus the manifest's honesty verdict for each.

Nothing here is simulated. If a backend cannot enforce a control, the output says so.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sandbox.backends import ContainerBackend, LocalBackend  # noqa: E402
from sandbox.capabilities import all_backends, mandate_matrix  # noqa: E402
from sandbox.models import (  # noqa: E402
    CapabilityReport,
    ResourceLimits,
    SandboxPolicy,
)
from sandbox.orchestrator import run_experiment  # noqa: E402

PROBES: dict[str, str] = {
    "network": (
        "import socket\n"
        "try:\n"
        "    s = socket.create_connection(('example.com', 80), timeout=10)\n"
        "    print('NETWORK_OK', s.getpeername())\n"
        "except Exception as exc:\n"
        "    print('NETWORK_FAILED', type(exc).__name__, exc)\n"
    ),
    "memory": ("chunks = []\nwhile True:\n    chunks.append(bytearray(32 * 1024 * 1024))\n"),
    "forkbomb": (
        "import os\n"
        "while True:\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        while True:\n"
        "            pass\n"
    ),
    "timeout": (
        "print('BEFORE_LOOP')\nflush_output()\nwhile True:\n    pass\n"
    ),
    "output_flood": (
        'line = "A" * 1000\n'
        "for _ in range(200):\n"
        "    print(line)\n"
        "flush_output()\n"
        'print("FLOOD_DONE")\n'
    ),
}

PROBE_LIMITS = {
    "network": ResourceLimits(wall_time_ms=20_000, output_limit_bytes=16 * 1024),
    "memory": ResourceLimits(memory_limit_mb=64, wall_time_ms=60_000, output_limit_bytes=8 * 1024),
    "forkbomb": ResourceLimits(
        pids_limit=32, memory_limit_mb=128, wall_time_ms=45_000, output_limit_bytes=8 * 1024
    ),
    "timeout": ResourceLimits(wall_time_ms=3000, output_limit_bytes=16 * 1024),
    "output_flood": ResourceLimits(wall_time_ms=30_000, output_limit_bytes=8192),
}


def main() -> int:
    from sandbox.models import ExecutionRequest

    payload = all_backends()
    print("=== 1. HOST / SELECTION ===")
    print(json.dumps(payload["host"] | {"selection": payload["selection_for_policy"]}, indent=2))
    print("\n=== 2. HONESTY SELF-CHECK ===")
    print(json.dumps(payload["honesty_check"], indent=2))

    reports = {
        name: CapabilityReport.model_validate(data) for name, data in payload["backends"].items()
    }
    matrix = mandate_matrix(reports)
    print("\n=== 3. MANDATE CONTROL MATRIX ===")
    print(json.dumps(matrix, indent=2, sort_keys=True))

    container = ContainerBackend()
    available, reason = container.is_available()
    print("\n=== 4. CONTAINER RUNTIME ===")
    print(
        json.dumps(
            {
                "available": available,
                "reason_if_not": reason,
                "server_version": container.server_version() if available else None,
                "image": container.image,
                "image_digest": container.resolve_image_digest(container.image)
                if available
                else None,
            },
            indent=2,
        )
    )
    if not available:
        print("container probes SKIPPED: " + str(reason))
        print("local-backend probes follow; they measure honesty, not isolation.")

    backend = container if available else LocalBackend()
    policy_allows_unisolated = not available
    print(f"\n=== 5. ATTACK PROBES (backend={backend.name}) ===")
    for name, code in PROBES.items():
        backend_kind = "container" if available else "local"
        req = ExecutionRequest(
            run_id=f"probe-{name}",
            code=code,
            params={},
            seed=0,
            limits=PROBE_LIMITS[name],
            policy=SandboxPolicy(
                require_real_isolation=not policy_allows_unisolated,
                enforce_module_allowlist=False,  # probe the isolation, not the guard
            ),
            backend=backend_kind,  # type: ignore[arg-type]
        )
        try:
            result = run_experiment(req, backend=backend)
        except Exception as exc:  # noqa: BLE001 - a probe failure is itself evidence
            print(f"\n--- {name}: RAISED {type(exc).__name__}: {exc}")
            continue
        manifest = result.manifest
        print(f"\n--- {name} ---")
        print(
            json.dumps(
                {
                    "exec_status": manifest.exec_status,
                    "exit_code": manifest.exit_code,
                    "timed_out": manifest.timed_out,
                    "isolation_mode": manifest.isolation_mode.value,
                    "wall_clock_timeout_enforced": manifest.isolation_controls_enforced.wall_clock_timeout,
                    "memory_limit_enforced": manifest.isolation_controls_enforced.memory_limit,
                    "pids_limit_enforced": manifest.isolation_controls_enforced.pids_limit,
                    "network_isolation_enforced": manifest.isolation_controls_enforced.network_isolation,
                    "output_limit_enforced": manifest.isolation_controls_enforced.output_cap,
                    "effective_limits": manifest.effective_limits.model_dump(),
                    "truncated": manifest.truncation.truncated,
                    "stdout_bytes_seen": manifest.truncation.stdout_bytes_seen,
                    "output_limit_bytes": manifest.truncation.limit_bytes,
                },
                indent=2,
            )
        )
        print("stdout[:400]: " + repr(result.stdout[:400]))
        print("stderr[:300]: " + repr(result.stderr[:300]))
        print("warnings: " + json.dumps(manifest.warnings, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
