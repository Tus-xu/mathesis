"""RV13 · 公式 AST 往返测试（先红后绿）。

钉住任务书第 3 条：MathLive/公式节点保存**可往返语义**的 canonical formula，
不用图片替代；中文、符号、行内/块级公式受测。
"""

from __future__ import annotations

import pytest

from research_vnext.core.paper.formula import (
    ast_hash,
    canonical,
    parse_latex,
    roundtrip,
    semantically_equal,
)


CASES = [
    # (作者输入, 语义等价的另一种写法)
    (r"y = Wx + b", r"y  =  Wx+b"),
    (r"W_q = \frac{\lambda}{\mu - \lambda}", r"W_q=\frac{ \lambda }{\mu-\lambda}"),
    (r"x^{2} + y^{2} = z^{2}", r"x^2+y^2=z^2"),
    (r"\sum_{i=1}^{n} x_i", r"\sum_{i=1}^n x_i"),
    (r"\int_0^1 f(x)\,dx", r"\int_0^1 f(x) dx"),
    (r"\sqrt{x^2 + 1}", r"\sqrt{x^{2}+1}"),
    (r"\alpha \le \beta", r"\alpha\leq\beta"),
    (r"a \times b \cdot c", r"a\times b\cdot c"),
    (r"\mathbb{R}^n", r"\mathbb{R}^{n}"),
    (r"\text{稳态队长} = W", r"\text{稳态队长}=W"),
]


@pytest.mark.parametrize(("authored", "variant"), CASES)
def test_roundtrip_is_semantically_stable(authored: str, variant: str) -> None:
    """parse→canonical→serialize→parse 的 canonical 必须**逐字节恒等**。"""
    first = roundtrip(authored)
    assert first["ok"] is True, first
    second = roundtrip(first["canonical"])
    assert second["ok"] is True
    assert second["canonical"] == first["canonical"]
    # 空白/括号变体必须与原式**语义相等**（canonical 相同）
    assert semantically_equal(authored, variant), (authored, variant)


def test_ast_hash_is_deterministic_and_content_sensitive() -> None:
    h1 = ast_hash(parse_latex(r"W_q = \frac{\lambda}{\mu-\lambda}"))
    h2 = ast_hash(parse_latex(r"W_q=\frac{\lambda}{\mu - \lambda}"))
    assert h1 == h2
    h3 = ast_hash(parse_latex(r"W_q = \frac{\lambda}{\mu+\lambda}"))
    assert h3 != h1, "语义不同必须得到不同 hash"


def test_chinese_and_symbols_survive_roundtrip() -> None:
    src = r"\text{当 } \rho < 1 \text{ 时队列稳定}"
    rt = roundtrip(src)
    assert rt["ok"] is True
    assert "队列稳定" in rt["canonical"]
    assert "\\rho" in rt["canonical"]


def test_inline_vs_display_are_both_storable() -> None:
    """行内/块级是**节点字段**（equation.display），canonical 本身与排版无关。"""
    inline = roundtrip(r"x_i^2")
    display = roundtrip(r"\sum_{i=1}^{\infty} p_i = 1")
    assert inline["ok"] and display["ok"]
    assert "display" not in inline, "display 归属节点，不属于 canonical formula"


def test_unclosed_brace_is_an_honest_failure() -> None:
    with pytest.raises(Exception):
        parse_latex(r"\frac{a}{b")
    rt = roundtrip(r"\frac{a}{")
    assert rt["ok"] is False and rt["error"]


def test_canonical_is_not_an_image_or_html() -> None:
    rt = roundtrip(r"E = mc^2")
    assert "<" not in rt["canonical"]
    assert "img" not in rt["canonical"]
    assert rt["astHash"]


def test_dfrac_normalizes_to_frac() -> None:
    assert semantically_equal(r"\dfrac{a}{b}", r"\frac{a}{b}")
