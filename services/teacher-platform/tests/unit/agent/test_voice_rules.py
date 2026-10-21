"""语音公式 L1 规则引擎单测（M3 验收项：20 条口语用例 + 词典 ≥60 条）。

用例覆盖 notes/B1-notes.md §4.4 的缺口清单：中文数字词组、n次根、希腊字母、
度、下标/第n项、求和/积分/极限/导数、底数对数、集合关系、括号口令。
"""
from __future__ import annotations

import pytest

from app.agent.graphs.voice_rules import _RULES, rule_confidence, voice_to_latex


def test_dictionary_has_at_least_60_rules() -> None:
    """工作卡硬指标：L1 词典 ≥60 条（mock 种子 22 条 + 缺口清单补齐）。"""
    assert len(_RULES) >= 60


# 工作卡 §5：语音管线 20 条口语用例（含指定样句「根号下x平方加y平方」）
CASES = [
    ("根号下x平方加y平方", "\\sqrt{x^{2}+y^{2}}"),
    ("根号下二分之一", "\\sqrt{\\frac{1}{2}}"),
    ("三次根号下27", "\\sqrt[3]{27}"),
    ("x的平方加y的平方等于1", "x^{2}+y^{2}=1"),
    ("sin30度加cos60度", "\\sin 30^{\\circ}+\\cos 60^{\\circ}"),
    ("a下标n加b下标n", "a_{n}+b_{n}"),
    ("从1到n的求和", "\\sum_{1}^{n}"),
    ("x趋于0的极限", "\\lim_{x \\to 0}"),
    ("f的导数", "f^\\prime"),
    ("以2为底8的对数", "\\log_{2}8"),
    ("圆周率乘以r平方", "\\pi \\times r^{2}"),
    ("x属于A", "x\\in A"),
    ("绝对值x减1", "\\left|x-1\\right|"),
    ("y大于等于零", "y\\geq 0"),
    ("e的x次方", "e^{x}"),
    ("十二分之五", "\\frac{5}{12}"),
    ("零点五加零点三", "0.5+0.3"),
    ("a恒等于1", "a\\equiv 1"),
    ("西塔等于零点五", "\\theta =0.5"),
    ("第n项", "a_{n}"),
    ("左括号x加1右括号的平方", "(x+1)^{2}"),
]


@pytest.mark.parametrize(("spoken", "expected"), CASES)
def test_voice_to_latex(spoken: str, expected: str) -> None:
    assert voice_to_latex(spoken) == expected


def test_all_20_cases_reach_high_confidence() -> None:
    """20 条用例全部命中规则且无残留中文（L1 直出 0.95）。"""
    passed = sum(1 for spoken, _ in CASES if rule_confidence(spoken, voice_to_latex(spoken)) == 0.95)
    assert passed >= 20, f"20 条用例应全过，实际 {passed}"


def test_residual_chinese_gets_text_wrap_and_low_confidence() -> None:
    latex = voice_to_latex("根号下x加一")
    assert "一" in latex  # 中文数字转换是 M3 缺口：残留保留，不编造
    assert latex.startswith("\\text{")
    assert rule_confidence("根号下x加一", latex) == 0.4
