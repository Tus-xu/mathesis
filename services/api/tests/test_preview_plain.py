"""首页学情摘要的纯文本化回归（2026-10-21）。

用户反馈：首页复习卡曾把 Markdown 表格与未闭合 LaTeX 直接漏到界面（乱码）。
_preview 必须在截断前剔除表格语法，并把公式压成可读 Unicode，不暴露美元符或反斜杠。
"""

from app.gateway.growth_router import _preview


def test_preview_strips_markdown_table_and_latex():
    text = (
        "某实验数据如下表所示：\n\n"
        "| $t$ | 1 | 2 | 3 |\n"
        "| :-: | :-: | :-: | :-: |\n"
        "| $y$ | 2 | 4 | 8 |\n\n"
        "若 $\\sin\\alpha+\\cos\\alpha=\\frac{1}{5}$，求 $\\sin2\\alpha$ 的值。"
    )
    out = _preview(text, 80)
    assert "|" not in out
    assert "$" not in out
    assert ":-:" not in out
    assert "sinα" in out
    assert "1/5" in out


def test_preview_truncates_without_dangling_formula():
    text = "已知函数 $f(x)=x^3-3x$，" + "并讨论其单调性。" * 20
    out = _preview(text, 40)
    assert "$" not in out
    assert out.endswith("…")
    assert len(out) <= 41


def test_preview_empty_and_table_only():
    assert _preview("") == ""
    assert _preview("| a | b |\n| :-: | :-: |") == "（题目含数据表，请打开原题查看）"
