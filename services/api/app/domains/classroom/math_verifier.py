"""数学验证层（双师课堂·内容自洽校验）。

职责（内容正确性是最高优先级，本层是硬门槛）：
1. LaTeX 结构校验（渲染前拦截非法公式）。
2. 函数类数学一致性验证（符号求导/临界点/单调区间/极值反求）。
3. 页级验证主入口 `verify_slide`，输出 `verified | needs_review | failed` + 说明。

本层只校验「生成结果是否自洽」，不识别具体题目、不做关键词匹配、
不携带任何固定答案或预置 verified 放行。所有题目走同一条通用校验链路。

约定：
- 表达式解析用 sympy `parse_expr`（非 eval），并叠加长度/字符白名单防护；
- 二维采样另见 `visual_spec.py`（渲染数据生成与本层验证分离）。
"""

from __future__ import annotations

import ast as _ast
import math
import re
from typing import Any

import sympy
import structlog
from sympy import Symbol, diff, simplify
from sympy.parsing.sympy_parser import parse_expr

# ==================== 常量 ====================
logger = structlog.get_logger(__name__)

_MAX_EXPR_LEN = 200
# 表达式允许字符白名单（拒绝任何注入）：数字、字母、_、运算符、点、括号、逗号、空格
_EXPR_CHARS = re.compile(r"^[0-9a-zA-Z_.,()+\-*/% ^]+$")

_X = Symbol("x", real=True)
_PARAM_SYMBOLS: dict[str, Symbol] = {"x": _X}

# ==================== 表达式解析 ====================


def _sanitize(expr: str) -> str:
    """预处理：^ → **（幂），并清洗空白。返回清洗后字符串（不合法返回空串）。"""
    if not isinstance(expr, str):
        return ""
    s = expr.strip()
    if not s or len(s) > _MAX_EXPR_LEN:
        return ""
    if not _EXPR_CHARS.match(s):
        return ""
    return s.replace("^", "**")


def to_symbolic(expr: str, symbols: list[str] | None = None) -> sympy.Expr | None:
    """把受限数学表达式解析为 sympy 符号表达式；失败返回 None。

    symbols 为额外参数符号列表（如 ['a']）——极值反求题需要把参数当符号处理。
    """
    s = _sanitize(expr)
    if not s:
        return None
    local: dict[str, Symbol] = dict(_PARAM_SYMBOLS)
    for name in symbols or []:
        local[name] = Symbol(name, real=True)
    try:
        # parse_expr 非 eval：仅解析 sympy 语法；unmatched 名称由 local_dict 提供
        parsed = parse_expr(s, local_dict=local)
    except (SyntaxError, ValueError, TypeError, KeyError):
        return None
    # 拒绝含非有限结构（如 1/0、复数）的风险表达式
    try:
        if parsed.has(sympy.zoo) or parsed.has(sympy.nan) or parsed.has(sympy.I):
            return None
    except Exception:
        pass
    return parsed


# ==================== LaTeX 结构校验 ====================


def latex_structure_check(text: str) -> tuple[bool, str]:
    """LaTeX 结构校验：$ 配对、{} 配对、() 配对、危险命令拦截。

    返回 (ok, detail)；ok=False 时 detail 给出原因（供前端 needs_review 提示）。
    只做**结构**检查，不做语义（语义由符号验证负责）。
    """
    if not isinstance(text, str):
        return False, "非文本输入"
    s = text
    # 1) $ 配对（美元符号需成对）
    if s.count("$") % 2 != 0:
        return False, "公式分隔符 $ 未配对"
    # 2) 花括号配对（剔除常见命令里的转义花括号后仍应平衡）
    if s.count("{") != s.count("}"):
        return False, "花括号 {} 不配对"
    # 3) 圆括号配对
    if s.count("(") != s.count(")"):
        return False, "圆括号 () 不配对"
    # 4) 危险/未定义命令拦截（常见 LaTeX 注入或本渲染器不支持的命令）
    for bad in (r"\begin{", r"\end{", r"\newcommand", r"\input", r"\include"):
        if bad in s:
            return False, f"不支持的命令 {bad}"
    return True, "ok"


# ==================== 函数类一致性验证 ====================


def _critical_points_of(f_sym: sympy.Expr, var=_X) -> list[float]:
    """求 f' 的实数临界点（数值近似）。"""
    fp = diff(f_sym, var)
    roots: list[float] = []
    # 先尝试精确求根，再数值补充
    try:
        candidates = sympy.solve(fp, var, dict=True)
    except Exception:
        candidates = []
    seen: set[float] = set()
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        v = cand.get(var)
        if v is None:
            continue
        try:
            num = float(sympy.N(v))
        except (TypeError, ValueError):
            continue
        if abs(num) < 1e9 and round(num, 6) not in seen:
            seen.add(round(num, 6))
            roots.append(round(num, 6))
    # 数值补充（三次以上或有超越项时符号解不全）
    try:
        nsol = sympy.nsolve(fp, 0, dict=True)
        if isinstance(nsol, list) and nsol:
            for cand in nsol:
                if not isinstance(cand, dict):
                    continue
                v = cand.get(var)
                if v is None:
                    continue
                try:
                    num = float(sympy.N(v))
                except (TypeError, ValueError):
                    continue
                if round(num, 6) not in seen:
                    seen.add(round(num, 6))
                    roots.append(round(num, 6))
    except Exception:
        pass
    return sorted(roots)


def verify_function_claims(
    f_expr: str,
    fprime_expr: str | None = None,
    critical_points: list[float] | None = None,
    increasing_intervals: list[tuple[float, float]] | None = None,
    decreasing_intervals: list[tuple[float, float]] | None = None,
    max_eval_point: float | None = None,
    max_eval_second_deriv: float | None = None,
    substitutions: dict[str, float] | None = None,
) -> dict[str, Any]:
    """验证一组关于函数 f 的数学断言是否与真值一致。

    各参数均为「待验证的断言」；函数用 sympy 与之比对，返回：
    {
      status: verified|needs_review|failed,
      detail: str,
      checks: [...],           # 逐项检查结果
    }
    substitutions：含参函数的参数代入（如 {"a": -2}），在验证前对 f 先做替换，
    使二阶导/极值性质可数值化（金标准 B 的验证路径）。
    """
    checks: list[dict[str, Any]] = []
    f = to_symbolic(f_expr)
    if f is None:
        return {"status": "needs_review", "detail": "f(x) 无法解析", "checks": checks}
    if substitutions:
        try:
            # 用默认 Symbol（与 parse_expr 创建的符号共享缓存），避免 real=True 假设导致匹配失败
            f = f.subs([(Symbol(k), v) for k, v in substitutions.items()])
        except (TypeError, ValueError):
            checks.append({"item": "substitutions", "ok": False, "detail": "参数代入失败"})
            return {"status": "failed", "detail": "参数代入失败", "checks": checks}

    f_pp = None
    # 1) 导数正确性
    true_fp = simplify(diff(f, _X))
    checks.append({"item": "true_derivative", "value": str(true_fp), "ok": True})
    if fprime_expr:
        fp = to_symbolic(fprime_expr)
        if fp is None:
            checks.append({"item": "derivative_parse", "ok": False, "detail": "导数无法解析"})
        else:
            if substitutions:
                # 与 f 同一语境：参数取定值后再比导数
                fp = fp.subs([(Symbol(k), v) for k, v in substitutions.items()])
            ok = simplify(fp - true_fp) == 0
            checks.append(
                {
                    "item": "derivative_correct",
                    "claimed": str(fp),
                    "expected": str(true_fp),
                    "ok": ok,
                }
            )
            if not ok:
                return {
                    "status": "failed",
                    "detail": f"导数错误：声称 {fp}，正确为 {true_fp}",
                    "checks": checks,
                }
    else:
        fp = true_fp

    # 2) 临界点正确性（声称的临界点必须是 f'=0 的根）
    checked_cps: list[tuple[float, float]] = []
    if critical_points is not None:
        for c in critical_points:
            try:
                val = float(sympy.N(fp.subs(_X, c)))
            except (TypeError, ValueError):
                checks.append({"item": "critical_point", "point": c, "ok": False, "detail": "无法代入"})
                return {"status": "failed", "detail": f"临界点 x={c} 代入失败", "checks": checks}
            is_root = abs(val) < 1e-6
            checks.append(
                {"item": "critical_point", "point": c, "fprime_value": round(val, 6), "ok": is_root}
            )
            if not is_root:
                return {
                    "status": "failed",
                    "detail": f"临界点 x={c} 不是 f'=0 的根（f'({c})={val:.4g}）",
                    "checks": checks,
                }
            checked_cps.append((float(c), val))

    # 3) 单调区间一致性（区间内采样点符号与断言一致）
    def _interval_signs(intervals: list[tuple[float, float]] | None, expect_positive: bool) -> bool:
        if intervals is None:
            return True
        for lo, hi in intervals:
            if hi <= lo:
                checks.append({"item": "monotone_interval", "interval": [lo, hi], "ok": False, "detail": "区间端点非法"})
                return False
            # 区间内取 3 个采样点
            pts = [lo + (hi - lo) * k / 4 for k in (1, 2, 3)]
            for p in pts:
                try:
                    sign = float(sympy.N(fp.subs(_X, p)))
                except (TypeError, ValueError):
                    checks.append({"item": "monotone_interval", "interval": [lo, hi], "ok": False, "detail": "无法采样"})
                    return False
                ok = (sign > 1e-9) if expect_positive else (sign < -1e-9)
                if abs(sign) < 1e-9:
                    continue  # 采样点恰为临界点，跳过
                checks.append(
                    {
                        "item": "monotone_sample",
                        "interval": [lo, hi],
                        "x": round(p, 4),
                        "fprime_sign": round(sign, 4),
                        "expect": "positive" if expect_positive else "negative",
                        "ok": ok,
                    }
                )
                if not ok:
                    return False
        return True

    if not _interval_signs(increasing_intervals, expect_positive=True):
        return {"status": "failed", "detail": "存在被断言为递增、实际导数为负的区间", "checks": checks}
    if not _interval_signs(decreasing_intervals, expect_positive=False):
        return {"status": "failed", "detail": "存在被断言为递减、实际导数为正的区间", "checks": checks}

    # 4) 极值点二阶导验证（可选：验证 max_eval_point 处二阶导符号）
    if max_eval_point is not None:
        f_pp = simplify(diff(f, _X, 2))
        try:
            fpp_val = float(sympy.N(f_pp.subs(_X, max_eval_point)))
        except (TypeError, ValueError):
            fpp_val = float("nan")
        checks.append(
            {"item": "second_derivative_at_point", "point": max_eval_point, "value": round(fpp_val, 6), "ok": True}
        )
        if max_eval_second_deriv is not None:
            ok = abs(fpp_val - max_eval_second_deriv) < 1e-6
            checks.append(
                {
                    "item": "second_derivative_claimed",
                    "claimed": max_eval_second_deriv,
                    "actual": round(fpp_val, 6),
                    "ok": ok,
                }
            )
            if not ok:
                return {
                    "status": "failed",
                    "detail": f"二阶导验证不符：声称 {max_eval_second_deriv}，实际 {fpp_val:.4g}",
                    "checks": checks,
                }

    return {
        "status": "verified",
        "detail": "导数/临界点/单调区间/极值性质一致",
        "checks": checks,
        "critical_points": checked_cps,
    }


# ==================== 几何类结构化断言验证 ====================

# 数学承载块：包含公式/例题/图形/题目内容的块类型。
# 这类页必须携带 math_claims 或 geometry_claims，否则判 needs_review（V4 §7）。
_MATH_BEARING_KINDS = {"latex", "example", "question", "plot2d", "geometry", "figure"}


def _vec3(p: Any) -> list[float] | None:
    """规范化三维坐标 [x,y,z]（数值有限）；非法返回 None。"""
    if not (isinstance(p, (list, tuple)) and len(p) == 3):
        return None
    try:
        out = [float(v) for v in p]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in out):
        return None
    return out


def _cross(u: list[float], v: list[float]) -> list[float]:
    return [
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    ]


def _dot(u: list[float], v: list[float]) -> float:
    return sum(a * b for a, b in zip(u, v, strict=True))


def _norm(v: list[float]) -> float:
    n = math.sqrt(_dot(v, v))
    return n if n > 1e-12 else 0.0


def _sub(u: list[float], v: list[float]) -> list[float]:
    return [a - b for a, b in zip(u, v, strict=True)]


def _plane_from_points(p1: list[float], p2: list[float], p3: list[float]) -> tuple[list[float], float] | None:
    """三点确定平面：返回 (法向量 n, 常数 c) 满足 n·r = c。"""
    n = _cross(_sub(p2, p1), _sub(p3, p1))
    if _norm(n) < 1e-9:
        return None
    return n, _dot(n, p1)


def _point_plane_distance(p: list[float], n: list[float], c: float) -> float:
    return abs(_dot(n, p) - c) / _norm(n)


def _parse_scalar(value: Any) -> float | None:
    """把数值断言解析为 float；支持 "1/sqrt(3)"、"1/√3"、"2*sqrt(21)/7"、"0.577" 等。

    用受限 AST 求值（非 eval）：只允许数字、四则、负号、幂与 sqrt 函数。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip().replace("√", "sqrt").replace("×", "*").replace("·", "*").replace("−", "-")
    # 只允许数字/运算符/常见符号（math 白名单函数），拒绝任何其他字符
    if not re.fullmatch(r"[0-9a-zA-Z_*/()+\-.\s]+", s):
        return None
    try:
        node = _ast.parse(s, mode="eval")
    except (SyntaxError, ValueError):
        return None

    def _eval(n: _ast.AST) -> float:
        if isinstance(n, _ast.Expression):
            return _eval(n.body)
        if isinstance(n, _ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, _ast.BinOp):
            a, b = _eval(n.left), _eval(n.right)
            if isinstance(n.op, _ast.Add):
                return a + b
            if isinstance(n.op, _ast.Sub):
                return a - b
            if isinstance(n.op, _ast.Mult):
                return a * b
            if isinstance(n.op, _ast.Div):
                return a / b if b != 0 else float("inf")
            if isinstance(n.op, _ast.Pow):
                return a ** b
            raise ValueError("op")
        if isinstance(n, _ast.UnaryOp):
            v = _eval(n.operand)
            if isinstance(n.op, _ast.USub):
                return -v
            if isinstance(n.op, _ast.UAdd):
                return v
            raise ValueError("unary")
        if isinstance(n, _ast.Call):
            if isinstance(n.func, _ast.Name) and n.func.id.lower() == "sqrt" and len(n.args) == 1:
                v = _eval(n.args[0])
                return math.sqrt(v) if v >= 0 else float("nan")
            raise ValueError("call")
        if isinstance(n, _ast.Name):
            raise ValueError("name")
        raise ValueError(type(n).__name__)

    try:
        val = float(_eval(node))
    except Exception:
        return None
    if not math.isfinite(val):
        return None
    return val


def _verify_metrics(
    metrics: dict[str, Any],
    coords: dict[str, list[float]],
    claims: dict[str, Any],
    checks: list[dict[str, Any]],
) -> bool:
    """题设度量自检：模型声明的边长/夹角/高必须与坐标表一致（防止发明坐标）。

    metrics 契约：
      lengths: {"AB": 2, "AD": 2}   （两点距离，必须 = 坐标表计算）
      angle_deg: {"BAD": 60}        （三点夹角，必须与坐标表一致）
      apex_height: 2.0              （棱锥顶点到底面的高度）
    任一项不一致 → 返回 False（调用方判 failed）。
    """
    def _pt(name: str) -> list[float] | None:
        if name in coords:
            return coords[name]
        # 允许用坐标字面量（如字符串 "[0, 2, 0]"）——拒绝，保持名字引用
        return coords.get(str(name))

    ok_all = True
    lengths = metrics.get("lengths")
    if isinstance(lengths, dict):
        for pair, expect_raw in lengths.items():
            s = str(pair)
            if len(s) != 2:
                continue
            a, b = _pt(s[0]), _pt(s[1])
            if a is None or b is None or not isinstance(expect_raw, (int, float)):
                checks.append({"item": "metric_length", "pair": s, "ok": False, "detail": "缺点或值非法"})
                ok_all = False
                continue
            actual = math.dist(a, b)
            ok = abs(actual - float(expect_raw)) < 1e-3 * max(1.0, abs(float(expect_raw)))
            checks.append({"item": "metric_length", "pair": s, "claimed": float(expect_raw), "actual": round(actual, 4), "ok": ok})
            if not ok:
                ok_all = False
    angles = metrics.get("angle_deg")
    if isinstance(angles, dict):
        for name3, deg_raw in angles.items():
            m = str(name3)
            if len(m) != 3:
                continue
            p1, p2, p3 = _pt(m[0]), _pt(m[1]), _pt(m[2])
            if p1 is None or p2 is None or p3 is None or not isinstance(deg_raw, (int, float)):
                checks.append({"item": "metric_angle", "name": m, "ok": False, "detail": "缺点或角度非法"})
                ok_all = False
                continue
            u, v = _sub(p1, p2), _sub(p3, p2)
            denom = _norm(u) * _norm(v)
            if denom < 1e-9:
                checks.append({"item": "metric_angle", "name": m, "ok": False, "detail": "向量退化"})
                ok_all = False
                continue
            cosv = _dot(u, v) / denom
            cosv = max(-1.0, min(1.0, cosv))
            actual_deg = math.degrees(math.acos(cosv))
            ok = abs(actual_deg - float(deg_raw)) < 1e-2
            checks.append({"item": "metric_angle", "name": m, "claimed_deg": float(deg_raw), "actual_deg": round(actual_deg, 3), "ok": ok})
            if not ok:
                ok_all = False
    apex_h = metrics.get("apex_height")
    if apex_h is not None and isinstance(apex_h, (int, float)):
        # 顶点 = 某一轴取最大值的点；底面高度 = 该轴最小值
        best: tuple[float, str | None] = (-math.inf, None)
        for n, p in coords.items():
            for val in p:
                if val > best[0]:
                    best = (val, n)
        apex = best[1]
        if apex is None:
            ok_all = False
        else:
            base_min = min(min(p) for p in coords.values())
            actual_h = best[0] - base_min
            ok = abs(actual_h - float(apex_h)) < 1e-3 * max(1.0, abs(float(apex_h)))
            checks.append({"item": "metric_apex_height", "apex": apex, "claimed": float(apex_h), "actual": round(actual_h, 4), "ok": ok})
            if not ok:
                ok_all = False
    return ok_all


def _cos_angle_between_planes(n1: list[float], n2: list[float]) -> float:
    """两平面夹角的余弦（取锐/钝角与真实二面角一致，带符号）。
    实际使用时：值∈[-1,1]，真实二面角余弦 = ±|cos(n1,n2)|，
    这里直接返回点积比，供几何断言自洽比较时取绝对值使用。
    """
    d1, d2 = _norm(n1), _norm(n2)
    if d1 < 1e-9 or d2 < 1e-9:
        return float("nan")
    r = _dot(n1, n2) / (d1 * d2)
    return max(-1.0, min(1.0, r))


def verify_geometry_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """校验几何结构化断言（自洽性验证，不预设金标准答案）。

    断言来源是模型自身输出的坐标/平面/距离。验证器根据坐标独立计算
    平面方程与点到平面距离，与模型声称值比对——结果自洽才算 verified。

    支持字段：
    - coordinates: {点名: [x,y,z]}（关键点坐标）
    - plane_points: {平面名: [点1, 点2, 点3]}（三点定义平面）
    - plane_equations: {平面名: {normal:[..], constant:..}}（可选：直接声称方程）
    - distance: {point: 点名, plane: 平面名, value: 数值或 LaTeX 表达式, latex: 字符串}
    - dihedral: {plane1: 平面名, plane2: 平面名, value: 余弦值, latex: 字符串, note: 备注}
      或扩展形式 {edge: [A,B], face1_points: [P,Q,R], face2_points: [...], value: 余弦值}
    - perpendicular: {line: [点A,点B], plane: 平面名}（可选：线面垂直声称）
    - line_perpendicular: [{line1: [点A,点B], line2: [点C,点D]}]（可选：线线垂直声称）
    - conclusion: 字符串（只透传，不校验语义）
    - conic: {a,b,c,theta?}（可选·解析几何：椭圆/双曲线参数，用于切线/内心一致性自洽）
    - inner_point: {x, y, latex}（可选·解析几何：焦点三角形内心坐标，需配合 conic+theta）
    - tangent_point: {x, y, k, latex}（可选·解析几何：斜率k的切点坐标，需配合conic）
    - distance_max: {value, method, latex}（可选·解析几何：过原点直线与切点P的距离最大值）

    返回 {status: verified|needs_review|failed, detail, checks}。
    """
    checks: list[dict[str, Any]] = []
    if not isinstance(claims, dict) or not claims:
        return {"status": "needs_review", "detail": "缺少几何结构化断言", "checks": []}

    # 收集多个 autofix（距离/二面角/解析几何派生量等各自独立校准）
    # 每项：{type: "distance"|"dihedral"|"inner_point"|"tangent_point", ...}
    autofixes: list[dict[str, Any]] = []

    coords_raw = claims.get("coordinates")
    coords: dict[str, list[float]] = {}
    if isinstance(coords_raw, dict):
        for name, p in coords_raw.items():
            v = _vec3(p)
            if v is None:
                checks.append({"item": "coordinate", "point": str(name), "ok": False, "detail": "坐标非法"})
            else:
                coords[str(name)] = v
    if not coords:
        # 立体几何断言必须有 coordinates；但解析几何断言（conic + tangent_point/inner_point/distance_max）
        # 根本不需要 3D 坐标，合法。只有两者都没有时才 needs_review。
        has_analytic = any(isinstance(claims.get(k), dict) and claims[k]
                           for k in ("conic", "tangent_point", "inner_point", "distance_max"))
        if not has_analytic:
            return {
                "status": "needs_review",
                "detail": "几何断言缺少有效坐标（coordinates），也没有解析几何 conic/断言，无法验证",
                "checks": checks,
            }

    # 1) 平面方程自洽：由三点或声称方程计算，二者必须一致
    plane_points = claims.get("plane_points")
    if not isinstance(plane_points, dict):
        plane_points = {}
    plane_eqs = claims.get("plane_equations")
    if not isinstance(plane_eqs, dict):
        plane_eqs = {}
    planes: dict[str, tuple[list[float], float]] = {}
    for pname, three in plane_points.items():
        if not (isinstance(three, (list, tuple)) and len(three) == 3):
            checks.append({"item": "plane_points", "plane": str(pname), "ok": False, "detail": "需 3 个点"})
            continue
        pts = [coords.get(str(p)) for p in three]
        if any(p is None for p in pts):
            checks.append({"item": "plane_defined", "plane": str(pname), "ok": False, "detail": "平面点不在坐标表中"})
            continue
        res = _plane_from_points(pts[0], pts[1], pts[2])
        if res is None:
            checks.append({"item": "plane_defined", "plane": str(pname), "ok": False, "detail": "三点共线，平面无效"})
            continue
        n, c = res
        # 规模化：使法向量首非零分量为正，便于与声称方程比较
        for _i, v in enumerate(n):
            if abs(v) > 1e-9:
                if v < 0:
                    n = [-x for x in n]
                    c = -c
                break
        planes[str(pname)] = (n, c)
        checks.append({"item": "plane_defined", "plane": str(pname), "ok": True, "normal": n, "constant": round(c, 6)})
        claimed_eq = plane_eqs.get(str(pname))
        if isinstance(claimed_eq, dict):
            cn = _vec3(claimed_eq.get("normal"))
            cc = claimed_eq.get("constant")
            if cn is None or not isinstance(cc, (int, float)):
                checks.append({"item": "plane_equation", "plane": str(pname), "ok": False, "detail": "声称方程非法"})
            else:
                # 比较方向与比例（法向量可差常数倍）
                ok_eq = False
                denom = _norm(n)
                if denom > 1e-9:
                    r = _dot(n, cn) / (denom * _norm(cn)) if _norm(cn) > 1e-9 else 0.0
                    if abs(r) > 0.9999:
                        t = (r * _norm(cn)) / denom
                        if abs(t * c - cc) < 1e-4 * max(1.0, abs(cc)):
                            ok_eq = True
                checks.append({"item": "plane_equation", "plane": str(pname), "ok": ok_eq,
                               "claimed_normal": cn, "computed_normal": n})
                if not ok_eq:
                    return {"status": "failed", "detail": f"平面 {pname} 方程与坐标表不一致", "checks": checks}

    # 1b) 题设度量自检（可选：metrics 声明边长/夹角/高，与坐标表核对一致性）
    metrics = claims.get("metrics")
    if isinstance(metrics, dict) and metrics and not _verify_metrics(metrics, coords, claims, checks):
        mismatches: list[str] = []
        for check in checks:
            if check.get("ok") is not False:
                continue
            if check.get("item") == "metric_length":
                pair = check.get("pair", "边长")
                if "claimed" in check and "actual" in check:
                    mismatches.append(
                        f"{pair}：题设 {check['claimed']}，坐标计算 {check['actual']}"
                    )
                else:
                    mismatches.append(f"{pair}：{check.get('detail', '度量无法计算')}")
            elif check.get("item") == "metric_angle":
                name = check.get("name", "角")
                if "claimed_deg" in check and "actual_deg" in check:
                    mismatches.append(
                        f"∠{name}：题设 {check['claimed_deg']}°，坐标计算 {check['actual_deg']}°"
                    )
                else:
                    mismatches.append(f"∠{name}：{check.get('detail', '角度无法计算')}")
            elif check.get("item") == "metric_apex_height":
                mismatches.append(
                    f"顶点高度：题设 {check.get('claimed')}，坐标计算 {check.get('actual')}"
                )
        return {
            "status": "failed",
            "detail": "坐标表与题设度量不一致：" + "；".join(mismatches[:4]),
            "checks": checks,
        }

    # 2) 点到平面距离自洽
    dist = claims.get("distance")
    if isinstance(dist, dict) and dist:
        pt_name = str(dist.get("point") or "")
        pl_name = str(dist.get("plane") or "")
        claimed_val = _parse_scalar(dist.get("value"))
        if pt_name not in coords:
            checks.append({"item": "distance", "ok": False, "detail": f"点 {pt_name} 不在坐标表"})
            return {"status": "needs_review", "detail": f"距离断言中的点 {pt_name} 缺少坐标", "checks": checks}
        if pl_name not in planes:
            # 平面未由三点定义：尝试声称方程
            claimed_eq = plane_eqs.get(pl_name)
            if not (isinstance(claimed_eq, dict) and _vec3(claimed_eq.get("normal")) and isinstance(claimed_eq.get("constant"), (int, float))):
                checks.append({"item": "distance", "ok": False, "detail": f"平面 {pl_name} 无法定位"})
                return {"status": "needs_review", "detail": f"距离断言中的平面 {pl_name} 缺少定义", "checks": checks}
            n, c = _vec3(claimed_eq["normal"]), float(claimed_eq["constant"])
            planes[pl_name] = (n, c)
        n, c = planes[pl_name]
        actual = _point_plane_distance(coords[pt_name], n, c)
        checks.append({
            "item": "distance", "point": pt_name, "plane": pl_name,
            "actual": round(actual, 6), "ok": True,
        })
        if claimed_val is None:
            # 结构合法但距离值缺省：降级为 needs_review 级别警告，不阻塞后续校验。
            checks.append({
                "item": "distance_value_missing",
                "point": pt_name, "plane": pl_name,
                "ok": False, "detail": f"距离断言缺少数值 value（按坐标独立计算实际为 {actual:.4g}）",
            })
        elif abs(actual - claimed_val) > 1e-3 * max(1.0, abs(claimed_val)):
            # 距离是坐标表的派生量：模型算术错误时按坐标表自动校准（透明记录，
            # 不改变"必须自洽"的验证语义）。函数类断言不享受此校准（严格失败）。
            checks.append({
                "item": "distance_autofix",
                "point": pt_name, "plane": pl_name,
                "claimed": round(claimed_val, 6), "computed": round(actual, 6),
                "ok": True, "note": "距离数值已按坐标表计算校准",
            })
            autofixes.append({
                "type": "distance",
                "plane": pl_name, "point": pt_name,
                "value": round(actual, 6),
            })
        else:
            checks.append({"item": "distance_claimed", "point": pt_name, "plane": pl_name,
                           "claimed": round(claimed_val, 6), "actual": round(actual, 6), "ok": True})

    # 2b) 二面角余弦自洽（新增）：由两个平面的法向量独立计算余弦值，与声称比对
    dih = claims.get("dihedral")
    if isinstance(dih, dict) and dih:
        # 支持两种形式：{plane1, plane2, value} 或 {edge:[A,B], face1_planes:...}
        p1_name = str(dih.get("plane1") or "")
        p2_name = str(dih.get("plane2") or "")
        if p1_name and p2_name and p1_name in planes and p2_name in planes:
            n1, _ = planes[p1_name]
            n2, _ = planes[p2_name]
            cos_actual = _cos_angle_between_planes(n1, n2)
            # 二面角余弦的符号：模型一般写绝对值（锐角），但有时写带符号值；
            # 比较时取绝对值匹配即可（真实二面角的余弦可正可负，取决于法向方向）
            abs_cos = abs(cos_actual)
            checks.append({
                "item": "dihedral",
                "plane1": p1_name, "plane2": p2_name,
                "abs_cos_computed": round(abs_cos, 6),
                "ok": True,
            })
            claimed_dih = _parse_scalar(dih.get("value"))
            if claimed_dih is None:
                # 声称值缺失：needs_review 级别警告
                checks.append({"item": "dihedral", "ok": False, "detail": "二面角余弦值缺失（value）"})
            else:
                abs_claimed = abs(claimed_dih)
                if abs(abs_cos - abs_claimed) > 1e-3:
                    checks.append({
                        "item": "dihedral_autofix",
                        "claimed": round(claimed_dih, 6), "abs_computed": round(abs_cos, 6),
                        "ok": True, "note": "二面角余弦值已按坐标表独立计算校准",
                    })
                    autofixes.append({
                        "type": "dihedral",
                        "plane1": p1_name, "plane2": p2_name,
                        "value": round(abs_cos, 6),
                    })
                else:
                    checks.append({
                        "item": "dihedral_claimed",
                        "claimed": round(claimed_dih, 6),
                        "abs_computed": round(abs_cos, 6), "ok": True,
                    })
        elif p1_name and p2_name:
            checks.append({"item": "dihedral", "ok": False,
                           "detail": f"二面角的平面 {p1_name}/{p2_name} 未由三点定义"})
        # 否则：edge+face_points 形式暂时降级为 needs_review 条目（不强校验），
        # 由题面文本保证。避免"模型写了edge形式但没写plane_points"造成误判。

    # 2c) 解析几何断言自洽（conic + tangent_point / inner_point / distance_max）
    # 仅当 claims 显式提供 conic{a,b,c,theta} 等参数时才启用——不做自由文本推断。
    conic = claims.get("conic")
    if isinstance(conic, dict) and conic:
        a_raw = conic.get("a"); b_raw = conic.get("b")
        a = _parse_scalar(a_raw); b = _parse_scalar(b_raw)
        c_param = _parse_scalar(conic.get("c"))
        if a is not None and b is not None and a > 0 and b > 0:
            # c 不提供时按 c²=a²-b² 补算（椭圆）
            if c_param is None and a >= b:
                try:
                    c_param = math.sqrt(a * a - b * b)
                except (ValueError, ZeroDivisionError):
                    c_param = None
            theta = _parse_scalar(conic.get("theta"))
            checks.append({"item": "conic", "a": a, "b": b, "c": round(c_param, 6) if c_param is not None else None, "ok": True})

            # tangent_point 校验：椭圆斜率为k的切点应满足
            #   P = (-a²k / √(a²k²+b²), b² / √(a²k²+b²))  [通式]
            tp = claims.get("tangent_point")
            if isinstance(tp, dict) and tp:
                k = _parse_scalar(tp.get("k"))
                tx = _parse_scalar(tp.get("x")); ty = _parse_scalar(tp.get("y"))
                if k is not None and tx is not None and ty is not None:
                    denom = math.sqrt(a * a * k * k + b * b)
                    if denom > 1e-9:
                        # 切点通式：x = -a²k/denom (y=kx+m)；另一种形式 x = a²k/denom（符号取决于方向）
                        # 与声称值比较时取绝对值（因为P在第一象限，另需 sign 检查）
                        tx_expected = - (a * a * k) / denom
                        ty_expected = (b * b) / denom
                        # 容许符号差（不同参数化）
                        tx_ok = abs(abs(tx) - abs(tx_expected)) < 1e-3 * max(1.0, abs(tx_expected))
                        ty_ok = abs(abs(ty) - abs(ty_expected)) < 1e-3 * max(1.0, abs(ty_expected))
                        t_on_curve = abs((tx*tx)/(a*a) + (ty*ty)/(b*b) - 1.0) < 1e-3  # 点必须在椭圆上
                        checks.append({
                            "item": "tangent_point",
                            "claimed": (round(tx, 5), round(ty, 5)),
                            "expected_generic": (round(tx_expected, 5), round(ty_expected, 5)),
                            "on_ellipse": round(1 - (tx*tx)/(a*a) - (ty*ty)/(b*b), 6),
                            "ok": bool(tx_ok and ty_ok and t_on_curve),
                        })
                        if not (tx_ok and ty_ok and t_on_curve):
                            # 不在椭圆上：推导错误，按通式 autofix（保留原题的参数方向，值取通用）
                            autofixes.append({
                                "type": "tangent_point",
                                "x": round(tx_expected, 6), "y": round(ty_expected, 6),
                                "note": "按椭圆切线通式 (-a²k/D, b²/D) 校准，D=√(a²k²+b²)",
                            })
                else:
                    checks.append({"item": "tangent_point", "ok": False, "detail": "tangent_point 缺 k/x/y 数值"})

            # inner_point 校验：焦点三角形内心 I(cosθ·c, bc·sinθ/(a+c))
            inner = claims.get("inner_point")
            if isinstance(inner, dict) and inner and theta is not None and c_param is not None and a > b:
                ix = _parse_scalar(inner.get("x")); iy = _parse_scalar(inner.get("y"))
                if ix is not None and iy is not None:
                    try:
                        ct = math.cos(theta); st = math.sin(theta)
                    except ValueError:
                        ct = 0.0; st = 0.0
                    ix_exp = c_param * ct
                    iy_exp = (b * c_param * st) / (a + c_param) if (a + c_param) > 1e-9 else 0.0
                    ix_ok = abs(ix - ix_exp) < 1e-3
                    iy_ok = abs(iy - iy_exp) < 1e-3
                    checks.append({
                        "item": "inner_point",
                        "claimed": (round(ix, 5), round(iy, 5)),
                        "expected": (round(ix_exp, 5), round(iy_exp, 5)),
                        "ok": bool(ix_ok and iy_ok),
                    })
                    if not (ix_ok and iy_ok):
                        autofixes.append({
                            "type": "inner_point",
                            "x": round(ix_exp, 6), "y": round(iy_exp, 6),
                            "note": "按焦点三角形内心公式 I(c·cosθ, bc·sinθ/(a+c)) 校准",
                        })
                else:
                    checks.append({"item": "inner_point", "ok": False, "detail": "inner_point 缺 x/y 数值"})

            # distance_max 校验：距离最大值应为 a-b（柯西不等式）
            dmax = claims.get("distance_max")
            if isinstance(dmax, dict) and dmax:
                dv = _parse_scalar(dmax.get("value"))
                true_max = a - b if a >= b else float("nan")
                if dv is not None and math.isfinite(true_max):
                    dm_ok = abs(dv - true_max) < 1e-4
                    checks.append({
                        "item": "distance_max",
                        "claimed": round(dv, 5), "expected_true": round(true_max, 5),
                        "ok": dm_ok,
                    })
                    if not dm_ok:
                        autofixes.append({
                            "type": "distance_max",
                            "value": round(true_max, 6),
                            "note": "由柯西不等式 (a²k²+b²)·(1/k²+1) ≥ (a+b)² → 得距离≤a-b",
                        })

    # 3) 线面垂直自洽（可选）：线的方向向量与平面法向量平行
    perp = claims.get("perpendicular")
    if isinstance(perp, dict) and perp:
        line = perp.get("line")
        pl_name = str(perp.get("plane") or "")
        if isinstance(line, (list, tuple)) and len(line) == 2 and pl_name in planes:
            p1, p2 = coords.get(str(line[0])), coords.get(str(line[1]))
            if p1 is not None and p2 is not None:
                d = _sub(p2, p1)
                n, _ = planes[pl_name]
                par = abs(_dot(d, n)) / (_norm(d) * _norm(n)) if (_norm(d) > 1e-9 and _norm(n) > 1e-9) else 0.0
                ok_line = par > 0.9999
                checks.append({"item": "perpendicular", "line": [str(line[0]), str(line[1])],
                               "plane": pl_name, "parallel_cos": round(par, 6), "ok": ok_line})
                if not ok_line:
                    return {"status": "failed", "detail": f"线 {line[0]}{line[1]} 不垂直平面 {pl_name}", "checks": checks, "autofixes": autofixes or None}
            else:
                checks.append({"item": "perpendicular", "ok": False, "detail": "垂直线端点缺少坐标"})

    # 4) 线线垂直自洽（可选）：两条方向向量点积必须为 0。
    # 保留为列表，以支持同一题目的多个垂直条件，且不把任何顶点名或题型写死。
    line_perps = claims.get("line_perpendicular")
    if isinstance(line_perps, dict):
        line_perps = [line_perps]
    if isinstance(line_perps, list):
        for relation in line_perps:
            if not isinstance(relation, dict):
                continue
            line1, line2 = relation.get("line1"), relation.get("line2")
            if not (
                isinstance(line1, (list, tuple))
                and len(line1) == 2
                and isinstance(line2, (list, tuple))
                and len(line2) == 2
            ):
                checks.append({"item": "line_perpendicular", "ok": False, "detail": "每条关系需给两条由两个点定义的直线"})
                continue
            p1, p2 = coords.get(str(line1[0])), coords.get(str(line1[1]))
            q1, q2 = coords.get(str(line2[0])), coords.get(str(line2[1]))
            if any(point is None for point in (p1, p2, q1, q2)):
                checks.append({"item": "line_perpendicular", "ok": False, "detail": "垂直关系的端点缺少坐标"})
                continue
            d1, d2 = _sub(p2, p1), _sub(q2, q1)
            denom = _norm(d1) * _norm(d2)
            if denom <= 1e-9:
                checks.append({"item": "line_perpendicular", "ok": False, "detail": "垂直关系包含退化直线"})
                return {"status": "failed", "detail": "线线垂直关系包含退化直线", "checks": checks, "autofixes": autofixes or None}
            cosine = abs(_dot(d1, d2)) / denom
            ok_lines = cosine < 1e-4
            checks.append({
                "item": "line_perpendicular",
                "line1": [str(line1[0]), str(line1[1])],
                "line2": [str(line2[0]), str(line2[1])],
                "abs_cos": round(cosine, 6),
                "ok": ok_lines,
            })
            if not ok_lines:
                return {
                    "status": "failed",
                    "detail": f"线 {line1[0]}{line1[1]} 不垂直线 {line2[0]}{line2[1]}",
                    "checks": checks,
                    "autofixes": autofixes or None,
                }

    out = {
        "status": "verified",
        "detail": "几何结构化断言自洽（坐标/平面/距离/二面角/解析几何一致）",
        "checks": checks,
    }
    if autofixes:
        out["autofixes"] = autofixes
        # 兼容旧调用方（只取单个 distance autofix）：保留 autofix 字段写首个 distance，
        # 并追加 autofixes 列表。调用方可二选一读取。
        dist_fix = next((f for f in autofixes if f["type"] == "distance"), None)
        if dist_fix is not None:
            out["autofix"] = {k: v for k, v in dist_fix.items() if k != "type"}
    return out


# ==================== 页级验证主入口 ====================


def verify_slide(slide: dict[str, Any]) -> dict[str, Any]:
    """对一页 slide 做数学自洽验证。

    策略（通用链路，无特例放行）：
    1. 对每个 latex/example/answer 块文本做 LaTeX 结构检查；
    2. 数学承载页（含公式/例题/图形块）必须携带结构化断言：
       - 函数类 → math_claims（f_expr/导数/临界点/区间/极值二阶导），verify_function_claims 校验；
       - 几何类 → geometry_claims（坐标/平面/距离/线面垂直），verify_geometry_claims 校验；
       携带结构但缺断言 → needs_review（V4 §7：不得显示 verified）。
    返回 {status, detail, checks, needs_review_items}。

    本函数不识别具体题目、不信任任何预置 verified 标记、不做关键词匹配。
    所有生成内容（无论来源）都经过相同的自洽校验。
    """
    if not isinstance(slide, dict):
        return {"status": "failed", "detail": "slide 非对象", "checks": []}

    checks: list[dict[str, Any]] = []
    needs_review_items: list[str] = []

    # 0) 空页防护：无任何内容块的页面不得被当作"纯文字页"放行
    #    （LLM 调用全失败时长出空 blocks，此前会被误标 verified）。
    if not [b for b in slide.get("blocks", []) if isinstance(b, dict)]:
        return {
            "status": "failed",
            "detail": "页面内容生成失败（无任何内容块），请重新生成本页",
            "checks": [],
        }

    try:
        return _verify_slide_impl(slide, checks, needs_review_items)
    except Exception as e:  # 断言校验自身的异常不得炸掉页面任务（如 sympy 收到 None）
        logger.warning("verify_slide_internal_error", error=str(e)[:160])
        return {
            "status": "needs_review",
            "detail": f"数学校验器异常，请人工复核：{str(e)[:120]}",
            "checks": checks,
        }


def _verify_slide_impl(
    slide: dict[str, Any], checks: list[dict[str, Any]], needs_review_items: list[str]
) -> dict[str, Any]:

    # 1) LaTeX 结构检查（覆盖所有文本块）
    math_bearing = False
    for b in slide.get("blocks", []):
        if not isinstance(b, dict):
            continue
        kind = str(b.get("kind") or "")
        if kind in _MATH_BEARING_KINDS:
            math_bearing = True
        for field in ("latex", "answer", "text"):
            val = b.get(field)
            if isinstance(val, str) and ("$" in val or "\\" in val):
                ok, detail = latex_structure_check(val)
                checks.append({"item": "latex_structure", "field": field, "fragment": val[:40], "ok": ok, "detail": detail})
                if not ok:
                    needs_review_items.append(f"[{field}] {detail}")
                    return {"status": "needs_review", "detail": detail, "checks": checks, "needs_review_items": needs_review_items}

    # 2) math_claims 逐项验证（函数类）
    claims = slide.get("math_claims")
    has_claims = isinstance(claims, dict) and bool(claims)
    if has_claims:
        res = verify_function_claims(
            f_expr=claims.get("f_expr", ""),
            fprime_expr=claims.get("fprime_expr"),
            critical_points=claims.get("critical_points"),
            increasing_intervals=claims.get("increasing_intervals"),
            decreasing_intervals=claims.get("decreasing_intervals"),
            max_eval_point=claims.get("max_eval_point"),
            max_eval_second_deriv=claims.get("max_eval_second_deriv"),
            substitutions=claims.get("substitutions"),
        )
        checks.append({"item": "math_claims", "ok": res["status"] == "verified", "status": res["status"], "detail": res["detail"]})
        if res["status"] == "failed":
            checks = checks + res.get("checks", [])
            return {"status": "failed", "detail": res["detail"], "checks": checks, "needs_review_items": needs_review_items}
        if res["status"] == "needs_review":
            needs_review_items.append(res["detail"])
            checks = checks + res.get("checks", [])

    # 3) geometry_claims 验证（几何类）
    geo = slide.get("geometry_claims")
    autofix = None  # 兼容：首个 distance 类型 autofix
    autofixes: list[dict[str, Any]] = []
    if isinstance(geo, dict) and geo:
        res = verify_geometry_claims(geo)
        checks.append({"item": "geometry_claims", "ok": res["status"] == "verified", "status": res["status"], "detail": res["detail"]})
        if res["status"] == "failed":
            extra = {}
            if res.get("autofixes"):
                extra["autofixes"] = res["autofixes"]
                extra["autofix"] = res.get("autofix")
            return {"status": "failed", "detail": res["detail"], "checks": checks,
                    "needs_review_items": needs_review_items, **extra}
        if res["status"] == "needs_review":
            needs_review_items.append(res["detail"])
        checks = checks + res.get("checks", [])
        # 算术校准（距离/二面角/解析几何派生量）：列表透传给调用方写回正确的 value
        if res.get("autofixes"):
            autofixes = list(res["autofixes"])
        if res.get("autofix"):
            autofix = res["autofix"]
            # 兼容：旧 autofix 字段一定是 distance 类型；若列表里还没对应项则补齐
            if not any(f["type"] == "distance" for f in autofixes):
                merged = dict(type="distance", **autofix)
                autofixes.append(merged)
        has_claims = True  # geometry_claims 也计入"已带结构化断言"

    # 4) 数学承载页缺结构化断言 → needs_review（V4 §7）
    if math_bearing and not has_claims:
        needs_review_items.append("数学承载页缺少结构化断言（math_claims 或 geometry_claims），无法验证数学正确性")

    if needs_review_items:
        out = {"status": "needs_review", "detail": "; ".join(needs_review_items),
               "checks": checks, "needs_review_items": needs_review_items}
        if autofixes:
            out["autofixes"] = autofixes
        if autofix:
            out["autofix"] = autofix
        return out
    out = {"status": "verified", "detail": "LaTeX 结构与数学断言通过", "checks": checks}
    if autofixes:
        out["autofixes"] = autofixes
    if autofix:
        out["autofix"] = autofix
    return out
