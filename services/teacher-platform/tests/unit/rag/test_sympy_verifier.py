"""MathVerifier（G5）单元测试 —— tests/unit/rag/ 归 B2 所有（G2 测试所有权）。

运行：cd services/teacher-platform && python -m pytest tests/unit/rag -q
"""

import pytest

from app.verification.sympy_verifier import (
    LatexParseError,
    MathVerifier,
    Verdict,
    _latex_to_expr,
    _normalize_latex,
    _split_answer_candidates,
)


@pytest.fixture(scope="module")
def mv() -> MathVerifier:
    return MathVerifier()


@pytest.mark.asyncio
async def test_check_equation_true_identity(mv: MathVerifier):
    v = await mv.check_equation("(x+1)^2", "x^2 + 2x + 1")
    assert v.passed is True
    assert v.decidable


@pytest.mark.asyncio
async def test_check_equation_false_domain_trap(mv: MathVerifier):
    # (x^2-1)/(x-1) = x+1 在 x=1 处不成立 —— 采样必须抓到反例
    v = await mv.check_equation("\\frac{x^2-1}{x-1}", "x+1")
    assert v.passed is False
    assert v.counterexample is not None


@pytest.mark.asyncio
async def test_check_equation_false_sqrt(mv: MathVerifier):
    v = await mv.check_equation("\\sqrt{x^2}", "x")
    assert v.passed is False  # sqrt(x^2)=|x|，x<0 时不等


@pytest.mark.asyncio
async def test_check_equation_parse_failure_is_undecidable(mv: MathVerifier):
    """铁律：解析失败 => passed=None，绝不默认通过。"""
    v = await mv.check_equation("x^2 + \\unknownmacro{y}", "0")
    assert v.passed is None
    assert "不可判定" in v.detail


@pytest.mark.asyncio
async def test_check_equation_empty_is_undecidable(mv: MathVerifier):
    v = await mv.check_equation("", "0")
    assert v.passed is None


@pytest.mark.asyncio
async def test_check_answer_set_match(mv: MathVerifier):
    v = await mv.check_answer("解方程 x^2-5x+6=0", "x=2 或 x=3", expected="x=3, x=2")
    assert v.passed is True


@pytest.mark.asyncio
async def test_check_answer_wrong_answer(mv: MathVerifier):
    v = await mv.check_answer("解方程 x^2-5x+6=0", "x=2 或 x=4", expected="x=2 或 x=3")
    assert v.passed is False


@pytest.mark.asyncio
async def test_check_answer_count_mismatch(mv: MathVerifier):
    v = await mv.check_answer("解方程 x^2-5x+6=0", "x=2", expected="x=2 或 x=3")
    assert v.passed is False
    assert "个数不一致" in v.detail


@pytest.mark.asyncio
async def test_check_answer_parse_failure_undecidable(mv: MathVerifier):
    v = await mv.check_answer("解方程 f(x)=0", "x=\\goodness{}", expected="x=1")
    assert v.passed is None


@pytest.mark.asyncio
async def test_sample_check_identity(mv: MathVerifier):
    v = await mv.sample_check("(a+b)^2 = a^2 + 2ab + b^2", n=20)
    assert v.passed is True  # 符号法可直接判定；采样为兜底路径


@pytest.mark.asyncio
async def test_sample_check_expression_definedness(mv: MathVerifier):
    v = await mv.sample_check("\\frac{1}{x^2+1}", n=20)
    assert v.passed is True  # 处处有定义


@pytest.mark.asyncio
async def test_choices_distinct(mv: MathVerifier):
    v = await mv.check_choices_distinct(["\\frac{3}{4}", "\\frac{1}{2}", "2", "1"])
    assert v.passed is True


@pytest.mark.asyncio
async def test_choices_not_distinct(mv: MathVerifier):
    v = await mv.check_choices_distinct(["\\frac{1}{2}", "0.5", "2"])
    assert v.passed is False  # 1/2 与 0.5 数值等价，互斥性破坏


def test_normalize_strips_wrappers():
    assert _normalize_latex("$x^2$") == "x^2"
    assert _normalize_latex("\\left(x+1\\right)^2") == "(x+1)^2"
    assert _normalize_latex("\\dfrac{1}{2}") == "\\frac{1}{2}"


def test_split_answer_candidates():
    assert _split_answer_candidates("x=2 或 x=3") == ["x=2", "x=3"]
    assert _split_answer_candidates("2；3") == ["2", "3"]
    assert _split_answer_candidates("") == []


def test_parse_failure_raises():
    with pytest.raises(LatexParseError):
        _latex_to_expr("\\unknownmacro{y}")


def test_verdict_ledger_shape():
    v = Verdict(passed=None, method="sympy", detail="x")
    ledger = v.to_ledger()
    assert set(ledger) <= {"passed", "method", "detail", "counterexample"}
