# `services/sandbox` — research experiment isolation

**Status: real service as of WP-I (2026-09-12).** Before this revision the directory
contained an 11-line placeholder (`dependencies = []` and a one-line comment) while
the repository README advertised it as a working service. That contradiction is
recorded as a finding, and this README now describes only what exists.

What this service is: the **only** sanctioned way to execute caller-supplied or
LLM-generated Python for research experiments. It runs the code in an isolated
worker, and — the part that matters most — it **records and reports exactly which
isolation controls were actually in force** for every single run.

## The rule this package exists to enforce

> Research experiment must not execute LLM-generated Python directly in the Web API
> process. It must go into an isolated worker + sandbox, with at minimum: non-root,
> resource limits, time limit, disk limit, output limit, no network by default, no
> host mount, no docker socket, and recorded code/input/output hashes, image digest,
> seed, dependency lock.

Every item in that list is tracked as a boolean in `isolation_controls_enforced` on
the run manifest, plus the exact mechanism. When a control is not enforced, the
manifest says `false` and gives the reason. **Nothing in this package reports
isolation it does not provide.** Two controls on the container backend are
deliberately reported `false` for that reason (see below).

## Install

```powershell
cd services/sandbox
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Dependencies are declared for real in `pyproject.toml` (`pydantic`, `typer`,
`redis`), with `docker` optional — the container backend drives the Docker/Podman
**CLI**, so no SDK is required.

## What is actually enforced

Generate this yourself rather than trusting the table:

```powershell
.\.venv\Scripts\sandbox.exe capabilities --backend all
.\.venv\Scripts\sandbox.exe capabilities --backend all --json
```

Summary (derived from that command, not written by hand):

| control | container backend | local backend |
|---|---|---|
| non-root user | ENFORCED (`--user 10001:10001`) | **NOT enforced** (runs as host user) |
| read-only rootfs | ENFORCED (`--read-only`) | **NOT enforced** |
| tmpfs workdir | **PARTIAL** (`/tmp` is tmpfs; `/work` is a bind mount) | **NOT enforced** |
| network isolation | ENFORCED (`--network=none`, kernel) | **NOT enforced** (verified: egress succeeds) |
| memory limit | ENFORCED (`--memory`) | **NOT enforced** |
| CPU limit | ENFORCED (`--cpus`) | **NOT enforced** |
| PID limit | ENFORCED (`--pids-limit`) | **NOT enforced** |
| disk limit | **PARTIAL** (tmpfs `/tmp` sized; workspace bind has no quota) | **NOT enforced** (measured after the fact) |
| output cap | ENFORCED (host read cap, truncation recorded) | ENFORCED |
| wall-clock timeout | ENFORCED (host deadline + stop/kill) | ENFORCED |
| no host mount | ENFORCED (one bind: the per-run workspace) | **NOT enforced** |
| no docker socket | ENFORCED (never added; argv asserted) | **NOT enforced** |
| env secrets | ENFORCED (fixed literal env) | ENFORCED (fixed literal env) |
| code/input/output hash | ENFORCED (always) | ENFORCED (always) |
| image by digest | ENFORCED (resolved to image ID before launch) | **NOT enforced** (no image) |
| seed | ENFORCED (recorded + applied) | ENFORCED |
| dependency lock | ENFORCED (recorded as a hash) | ENFORCED (image-pin fallback) |
| module allowlist | ENFORCED (in-container AST guard) | ENFORCED (AST guard, defence in depth) |

Full mechanism-by-mechanism detail: `docs/ISOLATION-MATRIX.md`.
Build/run instructions and caller contract: `docs/RUNBOOK.md`.

## Quick start

```powershell
# 1. What is enforced on this machine right now?
.\.venv\Scripts\sandbox.exe capabilities --backend all

# 2. Run something (container backend; refuses if isolation is unavailable)
.\.venv\Scripts\sandbox.exe run .\examples\hello.py --backend container

# 3. Same, without an OS-isolation claim (loud warning, local-unisolated manifest)
.\.venv\Scripts\sandbox.exe run .\examples\hello.py --backend local --allow-unisolated
```

## Layout

```
services/sandbox/
├── pyproject.toml              # real dependencies (was `dependencies = []`)
├── Dockerfile.sandbox          # non-root image for the container backend
├── README.md                   # this file
├── docs/
│   ├── ISOLATION-MATRIX.md     # per-control enforced/partial/NOT enforced, per backend
│   └── RUNBOOK.md              # build, run, caller contract, known gaps
├── runner/runner.py            # stdlib-only in-sandbox runner (audit copy)
├── src/sandbox/
│   ├── models.py               # typed request/result/manifest/capability contract
│   ├── orchestrator.py         # policy gate -> payload verify -> backend -> manifest
│   ├── backends/               # container backend + honest local backend
│   ├── capabilities.py         # "what is enforced right now?" + honesty self-check
│   ├── jobhandler.py           # typed job-handler interface for the Job Runtime
│   ├── worker.py               # standalone Valkey Stream consumer (python -m sandbox.worker)
│   ├── cli.py                  # `sandbox capabilities | run | selftest | worker`
│   └── _data/runner.py         # runner as package data (byte-identical to runner/)
└── tests/                      # reproducibility, guard, security, refusal tests
```

## What this service does NOT do

* It does not talk to the object store (`rvs/`). Callers inline payloads or pass a
  readable local path; WP-H owns object storage.
* It does not own the queue. `python -m sandbox.worker` consumes the frozen
  conventions from `deploy/registry.yml` and **fails closed** if Valkey is down —
  it never falls back to running jobs in-process.
* It does not make the local backend safe. It labels it `local-unisolated` and
  refuses it under the default policy.
* It does not protect against a rootful Docker daemon's own compromise, nor against
  a kernel/container-escape bug. Noted in `docs/RUNBOOK.md` under KNOWN GAPS.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not container"    # no Docker needed
.\.venv\Scripts\python.exe -m pytest -q -m container          # real Docker required
```

Container tests **skip with a printed reason** when no runtime is available. They
never fake a container run.
