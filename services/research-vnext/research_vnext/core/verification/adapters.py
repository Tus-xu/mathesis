"""RV12 · Verifier 适配器 —— 七类**确定性**检查。

设计原则（决定了为什么没有一处调用 LLM）：

- 一次验证的结论必须**可重放**：同样的输入永远给同样的 PASS/FAIL/UNKNOWN。
  把"是否通过"交给模型，等于让结论随采样温度漂移。
- 数据不足时返回 **UNKNOWN**，绝不返回 PASS（`UNKNOWN` 与 `PASS` 的区分是
  这个产品最重要的诚实性边界）。
- 反例命中时把**见证物**（表达式、变量、取值）原样留在 `detail`/`artifacts`
  里 —— 反证不允许被"结论变了就顺手删掉"。

`_eval_expr` 是一个**受限**算术求值器（AST 白名单）：只允许 `+ - * / ** %`
与一组数学函数，不允许属性访问 / 导入 / 下标 —— 因为待求值的表达式来自
"待验证的主张"，属于不可信输入。
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.experiment_run import compare_with_original
from .types import VerificationKind, VerifierOutcome

__all__ = [
    "OUTCOME_HANDLERS",
    "boundary_check",
    "code_repro_check",
    "counterexample_check",
    "data_repro_check",
    "math_check",
    "robustness_check",
    "run_offline_check",
    "sensitivity_check",
]


class ExpressionRejected(Exception):
    """表达式不在受限算术子集内（不可信输入的安全边界）。"""


_ALLOWED_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "floor": math.floor,
    "ceil": math.ceil,
    "fabs": math.fabs,
    "pow": math.pow,
}
_ALLOWED_CONSTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}
_BIN_OPS: dict[type, Any] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
    ast.Mod: lambda a, b: a % b,
    ast.FloorDiv: lambda a, b: a // b,
}


def _eval_node(node: ast.AST, variables: Mapping[str, Any]) -> float:
    if isinstance(node, ast.Expression):  # pragma: no cover - 兼容
        return _eval_node(node.body, variables)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionRejected(f"不支持的常量: {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.Name):
        name = node.id
        if name in variables:
            return float(variables[name])
        if name in _ALLOWED_CONSTS:
            return float(_ALLOWED_CONSTS[name])
        raise ExpressionRejected(f"未知变量: {name}")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ExpressionRejected(f"不支持的运算符: {type(node.op).__name__}")
        return float(op(_eval_node(node.left, variables), _eval_node(node.right, variables)))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _eval_node(node.operand, variables)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ExpressionRejected("只允许白名单内的数学函数")
        if node.keywords:
            raise ExpressionRejected("不允许关键字参数")
        return float(_ALLOWED_FUNCS[node.func.id](*[_eval_node(a, variables) for a in node.args]))
    raise ExpressionRejected(f"不支持的语法: {type(node).__name__}")


def _eval_expr(expression: str, variables: Mapping[str, Any]) -> float:
    try:
        tree = ast.parse(str(expression), mode="eval")
    except SyntaxError as exc:
        raise ExpressionRejected(f"表达式不是合法算术式: {exc}") from exc
    return _eval_node(tree.body, variables)


def _criterion(
    code: str,
    passed: bool,
    *,
    detail: str = "",
    blocking: str = "",
    observed: Any = None,
    expected: Any = None,
    reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "passed": bool(passed),
        "detail": detail,
        "blocking": "" if passed else blocking,
        "observed": observed,
        "expected": expected,
        "reason": reason or ("OK" if passed else blocking),
    }
    payload.update(extra)
    return payload


def _verdict(criteria: Sequence[Mapping[str, Any]], *, unknown: Sequence[str] = ()) -> str:
    """FAIL > PASS > UNKNOWN 的**全有或全无**判定。"""
    if any(c.get("blocking") for c in criteria):
        return "FAIL"
    if criteria:
        return "PASS"
    return "UNKNOWN"


# --------------------------------------------------------------------------
# 数学检查
# --------------------------------------------------------------------------


def math_check(params: Mapping[str, Any]) -> VerifierOutcome:
    checks = [c for c in (params.get("checks") or ()) if isinstance(c, Mapping)]
    criteria: list[dict[str, Any]] = []
    unknowns: list[str] = []
    if not checks:
        return VerifierOutcome(result="UNKNOWN", unknowns=("NO_CHECKS",))

    for idx, raw in enumerate(checks):
        name = str(raw.get("name") or f"check{idx + 1}")
        kind = str(raw.get("kind") or "IDENTITY").upper()
        tolerance = float(raw.get("tolerance") or 1e-9)
        try:
            if kind == "IDENTITY":
                lhs_expr = str(raw.get("lhs") or "")
                rhs_expr = str(raw.get("rhs") or "")
                points = [p for p in (raw.get("points") or ()) if isinstance(p, Mapping)]
                if not points:
                    unknowns.append(f"POINTS_MISSING:{name}")
                    continue
                worst = 0.0
                for point in points:
                    variables = {k: v for k, v in point.items()}
                    worst = max(
                        worst,
                        abs(_eval_expr(lhs_expr, variables) - _eval_expr(rhs_expr, variables)),
                    )
                criteria.append(
                    _criterion(
                        name,
                        worst <= tolerance,
                        detail=f"最大残差 {worst:.3e}",
                        blocking="MATH_IDENTITY_VIOLATED",
                        observed=worst,
                        expected=tolerance,
                        reason="IDENTITY_OK" if worst <= tolerance else "MATH_IDENTITY_VIOLATED",
                        maxResidual=worst,
                    )
                )
            elif kind == "DIMENSION":
                lhs_dim = str(raw.get("lhs_dim") or "")
                rhs_dim = str(raw.get("rhs_dim") or "")
                ok = bool(lhs_dim) and lhs_dim == rhs_dim
                criteria.append(
                    _criterion(
                        name,
                        ok,
                        detail=f"[{lhs_dim}] vs [{rhs_dim}]",
                        blocking="DIMENSION_MISMATCH",
                        observed=lhs_dim,
                        expected=rhs_dim,
                        reason="DIMENSION_OK" if ok else "DIMENSION_MISMATCH",
                    )
                )
            elif kind == "SIGN":
                variables = dict(raw.get("variables") or {})
                value = (
                    _eval_expr(str(raw.get("expression") or raw.get("value")), variables)
                    if raw.get("expression")
                    else float(raw.get("value") or 0.0)
                )
                expect = str(raw.get("expect") or "positive")
                ok = {
                    "positive": value > 0,
                    "negative": value < 0,
                    "nonzero": abs(value) > tolerance,
                }.get(expect, False)
                criteria.append(
                    _criterion(
                        name,
                        ok,
                        detail=f"value={value:g} expect={expect}",
                        blocking="SIGN_MISMATCH",
                        observed=value,
                        expected=expect,
                        reason="SIGN_OK" if ok else "SIGN_MISMATCH",
                    )
                )
            elif kind == "LIMIT":
                points = [float(x) for x in (raw.get("points") or ())]
                if not points:
                    unknowns.append(f"POINTS_MISSING:{name}")
                    continue
                ok = abs(points[-1]) <= tolerance
                criteria.append(
                    _criterion(
                        name,
                        ok,
                        detail=f"末尾值 {points[-1]:.3e}",
                        blocking="LIMIT_NOT_CONVERGING",
                        observed=points[-1],
                        expected=tolerance,
                        reason="LIMIT_OK" if ok else "LIMIT_NOT_CONVERGING",
                    )
                )
            else:
                unknowns.append(f"UNKNOWN_CHECK_KIND:{kind}")
        except ExpressionRejected as exc:
            unknowns.append(f"EXPRESSION_REJECTED:{name}:{exc}")
        except (ZeroDivisionError, OverflowError, ValueError) as exc:
            unknowns.append(f"EVALUATION_FAILED:{name}:{type(exc).__name__}")

    return VerifierOutcome(
        result=_verdict(criteria),
        criteria=tuple(criteria),
        detail={
            "evaluated": len(criteria),
            "skipped": len(checks) - len(criteria),
            "toleranceDefault": 1e-9,
        },
        unknowns=tuple(unknowns),
    )


# --------------------------------------------------------------------------
# 复现检查
# --------------------------------------------------------------------------


def code_repro_check(params: Mapping[str, Any]) -> VerifierOutcome:
    original = dict(params.get("original") or {})
    replica = dict(params.get("replica") or {})
    tolerance = float(params.get("tolerance") or 0.0)
    if not original or not replica:
        return VerifierOutcome(result="UNKNOWN", unknowns=("INSUFFICIENT_DATA",))

    cmp = compare_with_original(
        original,
        replica,
        metrics_orig=dict(params.get("metrics_original") or {}),
        metrics_replica=dict(params.get("metrics_replica") or {}),
        tolerance=tolerance,
    )
    verdict = str(cmp["verdict"])
    if verdict == "INSUFFICIENT_DATA":
        missing = cmp.get("missing") or {}
        unknowns = sorted(set(list(missing.get("original") or []) + list(missing.get("replica") or [])))
        return VerifierOutcome(
            result="UNKNOWN",
            unknowns=tuple(unknowns) or ("INSUFFICIENT_DATA",),
            detail={"verdict": verdict, "missing": missing},
        )
    # 复现的**硬要求**是输出哈希一致：哈希不同就不是同一次运行的结果，
    # 哪怕指标恰好落在容差内（那叫"看起来一样"，不叫可复现）。
    ok = verdict in {"EXACT_MATCH", "METRICS_TOLERANCE"} and bool(cmp.get("outputHashMatch"))
    effective = verdict if bool(cmp.get("outputHashMatch")) else "MISMATCH"
    return VerifierOutcome(
        result="PASS" if ok else "FAIL",
        criteria=(
            _criterion(
                "CODE_REPRO",
                ok,
                detail=f"verdict={effective}（outputHashMatch={bool(cmp.get('outputHashMatch'))}）",
                blocking="CODE_REPRO_MISMATCH",
                observed=effective,
                expected="EXACT_MATCH",
                reason=effective,
            ),
        ),
        detail={"verdict": effective, "comparison": cmp},
        artifacts=(f"artifact:repro/{effective.lower()}",),
    )


def data_repro_check(params: Mapping[str, Any]) -> VerifierOutcome:
    original = dict(params.get("original") or {})
    replica = dict(params.get("replica") or {})
    keys = ("datasetHash", "rows")
    missing = [k for k in keys if not original.get(k) or not replica.get(k)]
    if missing:
        return VerifierOutcome(result="UNKNOWN", unknowns=tuple(missing + ["INSUFFICIENT_DATA"]))

    mismatches: list[str] = []
    for key in keys:
        if original.get(key) != replica.get(key):
            mismatches.append(key)
    if original.get("checksum") and replica.get("checksum") and original["checksum"] != replica["checksum"]:
        mismatches.append("checksum")

    ok = not mismatches
    return VerifierOutcome(
        result="PASS" if ok else "FAIL",
        criteria=(
            _criterion(
                "DATA_REPRO",
                ok,
                detail=f"不一致字段: {mismatches}" if mismatches else "指纹/行数/校验和一致",
                blocking="DATA_REPRO_MISMATCH",
                observed=mismatches,
                expected=[],
                reason="DATA_REPRO_OK" if ok else "DATA_REPRO_MISMATCH",
            ),
        ),
        detail={"mismatches": mismatches},
        artifacts=(f"artifact:data-repro/{'ok' if ok else 'mismatch'}.json",),
    )


# --------------------------------------------------------------------------
# 敏感性 / robustness
# --------------------------------------------------------------------------


def sensitivity_check(params: Mapping[str, Any]) -> VerifierOutcome:
    baseline = params.get("baseline")
    tolerance = float(params.get("tolerance") or 0.05)
    perturbations = [p for p in (params.get("perturbations") or ()) if isinstance(p, Mapping)]
    if baseline is None or not perturbations:
        return VerifierOutcome(
            result="UNKNOWN", unknowns=("NO_PERTURBATIONS",) if baseline is not None else ("NO_BASELINE",)
        )

    scale = max(abs(float(baseline)), 1e-12)
    worst: dict[str, Any] | None = None
    for p in perturbations:
        metric = p.get("metric")
        if metric is None:
            continue
        relative = abs(float(metric) - float(baseline)) / scale
        row = {
            "parameter": str(p.get("parameter") or ""),
            "delta": p.get("delta"),
            "metric": float(metric),
            "relative": relative,
            "withinTolerance": relative <= tolerance,
        }
        if worst is None or row["relative"] > worst["relative"]:
            worst = row
    if worst is None:
        return VerifierOutcome(result="UNKNOWN", unknowns=("NO_METRIC_VALUES",))

    ok = worst["withinTolerance"]
    return VerifierOutcome(
        result="PASS" if ok else "FAIL",
        criteria=(
            _criterion(
                "SENSITIVITY",
                ok,
                detail=f"最差相对变化 {worst['relative']:.4f}（容差 {tolerance}，参数 {worst['parameter']}）",
                blocking="SENSITIVITY_UNSTABLE",
                observed=worst["relative"],
                expected=tolerance,
                reason="SENSITIVITY_OK" if ok else "SENSITIVITY_UNSTABLE",
            ),
        ),
        detail={"worst": worst, "tolerance": tolerance},
        artifacts=(f"artifact:sensitivity/{'stable' if ok else 'unstable'}.json",),
    )


def robustness_check(params: Mapping[str, Any]) -> VerifierOutcome:
    tolerance = float(params.get("tolerance") or 0.05)
    samples = [s for s in (params.get("samples") or ()) if isinstance(s, Mapping)]
    values = [float(s["metric"]) for s in samples if s.get("metric") is not None]
    if len(values) < 2:
        return VerifierOutcome(result="UNKNOWN", unknowns=("NEED_AT_LEAST_TWO_SEEDS",))

    mean = sum(values) / len(values)
    scale = max(abs(mean), 1e-12)
    spread = (max(values) - min(values)) / scale
    ok = spread <= tolerance
    seeds = [s.get("seed") for s in samples]
    return VerifierOutcome(
        result="PASS" if ok else "FAIL",
        criteria=(
            _criterion(
                "ROBUSTNESS",
                ok,
                detail=f"种子离散度 {spread:.4f}（容差 {tolerance}，{len(values)} 个种子）",
                blocking="ROBUSTNESS_SEED_SENSITIVE",
                observed=spread,
                expected=tolerance,
                reason="ROBUSTNESS_OK" if ok else "ROBUSTNESS_SEED_SENSITIVE",
            ),
        ),
        detail={"spread": spread, "seeds": seeds, "values": values},
        artifacts=(f"artifact:robustness/{'stable' if ok else 'seed-sensitive'}.json",),
    )


# --------------------------------------------------------------------------
# 反例 / 边界
# --------------------------------------------------------------------------

_VIOLATION_PREDICATES = {
    "negative": lambda v, tol: v < -tol,
    "positive": lambda v, tol: v > tol,
    "nonzero": lambda v, tol: abs(v) > tol,
    "zero": lambda v, tol: abs(v) <= tol,
}


def counterexample_check(params: Mapping[str, Any]) -> VerifierOutcome:
    candidates = [c for c in (params.get("candidates") or ()) if isinstance(c, Mapping)]
    if not candidates:
        return VerifierOutcome(result="UNKNOWN", unknowns=("NO_CANDIDATES",))

    unknowns: list[str] = []
    witness: dict[str, Any] | None = None
    for cand in candidates:
        name = str(cand.get("name") or "candidate")
        tolerance = float(cand.get("tolerance") or 1e-9)
        violates_when = str(cand.get("violates_when") or "nonzero")
        predicate = _VIOLATION_PREDICATES.get(violates_when)
        if predicate is None:
            unknowns.append(f"UNKNOWN_PREDICATE:{violates_when}")
            continue
        try:
            value = _eval_expr(str(cand.get("expression") or "0"), dict(cand.get("variables") or {}))
        except ExpressionRejected as exc:
            unknowns.append(f"EXPRESSION_REJECTED:{name}:{exc}")
            continue
        except (ZeroDivisionError, OverflowError, ValueError) as exc:
            unknowns.append(f"EVALUATION_FAILED:{name}:{type(exc).__name__}")
            continue
        if predicate(value, tolerance):
            witness = {
                "name": name,
                "expression": str(cand.get("expression") or ""),
                "variables": dict(cand.get("variables") or {}),
                "violatesWhen": violates_when,
                "value": value,
                "tolerance": tolerance,
            }
            break

    if witness is not None:
        return VerifierOutcome(
            result="FAIL",
            criteria=(
                _criterion(
                    "COUNTEREXAMPLE",
                    False,
                    detail=f"反例命中: {witness['name']} -> {witness['value']:g}",
                    blocking="COUNTEREXAMPLE_REPRODUCED",
                    observed=witness["value"],
                    expected=f"not {witness['violatesWhen']}",
                    reason="COUNTEREXAMPLE_REPRODUCED",
                ),
            ),
            detail={"witness": witness, "candidates": len(candidates)},
            # 反证必须留痕：见证物落成 artifact ref。
            artifacts=(f"artifact:counterexample/{_slug(witness['name'])}.json",),
        )
    if not [u for u in unknowns] and candidates:
        return VerifierOutcome(
            result="PASS",
            criteria=(
                _criterion(
                    "COUNTEREXAMPLE",
                    True,
                    detail=f"{len(candidates)} 个候选反例均未复现",
                    reason="NO_COUNTEREXAMPLE",
                ),
            ),
            detail={"witness": None, "candidates": len(candidates)},
            artifacts=("artifact:counterexample/none.json",),
        )
    return VerifierOutcome(result="UNKNOWN", unknowns=tuple(unknowns))


def boundary_check(params: Mapping[str, Any]) -> VerifierOutcome:
    cases = [c for c in (params.get("cases") or ()) if isinstance(c, Mapping)]
    if not cases:
        return VerifierOutcome(result="UNKNOWN", unknowns=("NO_CASES",))

    criteria: list[dict[str, Any]] = []
    unknowns: list[str] = []
    violations: list[dict[str, Any]] = []
    for idx, case in enumerate(cases):
        name = str(case.get("name") or f"case{idx + 1}")
        expect = str(case.get("expect") or "finite")
        expression = str(case.get("expression") or "0")
        variables = dict(case.get("variables") or {})
        try:
            value = _eval_expr(expression, variables)
            if expect == "finite":
                ok = math.isfinite(value)
            elif expect == "positive":
                ok = value > 0
            elif expect == "negative":
                ok = value < 0
            elif expect == "nonzero":
                ok = abs(value) > 1e-12
            else:
                unknowns.append(f"UNKNOWN_EXPECTATION:{expect}")
                continue
        except (ZeroDivisionError, OverflowError, ValueError):
            # 退化点上的除零/溢出 = 边界不变式被打破（这正是边界检查要找的东西）
            value = None
            ok = expect == "infinite"
        except ExpressionRejected as exc:
            unknowns.append(f"EXPRESSION_REJECTED:{name}:{exc}")
            continue

        row = _criterion(
            name,
            ok,
            detail=f"expect={expect} value={value!r}",
            blocking="BOUNDARY_VIOLATION",
            observed=value,
            expected=expect,
            reason="BOUNDARY_OK" if ok else "BOUNDARY_VIOLATION",
        )
        criteria.append(row)
        if not ok:
            violations.append({"name": name, "expect": expect, "value": value, "variables": variables})

    return VerifierOutcome(
        result=_verdict(criteria),
        criteria=tuple(criteria),
        detail={"violations": violations, "cases": len(cases)},
        artifacts=(
            (f"artifact:boundary/{_slug(violations[0]['name'])}.json",) if violations else ()
        ),
        unknowns=tuple(unknowns),
    )


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(text))[:64] or "item"


#: kind → 检查函数（**唯一**分发点；未知 kind 由注册表挡住）。
OUTCOME_HANDLERS: dict[str, Any] = {
    VerificationKind.MATH_CHECK.value: math_check,
    VerificationKind.DATA_REPRO.value: data_repro_check,
    VerificationKind.CODE_REPRO.value: code_repro_check,
    VerificationKind.SENSITIVITY.value: sensitivity_check,
    VerificationKind.ROBUSTNESS.value: robustness_check,
    VerificationKind.COUNTEREXAMPLE.value: counterexample_check,
    VerificationKind.BOUNDARY.value: boundary_check,
}


def run_offline_check(kind: str, params: Mapping[str, Any]) -> VerifierOutcome:
    handler = OUTCOME_HANDLERS.get(str(kind).upper())
    if handler is None:  # pragma: no cover - 注册表已保证封闭
        return VerifierOutcome(result="UNKNOWN", unknowns=(f"NO_HANDLER_FOR_KIND:{kind}",))
    return handler(params or {})


def run_verifier(verifier_id: str, params: Mapping[str, Any]) -> VerifierOutcome:
    """按 verifier id 执行检查（离线路径：不接触 DB / 网络）。"""
    from .registry import require_verifier

    spec = require_verifier(verifier_id)
    return run_offline_check(spec.kind, params or {})
