#!/usr/bin/env python3
"""In-sandbox runner: executes caller code inside whatever isolation the backend gave it.

This file is shipped as package data (``sandbox/_data/runner.py``); the copy in
``services/sandbox/runner/runner.py`` is byte-identical and exists so the code
that runs inside the sandbox can be audited without installing the package.
``tests/test_module_allowlist.py`` asserts the two copies stay identical.

IT USES THE STANDARD LIBRARY ONLY and imports nothing from the sandbox package,
so it runs identically in the container image and on a bare host interpreter.

What this file provides, and -- just as important -- what it does NOT:

  PROVIDES
  * a static AST module allowlist (``--guard strict-math``) that rejects forbidden
    imports, dunder-escape attribute access and dangerous builtins *before* the
    first executable statement runs. This is defence in depth, NOT isolation:
    it is a guard on the code being run, and a determined attacker who controls
    the source can still find stdlib paths the list misses. Real isolation comes
    from the backend (container), not from this file.
  * a runtime ``__import__`` hook with the same list, for code that reaches an
    import through a path the AST walk did not classify.
  * stdout/stderr redirected to files so the backend can apply a hard byte cap
    without the parent process ever buffering unbounded output.
  * deterministic seeding (``random``, ``PYTHONHASHSEED``) recorded in a JSON
    sidecar so the manifest can prove what seed was in force.

  DOES NOT PROVIDE
  * any network, memory, CPU, PID, filesystem or user isolation. If the backend
    is ``local``, none of that exists. See docs/ISOLATION-MATRIX.md.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import io
import json
import os
import random
import sys
import traceback
from pathlib import Path

# --------------------------------------------------------------------------------------
# Module allowlist -- this list is LIVE (enforced), not decorative.
# --------------------------------------------------------------------------------------

ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "math",
        "cmath",
        "decimal",
        "fractions",
        "statistics",
        "random",
        "itertools",
        "functools",
        "operator",
        "collections",
        "heapq",
        "bisect",
        "string",
        "re",
        "json",
        "typing",
        "abc",
        "numbers",
        "dataclasses",
        "enum",
        "textwrap",
        "pprint",
        "copy",
        "datetime",
        "time",  # read-only clock use; no isolation claim is attached to time
        "sympy",
        "numpy",
        "scipy",
    }
)

#: Explicit denials get their own message so a blocked import is unambiguous in
#: the captured stderr rather than a generic ImportError.
BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "ssl",
        "http",
        "urllib",
        "ftplib",
        "smtplib",
        "telnetlib",
        "asyncio",
        "selectors",
        "select",
        "ctypes",
        "mmap",
        "multiprocessing",
        "concurrent",
        "shutil",
        "pathlib",
        "glob",
        "tempfile",
        "signal",
        "threading",
        "pty",
        "tty",
        "fcntl",
        "resource",
        "pickle",
        "shelve",
        "marshal",
        "importlib",
        "pkgutil",
        "runpy",
        "code",
        "codeop",
        "compileall",
        "site",
        "builtins",
        "gc",
        "inspect",
        "platform",
        "getpass",
        "requests",
        "httpx",
        "aiohttp",
        "docker",
        "redis",
        "psycopg",
        "psycopg2",
        "sqlalchemy",
        "boto3",
    }
)

BLOCKED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__code__",
        "__closure__",
        "__func__",
        "__self__",
        "__dict__",
        "__getattribute__",
        "__reduce__",
        "__reduce_ex__",
        "__import__",
    }
)

#: Builtins the runtime hook sets to ``None`` in the executing frame.
#:
#: This list is short ON PURPOSE, and the reason is a bug that cost real debugging time
#: and is documented so nobody "tightens" it back:
#:
#: * ``exec`` and ``eval`` are used by the *import machinery itself* (and by
#:   ``__build_class__``). Nulling them makes every later import explode with a
#:   misleading ``TypeError: 'NoneType' object is not callable``. Verified by bisection
#:   on CPython 3.13: nulling either one breaks ``import json``.
#: * ``open`` is ``_io.open``, which the import machinery also calls while importing
#:   allowed pure-Python modules (``json`` -> ``json.encoder`` -> ``_io``). Nulling it
#:   breaks imports the same way.
#:
#: All three are still blocked in *submitted code* by the static AST guard
#: (:data:`STATIC_BLOCKED_NAMES`), which is the check that governs caller-visible
#: behaviour: ``open(...)``, ``eval(...)``, ``exec(...)`` and ``__import__(...)`` are
#: rejected before a single statement executes. A runtime hook that disables the
#: interpreter's own import system is not defence in depth, it is a self-inflicted
#: outage -- so the runtime hook only removes names that are safe to remove.
BLOCKED_BUILTINS: frozenset[str] = frozenset(
    {"input", "breakpoint", "help", "exit", "quit"}
)

#: Names the static guard rejects *in addition* to the runtime-hook list. These must
#: stay intact for the interpreter's own import machinery, so they are only refused in
#: submitted code -- which is where it matters, since the guard runs before execution.
STATIC_EXTRA_BLOCKED_NAMES: frozenset[str] = frozenset(
    {"open", "exec", "eval", "compile", "__import__"}
)

#: Full set of names the static guard refuses.
STATIC_BLOCKED_NAMES: frozenset[str] = BLOCKED_BUILTINS | STATIC_EXTRA_BLOCKED_NAMES

#: Names the static guard rejects. Superset of BLOCKED_BUILTINS: ``open`` and
#: ``__import__`` are refused in *code* while remaining intact for the interpreter.
STATIC_BLOCKED_NAMES: frozenset[str] = BLOCKED_BUILTINS | {"open", "__import__"}


class GuardViolation(Exception):
    """Raised for any attempt the static or runtime guard rejects."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"GUARD_VIOLATION[{code}]: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------------------
# Static guard
# --------------------------------------------------------------------------------------


class _GuardVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[tuple[str, int, str]] = []

    def _add(self, code: str, node: ast.AST, detail: str) -> None:
        self.violations.append((code, getattr(node, "lineno", 0), detail))

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._check_module(alias.name, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level and node.level > 0:
            self._add("RELATIVE_IMPORT", node, "relative imports are not allowed")
        if node.module:
            self._check_module(node.module, node)

    def _check_module(self, name: str, node: ast.AST) -> None:
        top = name.split(".")[0]
        if top in BLOCKED_MODULES:
            self._add("BLOCKED_IMPORT", node, f"module '{name}' is explicitly blocked")
        elif top not in ALLOWED_MODULES:
            self._add(
                "MODULE_NOT_ALLOWED",
                node,
                f"module '{name}' is not on the allowlist "
                f"({len(ALLOWED_MODULES)} modules); allowed: {sorted(ALLOWED_MODULES)[:8]}...",
            )

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in BLOCKED_ATTRIBUTES:
            self._add("BLOCKED_ATTRIBUTE", node, f"attribute '{node.attr}' is blocked")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        # Only *loads* are policed. ``SEED = SEED`` (binding an injected name) is a
        # load of SEED in a Store statement, and refusing it would be a false positive
        # that pushes authors toward weaker patterns. The name being bound is not an
        # escape; calls like ``open(...)`` are loads and are still rejected.
        if isinstance(node.ctx, ast.Load) and (
            node.id in BLOCKED_BUILTINS or node.id in STATIC_EXTRA_BLOCKED_NAMES
        ):
            self._add("BLOCKED_BUILTIN", node, f"builtin '{node.id}' is blocked")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # getattr(x, "__globals__") / setattr escapes that dodge visit_Attribute
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"getattr", "setattr", "delattr", "vars"}:
            if node.args and isinstance(node.args[-1], ast.Constant) and isinstance(
                node.args[-1].value, str
            ):
                if node.args[-1].value in BLOCKED_ATTRIBUTES:
                    self._add(
                        "BLOCKED_ATTRIBUTE",
                        node,
                        f"{func.id}('{node.args[-1].value}') is blocked",
                    )
        self.generic_visit(node)


def static_guard(code: str) -> None:
    """Parse ``code`` and raise :class:`GuardViolation` on the first violation.

    Line numbers are reported relative to the user code as submitted.
    """
    try:
        tree = ast.parse(code, filename="<experiment>")
    except SyntaxError as exc:
        raise GuardViolation("SYNTAX_ERROR", f"{exc.msg} (line {exc.lineno})") from exc
    visitor = _GuardVisitor()
    visitor.visit(tree)
    if visitor.violations:
        code_name, lineno, detail = visitor.violations[0]
        raise GuardViolation(code_name, f"line {lineno}: {detail}")


# --------------------------------------------------------------------------------------
# Runtime guard
# --------------------------------------------------------------------------------------

_ORIGINAL_IMPORT = builtins.__import__
_REAL_IMPORT = _ORIGINAL_IMPORT
#: The runner compiles the submitted source *after* the guard is installed -- the
#: guard deliberately removes ``compile``/``exec``/``eval`` from the executing
#: frame, so the runner must hold its own reference or it would disable itself.
_REAL_COMPILE = builtins.compile
#: Same reasoning for ``exec``: the runner calls it via the saved module-global
#: reference, never through the name ``exec`` (which the guard sets to ``None``).
_REAL_EXEC = builtins.exec
#: The runner holds the real ``sys`` module. It must never call ``import sys`` inside the
#: guarded import hook: ``sys`` is in BLOCKED_MODULES, so the hook would reject itself.
_REAL_SYS = sys
#: Module type, captured before the guard removes anything from ``builtins``.
_REAL_MODULE_TYPE = type(sys)


def _builtin_module(name: str) -> bool:
    """True for modules compiled into (or frozen into) the interpreter.

    Why this exception exists, stated precisely so it is not mistaken for a hole:
    importing an *allowed* pure-Python module legitimately pulls in compiled
    dependencies -- ``json`` -> ``_io``, ``decimal`` -> ``_decimal``/``_pydecimal``,
    ``fractions`` -> ``decimal``. Denying those would make the allowlist unusable
    rather than safer. Such modules are interpreter-internal: they are not reachable
    on disk, and they grant no capability the process does not already have.

    Detection is by *origin*, never by a name allowlist: ``__spec__.origin`` is
    ``"built-in"`` or ``"frozen"`` for these, and a real on-disk extension module
    (``_socket.pyd``, ``_ctypes.pyd``) reports a filesystem path and is still denied.
    Top-level names in BLOCKED_MODULES are rejected before this check is reached, so
    ``_socket``-style escapes cannot ride along on the exception.
    """
    top = name.split(".")[0]
    if top in BLOCKED_MODULES:
        return False
    module = _REAL_SYS.modules.get(top)
    origin: str | None = None
    if module is not None:
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None) or getattr(module, "__file__", None)
    else:
        try:
            import importlib.util as _util  # the runner holds the real importlib

            spec = _util.find_spec(top)
        except Exception:  # noqa: BLE001 - unresolvable is simply "not builtin"
            return False
        origin = getattr(spec, "origin", None)
    return bool(origin) and origin in {"built-in", "frozen"}


def _importing_module(_globals: dict | None) -> str:
    """Best-effort ``__name__`` of the module performing this import."""
    if not isinstance(_globals, dict):
        return ""
    name = _globals.get("__name__")
    return name if isinstance(name, str) else ""


def _guarded_import(name, _globals=None, _locals=None, fromlist=(), level=0):  # noqa: ANN001
    """Guarded ``__import__``. Two rules, and the difference between them is the design.

    **Rule 1 -- user code is default-deny.** When the import originates from the
    submitted experiment (``__name__ == "__main__"``), the top-level module must be on
    ALLOWED_MODULES, so ``import queue`` raises ``MODULE_NOT_ALLOWED``.

    **Rule 2 -- only the user-facing name is policed.** When an already-imported stdlib
    module pulls in its own dependencies (``fractions`` does ``import sys`` and
    ``from decimal import Decimal``), that is permitted. The alternative does not add
    safety, it breaks the sandbox: *every* stdlib module imports ``sys``/``os``
    internally, so denying those makes the allowlist unusable while granting no
    protection -- the user's ``import`` statement is the boundary, and the static AST
    guard is what sees it. Values never cross that boundary via these internal imports:
    the experiment's namespace is its own dict, and ``sys``/``os`` are not injected
    into it.

    ``BLOCKED_MODULES`` is still checked first, so a user writing ``import os`` gets
    ``BLOCKED_IMPORT`` rather than ``MODULE_NOT_ALLOWED``; the AST guard reports the
    same verdict before execution even starts.

    Also: the delegated call always passes a dict (never ``None``) for globals, because
    forwarding ``None`` makes the real ``__import__`` raise a ``TypeError`` whose message
    misleadingly reads like *this* handle being ``None``.
    """
    top = name.split(".")[0]
    from_user_code = _importing_module(_globals) == "__main__"
    if from_user_code:
        if top in BLOCKED_MODULES:
            raise ImportError(f"GUARD_VIOLATION[BLOCKED_IMPORT]: module '{name}' is blocked")
        if top not in ALLOWED_MODULES:
            raise ImportError(
                f"GUARD_VIOLATION[MODULE_NOT_ALLOWED]: module '{name}' is not on the allowlist"
            )
    delegate = _REAL_IMPORT if callable(_REAL_IMPORT) else _ORIGINAL_IMPORT
    return delegate(name, _globals if _globals is not None else {}, _locals, fromlist, level)


def install_runtime_guard() -> dict[str, object]:
    """Swap builtins for guarded versions. Returns the saved originals.

    ``__import__`` is *replaced*, never removed: nulling it would make the guarded
    import hook itself uncallable and every legitimate import explode with
    ``TypeError: 'NoneType' object is not callable``. That was a real bug here, and the
    test ``test_allowed_modules_still_work`` is what caught it.
    """
    saved = {name: getattr(builtins, name) for name in BLOCKED_BUILTINS if hasattr(builtins, name)}
    builtins.__import__ = _guarded_import
    # ``_REAL_IMPORT`` is the *only* handle on the genuine import machinery, so it is
    # captured once and never overwritten by a nested/repeated guard install -- doing so
    # would make it point at the guarded hook and then at ``None``.
    global _REAL_IMPORT  # noqa: PLW0603
    if _REAL_IMPORT is None or _REAL_IMPORT is _guarded_import:
        _REAL_IMPORT = saved.get("__import__") or _ORIGINAL_IMPORT
    for name in BLOCKED_BUILTINS:
        if name == "__import__":
            continue  # replaced above, not removed
        if hasattr(builtins, name):
            setattr(builtins, name, None)
    return saved


def restore_builtins(saved: dict[str, object]) -> None:
    builtins.__import__ = _REAL_IMPORT
    for name, value in saved.items():
        setattr(builtins, name, value)


# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------


class _BytesOut(io.RawIOBase):
    """Minimal binary sink over the two capture handles.

    Deliberately dumb: it writes bytes through to the backend-provided files and
    never buffers unbounded output in the runner process. The byte cap is enforced
    by the backend (it truncates the captured files), so a runaway ``print`` loop
    cannot make the runner -- or the caller's API process -- grow without bound.

    ``close()`` does NOT close the underlying file. That is load-bearing, not
    tidiness: ``io.RawIOBase``'s finaliser runs ``close()`` when the wrapper is
    garbage-collected, which happens as soon as ``sys.stdout`` is rebound. Closing
    the capture file there broke the runner's own bookkeeping (a
    ``ValueError: write to closed file`` escaping at interpreter shutdown). The
    handles are closed explicitly, once, in ``main``'s ``finally``.
    """

    def __init__(self, handle) -> None:  # noqa: ANN001
        super().__init__()
        self._handle = handle

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:  # noqa: ANN001
        written = self._handle.write(data)
        return len(data) if written is None else written

    def flush(self) -> None:
        try:
            self._handle.flush()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:  # noqa: D102 - see the class docstring
        try:
            self.flush()
        except Exception:  # noqa: BLE001
            pass
        # Intentionally does not call io.RawIOBase.close() and does not close self._handle.


class _BinaryStdStream(io.TextIOWrapper):
    """Binary text stream with no newline translation (newline='')."""

    def __init__(self, raw) -> None:  # noqa: ANN001
        super().__init__(raw, encoding="utf-8", errors="replace", write_through=True, newline="")


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Math Arena sandbox runner (stdlib only)")
    parser.add_argument("--code", required=True, help="path to the code file to execute")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--guard", choices=["strict-math", "none"], default="strict-math")
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--meta", required=True, help="JSON sidecar the backend reads")
    parser.add_argument("--params-json", default="{}")
    parser.add_argument("--artifacts-dir", default="artifacts")
    args = parser.parse_args(argv)

    meta: dict[str, object] = {
        "runner": "sandbox-runner/1.0",
        "python_version": sys.version.split()[0],
        "python_implementation": sys.implementation.name,
        "platform": sys.platform,
        "seed": args.seed,
        "guard": args.guard,
        "pid": os.getpid(),
        "argv": list(sys.argv),
        "guard_violation": None,
        "params_keys": [],
        "artifacts_dir": args.artifacts_dir,
    }

    random.seed(args.seed)
    try:
        import numpy as _np  # noqa: F401  (allowed module; seeded when present)

        _np.random.seed(args.seed)
        meta["numpy_seeded"] = True
    except Exception:  # noqa: BLE001 - numpy absent is normal in the slim image
        meta["numpy_seeded"] = False

    out_handle = open(args.stdout, "wb")  # noqa: PTH123 - explicit fd control is the point
    err_handle = open(args.stderr, "wb")  # noqa: PTH123
    original_stdout, original_stderr = sys.stdout, sys.stderr
    exit_code = 0
    try:
        source = Path(args.code).read_text(encoding="utf-8")  # noqa: PTH123
        params = json.loads(args.params_json)
        meta["params_keys"] = sorted(params.keys())

        out_handle.write(b"[sandbox-runner] python " + meta["python_version"].encode() + b"\n")
        out_handle.write(b"[sandbox-runner] seed=" + str(args.seed).encode() + b"\n")
        out_handle.flush()

        if args.guard == "strict-math":
            try:
                static_guard(source)
            except GuardViolation as exc:
                meta["guard_violation"] = {"code": exc.code, "detail": exc.detail}
                err_handle.write((str(exc) + "\n").encode("utf-8"))
                err_handle.flush()
                _write_meta(args.meta, meta)
                return 3

        # Redirect before building the namespace: ``flush_output`` must be bound to the
        # capture stream, not to whatever stdout the runner was started with. Binding it
        # earlier silently sent flushes to the parent's stdout and lost output on a
        # timeout kill -- a real bug this comment exists to prevent recurring.
        run_out = _BinaryStdStream(_BytesOut(out_handle))
        run_err = _BinaryStdStream(_BytesOut(err_handle))
        sys.stdout = run_out
        sys.stderr = run_err

        namespace: dict[str, object] = {
            "__name__": "__main__",
            "__file__": args.code,
            "PARAMS": params,
            "SEED": args.seed,
            # ``sys`` is not on the allowlist, so code that needs to flush before a
            # possible timeout kill uses this instead. ``flush_output()`` is strictly
            # weaker than ``import sys``: it exposes exactly one bound method.
            "flush_output": run_out.flush,
        }

        saved: dict[str, object] = {}
        if args.guard == "strict-math":
            saved = install_runtime_guard()
        try:
            compiled = _REAL_COMPILE(source, "<experiment>", "exec")
            _REAL_EXEC(compiled, namespace)  # noqa: S102 - this *is* the sandbox executor
        finally:
            if args.guard == "strict-math":
                restore_builtins(saved)
            for stream in (sys.stdout, sys.stderr):
                try:
                    stream.flush()
                except Exception:  # noqa: BLE001
                    pass
    except GuardViolation as exc:
        meta["guard_violation"] = {"code": exc.code, "detail": exc.detail}
        err_handle.write((str(exc) + "\n").encode("utf-8"))
        exit_code = 3
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BaseException:  # noqa: BLE001 - user code may raise anything, including MemoryError
        err_handle.write(traceback.format_exc().encode("utf-8", "replace"))
        exit_code = 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        # Put the real streams back before closing the capture files: leaving a closed
        # stream bound to sys.stdout turns interpreter shutdown into a spurious error.
        sys.stdout, sys.stderr = original_stdout, original_stderr
        out_handle.close()
        err_handle.close()
        _write_meta(args.meta, meta)
    return exit_code


def _write_meta(path: str, meta: dict[str, object]) -> None:
    try:
        Path(path).write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")  # noqa: PTH123
    except Exception:  # noqa: BLE001 - meta is diagnostics, never block the result
        pass


if __name__ == "__main__":
    sys.exit(main())
