"""数学文本规范化与排版工具（R3e 产物投影层；确定性、纯函数、零 LLM）。

三条产物红线（产物审查 G/H 回炉判据）：
1. **禁止裸源码**：`\\boxed{}` / `$…$` / `\\frac` 等 LaTeX 源码不得出现在任何产物文本
   （PPTX `<a:t>`、PDF 文本层、DOCX/MD 正文）里——生成源头必须产出干净文本；
2. **公式 canonical**：上标/下标正确（y₁、x₂、a²），显示式分式在 PDF/图形里必须是
   叠式分式（不是线性 `(a)/(b)` 退化写法）；
3. **记法统一**：点斜式课堂统一 P₁(x₁, y₁) 下标记法（苏教版口径；与教案/课件同源）。

分层：
- `latex_to_unicode`：任意可见文本 → 干净 Unicode 数学文本（deck 正文/MD 用）；
- `canonical_latex`：显示式 LaTeX 源码规范化（`latex` 字段 / PDF 排版 / OMML 用）；
- `parse_math` + `draw_math`：受限 LaTeX 子集 → 报告级排版（reportlab 画叠式分式、上下标）；
- `latex_to_omml`：LaTeX → OMML（DOCX 原生公式；latex2mathml + mathml2omml，
  复用 FROZEN `app.export.pptx_engine` 同款管线，失败降级纯文本）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# 1. 文本级净化：\boxed / $...$ / \frac … → 干净 Unicode 数学文本
# ─────────────────────────────────────────────────────────────────────────────

# 命令符号表（最长优先替换；未识别命令原样保留前会先剥反斜杠——见 latex_to_unicode）
_SYMBOLS = {
    "\\leq": "≤", "\\le": "≤", "\\geq": "≥", "\\ge": "≥", "\\neq": "≠", "\\ne": "≠",
    "\\pm": "±", "\\mp": "∓", "\\times": "×", "\\div": "÷", "\\cdot": "·",
    "\\approx": "≈", "\\equiv": "≡", "\\in": "∈", "\\notin": "∉",
    "\\subseteq": "⊆", "\\subset": "⊂", "\\supseteq": "⊇", "\\supset": "⊃",
    "\\cup": "∪", "\\cap": "∩", "\\emptyset": "∅", "\\varnothing": "∅",
    "\\infty": "∞", "\\perp": "⊥", "\\parallel": "∥", "\\angle": "∠",
    # 黑板粗体数集（`$\mathbb{R}$` 等）。不登记会被 _BARE_CMD_RE 剥掉反斜杠，
    # 产物里留下裸字面量 `mathbb{R}` 直接上屏（R8c 实测：幂函数性质对比表）。
    "\\mathbb{R}": "ℝ", "\\mathbb{N}": "ℕ", "\\mathbb{Z}": "ℤ",
    "\\mathbb{Q}": "ℚ", "\\mathbb{C}": "ℂ", "\\mathbb{P}": "ℙ", "\\mathbb{E}": "𝔼",
    "\\Rightarrow": "⇒", "\\rightarrow": "→", "\\leftarrow": "←",
    "\\leftrightarrow": "↔", "\\Leftrightarrow": "⇔", "\\implies": "⇒",
    "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ", "\\theta": "θ",
    "\\lambda": "λ", "\\mu": "μ", "\\pi": "π", "\\rho": "ρ", "\\sigma": "σ",
    "\\omega": "ω", "\\Delta": "Δ", "\\Omega": "Ω", "\\sum": "∑", "\\int": "∫",
    "\\quad": "  ", "\\qquad": "   ", "\\,": " ", "\\:": " ", "\\;": " ", "\\!": "",
    "\\ ": " ", "\\{": "{", "\\}": "}",
}
_SUP_MAP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
            "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻",
            "n": "ⁿ", "i": "ⁱ"}
_SUB_MAP = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
            "6": "₆", "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋",
            "n": "ₙ", "i": "ᵢ"}

_BOXED_RE = re.compile(r"\\boxed\s*\{")
_TAG_RE = re.compile(r"\\tag\s*\{[^{}]*\}")
_NOTAG_RE = re.compile(r"\\nonumber|\\displaystyle|\\limits")
_LEFT_RIGHT_RE = re.compile(r"\\left|\\right")
_TEXT_RE = re.compile(r"\\(?:text|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}")
_DFRAC_RE = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT_RE = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_SUP_RE = re.compile(r"\^\s*\{([^{}]+)\}|\^\s*([0-9+\-])")
_SUB_RE = re.compile(r"_\s*\{([^{}]+)\}|_\s*([0-9+\-])")
_DOLLARS_RE = re.compile(r"\$\$?([^$]*?)\$\$?")
_BARE_CMD_RE = re.compile(r"\\[a-zA-Z]+")
# 触发整段数学净化的证据（无证据的普通文本零改动，保护 ai_generated 之类的普通下标词）
_MATH_EVIDENCE_RE = re.compile(r"\\|\$|\^\{|\_\{|\^[-0-9]|_[-0-9]")


def _strip_boxed_once(text: str) -> str:
    """展开一层 `\\boxed{...}`（配对花括号；幂等直到无命中）。"""
    while True:
        m = _BOXED_RE.search(text)
        if not m:
            return text
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        inner = text[start : i - 1] if depth == 0 else text[start:]
        text = text[: m.start()] + inner + text[i if depth == 0 else len(text) :]


def strip_boxed(text: str) -> str:
    """剥掉全部 `\\boxed{...}` 包装（保留内部内容）。空文本原样返回。"""
    if not text or "\\boxed" not in text:
        return text
    return _strip_boxed_once(text)


def _frac_to_inline(m: re.Match[str]) -> str:
    return f"({_strip_math(m.group(1).strip())})/({_strip_math(m.group(2).strip())})"


def _sup_to_unicode(m: re.Match[str]) -> str:
    body = (m.group(1) or m.group(2) or "").strip()
    return "".join(_SUP_MAP.get(ch, ch) for ch in body)


def _sub_to_unicode(m: re.Match[str]) -> str:
    body = (m.group(1) or m.group(2) or "").strip()
    return "".join(_SUB_MAP.get(ch, ch) for ch in body)


def _strip_math(tex: str) -> str:
    """一段 LaTeX（不含 $ 定界）→ Unicode 数学文本。"""
    out = strip_boxed(tex)
    out = _TAG_RE.sub("", out)
    out = _NOTAG_RE.sub("", out)
    out = _TEXT_RE.sub(lambda m: m.group(1), out)
    out = _DFRAC_RE.sub(_frac_to_inline, out)
    out = _SQRT_RE.sub(lambda m: f"√({_strip_math(m.group(1).strip())})", out)
    out = _SUP_RE.sub(_sup_to_unicode, out)
    out = _SUB_RE.sub(_sub_to_unicode, out)
    out = _LEFT_RIGHT_RE.sub("", out)
    for cmd in sorted(_SYMBOLS, key=len, reverse=True):
        out = out.replace(cmd, _SYMBOLS[cmd])
    # 剩余未知命令（\implies 残留等）：剥反斜杠保留词干，绝不把 \cmd 裸源码留给产物
    out = _BARE_CMD_RE.sub(lambda m: m.group(0)[1:], out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


# 点斜式课堂记法统一（G20 ③）：教材（苏教版）口径 P₁(x₁, y₁)；
# (x₀, y₀) 成对出现或处于「点斜式」语境时统一为 1 下标（仅记法归一，不改数学事实）。
_P0_PATTERNS = (
    (re.compile(r"\bx_0\b"), "x_1"),
    (re.compile(r"\by_0\b"), "y_1"),
    (re.compile(r"\bP_0\b"), "P_1"),
    (re.compile(r"x₀"), "x₁"),
    (re.compile(r"y₀"), "y₁"),
    (re.compile(r"P₀"), "P₁"),
)


_ASCII_SUB_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])_([0-9n])(?![A-Za-z0-9])")
_ASCII_FRAC_PAREN_RE = re.compile(r"\)\s*/\s*\(([0-9A-Za-z]+)\)")


def normalize_ascii_math_text(text: str) -> str:
    """把正文里常见的 ASCII 数学记号转成可读 Unicode（确定性、幂等）。

    课件正文里真实出现过 `S_n=(n[a₁+a₁+(n-1)d])/(2)`：既不是 LaTeX 也不是可读排版。
    这里只做两件安全的事：
    - 单字母下标 ``S_n / a_1 / S_3`` → Unicode 下标；
    - ``...)/(2)`` → ``...)/2``（去掉分母多余括号）。
    不改数字事实，不把普通英文单词当数学。
    """
    if not text:
        return text
    out = str(text)

    def _sub(m: re.Match[str]) -> str:
        return m.group(1) + _SUB_MAP.get(m.group(2), m.group(2))

    out = _ASCII_SUB_RE.sub(_sub, out)
    out = _ASCII_FRAC_PAREN_RE.sub(r")/\1", out)
    # 纯文本数列记号（无下划线）：仅在该行确有数学运算符时转写，避免英文 an 误伤。
    if any(op in out for op in ("=", "+", "-", "−", "×", "÷", "≥", "≤")):
        out = re.sub(r"(?<![A-Za-z])an[-−]1(?![0-9])", "aₙ₋₁", out)
        out = re.sub(r"(?<![A-Za-z])an[+]1(?![0-9])", "aₙ₊₁", out)
        out = re.sub(r"(?<![A-Za-z])an(?![A-Za-z0-9])", "aₙ", out)
        out = re.sub(r"(?<![A-Za-z])Sn(?![A-Za-z0-9])", "Sₙ", out)
        out = re.sub(
            r"(?<![A-Za-z])a([0-9])(?![0-9])",
            lambda m: "a" + _SUB_MAP.get(m.group(1), m.group(1)),
            out,
        )
    return out


def normalize_point_slope_indices(text: str) -> str:
    """点斜式记法下标统一（x₀/y₀/P₀ → x₁/y₁/P₁）。确定性、幂等。"""
    if not text:
        return text
    has_pair = bool(re.search(r"[xy]_0|[xy]₀", text)) and bool(
        re.search(r"(x_0|x₀).{0,12}(y_0|y₀)|(y_0|y₀).{0,12}(x_0|x₀)", text)
    )
    if not (has_pair or "点斜式" in text):
        return text
    out = text
    for pat, repl in _P0_PATTERNS:
        out = pat.sub(repl, out)
    return out


def latex_to_unicode(text: str) -> str:
    """可见文本 → 干净 Unicode 数学文本（含 `$…$` 剥离；幂等）。

    这是「生成源头产出干净文本」的统一入口：deck 正文、例题、MD 导出共用。
    无数学证据（无 `\\`/`$`/`_{`/`^`）的普通文本零改动；残留 `$` 一律剥除（产物零源码）。
    """
    if not text:
        return text
    out = str(text)
    out = _DOLLARS_RE.sub(lambda m: _strip_math(m.group(1)), out)
    if _MATH_EVIDENCE_RE.search(out):
        out = _strip_math(out)
    out = out.replace("$", "")
    return normalize_point_slope_indices(out)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 显示式 LaTeX 规范化（latex 字段 / PDF 排版 / OMML 输入）
# ─────────────────────────────────────────────────────────────────────────────


def canonical_latex(latex: str) -> str:
    r"""显示式 LaTeX 规范化：剥 `\boxed`/`\left \right`/`\displaystyle`/`\tag`，归一分式命令。"""
    if not latex:
        return latex
    out = strip_boxed(str(latex))
    out = out.replace("$", "")
    out = _TAG_RE.sub("", out)
    out = _NOTAG_RE.sub("", out)
    out = _LEFT_RIGHT_RE.sub("", out)
    out = re.sub(r"\\[dt]?frac", r"\\frac", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip().strip("。")


def split_label_math(text: str) -> tuple[str, str | None]:
    """「标签：$latex$」→ (标签, latex)；非公式行 → (文本, None)。确定性。

    判定为显示式的条件（防「$b$是直线在$y$轴上的截距」这类行内小数学被误抽）：
    全文**恰好一个** `$…$` 片段，且片段含 `=` 或 `\\` 命令（真公式）。
    """
    if not text:
        return "", None
    spans = re.findall(r"\$\$?([^$]+?)\$\$?", text)
    if len(spans) != 1:
        return normalize_point_slope_indices(strip_boxed(text)), None
    span = spans[0].strip()
    if "=" not in span and "\\" not in span:
        return normalize_point_slope_indices(strip_boxed(text)), None
    m = re.search(r"\$\$?([^$]+?)\$\$?", text)
    assert m is not None
    label = re.sub(r"\s*[：:，,、]?\s*$", "", text[: m.start()]).strip()
    label = strip_boxed(label)
    latex = canonical_latex(span)
    return normalize_point_slope_indices(label), latex or None


def display_formulas_of(items: list[str]) -> list[tuple[str, str]]:
    """一批文本里抽取 (标签, latex) 公式对（整条即公式时标签为空）。"""
    out: list[tuple[str, str]] = []
    for raw in items or []:
        label, latex = split_label_math(raw)
        if latex:
            out.append((label, latex))
        elif re.fullmatch(r"\s*\$\$?[^$]+\$\$?\s*", raw or ""):
            out.append(("", canonical_latex(raw)))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. 受限 LaTeX 子集 → 排版 AST（reportlab 叠式分式/上下标；DOCX OMML 用源码）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MNode:
    kind: str  # atom | group | sub | sup | frac | sqrt
    text: str = ""
    items: list[MNode] = field(default_factory=list)
    base: MNode | None = None
    sub: MNode | None = None
    sup: MNode | None = None
    num: MNode | None = None
    den: MNode | None = None


def _read_group(s: str, i: int) -> tuple[MNode, int]:
    """读 `{...}` 组（i 指向 `{`）；缺右括号时读到串尾（不猜内容）。"""
    assert s[i] == "{"
    depth = 1
    j = i + 1
    while j < len(s) and depth:
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
        j += 1
    return parse_math(s[i + 1 : j - 1 if depth == 0 else j]), j


def parse_math(latex: str) -> MNode:
    """受限 LaTeX 子集解析（frac/sqrt/sub/sup/符号/普通字符）。确定性、无猜测。"""
    s = canonical_latex(latex)
    items: list[MNode] = []
    buf: list[str] = []
    i = 0

    def _flush() -> None:
        if buf:
            text = "".join(buf)
            buf.clear()
            items.append(MNode("atom", text=text))

    while i < len(s):
        ch = s[i]
        if ch == "{":
            _flush()
            node, i = _read_group(s, i)
            items.append(node)
            continue
        if ch == "\\":
            m = re.match(r"\\([a-zA-Z]+)|\\.", s[i:])
            word = m.group(1) if m and m.group(1) else (m.group(0)[1:] if m else ch)
            rest_i = i + (m.end() if m else 1)
            if word in ("frac", "dfrac", "tfrac"):
                num, j = _read_group(s, rest_i) if rest_i < len(s) and s[rest_i] == "{" else (MNode("atom", text=""), rest_i)
                den, j2 = _read_group(s, j) if j < len(s) and s[j] == "{" else (MNode("atom", text=""), j)
                _flush()
                items.append(MNode("frac", num=num, den=den))
                i = j2
                continue
            if word == "sqrt":
                arg, j = _read_group(s, rest_i) if rest_i < len(s) and s[rest_i] == "{" else (MNode("atom", text=""), rest_i)
                _flush()
                items.append(MNode("sqrt", items=[arg]))
                i = j
                continue
            if word in ("left", "right"):
                i = rest_i
                if i < len(s) and s[i] in "()[]|.{}":
                    i += 1
                continue
            sym = _SYMBOLS.get("\\" + word)
            if sym is not None:
                buf.append(sym)
                i = rest_i
                continue
            buf.append(word)
            i = rest_i
            continue
        if ch in "^_":
            _flush()
            base = items.pop() if items else MNode("atom", text="")
            j = i + 1
            if j < len(s) and s[j] == "{":
                exp, j = _read_group(s, j)
            else:
                exp = MNode("atom", text=s[j : j + 1])
                j += 1
            node = MNode("sub" if ch == "_" else "sup", base=base, sub=exp if ch == "_" else None, sup=exp if ch == "^" else None)
            items.append(node)
            i = j
            continue
        buf.append(ch)
        i += 1
    _flush()
    # 合并相邻 atom
    merged: list[MNode] = []
    for node in items:
        if merged and merged[-1].kind == "atom" and node.kind == "atom":
            merged[-1].text += node.text
        else:
            merged.append(node)
    return MNode("group", items=merged)


def math_node_to_unicode(node: MNode) -> str:
    if node.kind == "atom":
        return node.text
    if node.kind == "group":
        return "".join(math_node_to_unicode(n) for n in node.items)
    if node.kind == "frac":
        return f"({math_node_to_unicode(node.num or MNode('atom'))})/({math_node_to_unicode(node.den or MNode('atom'))})"
    if node.kind == "sqrt":
        return f"√({math_node_to_unicode(node.items[0]) if node.items else ''})"
    if node.kind == "sup":
        return math_node_to_unicode(node.base or MNode("atom")) + "".join(
            _SUP_MAP.get(c, c) for c in math_node_to_unicode(node.sup or MNode("atom"))
        )
    if node.kind == "sub":
        return math_node_to_unicode(node.base or MNode("atom")) + "".join(
            _SUB_MAP.get(c, c) for c in math_node_to_unicode(node.sub or MNode("atom"))
        )
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. reportlab 排版（叠式分式 / 上下标；PDF 文本层仍是真实文本，零源码）
# ─────────────────────────────────────────────────────────────────────────────

_FRAC_GAP = 1.6  # 分式线上下留白（pt，随字号缩放）


def measure_math(node: MNode, *, font: str, size: float, string_width) -> tuple[float, float, float]:
    """(总宽, 基线上高, 基线下高)。string_width(text, font, size) 由调用方给（reportlab）。"""
    if node.kind == "atom":
        w = string_width(node.text, font, size)
        return w, size * 0.73, size * 0.22
    if node.kind == "group":
        w = asc = desc = 0.0
        for n in node.items:
            nw, na, nd = measure_math(n, font=font, size=size, string_width=string_width)
            w += nw
            asc = max(asc, na)
            desc = max(desc, nd)
        return w, asc, desc
    if node.kind == "frac":
        nw, na, nd = measure_math(node.num or MNode("atom"), font=font, size=size * 0.92, string_width=string_width)
        dw, da, dd = measure_math(node.den or MNode("atom"), font=font, size=size * 0.92, string_width=string_width)
        w = max(nw, dw) + size * 0.35
        gap = size / _FRAC_GAP
        return w, na + gap + size * 0.12, da + gap + size * 0.12
    if node.kind == "sqrt":
        inner = node.items[0] if node.items else MNode("atom", text="")
        iw, ia, idd = measure_math(inner, font=font, size=size, string_width=string_width)
        return iw + size * 0.75, ia + size * 0.16, idd
    base = node.base or MNode("atom", text="")
    bw, ba, bd = measure_math(base, font=font, size=size, string_width=string_width)
    exp = node.sup or node.sub or MNode("atom", text="")
    ew, _, _ = measure_math(exp, font=font, size=size * 0.7, string_width=string_width)
    if node.kind == "sup":
        return bw + ew * 0.9, max(ba, size * 0.95), bd
    return bw + ew * 0.9, ba, max(bd, size * 0.8)


def draw_math(node: MNode, canvas, x: float, y: float, *, font: str, size: float,
              string_width, color=None) -> float:
    """在基线 (x, y) 起绘制；返回占用宽度。"""
    if color is not None:
        canvas.setFillColor(color)
    if node.kind == "atom":
        canvas.setFont(font, size)
        canvas.drawString(x, y, node.text)
        return string_width(node.text, font, size)
    if node.kind == "group":
        w = 0.0
        for n in node.items:
            w += draw_math(n, canvas, x + w, y, font=font, size=size, string_width=string_width, color=color)
        return w
    if node.kind == "frac":
        nw, na, _ = measure_math(node.num or MNode("atom"), font=font, size=size * 0.92, string_width=string_width)
        dw, _, _ = measure_math(node.den or MNode("atom"), font=font, size=size * 0.92, string_width=string_width)
        w = max(nw, dw) + size * 0.35
        gap = size / _FRAC_GAP
        inner = x + (w - nw) / 2
        draw_math(node.num or MNode("atom"), canvas, inner, y + gap, font=font, size=size * 0.92,
                  string_width=string_width, color=color)
        draw_math(node.den or MNode("atom"), canvas, x + (w - dw) / 2, y - gap - size * 0.78,
                  font=font, size=size * 0.92, string_width=string_width, color=color)
        canvas.setLineWidth(max(0.7, size * 0.055))
        canvas.line(x + size * 0.06, y + size * 0.12, x + w - size * 0.06, y + size * 0.12)
        return w
    if node.kind == "sqrt":
        inner = node.items[0] if node.items else MNode("atom", text="")
        iw, ia, _ = measure_math(inner, font=font, size=size, string_width=string_width)
        canvas.setFont(font, size)
        canvas.drawString(x, y, "√")
        canvas.setLineWidth(max(0.6, size * 0.05))
        canvas.line(x + size * 0.52, y + ia * 0.82, x + size * 0.72 + iw, y + ia * 0.82)
        draw_math(inner, canvas, x + size * 0.75, y, font=font, size=size, string_width=string_width, color=color)
        return iw + size * 0.75
    base = node.base or MNode("atom", text="")
    bw = draw_math(base, canvas, x, y, font=font, size=size, string_width=string_width, color=color)
    exp = node.sup or node.sub or MNode("atom", text="")
    if node.kind == "sup":
        draw_math(exp, canvas, x + bw, y + size * 0.52, font=font, size=size * 0.7,
                  string_width=string_width, color=color)
    else:
        draw_math(exp, canvas, x + bw, y - size * 0.28, font=font, size=size * 0.7,
                  string_width=string_width, color=color)
    return bw + measure_math(exp, font=font, size=size * 0.7, string_width=string_width)[0] * 0.9


# ─────────────────────────────────────────────────────────────────────────────
# 5. OMML（DOCX 原生公式）：latex2mathml → mathml2omml（与 FROZEN pptx_engine 同管线）
# ─────────────────────────────────────────────────────────────────────────────

_NS_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def latex_to_omml(latex: str) -> str | None:
    """LaTeX → `<m:oMath …>` 字符串；转换失败返回 None（调用方降级 Unicode 文本）。"""
    tex = canonical_latex(latex)
    if not tex:
        return None
    try:
        import latex2mathml.converter as l2m
        import mathml2omml

        mathml = l2m.convert(tex)
        omml = mathml2omml.convert(mathml)
        if "oMath" not in omml:
            return None
        if "xmlns:m" not in omml:
            omml = omml.replace("<m:oMath", f'<m:oMath xmlns:m="{_NS_M}"', 1)
        return omml
    except Exception:  # noqa: BLE001 - 转换器失败必须可降级（绝不抛给导出链）
        return None


__all__ = [
    "strip_boxed",
    "canonical_latex",
    "latex_to_unicode",
    "normalize_point_slope_indices",
    "split_label_math",
    "display_formulas_of",
    "parse_math",
    "math_node_to_unicode",
    "measure_math",
    "draw_math",
    "latex_to_omml",
]
