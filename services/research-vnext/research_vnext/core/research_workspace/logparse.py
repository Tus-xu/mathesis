"""RV17 · LaTeX `.log` → 结构化 `log_lines`（**必须含 file + line**）。

编译失败后前端要能"点错误行跳到源码"。裸日志做不到这一点：TeX 的错误
出现在 `! ...` 行，行号却在其后的 `l.<n>` 行；当前文件要靠 `(`/`)` 的
嵌套与栈来还原。本模块把这三件事做成**确定性**解析（不调用模型）。

输出契约（宪法 §23 / §3.4）：

    log_lines: [{"ts","level","text","file","line"}]

- `level` ∈ {`error`, `warning`, `info`}
- `file`：当前 TeX 文件（栈顶；无法判定时回退 entry）
- `line`：TeX 行号（无法判定为 `null`）
- `ts`：**序列时间戳**（运行起始 + 行序号毫秒），仅用于稳定排序，
  不是 TeX 内部时间 —— 如实声明，不假装日志带时间。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_FILE_OPEN_RE = re.compile(r"\(((?:\./)?[^\s()]+\.(?:tex|sty|cls|bib|cfg|def|clo|fd))")
_ERROR_RE = re.compile(r"^!\s*(.*)$")
_PKG_ERROR_RE = re.compile(r"^!?\s*(Package\s+\S+\s+Error:|LaTeX Error:)\s*(.*)$")
_PKG_WARN_RE = re.compile(r"^(Package\s+\S+\s+Warning:|LaTeX Warning:)\s*(.*)$")
_OVERFULL_RE = re.compile(r"^(Overfull|Underfull)\s+\\[hv]box")
_LINE_NO_RE = re.compile(r"^l\.(\d+)")
_INPUT_LINE_RE = re.compile(r"on input line (\d+)")
_AT_LINES_RE = re.compile(r"at lines (\d+)--(\d+)")
_FILELINE_COLON_RE = re.compile(r"^((?:\./)?[^\s:]+\.tex):(\d+):\s*(.*)$")
_OUTPUT_PAGES_RE = re.compile(r"Output written on .*?\((\d+) page")


def _norm_file(raw: str, *, entry: str) -> str:
    f = (raw or "").strip()
    if f.startswith("./"):
        f = f[2:]
    return f or entry


def _summarize(text: str, limit: int = 400) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _scan_file_stack(raw_line: str, stack: list[str], *, entry: str) -> None:
    """按**左右顺序**扫描一行日志，维护 TeX 文件栈（`stack` 原地更新）。

    为什么不能只数括号：日志里的 `)` 不全是「文件结束」——`(size option)`、
    `(preloaded format=…)`、`(Font)` 续行等**匿名括号**同样含 `)`。旧实现
    「先把本行所有 `(file` 压栈、再数 `)` 弹栈」有两个后果（2026-09-17 实测复现）：

    - `) (./sections/b.tex` 这种**先关后开写在同行**的写法，会把**刚压入的** b.tex
      弹掉，栈永久停在 a.tex —— 错误/警告被**归因到错误的文件**：
      点在错误行上跳到别的文件（违 §24），下游 `latex_error` 也会解释错文件、给不出补丁；
    - 匿名 `)` 会误弹真实文件（本文件 173/214 个含 `)` 的行是 `(size option)` 这类）。

    现在：文件型 `(` 压栈；`)` 先抵消**行内匿名括号**，抵消不掉才弹文件栈。
    匿名深度**逐行重置** —— TeX 在 79 列硬换行会把括号截断，跨行累计反而会吞掉真实关闭。
    """
    anon = 0
    pos = 0
    for m in _FILE_OPEN_RE.finditer(raw_line):
        for ch in raw_line[pos:m.start()]:
            if ch == ")":
                if anon > 0:
                    anon -= 1
                elif stack:
                    stack.pop()
            elif ch == "(":
                anon += 1
        stack.append(_norm_file(m.group(1), entry=entry))
        pos = m.end()
    for ch in raw_line[pos:]:
        if ch == ")":
            if anon > 0:
                anon -= 1
            elif stack:
                stack.pop()
        elif ch == "(":
            anon += 1


def parse_log_lines(
    log: str,
    *,
    entry: str = "main.tex",
    base_ts: datetime | None = None,
) -> tuple[list[dict], int, int]:
    """解析裸 xelatex 日志。

    Returns:
        `(log_lines, error_count, warning_count)`。
    """
    base = base_ts or datetime.now()
    lines = (log or "").splitlines()
    stack: list[str] = []
    out: list[dict] = []
    errors = 0
    warnings = 0

    def emit(level: str, text: str, line: int | None, file_override: str | None = None) -> None:
        nonlocal errors, warnings
        if level == "error":
            errors += 1
        elif level == "warning":
            warnings += 1
        ts = (base + timedelta(milliseconds=len(out))).isoformat()
        out.append(
            {
                "ts": ts,
                "level": level,
                "text": text,
                "file": file_override or (stack[-1] if stack else entry),
                "line": line,
            }
        )

    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i]

        # 1) 维护文件栈：**从左到右**扫描本行（TeX 会把「关旧文件 + 开新文件」写在同行）。
        _scan_file_stack(raw_line, stack, entry=entry)

        # 2) `file:line: message` 形式（部分工具/引擎）。
        fm = _FILELINE_COLON_RE.match(raw_line)
        if fm:
            emit(
                "error",
                _summarize(fm.group(3)),
                int(fm.group(2)),
                file_override=_norm_file(fm.group(1), entry=entry),
            )
            i += 1
            continue

        # 3) 错误块：`! ...`（含 Package Error）。
        em = _ERROR_RE.match(raw_line)
        if em:
            message = _summarize(em.group(1))
            line_no: int | None = None
            for j in range(i + 1, min(i + 5, n)):
                lm = _LINE_NO_RE.match(lines[j])
                if lm:
                    line_no = int(lm.group(1))
                    break
            if line_no is None:
                im = _INPUT_LINE_RE.search(raw_line)
                if im:
                    line_no = int(im.group(1))
            emit("error", message, line_no)
            i += 1
            continue

        # 4) 警告：Package/LaTeX Warning、Overfull/Underfull。
        wm = _PKG_WARN_RE.match(raw_line)
        if wm:
            line_no = None
            im = _INPUT_LINE_RE.search(raw_line)
            if im:
                line_no = int(im.group(1))
            emit("warning", _summarize(f"{wm.group(1)} {wm.group(2)}"), line_no)
            i += 1
            continue

        om = _OVERFULL_RE.match(raw_line)
        if om:
            line_no = None
            am = _AT_LINES_RE.search(raw_line)
            if am:
                line_no = int(am.group(1))
            elif (im := _INPUT_LINE_RE.search(raw_line)):
                line_no = int(im.group(1))
            emit("warning", _summarize(raw_line), line_no)
            i += 1
            continue

        # 5) 汇总信息行（info）：只留一条有意义的产出信息。
        om2 = _OUTPUT_PAGES_RE.search(raw_line)
        if om2:
            emit("info", _summarize(f"Output written ({om2.group(1)} pages)"), None)
            i += 1
            continue

        i += 1

    return out, errors, warnings


__all__ = ["parse_log_lines"]
