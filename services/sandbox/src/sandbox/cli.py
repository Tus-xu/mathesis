"""``sandbox`` CLI: run a job, or ask what isolation is actually in force.

``sandbox capabilities`` is the honest self-report. It is the first thing an
operator should run, and its output is what ``docs/ISOLATION-MATRIX.md`` is derived
from rather than the other way round.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .capabilities import MANDATE_CONTROLS, all_backends, capability_report, mandate_matrix
from .models import (
    CodeRef,
    DependencyLock,
    ExecutionRequest,
    NetworkPolicy,
    ResourceLimits,
    SandboxPolicy,
)
from .orchestrator import run_experiment

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Math Arena research sandbox: isolated execution with honest isolation reporting.",
)

BackendChoice = Annotated[str, typer.Option("--backend", help="auto | container | local")]
JsonFlag = Annotated[bool, typer.Option("--json", help="emit machine-readable JSON")]


@app.command()
def capabilities(
    backend: BackendChoice = "auto",
    json_out: JsonFlag = False,
    allow_unisolated: Annotated[
        bool,
        typer.Option(
            "--allow-unisolated",
            help="report for a policy that permits the local (NO OS isolation) backend",
        ),
    ] = False,
    network: Annotated[str, typer.Option("--network", help="deny | allow")] = "deny",
) -> None:
    """Report which isolation controls are ACTUALLY enforced right now.

    Every control is reported as ENFORCED / PARTIAL / NOT enforced with the exact
    mechanism. Nothing here is aspirational: if a control is not enforced, this
    command says so and the runtime manifest records the same verdict.
    """
    policy = SandboxPolicy(
        require_real_isolation=not allow_unisolated,
        network=NetworkPolicy(network),
    )
    if backend == "all":
        payload = all_backends(policy)
        if json_out:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
            return
        _print_all(payload)
        return

    report = capability_report(backend, policy)
    if json_out:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    _print_one(report)


@app.command()
def run(
    code_path: Annotated[Path, typer.Argument(help="path to a Python file to execute")],
    backend: BackendChoice = "auto",
    json_out: JsonFlag = False,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    timeout_ms: Annotated[int, typer.Option("--timeout-ms")] = 30_000,
    memory_mb: Annotated[int, typer.Option("--memory-mb")] = 512,
    cpu: Annotated[float, typer.Option("--cpus")] = 1.0,
    pids: Annotated[int, typer.Option("--pids-limit")] = 64,
    disk_mb: Annotated[int, typer.Option("--disk-mb")] = 64,
    output_kb: Annotated[int, typer.Option("--output-kb")] = 256,
    run_id: Annotated[str, typer.Option("--run-id")] = "cli-run",
    allow_unisolated: Annotated[
        bool,
        typer.Option(
            "--allow-unisolated",
            help="permit the local backend, which provides NO OS isolation (loud warning)",
        ),
    ] = False,
    image: Annotated[str | None, typer.Option("--image")] = None,
    lock_file: Annotated[Path | None, typer.Option("--lock-file")] = None,
    no_guard: Annotated[bool, typer.Option("--no-module-guard")] = False,
    keep_workspace: Annotated[bool, typer.Option("--keep-workspace", hidden=True)] = False,
) -> None:
    """Execute one experiment under the sandbox and print the provenance manifest."""
    del keep_workspace  # workspace cleanup is unconditional in the backends
    if not code_path.is_file():
        typer.secho(f"code file not found: {code_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    lock = DependencyLock(format="none")
    if lock_file and lock_file.is_file():
        lock = DependencyLock(
            format="requirements.txt", content=lock_file.read_text(encoding="utf-8")
        )

    policy = SandboxPolicy(
        require_real_isolation=not allow_unisolated,
        enforce_module_allowlist=not no_guard,
    )
    req = ExecutionRequest(
        run_id=run_id,
        code=code_path.read_text(encoding="utf-8"),
        seed=seed,
        dependency_lock=lock,
        limits=ResourceLimits(
            cpu_limit=cpu,
            memory_limit_mb=memory_mb,
            pids_limit=pids,
            disk_limit_mb=disk_mb,
            wall_time_ms=timeout_ms,
            output_limit_bytes=output_kb * 1024,
        ),
        policy=policy,
        backend=backend,
        sandbox_image=image,
    )
    result = run_experiment(req)
    manifest = result.manifest

    if json_out:
        typer.echo(
            json.dumps(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "artifacts": [a.model_dump() for a in result.artifacts],
                    "argv": result.argv,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_result(result)
    if manifest.exec_status != "pass":
        raise typer.Exit(code=1)


@app.command()
def selftest(
    json_out: JsonFlag = False,
    backend: BackendChoice = "auto",
    allow_unisolated: Annotated[bool, typer.Option("--allow-unisolated")] = False,
) -> None:
    """Run a trivial deterministic job and print its manifest (reproducibility smoke)."""
    policy = SandboxPolicy(require_real_isolation=not allow_unisolated)
    req = ExecutionRequest(
        run_id="selftest",
        code="import math\nprint('selftest', math.sqrt(2))\n",
        policy=policy,
        backend=backend,
        limits=ResourceLimits(wall_time_ms=30_000),
    )
    result = run_experiment(req)
    if json_out:
        typer.echo(json.dumps(result.manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        _print_result(result)


# --------------------------------------------------------------------------------------


def _print_one(report) -> None:  # noqa: ANN001
    typer.secho(
        f"sandbox {report.sandbox_version} | backend requested={report.backend_requested} "
        f"selected={report.backend_selected} available={report.backend_available}",
        bold=True,
    )
    if report.backend_unavailable_reason:
        typer.secho(f"  unavailable: {report.backend_unavailable_reason}", fg=typer.colors.YELLOW)
    typer.echo(f"  isolation_mode_if_run_now = {report.isolation_mode_if_run_now.value}")
    typer.echo(
        f"  would_refuse_current_policy = {report.would_refuse_current_policy}"
        + (f"  ({report.refusal_reason})" if report.refusal_reason else "")
    )
    typer.echo("  controls:")
    for control, enforced in sorted(report.controls_enforceable.items()):
        mechanism = report.control_mechanisms.get(control, "")
        partial = enforced and mechanism.upper().startswith("PARTIAL")
        label = "NOT enforced" if not enforced else ("PARTIAL" if partial else "ENFORCED")
        colour = typer.colors.RED if label == "NOT enforced" else (
            typer.colors.YELLOW if label == "PARTIAL" else typer.colors.GREEN
        )
        typer.secho(f"    {control:<22} {label:<13} {mechanism}", fg=colour)
    for warning in report.warnings:
        typer.secho(f"  WARNING: {warning}", fg=typer.colors.YELLOW)


def _print_all(payload: dict) -> None:
    typer.secho(f"sandbox {payload['sandbox_version']} | host {payload['host']}", bold=True)
    selection = payload["selection_for_policy"]
    typer.echo(
        f"selection for current policy: {selection['selected']} "
        f"({selection['isolation_mode']}, would_execute={selection['would_execute']})"
    )
    typer.echo(f"  reason: {selection['reason']}")
    matrix = mandate_matrix(
        {name: _report_from_dict(data) for name, data in payload["backends"].items()}
    )
    typer.echo("\nmandate control matrix (ENFORCED / PARTIAL / NOT enforced):")
    header = f"  {'control':<22}" + "".join(f"{name:<16}" for name in matrix)
    typer.echo(header)
    for control in MANDATE_CONTROLS:
        row = f"  {control:<22}"
        for name in matrix:
            verdict = matrix[name][control]
            colour = {
                "ENFORCED": typer.colors.GREEN,
                "PARTIAL": typer.colors.YELLOW,
            }.get(verdict, typer.colors.RED)
            row += _pad_coloured(verdict, 16, colour)
        typer.echo(row)
    findings = payload["honesty_check"]
    if findings:
        typer.secho(f"\nHONESTY DEFECTS: {len(findings)}", fg=typer.colors.RED)
        for finding in findings:
            typer.echo(f"  {finding}")
    else:
        typer.secho("\nhonesty_check: no defects found", fg=typer.colors.GREEN)


def _pad_coloured(text: str, width: int, colour: str) -> str:
    pad = " " * max(0, width - len(text))
    return typer.style(text, fg=colour) + pad


def _report_from_dict(data: dict):
    from .models import CapabilityReport

    return CapabilityReport.model_validate(data)


def _print_result(result) -> None:  # noqa: ANN001
    manifest = result.manifest
    typer.secho(f"exec_status={manifest.exec_status} exit_code={manifest.exit_code}", bold=True)
    typer.echo(f"  backend={manifest.backend} isolation_mode={manifest.isolation_mode.value}")
    typer.echo(f"  code_hash={manifest.code_hash}")
    typer.echo(f"  input_hash={manifest.input_hash}")
    typer.echo(f"  output_hash={manifest.output_hash}")
    typer.echo(f"  dependency_lock_hash={manifest.dependency_lock_hash}")
    typer.echo(f"  image_digest={manifest.image_digest}")
    typer.echo(f"  seed={manifest.seed} duration_ms={manifest.duration_ms}")
    forced = manifest.isolation_controls_enforced.enforced()
    missing = manifest.isolation_controls_enforced.missing()
    typer.secho(f"  controls enforced ({len(forced)}): {', '.join(forced) or '-'}", fg=typer.colors.GREEN)
    if missing:
        typer.secho(
            f"  controls NOT enforced ({len(missing)}): {', '.join(missing)}", fg=typer.colors.RED
        )
    if manifest.truncation.truncated:
        typer.secho(
            f"  output TRUNCATED at {manifest.truncation.limit_bytes} bytes "
            f"(stdout saw {manifest.truncation.stdout_bytes_seen}, "
            f"stderr saw {manifest.truncation.stderr_bytes_seen})",
            fg=typer.colors.YELLOW,
        )
    for warning in manifest.warnings:
        typer.secho(f"  WARNING: {warning}", fg=typer.colors.YELLOW)
    typer.echo("--- stdout ---")
    typer.echo(result.stdout.rstrip("\n"))
    if result.stderr.strip():
        typer.echo("--- stderr ---")
        typer.echo(result.stderr.rstrip("\n"))


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["app", "main", "run_experiment", "CodeRef", "__version__"]
