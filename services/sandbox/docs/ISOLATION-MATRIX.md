# Isolation Matrix — what is actually enforced

**Generated from the code's own capability report, not written by hand.**
Reproduce with:

```powershell
cd services/sandbox
.\.venv\Scripts\sandbox.exe capabilities --backend all
.\.venv\Scripts\python.exe tools\capability_evidence.py
```

Machine-readable form: `sandbox capabilities --backend all --json`.

**Verified environment for this document:** Windows host, Python 3.13.5 (host) /
3.12.13 (container), Docker server **29.6.2**, image
`docker.m.daocloud.io/library/python:3.12-slim` resolved to
`sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`.
The container backend **was** exercised for real; every "verified" claim below cites
the test or raw output that produced it.

Legend: **ENFORCED** = a named mechanism applied and observed to work.
**PARTIAL** = mechanism covers part of the surface; the uncovered part is named.
**NOT enforced** = no mechanism exists; the manifest reports `false` with a reason.

---

## 1. The user mandate's control list (§23)

| # | Control | Container backend | Mechanism | Local backend | Note |
|---|---|---|---|---|---|
| 1 | **non-root** | **ENFORCED** | `docker run --user 10001:10001` + image `USER sandbox`. Verified: probe printed `uid 10001`; `docker image inspect` shows `user=10001:10001` | **NOT enforced** | Local runs as the invoking host user; no uid namespace outside a container |
| 2 | **CPU limit** | **ENFORCED** | `--cpus <n>` (cgroup `cpu.max`) | **NOT enforced** | No cgroup; `effective_limits.cpu_limit == "UNBOUNDED"` |
| 3 | **memory limit** | **ENFORCED** | `--memory <n>m --memory-swap <n>m` (cgroup). Verified: 64 MiB cap → memory hog killed, **exit 137**, no sidecar written | **NOT enforced** | No cgroup; a hog can exhaust host RAM. Declared 32 MiB while allocating 32 MiB still succeeded locally — see `test_local_backend_resource_attacks_are_reported_honestly` |
| 4 | **time limit** | **ENFORCED** | host deadline + `docker stop -t 1` → `docker kill` → `docker rm -f`. Verified: `wall_time_ms=3000` → `exec_status=timeout`, `exit_code=137`, partial output `BEFORE_LOOP` preserved | **ENFORCED** | `subprocess.Popen` + deadline; `taskkill /F /T` (Windows) / `killpg` (POSIX) kills the process tree |
| 5 | **disk limit** | **NOT enforced** (PARTIAL) | PARTIAL: `--tmpfs /tmp:rw,noexec,nosuid,size=<n>m` bounds scratch, but the `/work` bind mount has **no filesystem quota**. Growth there is measured after the run and reported | **NOT enforced** (PARTIAL) | Honest gap. Reported `false` in the manifest with the reason. `effective_limits.disk_limit_mb` reads `"tmpfs /tmp only (N MiB); workspace bind measured-only"` |
| 6 | **output limit** | **ENFORCED** | runner writes stdout/stderr to files; host reads at most `output_limit_bytes` per stream and records true byte counts. Verified: cap 8192 → `truncated=true`, `stdout_bytes_seen=200267` | **ENFORCED** | Same mechanism; the runner never buffers unbounded output in the caller's process |
| 7 | **no network by default** | **ENFORCED** | `--network=none` (kernel). Verified: `socket.create_connection(('example.com',80))` → `NETWORK_FAILED gaierror [Errno -3] Temporary failure in name resolution` | **NOT enforced** | **Verified counter-example:** with the guard off, the same call **succeeds** from the local backend. Treat every local run as network-capable |
| 8 | **no host mount** | **ENFORCED** | Exactly one bind: the per-run workspace → `/work`. `assert_argv_safe()` rejects any other `-v`/`--mount` before launch. Verified: argv contains exactly one `-v`; a tainted argv raises | **NOT enforced** | Code runs on the host: every path the host user can read is readable. The guard's `open` block is friction, not a boundary |
| 9 | **no docker socket** | **ENFORCED** | The socket is never added to argv; `assert_argv_safe()` rejects argv containing any `FORBIDDEN_HOST_MOUNTS` path (`/var/run/docker.sock`, `/run/docker.sock`, `//./pipe/docker_engine`, `//./pipe/dockerDesktopLinuxEngine`) | **NOT enforced** | Nothing stops the code opening the socket path if the invoking user can |
| 10 | **code hash** | **ENFORCED** | `code_hash` = SHA-256 of UTF-8-normalised source (CRLF→LF). Content only, never a timestamp | **ENFORCED** | Proven by `tools/determinism_check.py`: identical across runs |
| 11 | **input hash** | **ENFORCED** | `input_hash` = SHA-256 over canonical JSON of sorted `{name, media_type, sha256}`; every caller-declared digest is verified before execution | **ENFORCED** | Same evidence |
| 12 | **output hash** | **ENFORCED** | SHA-256 over canonical JSON of `{stdout_sha256, stderr_sha256}`; captured with `surrogateescape` so arbitrary bytes round-trip exactly | **ENFORCED** | Same evidence |
| 13 | **image digest** | **ENFORCED** | `docker image inspect --format {{.Id}}` **before** launch; the resolved `sha256:` image ID is recorded. The backend **never pulls implicitly** — an unresolvable image aborts the run with `exec_status="error"` | **NOT enforced** | No image exists; the host interpreter is used as-is |
| 14 | **seed** | **ENFORCED** | `--seed <n>` → runner calls `random.seed(n)`, `numpy.random.seed(n)` and pins `PYTHONHASHSEED=0`; the value and the runner sidecar are recorded in the manifest | **ENFORCED** | Container probe printed `SEED_SEEN 4242` with `manifest.seed == 4242` |
| 15 | **dependency lock** | **ENFORCED** | `dependency_lock_hash` over canonical JSON of `{format, content_sha256, image}`. An unspecified lock is filled from the backend's real image and recorded as `format="image-pin"` | **ENFORCED** | An unset lock does **not** hash the same as a pinned one — asserted in `test_dependency_lock_hash_distinguishes_empty_lock_from_image_pin` |

Additional controls tracked per run (not in the mandate list but part of the
`isolation_controls_enforced` set):

| Control | Container | Mechanism | Local |
|---|---|---|---|
| read-only rootfs | **ENFORCED** | `--read-only` | **NOT enforced** |
| tmpfs workdir | **NOT enforced** (PARTIAL) | `/tmp` is tmpfs; `/work` is a bind mount to a fresh host dir — so the *workdir as a whole* is not memory-backed. Reported `false` deliberately | **NOT enforced** |
| no host env secrets | **ENFORCED** | Fixed literal `-e` set only; no `--env-file`, no inherited environment. Verified: probe printed `SECRETS {}` while the host process had `SANDBOX_TEST_CANARY` set | **ENFORCED** |
| module allowlist | **ENFORCED** | `runner/runner.py` static AST guard + import hook, running inside the container. Verified in-container: `import os` → `GUARD_VIOLATION[BLOCKED_IMPORT]`, exit 3 | **ENFORCED** |
| cap-drop / no-new-privileges | **ENFORCED** | `--cap-drop ALL --security-opt no-new-privileges` | **NOT enforced** |

---

## 2. Host-path reachability — differential evidence

Same probe run twice (raw output from
`tests/test_security.py::test_container_cannot_read_host_secrets_or_paths`):

| Path | Host (local backend) | Container (`--network=none --read-only`) |
|---|---|---|
| `C:/Windows/win.ini` | **READABLE** | FileNotFoundError |
| `C:/Users` | FileNotFoundError (directory) | FileNotFoundError |
| `/etc/passwd` | not applicable on Windows | **READABLE** — the *container's own* file |
| `/proc/1/environ` | not applicable on Windows | **READABLE** — the *container's own* `/proc` |
| `/var/run/docker.sock` | FileNotFoundError | FileNotFoundError |

The container leg asserts the host-only path (`C:/Windows/win.ini`) is unreachable and
that the environment secret set is empty. `/etc/passwd` and `/proc/1/environ` being
readable **inside the container** is expected: they are the container's own files,
an inevitable consequence of a read-only rootfs, and they hold no host secrets because
the container environment is the fixed literal set. This document states that rather
than claiming "no host path is readable", which would be false.

---

## 3. What is NOT enforced — the honest list

1. **Disk quota on the workspace bind mount** (container backend). `--tmpfs` covers
   `/tmp` only. A payload that writes into `/work` writes to the host disk inside the
   per-run directory; there is no quota, no `--storage-opt` and no filesystem-level
   cap. Growth is measured after the run and reported; a payload that fills the host
   disk will fill the host disk. **Both backends report `disk_limit: false`.**
2. **The local backend is not isolation.** Network, memory, CPU, PID, filesystem and
   user are all unbounded. It is labelled `isolation_mode: "local-unisolated"`, emits a
   loud warning, and is **refused** under the default policy.
3. **Windows process tricks are not isolation.** The local backend uses
   `taskkill /F /T` for timeout enforcement and nothing else. No job object, no
   `setrlimit`, no sandboxing primitive is claimed.
4. **Container escape is out of scope.** A rootful Docker daemon means the *daemon* is
   root. Kernel/container-runtime escape bugs are not mitigated here.
5. **Side channels are not addressed.** Timing, cache and resource-usage side channels
   between concurrent runs are not mitigated.
6. **The module allowlist is defence in depth, not isolation.** It constrains *code*,
   not the process. Its runtime half is deliberately lenient toward
   interpreter-internal imports (see `runner/runner.py` docstrings for the exact rules
   and the bugs that forced them); the static AST half is default-deny over user code.
7. **`no_host_mount: true` on the container backend means "no host path other than the
   per-run workspace".** The bind itself is a host mount and is disclosed in
   `manifest.runtime_facts.bind_mounts`.

---

## 4. Reproduction commands

```powershell
cd D:\math-arena\services\sandbox

# capability self-report (human + machine readable)
.\.venv\Scripts\sandbox.exe capabilities --backend all
.\.venv\Scripts\sandbox.exe capabilities --backend all --json

# raw attack probes with observed outcomes
.\.venv\Scripts\python.exe tools\capability_evidence.py

# determinism proof
.\.venv\Scripts\python.exe tools\determinism_check.py container

# full test suite (13 container tests exercise real Docker)
.\.venv\Scripts\python.exe -m pytest -q
```
