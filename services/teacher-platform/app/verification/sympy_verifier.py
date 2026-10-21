"""MathVerifier —— G5 确定性数学验证器（SymPy 等价判断 + 定义域随机采样 + 约束检查）。

契约来源（02-ARCHITECTURE §8 / §11，M0 冻结，签名不得变更）：

    class MathVerifier:
        async def check_equation(lhs_latex, rhs_latex, *, domain=None) -> Verdict
        async def check_answer(question_latex, answer_latex, *, expected) -> Verdict
        async def sample_check(expr_latex, *, n=20) -> Verdict

    Verdict: passed: bool | None, method: 'sympy'|'numeric'|'constraint', detail: str

铁律（G5 / README 纪律 9）：
- KaTeX 只证明「能渲染」，不证明「数学对」——本模块才是数学真理门。
- LaTeX 解析失败 = 不可判定：返回 passed=None 交教师审查，**绝不默认通过**。
- 验证通过也不等于正确——最终防线仍是教师确认。

采样可复现性（审计要求）：随机种子由输入表达式的 SHA-256 派生，
同一输入在任何时间重跑得到同一批采样点，便于 B5 数据审计回放。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from sympy import Eq, S, simplify, solveset, zoo
from sympy.core.expr import Expr
from sympy.core.numbers import Infinity, NaN, NegativeInfinity
from sympy.core.relational import Eq as RelationalEq
from sympy.parsing.latex import parse_latex

VerifyMethod = Literal["sympy", "numeric", "constraint"]

# simplify/equals 在病态输入上可能长时间占用 —— 用独立线程池 + 超时保护，
# 超时按「不可判定」处理（绝不默认通过）。
VERIFY_TIMEOUT_S = 20.0
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mathverifier")

_ANSWER_SEPARATORS = ("或", "；", ";", "，")

_ZH_TO_ASCII = {
    "＝": "=", "＋": "+", "－": "-", "×": "\\times", "÷": "\\div",
    "（": "(", "）": ")", "｛": "{", "｝": "}",
}


class LatexParseError(Exception):
    """LaTeX → SymPy 解析失败（含空输入/多方程/超时后的放弃）。"""


class Verdict(BaseModel):
    """验证裁决。passed=None 表示不可判定（解析失败/超时），必须交教师审查。"""

    passed: Optional[bool]
    method: VerifyMethod = "sympy"
    detail: str = Field(default="", description="人类可读判定依据；含反例/采样点数/失败原因")
    counterexample: Optional[str] = Field(default=None, description="数值反例（变量取值），用于教师快速复核")

    def to_ledger(self) -> dict[str, Any]:
        """写入 ai_runs.verifier_result 的紧凑投影（G6 联动）。"""
        d = {"passed": self.passed, "method": self.method, "detail": self.detail}
        if self.counterexample is not None:
            d["counterexample"] = self.counterexample
        return d

    @property
    def decidable(self) -> bool:
        return self.passed is not None


def _substitute_latex_artifacts(s: str) -> str:
    # \left/\right 只是定界修饰，ANTLR 语法不支持时会挡解析，摘除不影响数学含义
    s = s.replace("\\left", "").replace("\\right", "")
    # \dfrac/\tfrac 归一为 \frac（不能用 re.sub：替换串 \f 会被模板引擎吃成换页符）
    s = s.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    s = s.replace("\\cdot", "*").replace("\\times", "*")
    s = s.replace("\\div", "/")
    s = re.sub(r"\\[,;!? ]", "", s)                   # 间距命令
    s = s.replace("~", "")
    s = s.replace("°", "")                            # 角度符号：数值验证按弧度语义之外不参与
    for zh, ascii_ in _ZH_TO_ASCII.items():
        s = s.replace(zh, ascii_)
    return s.strip()


# 解析器白名单：sympy ANTLR 后端会把未知宏静默解析成普通符号（如 \foo{y} → foo*y），
# 把垃圾 LaTeX 误判成数学。凡不在白名单的反斜杠命令一律拒绝 → 不可判定（G5 铁律）。
_ALLOWED_LATEX_MACROS = frozenset({
    # 分式/根式/括号辅助
    "frac", "sqrt", "overline", "vec", "hat", "bar", "dot", "text", "mathrm",
    "left", "right",
    # 运算符
    "cdot", "times", "div", "pm", "mp", "leq", "geq", "neq", "ne", "approx",
    "equiv", "propto", "infty", "pm",
    # 三角/反三角/对数/指数/微积分/级数
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan",
    "log", "ln", "exp", "lim", "sum", "prod", "int",
    # 希腊字母（大小写常用集）
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi",
    "rho", "sigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega",
    # 集合/逻辑/其他
    "in", "notin", "subset", "subseteq", "cup", "cap", "emptyset", "forall",
    "exists", "neg", "land", "lor", "Rightarrow", "rightarrow", "leftrightarrow",
    "perp", "parallel", "angle", "triangle", "circ", "degree", "prime",
    "ldots", "cdots", "quad", "qquad", "operatorname",
})

_MACRO_PATTERN = re.compile(r"\\([a-zA-Z]+)")


def _normalize_latex(latex: str) -> str:
    s = (latex or "").strip()
    s = re.sub(r"^\$\$(.*)\$\$$", r"\1", s.strip())
    s = re.sub(r"^\$(.*)\$$", r"\1", s.strip())
    s = re.sub(r"^\\\[(.*)\\\]$", r"\1", s.strip(), flags=re.S)
    s = _substitute_latex_artifacts(s)
    s = s.rstrip("。，,；;. ")
    return s.strip()


def _latex_to_expr(latex: str) -> Expr:
    """LaTeX → SymPy Expr；任何失败抛 LatexParseError（调用方翻译成 passed=None）。"""
    cleaned = _normalize_latex(latex)
    if not cleaned:
        raise LatexParseError("空表达式")
    unknown = sorted({m.group(1) for m in _MACRO_PATTERN.finditer(cleaned)
                      if m.group(1) not in _ALLOWED_LATEX_MACROS})
    if unknown:
        raise LatexParseError(f"未知 LaTeX 命令（白名单外）: " + ", ".join("\\" + u for u in unknown))
    if "=" in cleaned:
        # 单方程交给 parse_latex 产 Eq；多方程（联立）不在 P0 可判定子集
        if cleaned.count("=") > 1:
            raise LatexParseError("多个等号（方程组不可判定）")
        cleaned_eq = cleaned
    else:
        cleaned_eq = cleaned
    try:
        parsed = parse_latex(cleaned_eq)
    except Exception as exc:  # antlr 的 LaTeXParsingError 及一切底层异常都算解析失败
        raise LatexParseError(f"LaTeX 解析失败: {type(exc).__name__}: {exc}") from exc
    if isinstance(parsed, (list, tuple)):
        if len(parsed) == 1:
            parsed = parsed[0]
        else:
            raise LatexParseError(f"解析出 {len(parsed)} 个对象（不可判定）")
    if parsed is None:
        raise LatexParseError("解析结果为空")
    return parsed


def _side_of(expr: Expr) -> tuple[Expr, Expr] | None:
    """方程返回 (lhs, rhs)，普通表达式返回 None。"""
    if isinstance(expr, RelationalEq):
        return expr.lhs, expr.rhs
    return None


def _is_undefined_value(v: Any) -> bool:
    return v is NaN or v is zoo or isinstance(v, (Infinity, NegativeInfinity))


def _stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _domain_bounds(domain: Optional[dict], name: str) -> tuple[float, float]:
    if not domain:
        return (-5.0, 5.0)
    bound = domain.get(name)
    if bound is None:
        return (-5.0, 5.0)
    lo, hi = float(bound[0]), float(bound[1])
    return (lo, hi) if lo <= hi else (hi, lo)


def _sample_points(symbols: list[Expr], domain: Optional[dict], n: int, rng: random.Random,
                   max_tries_factor: int = 8) -> list[dict[Expr, float]]:
    points: list[dict[Expr, float]] = []
    tries = 0
    max_tries = max(n * max_tries_factor, n)
    while len(points) < n and tries < max_tries:
        tries += 1
        pt = {sym: rng.uniform(*_domain_bounds(domain, sym.name)) for sym in symbols}
        points.append(pt)
    return points


def _numeric_equivalence(a: Expr, b: Expr, domain: Optional[dict], n: int,
                         rng: random.Random) -> tuple[Optional[bool], str, Optional[str]]:
    """定义域随机取点数值等价验证。返回 (passed, detail, counterexample)。"""
    symbols = sorted(a.free_symbols | b.free_symbols, key=str)
    points = _sample_points(symbols, domain, n, rng)
    if not points:
        return None, "未取得有效采样点", None
    tested = 0
    for pt in points:
        try:
            va_c = complex(a.evalf(subs=pt))
            vb_c = complex(b.evalf(subs=pt))
        except (ZeroDivisionError, TypeError, ValueError):
            continue
        if any(math.isinf(v.real) or math.isnan(v.real) for v in (va_c, vb_c)):
            continue
        if abs(va_c.imag) > 1e-9 or abs(vb_c.imag) > 1e-9:
            continue  # 复值点不作为实等价证据
        va, vb = va_c.real, vb_c.real
        tested += 1
        scale = max(1.0, abs(va), abs(vb))
        if abs(va - vb) > 1e-8 * scale:
            pt_repr = ", ".join(f"{k.name}={v:.4f}" for k, v in pt.items())
            detail = f"数值采样反例（{pt_repr}）：左={va:.6g} 右={vb:.6g}"
            return False, detail, pt_repr
    if tested == 0:
        return None, "采样点全部无定义（疑似处处奇点/复值），不可判定", None
    return True, f"{tested} 个采样点数值等价（容差 1e-8 相对）", None


def _poles_univariate(expr: Expr, sym: Expr) -> set[float]:
    """单变量表达式在实数域的有限极点集（分母零点）。解不出时返回空集（不误杀）。"""
    try:
        _num, den = expr.as_numer_denom()
        if den == 1:
            return set()
        sol = solveset(den, sym, domain=S.Reals)
        out: set[float] = set()
        for s in sol:
            try:
                v = float(s)
                if math.isfinite(v):
                    out.add(v)
            except (TypeError, ValueError):
                continue
        return out
    except Exception:
        return set()


def _domain_mismatch(a: Expr, b: Expr) -> Optional[tuple[str, str]]:
    """单变量场景下比较两侧实数定义域（极点集）。

    simplify 在有理函数域做形式恒等，会漏掉「去分母丢约束」陷阱
    （如 (x²-1)/(x-1) = x+1 在 x=1 处左边无定义）。返回 None=无矛盾/不可判，
    否则 (detail, counterexample)。
    """
    symbols = a.free_symbols | b.free_symbols
    if len(symbols) != 1:
        return None
    sym = next(iter(symbols))
    pa, pb = _poles_univariate(a, sym), _poles_univariate(b, sym)
    if pa == pb:
        return None
    if pa - pb:
        x0 = sorted(pa - pb)[0]
        return (f"定义域不一致：左侧在 {sym.name}={x0:.6g} 处无定义而右侧有定义"
                f"（形式恒等但实函数不等价——去分母/约分陷阱）", f"{sym.name}={x0:.6g}")
    x0 = sorted(pb - pa)[0]
    return (f"定义域不一致：右侧在 {sym.name}={x0:.6g} 处无定义而左侧有定义", f"{sym.name}={x0:.6g}")


def _equivalent(a: Expr, b: Expr, domain: Optional[dict], n: int,
                rng: random.Random) -> tuple[Optional[bool], VerifyMethod, str, Optional[str]]:
    """四层等价引擎：结构恒等 → simplify → 定义域一致性 → equals → 数值采样。"""
    if a == b:
        return True, "sympy", "结构恒等（sympy）", None
    try:
        diff = simplify(a - b)
        if diff == 0:
            mismatch = _domain_mismatch(a, b)
            if mismatch is not None:
                detail, cx = mismatch
                return False, "constraint", detail, cx
            return True, "sympy", "simplify(lhs-rhs)==0 且定义域一致（sympy）", None
    except Exception:
        pass  # 化简爆栈不判死，交给 equals/采样
    try:
        eq_res = a.equals(b)
    except Exception:
        eq_res = None
    if eq_res is True:
        mismatch = _domain_mismatch(a, b)
        if mismatch is not None:
            detail, cx = mismatch
            return False, "constraint", detail, cx
        return True, "sympy", "symPy equals() 判定等价", None
    if eq_res is False:
        # equals() 在复数点做否决测试；sqrt(·²) 等分支函数在实数域恒等但复数域不等。
        # G5 契约语义是实数域（定义域采样），实数域采样可推翻复数点否决。
        passed, detail, cx = _numeric_equivalence(a, b, domain, n, rng)
        if passed is True:
            return True, "numeric", f"equals() 复数点否决被实数域采样推翻（分支函数语义按实数域裁决）：{detail}", cx
        if passed is False:
            return False, "numeric", detail, cx
        return False, "sympy", "symPy equals() 判定不等价", None
    passed, detail, cx = _numeric_equivalence(a, b, domain, n, rng)
    if passed is not None:
        return passed, "numeric", f"符号法不可判定，数值采样裁决：{detail}", cx
    return None, "sympy", f"符号法与数值采样均不可判定（{detail}）", None


def _run_with_timeout(fn, *args, **kwargs):
    fut = _EXECUTOR.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=VERIFY_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise TimeoutError("验证超时")


def _split_answer_candidates(answer_latex: str) -> list[str]:
    """把「x=2 或 x=3」「2；3」类答案拆成候选片段（P0 诚实子集：单变量解集/单表达式）。"""
    s = (answer_latex or "").strip()
    if not s:
        return []
    parts = re.split(r"或|；|;|，|,", s)
    return [p.strip() for p in parts if p.strip()]


def _candidate_to_value(piece: str) -> Expr:
    """「x=2」取右值，「2」取本身。解析失败抛 LatexParseError。"""
    expr = _latex_to_expr(piece)
    side = _side_of(expr)
    if side is not None:
        return side[1]
    return expr


def _sync_check_equation(lhs_latex: str, rhs_latex: str, domain: Optional[dict], n: int) -> Verdict:
    try:
        lhs = _latex_to_expr(lhs_latex)
        rhs = _latex_to_expr(rhs_latex)
    except LatexParseError as exc:
        return Verdict(passed=None, method="sympy", detail=f"不可判定：{exc}")
    except TimeoutError:
        return Verdict(passed=None, method="sympy", detail="不可判定：SymPy 解析超时")
    seed = _stable_seed(str(lhs), str(rhs))
    passed, method, detail, cx = _equivalent(lhs, rhs, domain, n, random.Random(seed))
    return Verdict(passed=passed, method=method, detail=detail, counterexample=cx)


def _sync_check_answer(question_latex: str, answer_latex: str, expected: str, n: int) -> Verdict:
    _ = question_latex  # P0：题干仅入档 detail 语境；题干约束抽取（定义域/范围）为 P1 增强
    try:
        answer_values = [_candidate_to_value(p) for p in _split_answer_candidates(answer_latex)]
        expected_values = [_candidate_to_value(p) for p in _split_answer_candidates(expected)]
    except LatexParseError as exc:
        return Verdict(passed=None, method="sympy", detail=f"不可判定：{exc}")
    if not answer_values or not expected_values:
        return Verdict(passed=None, method="sympy", detail="不可判定：答案或参考答案为空")
    if len(answer_values) != len(expected_values):
        return Verdict(passed=False, method="constraint",
                       detail=f"解的个数不一致：AI 答案 {len(answer_values)} 个 vs 参考答案 {len(expected_values)} 个")
    rng = random.Random(_stable_seed(*(str(v) for v in answer_values + expected_values)))
    remaining = list(expected_values)
    for av in answer_values:
        matched_idx = None
        for i, ev in enumerate(remaining):
            passed, _m, _detail, _cx = _equivalent(av, ev, None, n, random.Random(rng.getrandbits(64)))
            if passed is True:
                matched_idx = i
                break
            if passed is None:
                return Verdict(passed=None, method="sympy",
                               detail=f"不可判定：候选解对比无法裁决（{_detail}）")
        if matched_idx is None:
            return Verdict(passed=False, method="sympy",
                           detail=f"AI 答案候选 {av} 与参考答案任一候选均不等价")
        remaining.pop(matched_idx)
    return Verdict(passed=True, method="sympy",
                   detail=f"AI 答案 {len(answer_values)} 个候选与参考答案逐一对等价（采样种子可复现）")


def _sync_sample_check(expr_latex: str, n: int) -> Verdict:
    try:
        expr = _latex_to_expr(expr_latex)
    except LatexParseError as exc:
        return Verdict(passed=None, method="numeric", detail=f"不可判定：{exc}")
    side = _side_of(expr)
    if side is not None:
        seed = _stable_seed(str(side[0]), str(side[1]))
        passed, method, detail, cx = _equivalent(side[0], side[1], None, n, random.Random(seed))
        return Verdict(passed=passed, method=method, detail=detail, counterexample=cx)
    # 普通表达式：定义域有效性验证 —— n 个采样点能否全部取到有限实值
    symbols = sorted(expr.free_symbols, key=str)
    rng = random.Random(_stable_seed(str(expr)))
    points = _sample_points(symbols, None, n, rng)
    defined = 0
    for pt in points:
        try:
            v = complex(expr.evalf(subs=pt))
        except (ZeroDivisionError, TypeError, ValueError):
            continue
        if math.isinf(v.real) or math.isnan(v.real) or abs(v.imag) > 1e-9:
            continue
        defined += 1
    if defined == n:
        return Verdict(passed=True, method="numeric", detail=f"{n} 个随机采样点全部有定义且为实值")
    if defined == 0:
        return Verdict(passed=None, method="numeric", detail="采样点全部无定义/复值，不可判定")
    return Verdict(passed=False, method="numeric",
                   detail=f"仅 {defined}/{n} 个采样点有定义，表达式在定义域内疑似存在奇点/复值分支")


class MathVerifier:
    """G5 数学真理门。所有方法异步非阻塞；重活在线程池跑，超时按不可判定处理。"""

    def __init__(self, *, timeout_s: float = VERIFY_TIMEOUT_S, sample_n: int = 20) -> None:
        self._timeout_s = timeout_s
        self._sample_n = sample_n

    async def _run(self, fn, *args, **kwargs) -> Verdict:
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_EXECUTOR, lambda: fn(*args, **kwargs)),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            return Verdict(passed=None, method="sympy",
                           detail=f"不可判定：验证超时（>{self._timeout_s:.0f}s），交教师审查")

    async def check_equation(self, lhs_latex: str, rhs_latex: str, *,
                             domain: Optional[dict[str, tuple[float, float]]] = None) -> Verdict:
        """等式等价验证：结构/化简 → equals → 定义域采样。domain: {符号名: (下界, 上界)}。"""
        return await self._run(_sync_check_equation, lhs_latex, rhs_latex, domain, self._sample_n)

    async def check_answer(self, question_latex: str, answer_latex: str, *,
                           expected: str) -> Verdict:
        """解题对比：AI 答案候选集 vs 参考答案候选集（个数一致 + 逐一二一等价）。"""
        return await self._run(_sync_check_answer, question_latex, answer_latex, expected,
                               self._sample_n)

    async def sample_check(self, expr_latex: str, *, n: int = 20) -> Verdict:
        """定义域随机取点数值验证：方程按恒等式验证，表达式按定义域有效性验证。"""
        return await self._run(_sync_sample_check, expr_latex, n)

    async def check_choices_distinct(self, options_latex: list[str]) -> Verdict:
        """客观题选项互斥性检查（§11：AI 配题必查）。任两项等价即判 False。"""
        def _sync() -> Verdict:
            parsed: list[Expr] = []
            try:
                for opt in options_latex:
                    parsed.append(_latex_to_expr(opt))
            except LatexParseError as exc:
                return Verdict(passed=None, method="constraint", detail=f"不可判定：{exc}")
            rng = random.Random(_stable_seed(*(str(v) for v in parsed)))
            for i in range(len(parsed)):
                for j in range(i + 1, len(parsed)):
                    passed, _m, detail, _cx = _equivalent(parsed[i], parsed[j], None, self._sample_n,
                                                          random.Random(rng.getrandbits(64)))
                    if passed is True:
                        return Verdict(passed=False, method="constraint",
                                       detail=f"选项 {chr(65 + i)} 与 {chr(65 + j)} 等价（互斥性破坏）：{detail}")
                    if passed is None:
                        return Verdict(passed=None, method="constraint",
                                       detail=f"不可判定：选项 {chr(65 + i)}/{chr(65 + j)} 对比无法裁决（{detail}）")
            return Verdict(passed=True, method="constraint",
                           detail=f"{len(parsed)} 个选项两两不等价")
        return await self._run(_sync)


if __name__ == "__main__":
    # G5 最小验证 demo（M1 门禁：3 条等价判断 + 1 条解析失败不可判定样例）
    import json
    import sys

    async def _demo() -> None:
        mv = MathVerifier()
        cases: list[tuple[str, Any]] = [
            ("等价·恒等式", lambda: mv.check_equation("(x+1)^2", "x^2 + 2x + 1")),
            ("不等·定义域陷阱(去分母丢根)", lambda: mv.check_equation("\\frac{x^2-1}{x-1}", "x+1")),
            ("不等·sqrt(x^2)≠x", lambda: mv.check_equation("\\sqrt{x^2}", "x")),
            ("不可判定·非法宏", lambda: mv.check_equation("x^2 + \\unknownmacro{y}", "0")),
        ]
        out = []
        for name, call in cases:
            v = await call()
            out.append({"case": name, **v.model_dump()})
            print(f"[{name}] passed={v.passed} method={v.method}\n  detail: {v.detail}", flush=True)
        print("DEMO_JSON=" + json.dumps(out, ensure_ascii=False))
        ok = [v["passed"] for v in out]
        assert ok[0] is True and ok[1] is False and ok[2] is False and ok[3] is None, "demo 断言失败"
        print("G5_MINIMAL_DEMO=PASS", file=sys.stderr)

    asyncio.run(_demo())
