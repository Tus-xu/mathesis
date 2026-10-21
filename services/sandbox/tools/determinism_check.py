"""Determinism evidence: run the same trivial job twice and print the hashes.

Run from services/sandbox::

    .venv\\Scripts\\python.exe tools\\determinism_check.py

Contract being demonstrated (acceptance condition #2): ``code_hash`` / ``input_hash`` /
``output_hash`` are identical across the two runs while ``executed_at`` and
``duration_ms`` differ -- i.e. the hashes describe content, never timestamps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sandbox.hashing import sha256_text  # noqa: E402
from sandbox.models import (  # noqa: E402
    DependencyLock,
    ExecutionRequest,
    InputRef,
    ResourceLimits,
    SandboxPolicy,
)
from sandbox.orchestrator import run_experiment  # noqa: E402

CODE = "import math\nprint('determinism', round(math.pi, 6))\n"
LOCK = "pydantic==2.9.2\n"


def main() -> int:
    backend = sys.argv[1] if len(sys.argv) > 1 else "container"
    allow_unisolated = backend == "local"
    results = []
    for run_id in ("determinism-1", "determinism-2"):
        req = ExecutionRequest(
            run_id=run_id,
            code=CODE,
            inputs=[InputRef(name="a.txt", inline="hello\n", sha256=sha256_text("hello\n"))],
            seed=7,
            dependency_lock=DependencyLock(format="requirements.txt", content=LOCK),
            limits=ResourceLimits(wall_time_ms=30_000),
            policy=SandboxPolicy(require_real_isolation=not allow_unisolated),
            backend=backend,  # type: ignore[arg-type]
        )
        results.append(run_experiment(req))

    rows = []
    for result in results:
        manifest = result.manifest
        rows.append(
            {
                "run_id": manifest.run_id,
                "exec_status": manifest.exec_status,
                "code_hash": manifest.code_hash,
                "input_hash": manifest.input_hash,
                "output_hash": manifest.output_hash,
                "dependency_lock_hash": manifest.dependency_lock_hash,
                "image_digest": manifest.image_digest,
                "seed": manifest.seed,
                "executed_at": manifest.executed_at,
                "duration_ms": manifest.duration_ms,
                "stdout": result.stdout.strip(),
            }
        )
    print(json.dumps(rows, indent=2, sort_keys=True))

    first, second = rows
    same = {
        key: first[key] == second[key]
        for key in ("code_hash", "input_hash", "output_hash", "dependency_lock_hash")
    }
    differ = {
        key: first[key] != second[key] for key in ("executed_at", "run_id")
    }
    print("\nCONTENT HASHES IDENTICAL:", json.dumps(same, sort_keys=True))
    print("RUN IDENTITY DIFFERS  :", json.dumps(differ, sort_keys=True))
    ok = all(same.values()) and all(differ.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
