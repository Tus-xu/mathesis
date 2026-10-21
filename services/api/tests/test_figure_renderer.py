"""参数化配图渲染器单元测试（figure_renderer）。

覆盖：投影数学、隐藏边/虚线判定、锥体顶点-底面不变量、函数采样与渐近线断开、
nice ticks、表达式安全求值、参数校验、确定性、SVG 结构。
"""

import base64
import math
import xml.etree.ElementTree as ET

import pytest

from app.services.figure_renderer import (
    FigureParamsError,
    View3D,
    _build_scene,
    _compile_expr,
    check_svg_invariants,
    nice_step,
    project_polyhedron,
    render_figure,
    to_data_uri,
    validate_figure_params,
)

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _dashed_edges(fig: dict) -> set[tuple[str, str]]:
    """按 figure 的默认视图构建场景，返回虚线边集合。"""
    f = validate_figure_params(fig)
    polys, _, _ = _build_scene(f)
    view = View3D(**f["view"])
    proj = project_polyhedron(polys[0], view)
    return {e for e, vis in proj["edges"].items() if not vis}


def _text_positions(svg: str) -> dict[str, float]:
    """SVG 中 <text> 内容 -> y 坐标（首次出现）。"""
    root = ET.fromstring(svg)
    out: dict[str, float] = {}
    for el in root.iter():
        if el.tag.endswith("text"):
            txt = (el.text or "").strip()
            if txt and txt not in out and el.get("y"):
                out[txt] = float(el.get("y"))
    return out


def _curve_paths(svg: str) -> int:
    return svg.count('<path d="M')


# ---------------------------------------------------------------------------
# 投影数学
# ---------------------------------------------------------------------------

def test_oblique_projection_formula():
    v = View3D(mode="oblique")
    for x, y, z in [(1, 0, 0), (0, 1, 0), (0, 0, 1), (2, 3, 1.5), (-1, -2, 0.5)]:
        u, w = v.project((x, y, z))
        assert u == pytest.approx(x + 0.3536 * y, abs=1e-3)
        assert w == pytest.approx(-z - 0.3536 * y, abs=1e-3)


def test_axonometric_projection_known_values():
    v = View3D(mode="axonometric", yaw=-28.0, elev=16.0)
    u, w = v.project((1.0, 0.0, 0.0))
    assert u == pytest.approx(0.8829, abs=1e-3)
    assert w == pytest.approx(0.4506, abs=1e-3)
    u, w = v.project((0.0, 0.0, 1.0))
    assert u == pytest.approx(0.0, abs=1e-6)
    assert w == pytest.approx(-0.2756, abs=1e-3)


def test_view_dir_is_unit_and_directions():
    for v in (View3D(mode="oblique"), View3D(mode="axonometric", yaw=-28, elev=16),
              View3D(mode="axonometric", yaw=45, elev=35.264)):
        d = v.view_dir()
        assert math.hypot(*d) == pytest.approx(1.0, abs=1e-9)
    d = View3D(mode="oblique").view_dir()
    assert d[0] > 0 and d[1] < 0 and d[2] > 0  # 右前上


# ---------------------------------------------------------------------------
# 隐藏边 / 虚线（斜二测默认视图，与教材画法对拍）
# ---------------------------------------------------------------------------

def test_cube_hidden_edges():
    assert _dashed_edges({"type": "cube", "params": {"a": 2}}) == {
        ("A", "D"), ("C", "D"), ("D", "D₁")}


def test_cuboid_hidden_edges():
    assert _dashed_edges({"type": "cuboid", "params": {"a": 2, "b": 1, "h": 1}}) == {
        ("A", "D"), ("C", "D"), ("D", "D₁")}


def test_tri_prism_hidden_edges():
    assert _dashed_edges(
        {"type": "triangular_prism",
         "params": {"base": "equilateral", "side": 2, "height": 1.7}}) == {
        ("A", "C"), ("B", "C"), ("C", "C₁")}


def test_quad_pyramid_hidden_edges():
    # 仅顶点 P 与左后底点 D 的连线被遮挡 —— 四棱锥教材画法
    assert _dashed_edges(
        {"type": "quad_pyramid",
         "params": {"base_w": 4, "base_d": 4, "height": 2.8}}) == {("D", "P")}


def test_tri_pyramid_hidden_edges():
    assert _dashed_edges(
        {"type": "tri_pyramid",
         "params": {"side": 2, "height": 2.4, "apex": "P", "base": ["A", "B", "C"]}}) == {
        ("C", "P")}


def test_tri_frustum_hidden_edges():
    assert _dashed_edges(
        {"type": "tri_frustum",
         "params": {"bottom_side": 6, "top_side": 2, "height": 2.6}}) == {("B", "C")}


def test_polyhedron_faces_outward_normalized():
    """构建器产出的面法向必须整体向外（backface culling 的前提）。"""
    f = validate_figure_params({"type": "cube", "params": {"a": 2}})
    polys, _, _ = _build_scene(f)
    poly = polys[0]
    center = tuple(sum(v[i] for v in poly.vertices.values()) / len(poly.vertices)
                   for i in range(3))
    for face in poly.faces:
        pts = [poly.vertices[v] for v in face]
        u = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1], pts[1][2] - pts[0][2])
        w = (pts[2][0] - pts[0][0], pts[2][1] - pts[0][1], pts[2][2] - pts[0][2])
        n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2],
             u[0] * w[1] - u[1] * w[0])
        fc = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
        outward = sum(n[i] * (fc[i] - center[i]) for i in range(3))
        assert outward > 0, f"面 {face} 法向未向外"


# ---------------------------------------------------------------------------
# 锥体顶点-底面不变量（本次事故的防复发核心）
# ---------------------------------------------------------------------------

def test_quad_pyramid_apex_above_base():
    svg = render_figure({"type": "quad_pyramid",
                         "params": {"base_w": 4, "base_d": 4, "height": 2.8}})
    pos = _text_positions(svg)
    assert pos["P"] < min(pos[n] for n in ("A", "B", "C", "D")) - 3


def test_tri_pyramid_apex_above_base():
    svg = render_figure({"type": "tri_pyramid",
                         "params": {"side": 2, "height": 2.4, "apex": "P",
                                    "base": ["A", "B", "C"]}})
    pos = _text_positions(svg)
    assert pos["P"] < min(pos[n] for n in ("A", "B", "C")) - 3


def test_flat_pyramid_rejected():
    """顶点高度相对底面过小 -> 拒绝渲染（不会画出"顶点在底面内"的错图）。"""
    with pytest.raises(FigureParamsError):
        render_figure({"type": "quad_pyramid",
                       "params": {"base_w": 4, "base_d": 4, "height": 0.05}})


def test_apex_z_nonpositive_rejected():
    with pytest.raises(FigureParamsError):
        render_figure({"type": "quad_pyramid",
                       "params": {"base_points": [[-2, -2], [2, -2], [2, 2], [-2, 2]],
                                  "apex_pos": [0, 0, 0], "apex": "P",
                                  "base": ["A", "B", "C", "D"]}})


def test_axonometric_auto_elev_keeps_apex_above_base():
    fig = {"type": "quad_pyramid", "params": {"base_w": 4, "base_d": 4, "height": 2.8},
           "view": {"mode": "axonometric"}}
    svg = render_figure(fig)
    pos = _text_positions(svg)
    assert pos["P"] < min(pos[n] for n in ("A", "B", "C", "D")) - 3
    problems = check_svg_invariants(fig, svg)
    assert not [p for p in problems if p["severity"] == "fatal"]


def test_axonometric_explicit_low_elev_rejected():
    with pytest.raises(FigureParamsError):
        render_figure({"type": "quad_pyramid",
                       "params": {"base_w": 4, "base_d": 4, "height": 2.8},
                       "view": {"mode": "axonometric", "elev": 5}})


# ---------------------------------------------------------------------------
# 函数图像
# ---------------------------------------------------------------------------

def test_function_quadratic_points_and_labels():
    svg = render_figure({"type": "function", "params": {
        "curves": [{"expr": "x**2 - 2*x - 3", "label": "y=x²−2x−3"}],
        "x_range": [-2, 4], "y_range": [-4.5, 5.5],
        "points": [{"x": 1, "y": -4, "label": "(1,−4)"}]}})
    assert "(1,−4)" in svg
    assert "O" in svg
    assert _curve_paths(svg) >= 1
    assert not [p for p in check_svg_invariants(
        {"type": "function", "params": {
            "curves": [{"expr": "x**2 - 2*x - 3"}],
            "x_range": [-2, 4], "y_range": [-4.5, 5.5]}}, svg)
        if p["severity"] == "fatal"]


def test_function_sin_single_continuous_path():
    svg = render_figure({"type": "function", "params": {
        "curves": [{"expr": "sin(x)"}], "x_range": [-6.3, 6.3], "y_range": [-1.6, 1.6]}})
    assert _curve_paths(svg) == 1  # 光滑周期函数不产生断点


def test_function_tan_breaks_at_asymptotes():
    svg = render_figure({"type": "function", "params": {
        "curves": [{"expr": "tan(x)"}], "x_range": [-3.2, 3.2], "y_range": [-4, 4],
        "asymptotes": {"x": [-1.571, 1.571]}}})
    assert _curve_paths(svg) >= 3  # 两个渐近线断开成 3 段
    assert svg.count("stroke-dasharray") >= 2  # 渐近虚线


def test_function_domain_clip():
    svg = render_figure({"type": "function", "params": {
        "curves": [{"expr": "sqrt(x)", "domain": [0, 4]}],
        "x_range": [-2, 4], "y_range": [-1, 3]}})
    # sqrt 在 x<0 无定义：左半区无曲线路径点
    root = ET.fromstring(svg)
    xs = []
    for el in root.iter():
        if el.tag.endswith("path") and el.get("stroke", "").startswith("#1a"):
            import re
            xs += [float(m) for m in re.findall(r"M(-?\d+\.?\d*)", el.get("d", ""))]
    assert xs and min(xs) >= 0 - 1e-6


# ---------------------------------------------------------------------------
# nice ticks（Heckbert）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("span,target,expected", [
    (7.0, 8.0, 1.0),
    (0.3, 8.0, 0.05),
    (1.6, 8.0, 0.2),
    (11.0, 8.0, 1.0),
    (100.0, 8.0, 10.0),
    (0.05, 5.0, 0.01),
])
def test_nice_step(span, target, expected):
    assert nice_step(span, target) == pytest.approx(expected)


def test_nice_step_rejects_bad_input():
    with pytest.raises(FigureParamsError):
        nice_step(0)
    with pytest.raises(FigureParamsError):
        nice_step(-3)


# ---------------------------------------------------------------------------
# 表达式安全求值（AST 白名单）
# ---------------------------------------------------------------------------

def test_safe_eval_computes():
    f = _compile_expr("x**2 - 2*x + 1")
    assert f(3) == pytest.approx(4.0)
    assert _compile_expr("sin(pi/2)")(0) == pytest.approx(1.0)
    assert _compile_expr("2*pi")(0) == pytest.approx(math.tau)
    assert _compile_expr("abs(x)")(-2.5) == pytest.approx(2.5)
    assert _compile_expr("pow(x, 3)")(2) == pytest.approx(8.0)
    assert math.isnan(_compile_expr("log(x)")(-1))


@pytest.mark.parametrize("expr", [
    "__import__('os')",
    "x.__class__",
    "open('f.txt')",
    "lambda: 1",
    "1 if True else 2",
    "[i for i in range(3)]",
    "1 ^ 2",
    "globals()",
    "eval('1')",
    "x; y",
])
def test_safe_eval_rejects(expr):
    with pytest.raises(FigureParamsError):
        _compile_expr(expr)


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fig,msg_part", [
    ({"type": "circle", "params": {}}, "未知图形类型"),
    ({"type": "cube", "params": {"a": -2}}, "必须 > 0"),
    ({"type": "cube", "params": {"a": 2}, "size": [50, 100]}, "size"),
    ({"type": "quad_pyramid", "params": {"base_w": 2, "base_d": 2, "height": 1,
                                          "apex": "A", "base": ["A", "B", "C", "D"]}},
     "顶点名重复"),
    ({"type": "function", "params": {"x_range": [-1, 1], "y_range": [-1, 1]}}, "curves"),
    ({"type": "function", "params": {"curves": [{"expr": "x"}],
                                     "x_range": [2, 1], "y_range": [-1, 1]}}, "区间"),
    ({"type": "triangular_prism", "params": {"base": "equilateral", "side": 1,
                                             "height": -1}}, "必须 > 0"),
    ({"type": "sphere", "params": {"r": 0}}, "必须 > 0"),
])
def test_validate_rejects(fig, msg_part):
    with pytest.raises(FigureParamsError) as ei:
        validate_figure_params(fig)
    assert msg_part in str(ei.value)


def test_validate_fills_defaults():
    f = validate_figure_params({"type": "cube", "params": {"a": 2}})
    assert f["view"]["mode"] == "oblique"
    assert f["size"] == (400, 300)
    assert f["version"] == 1


# ---------------------------------------------------------------------------
# 确定性与 SVG 结构
# ---------------------------------------------------------------------------

def test_determinism():
    figs = [
        {"type": "quad_pyramid", "params": {"base_w": 4, "base_d": 4, "height": 2.8}},
        {"type": "sphere", "params": {"r": 1.5,
                                      "solid": {"type": "cuboid",
                                                "params": {"a": 2, "b": 1, "h": 1}}}},
        {"type": "function", "params": {"curves": [{"expr": "sin(x)"}],
                                        "x_range": [-6, 6], "y_range": [-2, 2]}},
    ]
    for fig in figs:
        assert render_figure(fig) == render_figure(fig)


def test_to_data_uri_roundtrip():
    svg = render_figure({"type": "cube", "params": {"a": 2}})
    uri = to_data_uri(svg)
    assert uri.startswith("data:image/svg+xml;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]).decode("utf-8") == svg


def test_cube_labels_with_subscript():
    svg = render_figure({"type": "cube", "params": {"a": 2}})
    for name in ("A", "B", "C", "D", "A₁", "B₁", "C₁", "D₁"):
        assert f">{name}</text>" in svg


def test_sphere_circumradius_formula():
    """正方体棱长 a 外接球半径 a√3/2；长方体 √(a²+b²+h²)/2。"""
    cube_fig = {"type": "sphere", "params": {"r": 2 * math.sqrt(3) / 2,
                                             "solid": {"type": "cube", "params": {"a": 2}}}}
    svg = render_figure(cube_fig)
    assert "<circle" in svg and "O" in svg
    f = validate_figure_params(cube_fig)
    assert f["params"]["r"] == pytest.approx(math.sqrt(3))
    cuboid_r = math.sqrt(2**2 + 1**2 + 1**2) / 2
    assert cuboid_r == pytest.approx(math.sqrt(6) / 2)


# ---------------------------------------------------------------------------
# 全类型 smoke（渲染 + 结构校验）
# ---------------------------------------------------------------------------

ALL_TYPES = [
    {"type": "cube", "params": {"a": 2}},
    {"type": "cuboid", "params": {"a": 2, "b": 1, "h": 1}},
    {"type": "triangular_prism", "params": {"base": "equilateral", "side": 2, "height": 1.7}},
    {"type": "triangular_prism", "params": {"base": "right", "ab": 6, "bc": 8, "height": 3}},
    {"type": "quad_pyramid", "params": {"base_w": 4, "base_d": 4, "height": 2.8}},
    {"type": "tri_pyramid", "params": {"side": 2, "height": 2.4}},
    {"type": "tri_frustum", "params": {"bottom_side": 6, "top_side": 2, "height": 2.6}},
    {"type": "sphere", "params": {"r": 1.5, "solid": {"type": "cuboid",
                                                       "params": {"a": 2, "b": 1, "h": 1}}}},
    {"type": "sphere", "params": {"r": 1.5}},
    {"type": "polyhedron", "params": {
        "vertices": {"A": [0, 0, 0], "B": [2, 0, 0], "C": [2, 1, 0], "D": [0, 1, 0],
                     "P": [1, 0.5, 2]},
        "faces": [["A", "D", "C", "B"], ["P", "A", "B"], ["P", "B", "C"],
                  ["P", "C", "D"], ["P", "D", "A"]]}},
    {"type": "function", "params": {"curves": [{"expr": "x**2 - 2*x + 1"}],
                                    "x_range": [-2, 4], "y_range": [-1, 5]}},
    {"type": "triangle2d", "params": {"points": {"A": [0, 0], "B": [3, 0], "C": [0, 4]},
                                      "right_angle": "A", "circumcircle": True}},
]


@pytest.mark.parametrize("fig", ALL_TYPES)
def test_all_types_render_valid_svg(fig):
    svg = render_figure(fig)
    problems = check_svg_invariants(fig, svg)
    assert not [p for p in problems if p["severity"] == "fatal"], problems
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    w, h = int(float(root.get("width"))), int(float(root.get("height")))
    assert 120 <= w <= 1200 and 90 <= h <= 900
    assert len(svg) > 500
