"""RV14 · PaperSpec → Markdown / LaTeX 确定性 serializer。

任务书 1：全部 11 种节点类型**显式**转换；无 LLM 参与、无静默丢弃；
同输入两次序列化逐字节一致（无时间戳/随机源）。
任务书 7：不要求 Markdown/LaTeX 反写无损；lineage 头保留溯源性。

静态守卫：进入 LaTeX 的**数学 latex**（唯一原样进入编译器的内容）
先过禁止序列检查 —— shell-escape/文件读写的第一道防线在**写入前**，
第二道防线是编译容器的 `-no-shell-escape` + no-network。
"""

from __future__ import annotations

import re
from typing import Any

from .csl import render_reference

SERIALIZER_VERSION = "2"
MAX_BLOCKS = 500

#: 允许导出的格式（表内 key = 内部名；paper.<ext> = 产物文件名）
FORMAT_EXTENSIONS = {"markdown": "paper.md", "latex": "paper.tex", "pdf": "paper.pdf"}


class SerializerError(Exception):
    """带 source node locator 的序列化失败（code 供 JobContract 分类）。"""

    def __init__(self, code: str, message: str, *, node_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.node_id = node_id


# --------------------------------------------------------------------------
# 静态守卫：禁止控制序列（\includegraphics 显式放行）
# --------------------------------------------------------------------------

#: (名字, 正则)。`(?![a-zA-Z])` 保证 \include 不误伤 \includegraphics。
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rf"\\{name}\d*(?![a-zA-Z])"))
    for name in (
        "write", "openout", "openin", "input", "include", "catcode",
        "csname", "directlua", "luaexec", "immediate", "special", "read",
    )
)


def guard_latex(latex: str, *, node_id: str = "") -> None:
    """数学 latex 的静态守卫。命中即 EXPORT_FORBIDDEN_SEQUENCE（永久失败）。"""
    s = latex or ""
    for _name, pat in _FORBIDDEN_PATTERNS:
        if pat.search(s):
            raise SerializerError(
                "EXPORT_FORBIDDEN_SEQUENCE",
                "公式包含禁止的控制序列（shell-escape / 文件读写 / 脚本执行类）",
                node_id=node_id,
            )


# --------------------------------------------------------------------------
# LaTeX 转义（文本层：所有非数学文本都必须经过）
# --------------------------------------------------------------------------

_LATEX_ESCAPES: tuple[tuple[str, str], ...] = (
    ("\\", r"\textbackslash "),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
)


def _escape_latex(text: str) -> str:
    out = text or ""
    for ch, rep in _LATEX_ESCAPES:
        out = out.replace(ch, rep)
    return out


# --------------------------------------------------------------------------
# 行内富文本（RV14 v2：**粗体** 与 $行内公式$）
#
# 约定（导出层，非 PaperSpec canonical 结构变更）：
# - `**文本**` → 粗体（LaTeX \textbf{}）；
# - `$latex$` → 行内公式（LaTeX 原样嵌入；md 原样保留）。
# 解析在**转义之前**拆段：文本段照常转义，公式段过 guard 后原样保留，
# 粗体段对其内部文本转义后再包 \textbf。两标记不可交叉嵌套。
# --------------------------------------------------------------------------

_INLINE_TOKEN = re.compile(r"(\*\*.+?\*\*|\$[^$]+\$)", re.DOTALL)


def _inline_to_latex(text: str, *, node_id: str = "") -> str:
    out: list[str] = []
    pos = 0
    for m in _INLINE_TOKEN.finditer(text or ""):
        out.append(_escape_latex(text[pos:m.start()]))
        tok = m.group(0)
        if tok.startswith("**"):
            inner = tok[2:-2]
            out.append(f"\\textbf{{{_escape_latex(inner)}}}")
        else:  # $…$ 行内公式：唯一原样进入编译器的内容，先过静态守卫
            latex = tok[1:-1]
            guard_latex(latex, node_id=node_id)
            out.append(f"${latex}$")
        pos = m.end()
    out.append(_escape_latex(text[pos:]))
    return "".join(out)


def _inline_to_markdown(text: str, *, node_id: str = "") -> str:
    out: list[str] = []
    pos = 0
    for m in _INLINE_TOKEN.finditer(text or ""):
        out.append(text[pos:m.start()])
        tok = m.group(0)
        if tok.startswith("**"):
            out.append(tok)  # md 粗体原样
        else:
            guard_latex(tok[1:-1], node_id=node_id)  # md 行内公式原样
            out.append(tok)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


# --------------------------------------------------------------------------
# 导出层列表约定（v2）
# --------------------------------------------------------------------------

_BULLET_ITEM = re.compile(r"^\s*[-*]\s+(.*)$")
_ORDERED_ITEM = re.compile(r"^\s*\d+[.、]\s+(.*)$")


def _detect_list(raw: str) -> str:
    """'bullet' | 'ordered' | ''。要求段落中**所有**非空行都是同款列表项。"""
    rows = [ln for ln in (raw or "").splitlines() if ln.strip()]
    if not rows:
        return ""
    if all(_BULLET_ITEM.match(ln) for ln in rows):
        return "bullet"
    if all(_ORDERED_ITEM.match(ln) for ln in rows):
        return "ordered"
    return ""


def _split_list_items(raw: str) -> list[str]:
    items: list[str] = []
    for ln in (raw or "").splitlines():
        m = _BULLET_ITEM.match(ln) or _ORDERED_ITEM.match(ln)
        if m:
            items.append(m.group(1).strip())
    return items or [raw]


# --------------------------------------------------------------------------
# 计数（SERIALIZER-COVERAGE 的机器判据）
# --------------------------------------------------------------------------


def node_type_counts(blocks: list[dict], *, include_review_marks: bool = False) -> dict:
    """按类型计数：included = 进入产物；excluded = 被排除（review_mark）。"""
    included: dict[str, int] = {}
    excluded: dict[str, int] = {}
    for b in blocks:
        t = str((b or {}).get("type") or "unknown")
        if t == "review_mark" and not include_review_marks:
            included.setdefault(t, 0)
            included[t] += 0
            excluded[t] = excluded.get(t, 0) + 1
        else:
            included[t] = included.get(t, 0) + 1
            excluded.setdefault(t, 0)
    return {"included": included, "excluded": excluded}


# --------------------------------------------------------------------------
# 公共准备
# --------------------------------------------------------------------------


def _check_input(blocks: list[dict]) -> None:
    if not blocks:
        raise SerializerError("EXPORT_EMPTY_DOCUMENT", "PaperSpec 没有任何可导出节点")
    if len(blocks) > MAX_BLOCKS:
        raise SerializerError(
            "EXPORT_DOC_TOO_LARGE",
            f"节点数 {len(blocks)} 超过硬上限 {MAX_BLOCKS}（导出前请拆分文档）",
        )


def _lineage_line(meta: dict, *, tex: bool) -> str:
    pid = str(meta.get("paperId") or "")
    rev = int(meta.get("revision") or 0)
    chash = str(meta.get("contentHash") or "")
    body = (
        f"paperId={pid} revision={rev} contentHash={chash} "
        f"serializerVersion={SERIALIZER_VERSION}"
    )
    return f"% rv14 lineage: {body}" if tex else f"<!-- rv14 lineage: {body} -->"


def _citation_numbers(blocks: list[dict]) -> list[str]:
    """引用 ref 的首现顺序（确定性编号的来源）。"""
    order: list[str] = []
    for b in blocks:
        if (b or {}).get("type") != "citation":
            continue
        for r in (b.get("refs") or []):
            r = str(r)
            if r and r not in order:
                order.append(r)
    return order


def _reference_text(ref: str, citations: dict | None) -> str:
    csl = (citations or {}).get(ref)
    if not csl:
        return f"（引用元数据缺失）{ref}"
    return render_reference(csl)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def serialize_markdown(
    blocks: list[dict],
    *,
    meta: dict,
    citations: dict | None = None,
    include_review_marks: bool = False,
) -> str:
    _check_input(blocks)
    order = _citation_numbers(blocks)
    num_of = {ref: i + 1 for i, ref in enumerate(order)}
    lines: list[str] = [_lineage_line(meta, tex=False), ""]

    for b in blocks:
        ntype = (b or {}).get("type")
        nid = str((b or {}).get("id") or "")
        if ntype == "section":
            number = str((b or {}).get("number") or "").strip()
            head_text = str(b.get("text") or "")
            head = f"# {number} {head_text}".strip() if number else f"# {head_text}"
            lines += [head, ""]
        elif ntype == "heading":
            level = max(1, min(6, int((b or {}).get("level") or 1)))
            lines += ["#" * level + " " + _inline_to_markdown(str(b.get("text") or ""), node_id=nid), ""]
        elif ntype == "paragraph":
            text = _inline_to_markdown(str(b.get("text") or ""), node_id=nid)
            claim = str(b.get("claimRef") or "").strip()
            if claim:
                text = f"{text} [claim:{claim}]".strip()
            lines += [text, ""]
        elif ntype == "equation":
            latex = str(b.get("latex") or "")
            guard_latex(latex, node_id=nid)
            lines += [f"$${latex}$$" if b.get("display") else f"${latex}$", ""]
        elif ntype == "citation":
            for r in (b.get("refs") or []):
                r = str(r)
                n = num_of.get(r)
                lines.append(f"[{n}]" if n else f"[ref:{r[:8]}]")
            lines.append("")
        elif ntype == "figure":
            anchor = str((b.get("refs") or ["?"])[0])
            caption = str(b.get("caption") or "")
            lines += [f"![{caption}](rv14-asset://{anchor})", "",
                      f"**图**：{caption}（资产 anchor: {anchor}）", ""]
        elif ntype == "table":
            anchor = str((b.get("refs") or ["?"])[0])
            caption = str(b.get("caption") or "")
            cols = "、".join(str(c) for c in (b.get("columns") or []))
            suffix = f"，列：{cols}" if cols else ""
            lines += [f"**表**：{caption}{suffix}（资产 anchor: {anchor}）", ""]
        elif ntype == "callout":
            kind = str(b.get("kind") or "NOTE")
            lines += [f"> **{kind}**: {_inline_to_markdown(str(b.get('text','')), node_id=nid)}", ""]
        elif ntype == "claim_ref":
            ref0 = str((b.get("refs") or [""])[0])
            lines += [f"{b.get('text','')} [claim:{ref0}]".strip(), ""]
        elif ntype == "evidence_ref":
            ref0 = str((b.get("refs") or [""])[0])
            lines += [f"{b.get('text','')} [evidence:{ref0}]".strip(), ""]
        elif ntype == "review_mark":
            if include_review_marks:
                lines += [f"> [REVIEW] {b.get('text','')}", ""]
        else:  # 未知类型：显式落一个标记行，绝不静默丢弃
            lines += [f"<!-- 未知节点类型 {ntype!r}（id={nid}）未导出 -->", ""]

    if order:
        lines += ["## 参考文献", ""]
        for i, ref in enumerate(order):
            lines.append(f"{i + 1}. {_reference_text(ref, citations)}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# LaTeX（body：不含 documentclass/preamble —— 由 template.render_document 包裹）
# --------------------------------------------------------------------------


def serialize_latex(
    blocks: list[dict],
    *,
    meta: dict,
    citations: dict | None = None,
    include_review_marks: bool = False,
) -> str:
    _check_input(blocks)
    order = _citation_numbers(blocks)
    lines: list[str] = [_lineage_line(meta, tex=True), ""]

    # 每个 node 前输出 marker 注释行（service 扫描得到 TeX 行号 → node 映射，
    # 供编译错误诊断定位 source node；注释不参与编译）。
    def _mark(node: dict) -> None:
        nid = str((node or {}).get("id") or "")
        if nid:
            lines.append(f"%RV14NODE:{nid}")

    for b in blocks:
        ntype = (b or {}).get("type")
        nid = str((b or {}).get("id") or "")
        _mark(b)
        if ntype == "section":
            lines += [f"\\section{{{_inline_to_latex(str(b.get('text') or ''), node_id=nid)}}}", ""]
        elif ntype == "heading":
            level = max(1, min(3, int((b or {}).get("level") or 1)))
            cmd = {1: "section", 2: "subsection", 3: "subsubsection"}[level]
            lines += [f"\\{cmd}{{{_inline_to_latex(str(b.get('text') or ''), node_id=nid)}}}", ""]
        elif ntype == "paragraph":
            raw = str(b.get("text") or "")
            claim = str(b.get("claimRef") or "").strip()
            # 导出层列表约定（v2）：连续的 `- `/`* `/`N. ` 行渲染为
            # itemize/enumerate（图片样式：圆点参数列表）。段内多行才成列表；
            # 单行恰好以这些前缀开头的普通段落同样按列表项渲染（与 md 视觉一致）。
            list_kind = _detect_list(raw)
            if list_kind == "bullet":
                items = _split_list_items(raw)
                lines += ["\\begin{itemize}"]
                for it in items:
                    lines.append(f"  \\item {_inline_to_latex(it, node_id=nid)}")
                lines += ["\\end{itemize}", ""]
            elif list_kind == "ordered":
                items = _split_list_items(raw)
                lines += ["\\begin{enumerate}"]
                for it in items:
                    lines.append(f"  \\item {_inline_to_latex(it, node_id=nid)}")
                lines += ["\\end{enumerate}", ""]
            else:
                text = _inline_to_latex(raw, node_id=nid)
                if claim:
                    text = f"{text} [claim:{claim}]".strip()
                lines += [text, ""]
        elif ntype == "equation":
            latex = str(b.get("latex") or "")
            guard_latex(latex, node_id=nid)
            lines += [f"\\[\n{latex}\n\\]" if b.get("display") else f"\\({latex}\\)", ""]
        elif ntype == "citation":
            keys = " ".join(f"\\cite{{{r}}}" for r in (b.get("refs") or []) if str(r))
            lines += [keys, ""]
        elif ntype == "figure":
            anchor = str((b.get("refs") or ["?"])[0])
            caption = _escape_latex(str(b.get("caption") or ""))
            lines += [
                "\\begin{figure}[htbp]",
                "\\centering",
                f"\\includegraphics{{assets/{anchor}}}",
                f"\\caption{{{caption}}}",
                "\\end{figure}",
                "",
            ]
        elif ntype == "table":
            caption = _escape_latex(str(b.get("caption") or ""))
            cols = [str(c) for c in (b.get("columns") or [])]
            colspec = "l" * max(1, len(cols))
            rows = " & ".join(_escape_latex(c) for c in cols) + r" \\" if cols else ""
            lines += [
                "\\begin{table}[htbp]",
                "\\centering",
                f"\\caption{{{caption}}}",
                "% RV14 诚实边界：表格数据实体在资产锚点中，此处只渲染表头骨架",
                f"\\begin{{tabular}}{{{colspec}}}",
                rows,
                "\\end{tabular}",
                "\\end{table}",
                "",
            ]
        elif ntype == "callout":
            kind = _escape_latex(str(b.get("kind") or "NOTE"))
            text = _inline_to_latex(str(b.get("text") or ""), node_id=nid)
            lines += [f"\\begin{{quote}}\\textbf{{{kind}}}: {text}\\end{{quote}}", ""]
        elif ntype == "claim_ref":
            ref0 = str((b.get("refs") or [""])[0])
            text = _escape_latex(str(b.get("text") or ""))
            lines += [f"{text} [claim:{ref0}]".strip(), ""]
        elif ntype == "evidence_ref":
            ref0 = str((b.get("refs") or [""])[0])
            text = _escape_latex(str(b.get("text") or ""))
            lines += [f"{text} [evidence:{ref0}]".strip(), ""]
        elif ntype == "review_mark":
            if include_review_marks:
                text = _escape_latex(str(b.get("text") or ""))
                lines += [f"\\begin{{quote}}\\textbf{{REVIEW}}: {text}\\end{{quote}}", ""]
        else:
            nid_esc = _escape_latex(nid)
            lines += [f"% 未知节点类型 {ntype!r}（id={nid_esc}）未导出", ""]

    if order:
        lines += [
            "\\begin{thebibliography}{9}",
            "% RV14：引用编号 = 首现顺序（确定性）；渲染器 = 内置 GB/T 7714 子集",
        ]
        for ref in order:
            csl = (citations or {}).get(ref)
            if csl:
                lines.append(f"\\bibitem{{{ref}}} {_escape_latex(_reference_text(ref, citations))}")
            else:
                lines.append(f"\\bibitem{{{ref}}} 引用元数据缺失（{ref}）")
        lines += ["\\end{thebibliography}", ""]
    return "\n".join(lines)
