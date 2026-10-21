"""visual_spec：结构化图形规格生成（生成与渲染分离）。

职责：
1. `sample_2d` —— 把 f(x) 表达式采样为二维折线点集（渲染数据，前端不执行表达式）。
2. `build_2d_plot` —— 构建二维函数图的视觉规格（含标记点/临界点/单调区间/purpose）。
3. `is_3d_topic` —— 判断主题是否需要立体几何（只有立体几何才允许 3D solids）。

病根修复：函数/数列/概率/平面几何主题一律走二维 `2d_plot`；
`stage_router._ensure_solids` 不得再对这类页面注入 3D 默认识体。
"""

from __future__ import annotations

import math
from typing import Any

import sympy

from app.domains.classroom.math_verifier import _X, to_symbolic

# 渲染纵向裁剪阈值（绝对值超出丢弃，防图爆）
_MAX_Y = 50.0

# 立体几何主题关键词（仅命中才允许 3D solids）
_3D_KEYWORDS = (
    "多面体", "棱柱", "棱锥", "棱台", "正方体", "长方体", "三棱柱", "四棱柱",
    "三棱锥", "四棱锥", "六面体", "立体几何", "空间几何", "空间向量", "旋转体",
    "圆柱", "圆锥", "圆台", "球体", "球", "二面角", "线面角", "异面直线",
    "线面垂直", "线面平行", "面面垂直", "面面平行",
)

# 二维主题例外：包含 3D 关键词但实际是二维内容的词组
_2D_EXCEPTIONS = (
    "圆锥曲线",  # 椭圆/抛物线/双曲线是平面曲线
    "平面解析几何",
    "三角函数图像",
)


def is_3d_topic(title: str) -> bool:
    """判断主题/页标题是否属于立体几何（只有立体几何才可用 3D solids）。

    规则：
    1. 先检查是否命中二维例外词组（如「圆锥曲线」）→ 判为 2D；
    2. 再检查是否命中立体几何关键词 → 判为 3D；
    3. 否则默认 2D。
    """
    t = title or ""
    # 二维例外优先（避免「圆锥曲线」被误判为 3D）
    for exc in _2D_EXCEPTIONS:
        if exc in t:
            return False
    return any(k in t for k in _3D_KEYWORDS)


def sample_2d(
    expr: str,
    x0: float = -4.0,
    x1: float = 4.0,
    samples: int = 160,
    y_max: float = _MAX_Y,
) -> list[list[float]]:
    """把 f(x) 采样为二维点集 [[x, y], ...]；越界/非有限值裁剪。

    返回空列表表示表达式不可解析或无法采样（调用方应回退为仅板书）。
    """
    sym = to_symbolic(expr)
    if sym is None:
        return []
    try:
        x0, x1 = float(x0), float(x1)
    except (TypeError, ValueError):
        x0, x1 = -4.0, 4.0
    if x1 <= x0:
        x0, x1 = -4.0, 4.0
    if abs(x0) > 100 or abs(x1) > 100:
        x0, x1 = -4.0, 4.0
    n = min(max(int(samples), 24), 400)
    pts: list[list[float]] = []
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        try:
            v = float(sympy.N(sym.subs(_X, x)))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v) or abs(v) > y_max:
            continue
        pts.append([round(x, 4), round(v, 4)])
    return pts


def _finite_point(p: Any) -> list[float] | None:
    if not isinstance(p, (list, tuple)) or len(p) < 2:
        return None
    try:
        x, y = float(p[0]), float(p[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)) or abs(x) > 100 or abs(y) > _MAX_Y:
        return None
    return [round(x, 4), round(y, 4)]


def solid_from_coordinates(coordinates: dict) -> dict | None:
    """用选题给出的结构化坐标（geometry_claims.coordinates）确定性构建 3D 多面体图。

    V4 §5：图形只能由当前题目的结构化数据驱动。当 LLM 给出了关键点坐标
    但遗漏了 geometry 图形块时，依据坐标构建「点/边/面/虚线」完整的多面体：
    - 顶点 = 全部命名坐标点（标签与题意命名一致）；
    - 棱 = 底面多边形边 + 顶点(最高点)到底面各点 + 底面虚线对角线。

    返回 MathFigure3D figure 契约；坐标不足（<4 点）返回 None。
    """
    if not isinstance(coordinates, dict) or len(coordinates) < 4:
        return None
    pts: dict[str, list[float]] = {}
    for name, p in coordinates.items():
        v = _finite_point3(p)
        if v is None:
            continue
        pts[str(name)] = v
    if len(pts) < 4:
        return None
    # 顶点判定：找"该坐标轴上唯一取最大值的点"（z-up 与 y-up 建系都适用），
    # 无法唯一时取扩散最大的轴。
    axis_candidates: list[tuple[int, int, float, str | None]] = []  # (axis, spread, unique_max_name)
    for axis in range(3):
        vals = [(p[axis], n) for n, p in pts.items()]
        vals.sort(reverse=True)
        spread = vals[0][0] - vals[-1][0]
        if spread < 1e-9:
            continue
        unique_max = vals[1][0] < vals[0][0] - 1e-9
        axis_candidates.append((axis, round(spread, 6), unique_max, vals[0][1]))
    if not axis_candidates:
        return None
    axis_candidates.sort(key=lambda t: (t[2], t[1]), reverse=True)
    apex_name = axis_candidates[0][3]
    apex_axis = axis_candidates[0][0]
    min_h = min(p[apex_axis] for n, p in pts.items() if n != apex_name)
    # 底面角点 = 底面平面（高度最低）上的点；其余点（如中点 E）作为带标签顶点保留
    corners = sorted(
        [n for n, p in pts.items() if n != apex_name and abs(p[apex_axis] - min_h) < 1e-9]
    )
    if len(corners) < 3:
        return None
    vertices = [{"name": n, "pos": pts[n]} for n in pts]
    u, v = [i for i in range(3) if i != apex_axis]
    cu = sum(pts[n][u] for n in corners) / len(corners)
    cv = sum(pts[n][v] for n in corners) / len(corners)
    corners.sort(key=lambda n: math.atan2(pts[n][v] - cv, pts[n][u] - cu))
    edges: list[list[str]] = []
    dashed: list[list[str]] = []
    for i in range(len(corners)):
        a, b = corners[i], corners[(i + 1) % len(corners)]
        edges.append([a, b])
    for n in corners:
        edges.append([apex_name, n])
    # 底面角点对角线（虚线辅助）
    for i in range(len(corners)):
        for j in range(i + 1, len(corners)):
            a, b = corners[i], corners[j]
            if (i + 1) % len(corners) != j and (j + 1) % len(corners) != i:
                dashed.append([a, b])
    figure: dict[str, Any] = {
        "axes": True,
        "grid": False,
        "solids": [
            {
                "kind": "polyhedron",
                "vertices": vertices,
                "edges": edges,
                "opacity": 0.35,
                "color": "#4f8ef7",
            }
        ],
    }
    if dashed:
        figure["segments"] = [
            {"a": pts[a], "b": pts[b], "dashed": True, "label": ""} for a, b in dashed
        ]
    return figure


def _finite_point3(p: Any) -> list[float] | None:
    """三维坐标有限性校验。"""
    if not (isinstance(p, (list, tuple)) and len(p) == 3):
        return None
    try:
        out = [float(v) for v in p]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in out) or any(abs(v) > 50 for v in out):
        return None
    return out


def build_2d_plot(
    *,
    expr: str,
    label: str = "",
    x_domain: tuple[float, float] = (-4.0, 4.0),
    critical_points: list[float] | None = None,
    markers: list[dict[str, Any]] | None = None,
    increasing_intervals: list[tuple[float, float]] | None = None,
    decreasing_intervals: list[tuple[float, float]] | None = None,
    purpose: str = "验证",
    expected_observation: str = "",
    linked_step_id: str = "",
) -> dict[str, Any]:
    """构建二维函数图 visual_spec（前端 FunctionPlot2D 消费）。

    所有曲线点由真实采样生成；标记点/单调区间与文字结论同源，杜绝图文矛盾。
    """
    points = sample_2d(expr, x_domain[0], x_domain[1])
    if not points:
        # 采样失败：返回空图规格（前端回退为仅板书）
        return {"kind": "2d_plot", "expr": expr, "label": label or expr, "points": []}

    norm_markers: list[dict[str, Any]] = []
    for m in markers or []:
        if not isinstance(m, dict):
            continue
        p = _finite_point(m.get("pos") or [m.get("x"), m.get("y")])
        if p:
            norm_markers.append(
                {"pos": p, "label": str(m.get("label") or "")[:20]}
            )

    def _norm_intervals(ivs):
        out = []
        for iv in ivs or []:
            if not (isinstance(iv, (list, tuple)) and len(iv) == 2):
                continue
            try:
                lo, hi = float(iv[0]), float(iv[1])
            except (TypeError, ValueError):
                continue
            if hi > lo:
                out.append([round(lo, 4), round(hi, 4)])
        return out

    return {
        "kind": "2d_plot",
        "expr": expr,
        "label": label or expr,
        "x_domain": [round(x_domain[0], 4), round(x_domain[1], 4)],
        "points": points,
        "critical_points": [round(float(c), 4) for c in (critical_points or []) if isinstance(c, (int, float))],
        "markers": norm_markers,
        "increasing_intervals": _norm_intervals(increasing_intervals),
        "decreasing_intervals": _norm_intervals(decreasing_intervals),
        "purpose": purpose or "验证",
        "expected_observation": expected_observation or "",
        "linked_step_id": linked_step_id or "",
    }

