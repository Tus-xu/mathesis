r"""RV13 · 公式 canonical model：LaTeX → AST → canonical（可往返语义）。

立场（任务书第 3 条）
--------------------
- 公式节点的 canonical 是**语义 AST 的规范化序列化**，不是图片、不是编辑器 DOM；
- `canonical` 与 `astHash` 由**服务端**从 `latex` 重算（AI 自带的一律忽略）；
- 往返不变量：`canonical(parse(canonical(x))) == canonical(x)`（自检强制）；
- 语义相等：空白差异、`\dfrac` 与 `\frac`、`\leq` 与 `\le` 等别名归一后相同。

支持的子集（诚实边界）：四则运算、上下标、分式/根式、希腊字母、
常见函数与关系符、求和/积分（带上下限）、`\text{}` 内的**中文与符号**、
`\mathbb{}`、定界符。超集（环境、矩阵、自定义宏）→ 诚实报错，
不做"看起来解析了"的假象。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

#: 命令别名 → canonical 名（语义相同、写法不同）
_COMMAND_ALIASES: dict[str, str] = {
    "dfrac": "frac",
    "tfrac": "frac",
    "leq": "le",
    "geq": "ge",
    "neq": "ne",
    "to": "rightarrow",
}

_RELATION_AND_BINARY = {
    "cdot", "times", "div", "pm", "mp", "le", "ge", "ne", "approx", "equiv",
    "sim", "propto", "rightarrow", "leftarrow", "Rightarrow", "Leftrightarrow",
    "in", "notin", "subset", "subseteq", "cup", "cap", "oplus", "otimes",
    "perp", "parallel",
}
_GREEK_LOWER = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi",
    "rho", "sigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
}
_GREEK_UPPER = {"Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega"}

#: 简单符号命令（无参数）
_SYMBOL_COMMANDS: frozenset[str] = frozenset(
    _GREEK_LOWER | _GREEK_UPPER | _RELATION_AND_BINARY | {
        "infty", "forall", "exists", "partial", "nabla", "hbar", "ell", "prime",
        "circ", "bullet", "langle", "rangle", "angle", "triangle", "ldots", "cdots",
    }
)

#: 单参数函数名（作为操作符出现）
_FUN_COMMANDS: frozenset[str] = frozenset(
    {"log", "ln", "exp", "sin", "cos", "tan", "cot", "sec", "csc", "arcsin",
     "arccos", "arctan", "lim", "sup", "inf", "max", "min", "det", "dim", "deg", "arg"}
)

#: 单参数结构命令
_ONE_ARG_COMMANDS: frozenset[str] = frozenset(
    {"sqrt", "mathbb", "mathcal", "mathbf", "bar", "hat", "vec", "overline",
     "underline", "widehat", "widetilde"}
)

#: 双参数结构命令
_TWO_ARG_COMMANDS: frozenset[str] = frozenset({"frac", "binom"})

#: 带上下限的算子（`_` 与 `^` 皆可挂）
_LIMITED_OPS: frozenset[str] = frozenset(
    {"sum", "prod", "coprod", "int", "iint", "oint", "bigcup", "bigcap", "lim"}
)


class FormulaError(ValueError):
    """公式解析失败（诚实失败，不猜）。"""


@dataclass
class Node:
    kind: str
    value: str = ""
    children: list["Node"] = field(default_factory=list)


# --------------------------------------------------------------------------
# 词法：产出带类型的 token
#   ("cmd", name) ("text", raw) ("brace", "{"|"}") ("sup","^") ("sub","_")
#   ("num", "12.5") ("char", "x") ("punct", "=") ("other", raw)
# --------------------------------------------------------------------------


def _tokenize(src: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "\\":
            j = i + 1
            if j < n and src[j].isalpha():
                k = j
                while k < n and src[k].isalpha():
                    k += 1
                name = src[j:k]
                i = k
                if name == "text":
                    raw, i = _capture_text_group(src, i)
                    tokens.append(("text", raw))
                else:
                    tokens.append(("cmd", name))
            elif j < n and src[j] in ",;:!":
                i = j + 1  # 细间距（\, \; \! \ ）：语义为空，直接丢弃
            elif j < n and src[j] == "\\":
                i = j + 1  # 换行 \\：子集不支持环境，语义为空
            elif j < n:
                tokens.append(("punct", src[j]))
                i = j + 1
            else:
                raise FormulaError("孤立的反斜杠")
        elif ch in "{}":
            tokens.append(("brace", ch))
            i += 1
        elif ch in "^_":
            tokens.append(("sup" if ch == "^" else "sub", ch))
            i += 1
        elif ch.isdigit() or ch == ".":
            j = i
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            tokens.append(("num", src[i:j]))
            i = j
        elif ch.isalpha() and ch.isascii():
            tokens.append(("char", ch))
            i += 1
        elif ch in "=+-<>|,;:!?'()" or ch in "[]":
            tokens.append(("punct", ch))
            i += 1
        elif ord(ch) > 127:
            j = i
            while j < n and ord(src[j]) > 127 and not src[j].isspace():
                j += 1
            tokens.append(("other", src[i:j]))
            i = j
        else:
            raise FormulaError(f"无法识别的字符: {ch!r}")
    return tokens


def _capture_text_group(src: str, i: int) -> tuple[str, int]:
    """捕获 `\text{...}` 的**原文**（含中文/空格/符号），brace 匹配。"""
    while i < len(src) and src[i].isspace():
        i += 1
    if i >= len(src) or src[i] != "{":
        raise FormulaError("\\text 必须带花括号参数")
    depth = 0
    j = i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1 : j], j + 1
        j += 1
    raise FormulaError("\\text 缺少收尾 '}'")


# --------------------------------------------------------------------------
# 语法
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise FormulaError("公式意外结束")
        self.pos += 1
        return tok

    def parse(self) -> Node:
        node = self.parse_sequence(stop_at_brace=False)
        if self.peek() is not None:
            raise FormulaError(f"多余的输入: {self.peek()!r}")
        return node

    def parse_sequence(self, *, stop_at_brace: bool) -> Node:
        children: list[Node] = []
        while (tok := self.peek()) is not None:
            if tok[0] == "brace" and tok[1] == "}":
                if stop_at_brace:
                    break
                raise FormulaError("多余的右花括号")
            children.append(self.parse_atom())
        return Node("seq", children=children)

    def parse_group(self) -> Node:
        tok = self.next()
        if tok != ("brace", "{"):
            raise FormulaError(f"期望 '{{'，得到 {tok!r}")
        inner = self.parse_sequence(stop_at_brace=True)
        if self.next() != ("brace", "}"):
            raise FormulaError("缺少 '}'")
        return inner

    def parse_arg(self) -> Node:
        tok = self.peek()
        if tok is None:
            raise FormulaError("缺少参数")
        if tok == ("brace", "{"):
            return self.parse_group()
        return self.parse_atom()

    def parse_subsup(self, base: Node) -> Node:
        r"""处理 base 之后的 `_` / `^` 链。

        上下标的参数是**单原子**（`x_i^2` 的 `i` 不许把后面的 `^` 吃掉）——
        否则 `\int_0^1` 会解析成 `\int_{0^1}`，语义就错了。
        """
        node = base
        while (tok := self.peek()) is not None and tok[0] in {"sub", "sup"}:
            kind = tok[0]
            self.next()
            arg = self.parse_single()
            node = Node(kind, children=[node, arg])
        return node

    def parse_single(self) -> Node:
        """单个原子（不吸收后续 sub/sup 链）。"""
        tok = self.peek()
        if tok is None:
            raise FormulaError("缺少参数")
        if tok == ("brace", "{"):
            return self.parse_group()
        kind, val = self.next()
        if kind == "text":
            return Node("text", val)
        if kind == "cmd":
            node = self._parse_command_no_chain(val)
            return node
        if kind == "num":
            return Node("num", val)
        if kind == "char":
            return Node("sym", val)
        if kind == "punct":
            if val in {"_", "^"}:
                raise FormulaError(f"{val!r} 缺少左操作数")
            return Node("punct", val)
        if kind == "other":
            raise FormulaError("中文必须放在 \\text{...} 内")
        raise FormulaError(f"无法解析的记号: {val!r}")

    def parse_atom(self) -> Node:
        return self.parse_subsup(self.parse_single())

    def _parse_command(self, name: str) -> Node:
        """带 sub/sup 链的命令解析（供 parse_atom 兼容路径使用）。"""
        return self.parse_subsup(self._parse_command_no_chain(name))

    def _parse_command_no_chain(self, name: str) -> Node:
        name = _COMMAND_ALIASES.get(name, name)
        if name in _TWO_ARG_COMMANDS:
            num = self.parse_arg()
            den = self.parse_arg()
            return Node(name, children=[num, den])
        if name in _ONE_ARG_COMMANDS:
            arg = self.parse_arg()
            return Node(name, children=[arg])
        if name in _FUN_COMMANDS:
            return Node("func", name)
        if name in _LIMITED_OPS:
            return Node("op", name)
        if name in _SYMBOL_COMMANDS:
            return Node("symcmd", name)
        if name == "left" or name == "right":
            kind, val = self.next()
            if kind != "punct":
                raise FormulaError(f"\\{name} 后必须是定界符")
            return Node("punct", val)
        raise FormulaError(f"不支持的命令: \\{name}")


def parse_latex(src: str) -> Node:
    """LaTeX 子集 → AST。失败抛 `FormulaError`。"""
    if not src or not src.strip():
        raise FormulaError("空公式")
    tokens = _tokenize(src)
    if not tokens:
        raise FormulaError("空公式")
    return _Parser(tokens).parse()


# --------------------------------------------------------------------------
# canonical 序列化
# --------------------------------------------------------------------------


def canonical(node: Node) -> str:
    return _ser(node)


def _ser(node: Node) -> str:
    k = node.kind
    if k == "seq":
        return "".join(_ser(c) for c in node.children)
    if k == "text":
        return f"\\text{{{node.value}}}"
    if k in {"num", "sym", "punct"}:
        return node.value
    if k in {"func", "op"}:
        # 控制字后必须带空格终结符（LaTeX 命令名吃掉后续字母：
        # `\times b` 若序列化成 `\timesb` 再解析就成了未知命令）
        return f"\\{node.value} "
    if k == "symcmd":
        return f"\\{node.value} "
    if k == "sub":
        return f"{_base(_ser(node.children[0]), node.children[0])}_{{{_ser(node.children[1])}}}"
    if k == "sup":
        return f"{_base(_ser(node.children[0]), node.children[0])}^{{{_ser(node.children[1])}}}"
    if k == "frac":
        return f"\\frac{{{_ser(node.children[0])}}}{{{_ser(node.children[1])}}}"
    if k == "binom":
        return f"\\binom{{{_ser(node.children[0])}}}{{{_ser(node.children[1])}}}"
    if k in _ONE_ARG_COMMANDS:
        return f"\\{k}{{{_ser(node.children[0])}}}"
    raise FormulaError(f"未知节点类型: {k}")


def _base(ser: str, node: Node) -> str:
    """sub/sup 的左操作数：链式（sub/sup 已挂、单 token、带参命令）不加括号；
    多元素 group 必须加，否则重解析会把上下标绑错。"""
    if node.kind == "seq" and len(node.children) != 1:
        return f"{{{ser}}}"
    return ser


def ast_hash(node: Node) -> str:
    return hashlib.sha256(canonical(node).encode("utf-8")).hexdigest()


def semantically_equal(a: str, b: str) -> bool:
    try:
        return canonical(parse_latex(a)) == canonical(parse_latex(b))
    except FormulaError:
        return False


def roundtrip(latex: str) -> dict[str, Any]:
    """一次完整往返：`{ok, latex, canonical, astHash, error}`。

    自检强制往返不变量：canonical 再解析必须得到同一 canonical，
    否则如实返回 ok=False（不做"看起来成功了"的假象）。
    """
    try:
        ast = parse_latex(latex)
        canon = canonical(ast)
        re_canon = canonical(parse_latex(canon))
        if re_canon != canon:
            return {
                "ok": False, "latex": latex, "canonical": canon, "astHash": "",
                "error": f"往返不稳定: {canon!r} → {re_canon!r}",
            }
        return {
            "ok": True, "latex": latex, "canonical": canon,
            "astHash": ast_hash(ast), "error": "",
        }
    except FormulaError as e:
        return {"ok": False, "latex": latex, "canonical": "", "astHash": "",
                "error": str(e)}
