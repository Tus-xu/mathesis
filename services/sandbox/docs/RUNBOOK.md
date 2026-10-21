# Runbook — build, run, and what a caller must pass

Everything below was executed on the machine described in
`docs/ISOLATION-MATRIX.md` (Windows, Docker 29.6.2). Commands that produce a non-zero
exit code for a *failed job* are normal: `sandbox run` exits 1 when the job did not
pass, 2 for a refused run, and 0 on pass.

---

## 1. Install

```powershell
cd services/sandbox
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Dependencies are declared in `pyproject.toml` (`pydantic`, `typer`, `redis`).
`docker` is optional — the container backend drives the **CLI**, not an SDK.

Verify:

```powershell
.\.venv\Scripts\sandbox.exe --help
.\.venv\Scripts\sandbox.exe capabilities --backend all
```

---

## 2. Build the sandbox image

The backend **never pulls implicitly** — an image that is not present locally makes the
run abort with `exec_status="error"` rather than silently resolving a moving tag.

```powershell
cd services/sandbox
docker build -f Dockerfile.sandbox -t math-arena-sandbox:0.2.0 .
```

Record the digest and pin it:

```powershell
docker image inspect --format "{{.Id}} | user={{.Config.User}}" math-arena-sandbox:0.2.0
# observed: sha256:d7da8e15b850dd0ea4edc1876c70e33f94a2abe58db2a9e63cfe1f7c264e0096 | user=10001:10001
```

Confirm non-root without any `--user` override:

```powershell
docker run --rm --network=none --read-only --entrypoint python3 math-arena-sandbox:0.2.0 -c "import os;print('uid',os.getuid())"
# observed: uid 10001
```

For a production pin, build from a digest:

```powershell
docker build -f Dockerfile.sandbox --build-arg BASE_IMAGE=python:3.12-slim@sha256:<digest> -t math-arena-sandbox:<version> .
```

If an experiment needs extra packages, uncomment the dependency-lock block in
`Dockerfile.sandbox` and pin exact versions with `--require-hashes`. The recorded
`dependency_lock_hash` then describes a real dependency set instead of an image pin.

---

## 3. Run a job — CLI

```powershell
# container backend (production path; refuses if isolation is unavailable)
.\.venv\Scripts\sandbox.exe run tools\selftest_job.py --backend container

# pin the image explicitly
.\.venv\Scripts\sandbox.exe run tools\selftest_job.py --backend container --image math-arena-sandbox:0.2.0

# full manifest as JSON
.\.venv\Scripts\sandbox.exe run tools\selftest_job.py --backend container --json

# local backend: requires an explicit opt-in, and the manifest says local-unisolated
.\.venv\Scripts\sandbox.exe run tools\selftest_job.py --backend local --allow-unisolated
```

Useful flags: `--timeout-ms`, `--memory-mb`, `--cpus`, `--pids-limit`, `--disk-mb`,
`--output-kb`, `--seed`, `--lock-file requirements.txt`, `--run-id`, `--no-module-guard`.

**Without `--allow-unisolated`, `--backend local` refuses** and prints the missing
controls. That refusal is a result (`exec_status="refused"`), not an exception, and it
never reports `pass`.

---

## 4. Run a job — Python API

```python
from sandbox.models import ExecutionRequest, DependencyLock, InputRef, ResourceLimits, SandboxPolicy
from sandbox.orchestrator import run_experiment
from sandbox.hashing import sha256_text

result = run_experiment(
    ExecutionRequest(
        run_id="exp-001",
        experiment_id="RV11-experiment-7",
        code="import math\nprint(math.sqrt(2))\n",
        inputs=[InputRef(name="data.csv", inline="x,y\n1,2\n", sha256=sha256_text("x,y\n1,2\n"))],
        params={"alpha": 0.05},
        seed=42,
        dependency_lock=DependencyLock(format="requirements.txt", content="pydantic==2.9.2\n"),
        limits=ResourceLimits(cpu_limit=1.0, memory_limit_mb=512, pids_limit=64,
                              disk_limit_mb=64, wall_time_ms=30_000, output_limit_bytes=256 * 1024),
        policy=SandboxPolicy(),          # require_real_isolation=True, network=deny
        backend="container",
    )
)
print(result.manifest.exec_status, result.manifest.isolation_controls_enforced.missing())
```

### What a caller MUST pass

| Field | Required? | Why |
|---|---|---|
| `run_id` | yes | identity for logs/manifest |
| `code` **or** `code_ref` | yes (exactly one) | `code_ref` requires `sha256`; a mismatch aborts before execution |
| `inputs[].sha256` | yes for `object_ref`; auto-computed for `inline` | an unverified payload makes `input_hash` meaningless |
| `seed` | recommended | recorded and applied; reproducibility |
| `dependency_lock` | recommended | falls back to an image pin, which is recorded as such |
| `limits` | recommended | defaults are sane (512 MiB, 1 CPU, 64 PIDs, 30 s, 256 KiB output) |
| `policy.require_real_isolation` | default `true` | leave it on unless you knowingly accept no isolation |

Bench defaults: 100 ms ≤ `wall_time_ms` ≤ 600 s, 32 MiB ≤ `memory_limit_mb` ≤ 32 GiB,
1 KiB ≤ `output_limit_bytes` ≤ 16 MiB, 1 MiB ≤ `disk_limit_mb` ≤ 4 GiB, 1 ≤ `pids_limit` ≤ 4096.

### Reading the result

* `manifest.exec_status` ∈ `pass | fail | timeout | refused | error`.
* `manifest.isolation_controls_enforced` — per-control booleans. **Read this before
  trusting any result.** `missing()` lists what was not enforced.
* `manifest.unenforced_reasons` — why, for each `false`.
* `manifest.truncation` — `truncated`, `limit_bytes`, and the true byte counts seen.
* `manifest.effective_limits` — what was applied; `"UNBOUNDED"` means unbounded.
* `result.argv` — the exact command executed (auditable; contains no secrets).

---

## 5. Run as a queue worker (WP-H integration)

The worker consumes the frozen conventions from `deploy/registry.yml`
(`research-vnext-8300`): Valkey Stream on port 6380, prefix `strm:rv8300`, group
`rv8300-workers`, dedupe scope `rv8300`, object prefix `rvs/`.

```powershell
$env:SANDBOX_VALKEY_URL = "redis://127.0.0.1:6380/0"
.\.venv\Scripts\python.exe -m sandbox.worker --once --json
.\.venv\Scripts\python.exe -m sandbox.worker --count 4        # loop
```

**Fail-closed behaviour (verified):** with nothing listening on the port the worker
exits **2** and prints

```json
{"status":"failed","reason":"queue_unavailable","executed_jobs":0,"in_process_fallback":false, ...}
```

It never executes jobs in-process and never reports success without a queue.

### Registering the handler with the Job Runtime

`SandboxRunHandler().spec` is a `JobHandlerSpec` a runtime can consume without
importing this package's internals:

* `kind`: `sandbox.run_experiment`
* `input_schema` / `output_schema`: exported JSON Schema (`ExecutionRequest` / `JobResult`)
* `timeout_ms`: 600000, `max_attempts`: 2, `idempotent`: true, `concurrency`: 1
* `idempotency_key_fields`: `code_hash`, `input_hash`, `seed`, `dependency_lock_hash`, `limits`, `backend`
* `side_effects`: `[]` — the handler writes nothing outside its per-run workspace, which is deleted
* `required_isolation`: true

`SandboxRunHandler.idempotency_key(req)` derives a content-addressed key that
**excludes `run_id`**, so two identical submissions collapse to one execution.

Envelope field names (`JobEnvelope`): `job_id`, `kind`, `attempt`, `dedupe_scope`,
`idempotency_key`, `lease_token`, `fencing_token`, `trace_id`, `payload_ref`,
`payload`, `enqueued_at`. `payload` may be the request JSON inline, or a `rvs/` object
reference with `payload_ref`.

This package imports **nothing** from `services/research-vnext`.

---

## 6. Operations

### Health / drift check

```powershell
.\.venv\Scripts\sandbox.exe capabilities --backend all --json   # honesty_check must be []
```

`honesty_check` is a self-audit: it flags any control reported enforced without a named
mechanism, any control reported unenforced without a reason, and any available backend
that was not selected. Non-empty output is a defect in this package, not a warning.

### Workspace

Per-run workspaces live under `%TEMP%\math-arena-sandbox\<run_id>-<random>` (override
with `SANDBOX_WORK_ROOT`). They are deleted after every run — including after a
timeout kill. If a run leaves one behind, that is a bug worth reporting.

### Failure playbook

| Symptom | Meaning | Action |
|---|---|---|
| `exec_status="refused"` | policy demanded real isolation; nothing ran | use `--backend container`, or explicitly accept `--allow-unisolated` |
| `exec_status="error"`, "image digest unresolvable" | image not present locally | build `Dockerfile.sandbox` or pre-pull; the backend will not pull for you |
| `exit_code=137`, no sidecar | OOM kill or SIGKILL (timeout) | raise `memory_limit_mb` if the job is legitimately large; check `timed_out` |
| `runner sidecar missing` warning | code never reached the runner | treat as failure, never as a pass |
| worker exits 2 | Valkey unreachable | fix the queue; the worker will not run jobs in-process |
| `truncated: true` | output exceeded the cap | raise `output_limit_bytes`, or write artifacts instead of printing |

### Environment variables

| Variable | Effect |
|---|---|
| `SANDBOX_VALKEY_URL` | worker queue URL (default `redis://127.0.0.1:6380/0`) |
| `SANDBOX_REQUIRE_REAL_ISOLATION` | `0`/`false` permits the local backend. **The only way to relax the default** |
| `SANDBOX_WORK_ROOT` | workspace parent directory |
| `SANDBOX_IMAGE` / `--image` | sandbox image reference |
| `SANDBOX_TEST_IMAGE` | image used by the container test tier |

---

## 7. Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not container"   # no Docker needed
.\.venv\Scripts\python.exe -m pytest -q -m container         # 13 tests, real Docker
.\.venv\Scripts\python.exe -m pytest -q                      # everything
```

Container tests **skip with a printed reason** when no runtime or image is available.
They never fake a container run and never fall back to the local backend while claiming
container evidence.

---

## 8. KNOWN GAPS

1. **No disk quota on the workspace bind mount.** `--tmpfs` covers `/tmp` only. This is
   reported as `disk_limit: false` on both backends and is the largest remaining gap
   against the mandate's list.
2. **Local backend is not isolation.** Developer convenience only. Default-refused.
3. **Rootful Docker daemon.** Daemon-level compromise is out of scope; injection of a
   malicious image is possible for anyone who can push to this daemon.
4. **No seccomp profile beyond Docker's default.** `--cap-drop ALL` and
   `no-new-privileges` are applied; a custom seccomp/AppArmor profile is not.
5. **No user-namespace remapping.** `--userns=host` in effect; the container's uid
   10001 is mapped directly.
6. **Single node, no scheduler.** Concurrency, quotas and fair-sharing belong to the
   Job Runtime (WP-H); this worker is a consumer with `concurrency: 1`.
7. **No object-store integration.** `rvs/` payloads must be inlined or passed as a
   readable local path; WP-H owns object storage.
8. **No result persistence beyond the queue entry.** Durable `ExperimentRun` records
   are WP-H's; this package returns the manifest and publishes it to the result stream.
