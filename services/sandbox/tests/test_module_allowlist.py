"""The module allowlist is LIVE code with tests proving it blocks.

The audit finding was that ``services/api/app/providers/sandbox.py`` defines
``_ALLOWED_MODULES``, never reads it, and relies on a ``_BLOCKED_MODULES`` denylist
that a trivial ``__import__`` also bypasses. This package's allowlist is an
allowlist (default-deny), applied statically before execution and again at runtime,
and these tests are what make that a fact rather than a claim.

Scope note, repeated because it matters: this guard is **defence in depth**, not
isolation. It constrains the *code*; it does not constrain the process. The
container backend's namespaces are the isolation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sandbox.orchestrator import run_experiment

from .conftest import make_request

REPO_RUNNER = Path(__file__).resolve().parents[1] / "runner" / "runner.py"
PACKAGED_RUNNER = Path(__file__).resolve().parents[1] / "src" / "sandbox" / "_data" / "runner.py"


def test_the_two_runner_copies_are_byte_identical() -> None:
    """The audited copy and the executed copy must not drift apart."""
    assert REPO_RUNNER.read_bytes() == PACKAGED_RUNNER.read_bytes(), (
        "runner/runner.py and src/sandbox/_data/runner.py differ; the code that runs "
        "in-sandbox would then differ from the code under review"
    )


def _local(code: str, run_id: str = "guard"):
    return run_experiment(
        make_request(code, backend="local", require_real_isolation=False, run_id=run_id)
    )


BLOCKED_SNIPPETS = [
    ("import socket", "BLOCKED_IMPORT"),
    ("import os", "BLOCKED_IMPORT"),
    ("import subprocess", "BLOCKED_IMPORT"),
    ("import urllib.request", "BLOCKED_IMPORT"),
    ("from os import getenv", "BLOCKED_IMPORT"),
    ("import shutil", "BLOCKED_IMPORT"),
    ("import importlib", "BLOCKED_IMPORT"),
    ("import pathlib", "BLOCKED_IMPORT"),
    ("import threading", "BLOCKED_IMPORT"),
    ("import ctypes", "BLOCKED_IMPORT"),
]

UNLISTED_SNIPPETS = [
    ("import base64", "MODULE_NOT_ALLOWED"),
    ("import asyncio", "BLOCKED_IMPORT"),
    ("import queue", "MODULE_NOT_ALLOWED"),
]

ESCAPE_SNIPPETS = [
    ("print((1).__class__.__bases__)", "BLOCKED_ATTRIBUTE"),
    ("print(getattr(print, '__globals__'))", "BLOCKED_ATTRIBUTE"),
    ("__import__('os').system('echo pwned')", "BLOCKED_BUILTIN"),
    ("open('/etc/passwd').read()", "BLOCKED_BUILTIN"),
    ("eval('1+1')", "BLOCKED_BUILTIN"),
    ("exec('x=1')", "BLOCKED_BUILTIN"),
    ("compile('x=1', '<s>', 'exec')", "BLOCKED_BUILTIN"),
]


@pytest.mark.parametrize(("snippet", "expected"), BLOCKED_SNIPPETS + UNLISTED_SNIPPETS)
def test_guard_blocks_forbidden_and_unlisted_imports(snippet: str, expected: str) -> None:
    result = _local(f"{snippet}\nprint('SHOULD_NOT_PRINT')\n")
    assert result.manifest.exec_status == "fail"
    assert result.manifest.exit_code == 3, result.stderr
    assert f"GUARD_VIOLATION[{expected}]" in result.stderr, result.stderr
    assert "SHOULD_NOT_PRINT" not in result.stdout


@pytest.mark.parametrize(("snippet", "expected"), ESCAPE_SNIPPETS)
def test_guard_blocks_sandbox_escapes(snippet: str, expected: str) -> None:
    result = _local(f"{snippet}\n")
    assert result.manifest.exec_status == "fail"
    assert f"GUARD_VIOLATION[{expected}]" in result.stderr, result.stderr


def test_guard_blocks_before_the_first_statement_runs() -> None:
    """A blocked import on line 3 must not let lines 1-2 execute."""
    result = _local("print('BEFORE')\nprint('ALSO_BEFORE')\nimport os\n")
    assert "BEFORE" not in result.stdout, (
        "the guard is applied statically, so no statement may run when the module is rejected"
    )
    assert result.manifest.exit_code == 3


def test_allowed_modules_still_work() -> None:
    result = _local(
        "import math, json, fractions, itertools, statistics\n"
        "print('ok', math.factorial(5), json.dumps({'a': 1}), "
        "str(fractions.Fraction(1, 3)), list(itertools.islice(itertools.count(), 3)), "
        "statistics.mean([1, 2, 3]))\n",
        run_id="guard-allowed",
    )
    assert result.manifest.exec_status == "pass", result.stderr
    assert "ok 120" in result.stdout


def test_disabling_the_guard_is_recorded_in_the_manifest() -> None:
    """A caller that turns the guard off must see that in the manifest, not guess."""
    req = make_request(
        "import base64\nprint(base64.b64encode(b'x').decode())\n",
        backend="local",
        require_real_isolation=False,
        enforce_module_allowlist=False,
        run_id="guard-off",
    )
    result = run_experiment(req)
    assert result.manifest.isolation_controls_enforced.module_allowlist is False
    assert "module_allowlist" in result.manifest.unenforced_reasons
    assert result.manifest.exec_status == "pass"


def test_runner_runs_standalone_without_the_package_installed() -> None:
    """The runner must be stdlib-only, so a bare interpreter can execute it."""
    probe = subprocess.run(  # noqa: S603
        [sys.executable, str(REPO_RUNNER), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "Math Arena sandbox runner" in probe.stdout


def test_guard_reports_syntax_errors_without_executing() -> None:
    result = _local("def broken(:\n    pass\n", run_id="guard-syntax")
    assert "GUARD_VIOLATION[SYNTAX_ERROR]" in result.stderr
    assert result.manifest.exit_code == 3
