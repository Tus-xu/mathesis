"""Self-test job: exercises seeding, params injection and manifest capture.

Runnable under both backends::

    .venv\\Scripts\\sandbox.exe run tools\\selftest_job.py --backend container
    .venv\\Scripts\\sandbox.exe run tools\\selftest_job.py --backend local --allow-unisolated

Guard-compatible on purpose: it imports only allowlisted modules and never calls
``open``, so it runs with the module guard ON. Writing artifacts requires ``open``,
which the static guard blocks -- artifact collection is demonstrated by
``tests/test_container_backend.py::test_container_collects_and_hashes_artifacts``,
which runs with the guard explicitly disabled so the check is not muddled with the
guard's verdict.

``flush_output()`` is provided by the runner; ``import sys`` is not on the allowlist.
"""

from __future__ import annotations

import math
import random

SEED = SEED  # noqa: PLW0127  (injected by the sandbox runner)
PARAMS = PARAMS  # noqa: PLW0127  (injected by the sandbox runner)

random.seed(SEED)
print("seed =", SEED)
print("first random value =", random.random())
print("params keys =", sorted(PARAMS))
print("comb(10, 3) =", math.comb(10, 3))
print("pi =", "%.12f" % math.pi)
flush_output()
