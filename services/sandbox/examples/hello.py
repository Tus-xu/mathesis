"""Minimal deterministic experiment.

Used by the README quick start and by ``tools/capability_evidence.py``. It imports
only allowlisted modules, so it also demonstrates the module guard in its allowing
mode (as opposed to tests/test_module_allowlist.py, which demonstrates blocking).

It writes nothing, so it produces no artifacts -- use ``tools/selftest_job.py`` if you
want to see artifact collection and hashing.
"""

from __future__ import annotations

import json
import math

#: Injected by the runner. Declared here so a linter does not flag them, and so the
#: contract is visible to anyone reading an example: the runner seeds ``random``/numpy
#: with SEED and passes the request's ``params`` as PARAMS.
SEED = SEED  # noqa: PLW0127  (provided by the sandbox runner)
PARAMS = PARAMS  # noqa: PLW0127  (provided by the sandbox runner)

print("hello from the sandbox")
print("sqrt(2) =", round(math.sqrt(2), 9))
print("seed =", SEED)
print("params =", json.dumps(PARAMS, sort_keys=True, separators=(",", ":")))
