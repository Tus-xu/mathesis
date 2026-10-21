"""R0-C7 回归套件：f(x)=ax³−3x²+1 单调性讨论（讲评页标准答案数学错误拦截）。

缺陷源：D:/frontend/src/mock/teacherV3Data.ts:505 standard_answer（R0 缺陷库 C7，P1）：
  ① 「a=0 时 f=-3x²+1 在 R 递减」——错：增 (-∞,0)、减 (0,+∞)；
  ② 「a>0 时…按 0<2/a 即 a<2 与 a≥2 讨论」——错：0<2/a ⟺ a>0 恒成立（若比较 2/a 与 2，
     边界是 a=1 而非 a=2）；a>0 无需再分流，减区间 (0,2/a)。
  （a<0 情形「递增区间 (2/a,0)」实测正确。）

防線设计（G5 + 03-ACCEPTANCE §6-6）：
  1. 本文件用 SymPy 数值采样固化**正确分类表**（可复现种子）——ground truth 源；
  2. MathVerifier 对 mock 中**正确的求导恒等式**不得误杀（f'(x)=x(3ax-6) 本身对）；
  3. facts.yaml 两条 C7 用例的 forbid 模式必须能命中 mock 原句、expect 必须命中
     规范正确答案文本——断言用例被证明「拦得住」。
"""

import random
import re
from pathlib import Path

import pytest
import sympy as sp
import yaml

from app.verification.sympy_verifier import MathVerifier

FACTS = Path(__file__).resolve().parents[3] / "app" / "prompts" / "tests" / "facts.yaml"

# mock 原句（缺陷语料，逐字摘自 teacherV3Data.ts:505）
MOCK_SENTENCE_A0 = "a=0 时 f=-3x²+1 在 R 递减"
MOCK_SENTENCE_SPLIT = "a>0 时极值点 x=0,2/a，按 0<2/a 即 a<2 与 a≥2 讨论"
# 规范正确答案文本（sympy 数值表的人工转述，作为 expect 命中标的）
CORRECT_A0 = "a=0 时 f(x)=-3x²+1 在 (-∞,0) 上递增，在 (0,+∞) 上递减"
CORRECT_SPLIT = ("a>0 时极值点为 x=0 和 x=2/a，减区间为 (0,2/a)，"
                 "增区间为 (-∞,0) 和 (2/a,+∞)，无需再按 a 大小分流")


def _sign(v: float) -> str:
    return "+" if v > 1e-9 else ("-" if v < -1e-9 else "0")


class TestCorrectClassificationTable:
    """SymPy 数值固化的正确单调性表（任何「修 facts 修歪了」都会在这里红）。"""

    x, a = sp.symbols("x a")
    fp = sp.diff(a * x**3 - 3 * x**2 + 1, x)  # 3*x*(a*x - 2)

    def _probe(self, a_val: float, lo: float, hi: float, n: int = 40) -> set[str]:
        rng = random.Random(42)
        return {_sign(float(self.fp.subs({self.a: a_val, self.x: rng.uniform(lo, hi)})))
                for _ in range(n)}

    def test_a_positive(self):
        for a_val, c in ((2.0, 1.0), (0.5, 4.0)):  # c = 2/a
            assert self._probe(a_val, -8, -0.05) == {"+"}   # (−∞,0) 增
            assert self._probe(a_val, 0.05, c - 0.05) == {"-"}  # (0,2/a) 减
            assert self._probe(a_val, c + 0.05, 8) == {"+"}  # (2/a,+∞) 增

    def test_a_negative(self):
        a_val, c = -1.0, -2.0  # c = 2/a < 0
        assert self._probe(a_val, -8, c - 0.05) == {"-"}    # (−∞,2/a) 减
        assert self._probe(a_val, c + 0.05, -0.05) == {"+"}  # (2/a,0) 增（mock 此情形正确）
        assert self._probe(a_val, 0.05, 8) == {"-"}          # (0,+∞) 减

    def test_a_zero(self):
        # a=0: f'=-6x → 增 (-∞,0)、减 (0,+∞)——「在 R 递减」的数值反证
        assert self._probe(0.0, -8, -0.05) == {"+"}
        assert self._probe(0.0, 0.05, 8) == {"-"}

    def test_case_split_predicate(self):
        # 0<2/a ⟺ a>0（mock 用它推出 a<2，谓词层面即不成立）
        assert (0 < 2 / 0.5) is True and (0 < 2 / -1.0) is False
        # 若按「比较 2/a 与 2」的正确意图，边界 a=1 而非 a=2
        assert 2 / 2 == 1.0


class TestVerifierOnC7Statements:
    """G5 门对 C7 语句族的行为：正确的代数不得误杀；错误的结论拦得住。

    编码规范（G5 实测边界，见 notes/B2-notes.md）：
    - ANTLR 把 `x(...)` 解析为函数应用而非乘法 → 显式乘法必须写 `\\cdot`；
    - `abs` 不是合法 LaTeX token（会被拆成 a*b*s 函数）→ 绝对值用 sqrt(·²) 表达
      （数值上 sqrt(v²)=|v|）；\\abs 未进宏白名单 → 不可判定，符合铁律。
    """

    @pytest.fixture(scope="class")
    def mv(self) -> MathVerifier:
        return MathVerifier()

    @pytest.mark.asyncio
    async def test_derivative_identity_not_false_killed(self, mv: MathVerifier):
        # mock 的求导本身正确：f'(x)=x(3ax-6) ⟺ 3ax²-6x —— 验证器必须放行
        v = await mv.check_equation("x \\cdot (3ax - 6)", "3ax^2 - 6x")
        assert v.passed is True

    @pytest.mark.asyncio
    async def test_mock_a0_claim_is_numerically_refuted(self, mv: MathVerifier):
        # 「f=-3x²+1 在 R 递减」⟺ f'(x)=-6x ≤ 0 ∀x —— 在 (-∞,0) 上 f'=-6x>0，
        # 与 -sqrt(f'²)（即 -|f'|，递减的恒等式编码）不恒等 → 数值反例裁决 False
        v = await mv.check_equation("-6x", "-\sqrt{(-6x)^2}", domain={"x": (-8, -0.05)})
        assert v.passed is False
        # 而「在 (-∞,0) 递增」是真的：f'(x)=|f'(x)| 成立
        v2 = await mv.check_equation("-6x", "\sqrt{(-6x)^2}", domain={"x": (-8, -0.05)})
        assert v2.passed is True

    @pytest.mark.asyncio
    async def test_correct_monotonic_segments_hold(self, mv: MathVerifier):
        # a>0（取 a=2）：(0,2/a)=(0,1) 递减 ⟺ f'=6x²-12x=-|f'|；(-∞,0) 递增 ⟺ f'=|f'|
        v = await mv.check_equation("6x^2 - 12x", "-\sqrt{(6x^2 - 12x)^2}",
                                    domain={"x": (0.05, 0.95)})
        assert v.passed is True
        v2 = await mv.check_equation("6x^2 - 12x", "\sqrt{(6x^2 - 12x)^2}",
                                     domain={"x": (-8, -0.05)})
        assert v2.passed is True


class TestFactsAssertionsCatchDefect:
    """facts.yaml 的 C7 用例必须「拦得住」：forbid 命中 mock 原句、expect 命中正确答案。"""

    @pytest.fixture(scope="class")
    def cases(self) -> dict:
        data = yaml.safe_load(FACTS.read_text(encoding="utf-8"))
        return {c["id"]: c for c in data["cases"]}

    def test_c7_cases_registered_and_compiled(self, cases: dict):
        assert "cubic_monotonicity_a0" in cases and "cubic_monotonicity_case_split" in cases
        for cid in ("cubic_monotonicity_a0", "cubic_monotonicity_case_split"):
            for pat in cases[cid]["expect"] + cases[cid]["forbid"]:
                re.compile(pat)

    def test_forbid_catches_mock_sentences(self, cases: dict):
        a0 = cases["cubic_monotonicity_a0"]
        assert any(re.search(p, MOCK_SENTENCE_A0) for p in a0["forbid"])
        split = cases["cubic_monotonicity_case_split"]
        assert any(re.search(p, MOCK_SENTENCE_SPLIT) for p in split["forbid"])

    def test_forbid_spares_correct_statements(self, cases: dict):
        a0, split = cases["cubic_monotonicity_a0"], cases["cubic_monotonicity_case_split"]
        assert not any(re.search(p, CORRECT_A0) for p in a0["forbid"])
        assert not any(re.search(p, CORRECT_SPLIT) for p in split["forbid"])
        assert not any(re.search(p, MOCK_SENTENCE_A0[:11]) for p in a0["forbid"])  # 「a=0 时 f=-3x²+1」前缀无害

    def test_expect_matches_correct_statements(self, cases: dict):
        a0, split = cases["cubic_monotonicity_a0"], cases["cubic_monotonicity_case_split"]
        assert any(re.search(p, CORRECT_A0) for p in a0["expect"])
        assert any(re.search(p, CORRECT_SPLIT) for p in split["expect"])
