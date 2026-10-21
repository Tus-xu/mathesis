"""RV14 · 编译诊断与限制（红→绿）。

任务书 6：编译失败返回结构化诊断、source node locator 和脱敏 log。
任务书 3：限 CPU/memory/disk/time/output；命令 allowlist。
超时逻辑用**可注入 runner** 确定性测试（不赌真实 TeX 的耗时）。
"""

from __future__ import annotations

import pytest

from research_vnext.core.export.compiler import (
    CompileError,
    CompileResult,
    CompileTimeout,
    LocalTexCompiler,
)
from research_vnext.core.export.diagnostics import parse_compile_log

FAKE_LOG = r"""This is XeTeX, Version 3.141592653-2.6 (TeX Live 2025)
entering extended mode
(./main.tex
LaTeX2e <2025-06-01>
(/tmp/rv14-work-e2ab/texmf/tex/latex/ctex/ctexart.cls)
! LaTeX Error: Environment alignat undefined.
See the LaTeX manual or LaTeX Companion for explanation.
l.45 \begin{alignat}

! Undefined control sequence.
l.112 ...\badmacro{3}
C:\Users\someone\AppData\Local\Temp\rv14\main.tex: absolute path leak
No pages of output.
"""


def test_parse_compile_log_extracts_errors_with_line() -> None:
    d = parse_compile_log(FAKE_LOG, node_markers={45: "e1", 112: "e2"})
    codes = [e["code"] for e in d["errors"]]
    assert "LATEX_ERROR" in codes and "UNDEFINED_CONTROL_SEQUENCE" in codes
    first = d["errors"][0]
    assert first["line"] == 45
    assert first["nodeId"] == "e1"
    assert d["outputPages"] == 0


def test_diagnostics_redact_paths_and_cap_size() -> None:
    d = parse_compile_log(FAKE_LOG, node_markers={})
    red = d["logRedacted"]
    assert "C:\\Users" not in red
    assert "/tmp/rv14-work-e2ab" not in red
    assert len(red) <= 20000


def test_node_locator_maps_error_line_to_source_node() -> None:
    d = parse_compile_log(FAKE_LOG, node_markers={112: "e2"})
    err = [e for e in d["errors"] if e["line"] == 112][0]
    assert err["nodeId"] == "e2"


# --------------------------------------------------------------------------
# 超时 / 输出上限（stub runner，确定性）
# --------------------------------------------------------------------------


class _SlowRunner:
    def __init__(self, sleep: float) -> None:
        self.sleep = sleep
        self.calls = 0

    def __call__(self, argv, *, workdir, timeout_seconds, env=None):
        import time

        self.calls += 1
        time.sleep(self.sleep)
        return CompileResult(returncode=0, log="", pages=1)


def test_compile_timeout_is_enforced_and_structured() -> None:
    runner = _SlowRunner(sleep=30)
    c = LocalTexCompiler(runner=runner, timeout_seconds=1, max_output_bytes=10**9)
    with pytest.raises(CompileTimeout) as ei:
        c.compile(workdir=".", passes=2)
    assert ei.value.code == "EXPORT_COMPILE_TIMEOUT"
    assert runner.calls == 1  # 超时后立即停止，不再跑第二遍


class _HugeOutputRunner:
    def __call__(self, argv, *, workdir, timeout_seconds, env=None):
        (workdir / "main.log").write_bytes(b"x" * (10**7))
        return CompileResult(returncode=0, log="x" * (10**7), pages=1)


def test_output_size_cap(tmp_path) -> None:
    c = LocalTexCompiler(runner=_HugeOutputRunner(), timeout_seconds=30, max_output_bytes=1024)
    with pytest.raises(CompileError) as ei:
        c.compile(workdir=tmp_path, passes=1)
    assert ei.value.code == "EXPORT_OUTPUT_LIMIT"


class _OkRunner:
    def __call__(self, argv, *, workdir, timeout_seconds, env=None):
        return CompileResult(returncode=0, log="ok", pages=3)


def test_compile_success_returns_pages(tmp_path) -> None:
    c = LocalTexCompiler(runner=_OkRunner(), timeout_seconds=30, max_output_bytes=10**6)
    res = c.compile(workdir=tmp_path, passes=2)
    assert res.pages == 3
