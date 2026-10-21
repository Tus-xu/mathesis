r"""RV17 · LaTeX 日志 `file`/`line` 归因的回归测试（纯函数：不连库、不联网）。

被锁定的缺陷（2026-09-17 实测复现，输入为**真实 xelatex 日志**）：

    注入 `sections/theory.tex:4 \undefinedcommand{}` 后，`parse_log_lines` 却把
    `! Undefined control sequence.` 归成 `sections/introduction.tex:4` ——
    前端「点错误行跳转」会跳到**别的文件**（违宪法 §24），下游 `latex_error`
    能力也会解释错文件、给不出可应用补丁（C10 因此卡住）。

根因：旧实现「先把本行所有 `(file` 压栈、再数本行 `)` 弹栈」，而 TeX 把
「关旧文件 + 开新文件」写成同一行 `) (./sections/method.tex` —— 于是弹掉的是
**刚压入的** method.tex，栈永久停在 introduction.tex。此外日志里 `(size option)`、
`(Font)` 等**匿名括号**也含 `)`，会误弹真实文件。

本文件同时锁定上述两类，以及普通嵌套不得回归。
"""

from __future__ import annotations

from research_vnext.core.research_workspace.logparse import parse_log_lines

# ---------------------------------------------------------------------------
# 真实日志片段（逐字取自 2026-09-17 的 xelatex 失败日志；仅取关键邻域）
# 关注：`) (./sections/method.tex` 这一行的「先关后开」。
# ---------------------------------------------------------------------------
REAL_EXCERPT = r"""(/usr/local/texlive/2026/texmf-dist/tex/latex/amsfonts/umsb.fd
File: umsb.fd 2013/01/14 v3.01 AMS symbols B
)
(./sections/introduction.tex

LaTeX Warning: Citation `Ciarlet2002' on page 1 undefined on input line 6.

) (./sections/method.tex

LaTeX Warning: Citation `Ciarlet2002' on page 1 undefined on input line 2.

) (./sections/theory.tex
! Undefined control sequence.
l.4 \undefinedcommand
                     {}
The control sequence at the end of the top line
of your error message was never \def'ed. If you have
misspelled it (e.g., `\hobx'), type `I' and the correct
"""


def _find(lines: list[dict], *, level: str, contains: str) -> dict:
    for ln in lines:
        if ln.get("level") == level and contains in str(ln.get("text") or ""):
            return ln
    raise AssertionError(f"未找到 {level} / {contains!r}：{lines}")


def test_real_excerpt_error_lands_on_the_failing_file() -> None:
    """真实日志：错误必须落在 `sections/theory.tex:4`，而不是栈里残留的 introduction.tex。"""
    lines, errors, _ = parse_log_lines(REAL_EXCERPT, entry="main.tex")
    err = _find(lines, level="error", contains="Undefined control sequence")
    assert err["file"] == "sections/theory.tex", f"归因错误：{err['file']}"
    assert err["line"] == 4
    assert errors == 1


def test_real_excerpt_warnings_belong_to_their_own_files() -> None:
    """两条 citation 警告分别属于 introduction.tex:6 与 method.tex:2（不能都算到同一个文件）。"""
    lines, _, warnings = parse_log_lines(REAL_EXCERPT, entry="main.tex")
    cites = [ln for ln in lines if ln.get("level") == "warning" and "Citation" in str(ln.get("text"))]
    assert len(cites) == 2, cites
    assert (cites[0]["file"], cites[0]["line"]) == ("sections/introduction.tex", 6)
    assert (cites[1]["file"], cites[1]["line"]) == ("sections/method.tex", 2)
    assert warnings == 2


def test_anonymous_parens_do_not_pop_file_stack() -> None:
    """`(size option)` / `(Font)` 这类匿名括号不得把文件栈弹空。"""
    log = (
        "(./sections/a.tex\n"
        "File: size11.clo 2025/01/22 v1.4n Standard LaTeX file (size option)\n"
        "(Font)                  OT1/cmtt/m/n --> TU/lmtt/b/n on input line 12.\n"
        "LaTeX Warning: Reference `X' on page 1 undefined on input line 7.\n"
        "! Undefined control sequence.\n"
        "l.9 \\oops\n"
    )
    lines, errors, _ = parse_log_lines(log, entry="main.tex")
    warn = _find(lines, level="warning", contains="Reference")
    err = _find(lines, level="error", contains="Undefined control sequence")
    assert warn["file"] == "sections/a.tex", f"匿名括号误弹栈：{warn['file']}"
    assert err["file"] == "sections/a.tex"
    assert err["line"] == 9
    assert errors == 1


def test_plain_nesting_still_works() -> None:
    """普通「进入子文件 → 关闭回到主文件」的归因不得回归。"""
    log = (
        "(./main.tex\n"
        "(./sections/sub.tex\n"
        "! Undefined control sequence.\n"
        "l.3 \\bad\n"
        ")\n"
        "! Missing $ inserted.\n"
        "l.12 x\n"
    )
    lines, errors, _ = parse_log_lines(log, entry="main.tex")
    assert _find(lines, level="error", contains="Undefined control sequence")["file"] == "sections/sub.tex"
    assert _find(lines, level="error", contains="Missing $ inserted")["file"] == "main.tex"
    assert errors == 2


def test_nested_close_and_open_same_line_keeps_correct_top() -> None:
    """`) (./x.tex` 连续出现两次时，栈顶必须跟着最新打开的文件走（旧实现会卡在第一个）。"""
    log = (
        "(./a.tex\n"
        ") (./b.tex\n"
        ") (./c.tex\n"
        "! Undefined control sequence.\n"
        "l.1 \\zzz\n"
    )
    lines, _, _ = parse_log_lines(log, entry="main.tex")
    assert _find(lines, level="error", contains="Undefined control sequence")["file"] == "c.tex"
