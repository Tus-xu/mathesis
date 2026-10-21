"""RV14 · 编译日志结构化诊断。

任务书 6：编译失败返回结构化诊断（错误码 + TeX 行号 → source node
locator）与**脱敏** log（绝对路径一律 [PATH]），且截断到 20KB。
"""

from __future__ import annotations

import re

_LOG_CAP = 20000

#: `! LaTeX Error: ...` / `! Undefined control sequence.`
_ERROR_LINE = re.compile(r"^!\s*(.*)$")
_LINE_NO = re.compile(r"^l\.(\d+)")
_OUTPUT_PAGES = re.compile(r"Output written on .*?\((\d+) page")

# 脱敏：Windows 绝对路径与 POSIX 多段绝对路径 → [PATH]
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s\)\"']+")
_POSIX_PATH = re.compile(r"(?<!\.)[/\\](?:tmp|home|root|Users|var|usr|etc|opt|work|srv)[^\s\)\"',;]*")


def _redact(log: str) -> str:
    out = _WIN_PATH.sub("[PATH]", log or "")
    out = _POSIX_PATH.sub("[PATH]", out)
    if len(out) > _LOG_CAP:
        out = out[:_LOG_CAP] + "\n... [RV14: log 截断至 20000 字符]"
    return out


def parse_compile_log(log: str, *, node_markers: dict[int, str] | None = None) -> dict:
    """解析 xelatex 日志 → 结构化诊断。

    `node_markers`：body 中 `%%RV14NODE:<nodeId>:L<line>` 注释行映射出的
    「TeX 行号 → PaperSpec 节点」对照表（由 serializer 产物与 body 行号
    计算得到）。错误行号就近映射到节点；映射不到时 nodeId 为空串。
    """
    markers = {int(k): str(v) for k, v in (node_markers or {}).items()}
    errors: list[dict] = []
    lines = (log or "").splitlines()
    for i, line in enumerate(lines):
        m = _ERROR_LINE.match(line)
        if not m:
            continue
        msg = m.group(1).strip()
        code = "LATEX_ERROR"
        if "Undefined control sequence" in msg:
            code = "UNDEFINED_CONTROL_SEQUENCE"
        elif "Emergency stop" in msg:
            code = "LATEX_EMERGENCY_STOP"
        elif "Missing" in msg and "delimited" in msg:
            code = "LATEX_MATH_ERROR"
        # 就近找 `l.<n>`（TeX 错误块内 1–3 行内出现）
        line_no = 0
        for j in range(i + 1, min(i + 4, len(lines))):
            lm = _LINE_NO.match(lines[j])
            if lm:
                line_no = int(lm.group(1))
                break
        node_id = markers.get(line_no, "")
        errors.append(
            {"code": code, "line": line_no, "nodeId": node_id, "message": msg[:200]}
        )

    pages = 0
    pm = _OUTPUT_PAGES.search(log or "")
    if pm:
        pages = int(pm.group(1))
    elif "No pages of output" in (log or ""):
        pages = 0

    return {"errors": errors, "outputPages": pages, "logRedacted": _redact(log or "")}
