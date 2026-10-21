"""RV17 · LaTeX 大纲解析（`\\section` / `\\subsection` / `\\begin{abstract}` → 带行号结构树）。

Editor 需要**行号**才能把大纲项与源码位置对齐（宪法 §18）。本模块只做
词法级扫描（不编译、不展开宏），因此是确定性且 O(n) 的。
"""

from __future__ import annotations

import re
from typing import Any

#: `\section{...}` / `\section*[...]{...}`（chapter/section/subsection/subsubsection）。
_SECTION_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection)\*?\s*(?:\[[^\]]*\])?\s*\{",
)
_ABSTRACT_RE = re.compile(r"\\begin\{abstract\}")
_BIB_RE = re.compile(r"\\(?:begin\{thebibliography\}|bibliography\s*\{|printbibliography)")
_APPENDIX_RE = re.compile(r"\\appendix\b")

_LEVEL_BY_CMD = {
    "chapter": 1,
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
}

_STRIP_CMD_RE = re.compile(r"\\(?:label|cite[pt]?|ref|eqref|autoref)\s*\{[^{}]*\}")


def _read_braced(text: str, open_index: int) -> tuple[str, int]:
    """从 `open_index`（指向 `{`）开始读取配平花括号内的内容。

    Returns:
        `(inner_text, index_after_closing_brace)`；未配平时读到行末。
    """
    depth = 0
    i = open_index
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2  # 跳过被转义的字符
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : i], i + 1
        elif ch == "\n" and depth <= 0:
            break
        i += 1
    return text[open_index + 1 :], n


def _clean_title(raw: str) -> str:
    title = _STRIP_CMD_RE.sub("", raw or "")
    title = title.replace("~", " ").replace("\\\\", " ")
    return re.sub(r"\s+", " ", title).strip()


def build_tree(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """由扁平 items（按文档序）组装嵌套树（level 小的为父）。"""
    tree: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for it in items:
        node = {**it, "children": []}
        while stack and stack[-1]["level"] >= node["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            tree.append(node)
        stack.append(node)
    return tree


def parse_outline(tex: str, *, source_file: str = "main.tex") -> dict[str, Any]:
    """解析 TeX 源码，返回带行号的结构树。

    Returns:
        {
          "source_file": str,
          "items": [{"title","level","line","kind"}, ...],   # 扁平、按行号升序
          "tree":  [{"title","level","line","kind","children":[...]}, ...],  # 嵌套
        }
    """
    source = tex or ""
    items: list[dict[str, Any]] = []

    for lineno, line in enumerate(source.splitlines(), start=1):
        m = _SECTION_RE.search(line)
        if m:
            brace = line.find("{", m.start())
            raw, _ = _read_braced(line, brace)
            items.append(
                {
                    "title": _clean_title(raw) or "(未命名)",
                    "level": _LEVEL_BY_CMD.get(m.group(1), 1),
                    "line": lineno,
                    "kind": m.group(1),
                }
            )
            continue
        if _ABSTRACT_RE.search(line):
            items.append({"title": "摘要", "level": 1, "line": lineno, "kind": "abstract"})
            continue
        if _APPENDIX_RE.search(line):
            items.append({"title": "附录", "level": 1, "line": lineno, "kind": "appendix"})
            continue
        if _BIB_RE.search(line):
            items.append({"title": "参考文献", "level": 1, "line": lineno, "kind": "bibliography"})

    items.sort(key=lambda it: it["line"])

    return {"source_file": source_file, "items": items, "tree": build_tree(items)}


__all__ = ["build_tree", "parse_outline"]
