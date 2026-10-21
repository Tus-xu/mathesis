"""参数化配图渲染器（v1）—— 代码精确计算，替代大模型自由生成 SVG。

设计原则：
- 确定性：同一组 figure_params 永远渲染出同一份 SVG（无随机、无时间、无网络）。
- 纯标准库：math / ast / html / xml / base64 / dataclasses，零第三方依赖。
- 立体几何：Rz(yaw)·Rx(-elev) 轴测正交投影（可选透视）+ 凸多面体 backface culling
  隐藏边虚线 + painter 排序半透明面填充。参考 JSXGraph View3D / prideout wireframes。
- 函数图像：AST 白名单安全求值 + 递归中点自适应采样（参考 function-plot sampler）
  + 渐近线断开 + Heckbert nice ticks（参考 d3-scale tickIncrement）。

公开 API：
    validate_figure_params(fig: dict) -> dict    # 校验+规范化参数（默认值补齐）
    render_figure(fig: dict) -> str              # 参数 -> SVG 字符串
    to_data_uri(svg: str) -> str                 # SVG -> base64 data URI
    check_svg_invariants(fig, svg) -> list[dict] # 结构/几何不变量检查
    derive_figure_frames(fig: dict) -> list[dict]# F13 完整图 -> 渐进揭示帧序列
    render_figure_frames(fig, ...) -> dict       # F13 帧渲染 -> figure 事件载荷
    FIGURE_SCHEMA_DOC                            # 供 LLM 提取参数的 schema 说明
"""

from __future__ import annotations

import ast
import base64
import html
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_SIZE = (400, 300)
FUNCTION_DEFAULT_SIZE = (460, 330)
DEFAULT_VIEW = {"mode": "oblique", "yaw": -28.0, "elev": 16.0, "perspective": None}
# axonometric 模式下锥体的默认仰角（高于棱柱，保证顶点露出底面轮廓）
_TYPE_ELEVS = {"quad_pyramid": 34.0, "tri_pyramid": 28.0}

_CURVE_COLORS = ("#1a5fb4", "#c01c28", "#1c7a43", "#8a5a1e")
_FACE_FILL = "#e8eef7"
_LINE_SOLID = "#222222"
_LINE_HIDDEN = "#5b6572"
_LINE_AUX = "#8a93a0"
_LABEL_FONT = "Georgia, 'Times New Roman', serif"

_SUPPORTED_TYPES = (
    "cube", "cuboid", "triangular_prism", "quad_pyramid", "tri_pyramid",
    "tri_frustum", "sphere", "polyhedron", "function", "triangle2d", "conic",
)
_SOLID_TYPES = (
    "cube", "cuboid", "triangular_prism", "quad_pyramid", "tri_pyramid",
    "tri_frustum", "sphere", "polyhedron",
)


class FigureRendererError(ValueError):
    """渲染器错误基类。"""


class FigureParamsError(FigureRendererError):
    """参数不合法（消息可反馈给 LLM 重试）。"""


class _ApexBelowError(FigureParamsError):
    """内部信号：锥体顶点投影未在底面上方（触发自动抬升仰角）。"""


def _sub_digits(name: str) -> str:
    """'A1' -> 'A₁'（仅数字部分转下标字符）。"""
    table = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    i = len(name)
    while i > 0 and name[i - 1].isdigit():
        i -= 1
    return name[:i] + name[i:].translate(table)


def _default_label(name: str) -> str:
    """顶点名缺省显示名：字母+数字 -> 字母+下标。"""
    if name.rstrip("0123456789").isalpha():
        return _sub_digits(name)
    return name


def _top_name(name: str) -> str:
    """下底顶点名 -> 上底顶点名（'A' -> 'A₁'，'A1' 保持原样显示时自动转下标）。"""
    return name if name[-1:].isdigit() else name + "₁"


# ---------------------------------------------------------------------------
# 3D 投影
# ---------------------------------------------------------------------------

Vec3 = tuple[float, float, float]
Point2 = tuple[float, float]


@dataclass
class View3D:
    """视图。

    - mode="oblique"（默认，教材斜二测画法）：屏幕 = (x + 0.3536y, −z − 0.3536y)，
      深度轴 45° 半比例。顶点恒在底面上方、前棱在下，符合人教版教材画法。
    - mode="axonometric"：Rz(yaw)·Rx(-elev) 轴测正交投影（可选透视），
      几何严格但扁锥体从低角度观察时顶点会真实地落入底面轮廓内（已由自动抬升规避）。
    """

    mode: str = "oblique"
    yaw: float = -28.0
    elev: float = 16.0
    perspective: float | None = None  # 焦距；仅 axonometric 模式生效

    def __post_init__(self) -> None:
        if self.mode not in ("oblique", "axonometric"):
            raise FigureParamsError(f"view.mode 取值非法: {self.mode!r}")
        self._c1 = math.cos(math.radians(self.yaw))
        self._s1 = math.sin(math.radians(self.yaw))
        self._c2 = math.cos(math.radians(self.elev))
        self._s2 = math.sin(math.radians(self.elev))

    def rotate(self, p: Vec3) -> Vec3:
        """世界坐标 -> 视图坐标 (X, Y, Z)，Z 为深度（越大越近），Y 向上。"""
        x, y, z = p
        if self.mode == "oblique":
            return (x + 0.3536 * y, z + 0.3536 * y, -0.3536 * y + z)
        x1 = x * self._c1 - y * self._s1
        y1 = x * self._s1 + y * self._c1
        return (x1, y1 * self._c2 + z * self._s2, -y1 * self._s2 + z * self._c2)

    def project(self, p: Vec3) -> Point2:
        """世界坐标 -> 屏幕坐标（y 向下，scale=1、原点居中，之后统一 fit）。"""
        x, y, z = self.rotate(p)
        if self.perspective and self.mode == "axonometric":
            f = self.perspective
            d = f - z  # 越近 z 越大 -> d 越小 -> 放大
            if d <= 1e-9:
                raise FigureParamsError("透视参数导致点在相机后方，请减小 perspective 值")
            x, y = x * f / d, y * f / d
        return (x, -y)

    def view_dir(self) -> Vec3:
        """场景 -> 观察者的单位方向（世界系），backface culling 用。"""
        if self.mode == "oblique":
            # 斜二测隐含的观察方位：右前上方（法向选择使前面/右面/顶面可见、背面隐藏）
            v = (0.45, -0.45, 0.5)
            n = math.sqrt(sum(c * c for c in v))
            return (v[0] / n, v[1] / n, v[2] / n)
        # M = Rx(-elev)·Rz(yaw)，d = Mᵀ·(0,0,1) = (-s2·s1, -s2·c1, c2)
        return (-self._s2 * self._s1, -self._s2 * self._c1, self._c2)


# ---------------------------------------------------------------------------
# 多面体数据结构
# ---------------------------------------------------------------------------

@dataclass
class Polyhedron:
    """凸多面体：顶点表 + 面表（自动归一化为外法向）+ 附加边。

    always_visible_faces：面索引列表，豁免 backface culling（教材画法中
    锥体的底面轮廓始终可见，即使其外法向背对观察者）。
    """

    vertices: dict[str, Vec3]
    faces: list[list[str]]
    extra_edges: list[tuple[str, str]] = field(default_factory=list)
    always_visible_faces: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.vertices:
            raise FigureParamsError("多面体无顶点")
        for name, p in self.vertices.items():
            if len(p) != 3 or not all(math.isfinite(float(c)) for c in p):
                raise FigureParamsError(f"顶点 {name} 坐标非法: {p!r}")
        center = tuple(
            sum(float(v[i]) for v in self.vertices.values()) / len(self.vertices)
            for i in range(3)
        )
        for i, face in enumerate(self.faces):
            if len(face) < 3:
                raise FigureParamsError(f"面 {face} 顶点数不足 3")
            missing = [v for v in face if v not in self.vertices]
            if missing:
                raise FigureParamsError(f"面 {face} 引用了未定义的顶点 {missing}")
            pts = [self.vertices[v] for v in face]
            n = _cross(_sub3(pts[1], pts[0]), _sub3(pts[2], pts[0]))
            if _norm(n) < 1e-12:
                raise FigureParamsError(f"面 {face} 退化（共线/共点）")
            # 外法向：面中心相对多面体中心的方向应与法向同侧，否则翻转顶点序
            fcenter = tuple(sum(float(p[j]) for p in pts) / len(pts) for j in range(3))
            if _dot(n, _sub3(fcenter, center)) < 0:
                self.faces[i] = list(reversed(face))
        for idx in self.always_visible_faces:
            if not 0 <= idx < len(self.faces):
                raise FigureParamsError(f"always_visible_faces 面索引越界: {idx}")
        for a, b in self.extra_edges:
            for v in (a, b):
                if v not in self.vertices:
                    raise FigureParamsError(f"附加边 {a}-{b} 引用了未定义的顶点 {v}")


@dataclass
class _Sphere:
    """球（外接球/独立球）：屏幕上为圆 + 可选经线/纬线椭圆。"""

    r: float
    center: Vec3 = (0.0, 0.0, 0.0)
    center_label: str | None = "O"
    equator: bool = True
    meridian: bool = False


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _is_convex_2d(pts: list[Point2]) -> bool:
    """屏幕多边形（按给定顺序）是否严格凸（转向一致且无共线/自交）。"""
    if len(pts) < 3:
        return False
    signs = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cr) < 1e-9:
            return False
        signs.append(cr > 0)
    return all(signs) or not any(signs)


def project_polyhedron(poly: Polyhedron, view: View3D) -> dict:
    """投影多面体：屏幕坐标 + 面可见性 + 边可见性（backface culling）。"""
    pts = {name: view.project(p) for name, p in poly.vertices.items()}
    depth = {name: view.rotate(p)[2] for name, p in poly.vertices.items()}
    d = view.view_dir()

    face_visible: list[bool] = []
    face_screen: list[tuple[list[str], list[Point2]]] = []
    face_depth: list[float] = []
    for fi, face in enumerate(poly.faces):
        world = [poly.vertices[v] for v in face]
        n = _cross(_sub3(world[1], world[0]), _sub3(world[2], world[0]))
        vis = fi in poly.always_visible_faces or _dot(n, d) > 0
        face_visible.append(vis)
        face_screen.append((face, [pts[v] for v in face]))
        face_depth.append(sum(depth[v] for v in face) / len(face))

    # 边 -> 相邻面索引
    edge_faces: dict[frozenset[str], list[int]] = {}
    for fi, face in enumerate(poly.faces):
        m = len(face)
        for i in range(m):
            edge = frozenset((face[i], face[(i + 1) % m]))
            edge_faces.setdefault(edge, []).append(fi)
    for a, b in poly.extra_edges:
        edge_faces.setdefault(frozenset((a, b)), [])

    # 边可见性：至少邻接一个可见面 -> 实线；全部邻接面不可见 -> 虚线
    edges: dict[tuple[str, str], bool] = {}
    for edge, face_ids in edge_faces.items():
        a, b = sorted(edge)
        edges[(a, b)] = any(face_visible[fi] for fi in face_ids)

    # 可见面按深度从远到近（painter 排序）
    vis_faces = sorted(
        (face_screen[fi] for fi in range(len(poly.faces)) if face_visible[fi]),
        key=lambda item: face_depth[poly.faces.index(item[0])],
    )
    return {"pts": pts, "edges": edges, "vis_faces": vis_faces,
            "face_visible": face_visible}


# ---------------------------------------------------------------------------
# 参数化几何体构建（世界坐标，底面 z=0，中心在原点附近）
# ---------------------------------------------------------------------------

def _poly_cube(a: float) -> Polyhedron:
    """正方体 ABCD-A₁B₁C₁D₁，A 前左、AB 为前棱。"""
    h = a / 2
    v = {
        "A": (-h, -h, 0.0), "B": (h, -h, 0.0), "C": (h, h, 0.0), "D": (-h, h, 0.0),
        "A₁": (-h, -h, a), "B₁": (h, -h, a), "C₁": (h, h, a), "D₁": (-h, h, a),
    }
    f = [
        ["A", "D", "C", "B"], ["A₁", "B₁", "C₁", "D₁"],
        ["A", "B", "B₁", "A₁"], ["B", "C", "C₁", "B₁"],
        ["C", "D", "D₁", "C₁"], ["D", "A", "A₁", "D₁"],
    ]
    return Polyhedron(v, f)


def _poly_cuboid(a: float, b: float, h: float) -> Polyhedron:
    """长方体：长 a(x)、宽 b(y)、高 h(z)。"""
    x, y = a / 2, b / 2
    v = {
        "A": (-x, -y, 0.0), "B": (x, -y, 0.0), "C": (x, y, 0.0), "D": (-x, y, 0.0),
        "A₁": (-x, -y, h), "B₁": (x, -y, h), "C₁": (x, y, h), "D₁": (-x, y, h),
    }
    f = [
        ["A", "D", "C", "B"], ["A₁", "B₁", "C₁", "D₁"],
        ["A", "B", "B₁", "A₁"], ["B", "C", "C₁", "B₁"],
        ["C", "D", "D₁", "C₁"], ["D", "A", "A₁", "D₁"],
    ]
    return Polyhedron(v, f)


def _poly_tri_prism(base_xy: list[Point2], h: float, names: list[str]) -> Polyhedron:
    """三棱柱：底面任意三角形（z=0），顶面 z=h。"""
    if len(base_xy) != 3 or len(names) != 3:
        raise FigureParamsError("三棱柱底面需要 3 个点与 3 个顶点名")
    cr = _cross(
        (base_xy[1][0] - base_xy[0][0], base_xy[1][1] - base_xy[0][1], 0.0),
        (base_xy[2][0] - base_xy[0][0], base_xy[2][1] - base_xy[0][1], 0.0),
    )
    if abs(cr[2]) < 1e-9:
        raise FigureParamsError("三棱柱底面三点共线")
    v: dict[str, Vec3] = {}
    for n, (x, y) in zip(names, base_xy, strict=False):
        v[n] = (float(x), float(y), 0.0)
        v[_top_name(n)] = (float(x), float(y), float(h))
    a, b, c = names
    a1, b1, c1 = (_top_name(n) for n in names)
    f = [
        [a, c, b], [a1, b1, c1],
        [a, b, b1, a1], [b, c, c1, b1], [c, a, a1, c1],
    ]
    return Polyhedron(v, f)


def _poly_quad_pyramid(base_xy: list[Point2], apex: Vec3, apex_name: str,
                       base_names: list[str]) -> Polyhedron:
    """四棱锥：底面凸四边形（z=0）+ 顶点。"""
    if len(base_xy) != 4 or len(base_names) != 4:
        raise FigureParamsError("四棱锥底面需要 4 个点与 4 个顶点名")
    if not _is_convex_2d(base_xy):
        raise FigureParamsError("四棱锥底面不是凸四边形")
    if apex[2] <= 0:
        raise FigureParamsError("四棱锥顶点 z 必须大于 0（在底面上方）")
    v: dict[str, Vec3] = {apex_name: tuple(float(c) for c in apex)}
    for n, (x, y) in zip(base_names, base_xy, strict=False):
        v[n] = (float(x), float(y), 0.0)
    n0, n1, n2, n3 = base_names
    f = [
        [n0, n3, n2, n1],
        [apex_name, n0, n1], [apex_name, n1, n2],
        [apex_name, n2, n3], [apex_name, n3, n0],
    ]
    return Polyhedron(v, f, always_visible_faces=[0])  # 底面轮廓教材画法始终可见


def _poly_tri_pyramid(base_xy: list[Point2], apex: Vec3, apex_name: str,
                      base_names: list[str]) -> Polyhedron:
    """三棱锥：底面三角形（z=0）+ 顶点。"""
    if len(base_xy) != 3 or len(base_names) != 3:
        raise FigureParamsError("三棱锥底面需要 3 个点与 3 个顶点名")
    if apex[2] <= 0:
        raise FigureParamsError("三棱锥顶点 z 必须大于 0（在底面上方）")
    v: dict[str, Vec3] = {apex_name: tuple(float(c) for c in apex)}
    for n, (x, y) in zip(base_names, base_xy, strict=False):
        v[n] = (float(x), float(y), 0.0)
    n0, n1, n2 = base_names
    f = [
        [n0, n2, n1],
        [apex_name, n0, n1], [apex_name, n1, n2], [apex_name, n2, n0],
    ]
    return Polyhedron(v, f, always_visible_faces=[0])  # 底面轮廓教材画法始终可见


def _equilateral_pts(side: float) -> list[Point2]:
    """边长为 side 的等边三角形，中心在原点，顶点 A 在正前方（-y 侧）。"""
    r = side / math.sqrt(3.0)
    return [
        (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
        for a in (-90.0, 30.0, 150.0)
    ]


def _poly_tri_frustum(bottom_side: float, top_side: float, h: float) -> Polyhedron:
    """正三棱台 ABC-A₁B₁C₁：下底 bottom_side、上底 top_side、高 h，上下同心同向。"""
    rb = bottom_side / math.sqrt(3.0)
    rt = top_side / math.sqrt(3.0)
    angles = (-90.0, 30.0, 150.0)
    bottom = [(rb * math.cos(math.radians(a)), rb * math.sin(math.radians(a))) for a in angles]
    top = [(rt * math.cos(math.radians(a)), rt * math.sin(math.radians(a))) for a in angles]
    v: dict[str, Vec3] = {}
    for i, n in enumerate(("A", "B", "C")):
        v[n] = (*bottom[i], 0.0)
        v[_top_name(n)] = (*top[i], float(h))
    f = [
        ["A", "C", "B"], ["A₁", "B₁", "C₁"],
        ["A", "B", "B₁", "A₁"], ["B", "C", "C₁", "B₁"], ["C", "A", "A₁", "C₁"],
    ]
    return Polyhedron(v, f)


# ---------------------------------------------------------------------------
# SVG 输出工具
# ---------------------------------------------------------------------------

def _fmt(v: float) -> str:
    """数值 -> SVG 字符串（拒绝非有限值）。"""
    v = float(v)
    if not math.isfinite(v):
        raise FigureParamsError(f"渲染出现非有限数值: {v!r}")
    return f"{v:.3f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _svg_doc(size: tuple[int, int], body: str) -> str:
    w, h = size
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">\n{body}</svg>'
    )


def _line(x1, y1, x2, y2, stroke, width=1.6, dash=None, opacity=None) -> str:
    attrs = f'x1="{_fmt(x1)}" y1="{_fmt(y1)}" x2="{_fmt(x2)}" y2="{_fmt(y2)}"'
    attrs += f' stroke="{stroke}" stroke-width="{width}" fill="none"'
    if dash:
        attrs += f' stroke-dasharray="{dash}"'
    if opacity is not None:
        attrs += f' opacity="{opacity}"'
    return f"<line {attrs}/>"


def _polygon(pts: list[Point2], fill, opacity=None) -> str:
    p = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)
    attrs = f'points="{p}" fill="{fill}" stroke="none"'
    if opacity is not None:
        attrs += f' opacity="{opacity}"'
    return f"<polygon {attrs}/>"


def _circle(cx, cy, r, stroke, width=1.4, dash=None) -> str:
    attrs = f'cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" fill="none"'
    attrs += f' stroke="{stroke}" stroke-width="{width}"'
    if dash:
        attrs += f' stroke-dasharray="{dash}"'
    return f"<circle {attrs}/>"


def _dot_mark(cx, cy, r=1.9, fill=_LINE_SOLID) -> str:
    return f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" fill="{fill}"/>'


def _text(x, y, s, size=13, italic=True, anchor="middle", fill=_LINE_SOLID,
          weight=None) -> str:
    esc = html.escape(s, quote=True)
    style = f'font-family:{_LABEL_FONT};font-style:{"italic" if italic else "normal"}'
    if weight:
        style += f";font-weight:{weight}"
    return (
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" font-size="{size}" style="{style}" '
        f'text-anchor="{anchor}" fill="{fill}">{esc}</text>'
    )


def _path(pts: list[Point2], stroke, width=1.8, dash=None) -> str:
    if len(pts) < 2:
        return ""
    d = "M" + " L".join(f"{_fmt(x)} {_fmt(y)}" for x, y in pts)
    attrs = f'd="{d}" fill="none" stroke="{stroke}" stroke-width="{width}"'
    attrs += ' stroke-linejoin="round" stroke-linecap="round"'
    if dash:
        attrs += f' stroke-dasharray="{dash}"'
    return f"<path {attrs}/>"


def _fit_transform(pts: list[Point2], size: tuple[int, int], margin: int = 30):
    """线性 fit：世界屏幕坐标 -> 画布坐标（居中 + 缩放）。"""
    if not pts:
        raise FigureParamsError("渲染场景为空")
    w, h = size
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    span_x = max(maxx - minx, 1e-9)
    span_y = max(maxy - miny, 1e-9)
    k = min((w - 2 * margin) / span_x, (h - 2 * margin) / span_y)
    midx, midy = (minx + maxx) / 2, (miny + maxy) / 2

    def t(p: Point2) -> Point2:
        return (w / 2 + (p[0] - midx) * k, h / 2 + (p[1] - midy) * k)

    return t, k


def _render_scene(polys: list[Polyhedron], spheres: list[_Sphere], view: View3D,
                  size: tuple[int, int], labels: dict[str, str] | None,
                  style: dict, apex_checks: list[tuple[str, list[str]]]) -> str:
    """渲染整个 3D 场景：多面体 + 球（外接球组合图）。"""
    projs = [project_polyhedron(p, view) for p in polys]
    all_pts: list[Point2] = []
    for pr in projs:
        all_pts.extend(pr["pts"].values())
    for sp in spheres:
        c = view.project(sp.center)
        all_pts.append((c[0] - sp.r, c[1] - sp.r))
        all_pts.append((c[0] + sp.r, c[1] + sp.r))
    t, k = _fit_transform(all_pts, size)

    body: list[str] = [f'<rect x="0" y="0" width="{size[0]}" height="{size[1]}" fill="#ffffff"/>']
    show_fill = style.get("fill_faces", True)
    show_dots = style.get("show_vertices", True)
    show_labels = style.get("show_labels", True)

    for poly, pr in zip(polys, projs, strict=False):
        pts2 = {n: t(p) for n, p in pr["pts"].items()}
        lab = {n: labels.get(n, _default_label(n)) if labels else _default_label(n)
               for n in poly.vertices}
        # 面填充（可见面，远 -> 近）
        if show_fill:
            for _face, _fpts in pr["vis_faces"]:
                body.append(_polygon([pts2[v] for v in _face], _FACE_FILL, opacity=0.9))
        # 边：先虚线（隐藏）后实线
        for (a, b), vis in pr["edges"].items():
            if vis:
                continue
            body.append(_line(*pts2[a], *pts2[b], _LINE_HIDDEN, 1.4, dash="5,4"))
        for (a, b), vis in pr["edges"].items():
            if not vis:
                continue
            body.append(_line(*pts2[a], *pts2[b], _LINE_SOLID, 1.7))
        # 顶点圆点
        if show_dots:
            for _n, p in pts2.items():
                body.append(_dot_mark(p[0], p[1]))
        # 顶点字母标注（沿质心向外偏移，避免压线）
        if show_labels:
            cx = sum(p[0] for p in pts2.values()) / len(pts2)
            cy = sum(p[1] for p in pts2.values()) / len(pts2)
            for n, p in pts2.items():
                dx, dy = p[0] - cx, p[1] - cy
                L = math.hypot(dx, dy) or 1.0
                lx, ly = p[0] + dx / L * 11, p[1] + dy / L * 11 - 2
                body.append(_text(lx, ly, lab[n]))
        # 顶点-底面不变量（锥体：顶点必须在底面上方）
        for apex_name, base_names in apex_checks:
            ay = pts2[apex_name][1]
            by = min(pts2[n][1] for n in base_names)
            if ay >= by - 3:
                raise _ApexBelowError(
                    f"顶点 {apex_name} 的投影未位于底面上方（{ay:.1f} 不高于底面最高点 {by:.1f}），"
                    "图形会呈现「顶点在底面内部」的错误效果——请调整 apex_pos 或 view"
                )

    # 球：圆 + 纬线（赤道）/经线椭圆（斜二测下球轮廓仍按教材画法画圆）
    for sp in spheres:
        c2 = t(view.project(sp.center))
        body.append(_circle(c2[0], c2[1], sp.r * k, _LINE_SOLID, 1.4))
        if sp.equator:
            eq = [
                t(view.project(
                    (sp.center[0] + sp.r * math.cos(a), sp.center[1] + sp.r * math.sin(a),
                     sp.center[2])))
                for a in (i * math.tau / 60 for i in range(61))
            ]
            body.append(_path(eq, _LINE_AUX, 1.0, dash="4,4"))
        if sp.meridian:
            me = [
                t(view.project(
                    (sp.center[0], sp.center[1] + sp.r * math.sin(a),
                     sp.center[2] + sp.r * math.cos(a))))
                for a in (i * math.tau / 60 for i in range(61))
            ]
            body.append(_path(me, _LINE_AUX, 1.0, dash="4,4"))
        if sp.center_label:
            body.append(_dot_mark(c2[0], c2[1], 2.0))
            body.append(_text(c2[0] - 9, c2[1] + 14, sp.center_label, anchor="middle"))

    return _svg_doc(size, "\n".join(body))


# ---------------------------------------------------------------------------
# 函数表达式安全求值（AST 白名单，无 eval/exec）
# ---------------------------------------------------------------------------

_SAFE_FUNCS: dict[str, Callable] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "exp": math.exp, "log": math.log, "log2": math.log2, "log10": math.log10,
    "sqrt": math.sqrt, "abs": math.fabs, "fabs": math.fabs,
    "floor": math.floor, "ceil": math.ceil, "pow": math.pow,
}
_SAFE_CONSTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}
_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_UNARY_OPS = (ast.USub, ast.UAdd)


def _compile_expr(expr: str) -> Callable[[float], float]:
    """把用户表达式编译为安全的单变量函数（拒绝一切未白名单语法）。"""
    if not isinstance(expr, str) or not expr.strip():
        raise FigureParamsError("函数表达式为空")
    if len(expr) > 200:
        raise FigureParamsError("函数表达式过长（>200 字符）")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FigureParamsError(f"表达式语法错误: {e.msg}") from e

    def build(node: ast.AST):
        if isinstance(node, ast.Expression):
            return build(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return lambda x: float(node.value)
            raise FigureParamsError(f"表达式含不支持的常量: {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id == "x":
                return lambda x: x
            if node.id in _SAFE_CONSTS:
                val = _SAFE_CONSTS[node.id]
                return lambda x, val=val: val
            raise FigureParamsError(f"表达式含未定义的符号: {node.id}")
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _BIN_OPS:
                raise FigureParamsError(f"不支持的运算符: {type(node.op).__name__}")
            lf, rf = build(node.left), build(node.right)
            if isinstance(node.op, ast.Add):
                return lambda x: lf(x) + rf(x)
            if isinstance(node.op, ast.Sub):
                return lambda x: lf(x) - rf(x)
            if isinstance(node.op, ast.Mult):
                return lambda x: lf(x) * rf(x)
            if isinstance(node.op, ast.Div):
                return lambda x: lf(x) / rf(x)
            if isinstance(node.op, ast.Pow):
                return lambda x: lf(x) ** rf(x)
            return lambda x: lf(x) % rf(x)
        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARY_OPS:
                raise FigureParamsError(f"不支持的运算符: {type(node.op).__name__}")
            f = build(node.operand)
            return (lambda x: -f(x)) if isinstance(node.op, ast.USub) else f
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
                raise FigureParamsError(f"不支持的函数调用: {ast.dump(node.func)[:40]}")
            if node.keywords:
                raise FigureParamsError("函数调用不支持关键字参数")
            if not 1 <= len(node.args) <= 2:
                raise FigureParamsError("函数调用参数个数须为 1~2")
            fn = _SAFE_FUNCS[node.func.id]
            afs = [build(a) for a in node.args]
            return lambda x, fn=fn, afs=afs: fn(*(a(x) for a in afs))
        raise FigureParamsError(f"表达式含不支持的语法节点: {type(node).__name__}")

    fn = build(tree)

    def safe(x: float) -> float:
        try:
            v = fn(x)
            if isinstance(v, complex):
                return math.nan
            return float(v)
        except (ArithmeticError, ValueError, OverflowError):
            return math.nan

    return safe


# ---------------------------------------------------------------------------
# 自适应采样 + 路径断开（参考 function-plot sampler）
# ---------------------------------------------------------------------------

def _sample_curve(f, xmin: float, xmax: float, *, n0: int = 64, tol: float = 1e-3,
                  max_depth: int = 12) -> list[tuple[float, float]]:
    """递归中点细分采样：段内 |f(mid) − (f(a)+f(b))/2| ≤ tol 即视为线性。"""
    pts: list[tuple[float, float]] = []

    def refine(xa, fa, xb, fb, depth):
        xm = (xa + xb) / 2
        fm = f(xm)
        linear_ok = (
            math.isfinite(fa) and math.isfinite(fb) and math.isfinite(fm)
            and abs(fm - (fa + fb) / 2) <= tol
        )
        if linear_ok or depth >= max_depth:
            pts.append((xm, fm))
        else:
            refine(xa, fa, xm, fm, depth + 1)
            refine(xm, fm, xb, fb, depth + 1)

    step = (xmax - xmin) / n0
    xs = [xmin + i * step for i in range(n0 + 1)]
    fs = [f(x) for x in xs]
    out: list[tuple[float, float]] = [(xs[0], fs[0])]
    for i in range(n0):
        refine(xs[i], fs[i], xs[i + 1], fs[i + 1], 0)
        out.append((xs[i + 1], fs[i + 1]))
    return out


def _points_to_paths(pts: list[tuple[float, float]], jump: float,
                     ymin: float, ymax: float) -> list[list[Point2]]:
    """采样点 -> 多个 path（渐近线/间断处断开）。y 值裁剪到视口附近。

    断开判定（在裁剪之前用原始值）：
    - y 非有限；
    - 相邻 |Δy| > jump（默认 1000，超大跳变）；
    - 相邻 y 异号且较大一侧 |y| > 4×(ymax−ymin)（穿过视口外的渐近线，
      tan 在 π/2 处两侧均为视口外大值）。
    """
    lo, hi = ymin - 0.5 * (ymax - ymin), ymax + 0.5 * (ymax - ymin)
    big = 4.0 * (ymax - ymin)
    paths: list[list[Point2]] = []
    cur: list[Point2] = []
    last_raw: float | None = None

    def flush():
        if len(cur) >= 2:
            paths.append(cur[:])
        cur.clear()

    for x, y in pts:
        if not math.isfinite(y):
            flush()
            last_raw = None
            continue
        if last_raw is not None and (
            abs(y - last_raw) > jump
            or (y < 0 < last_raw or last_raw < 0 < y) and max(abs(y), abs(last_raw)) > big
        ):
            flush()
        cur.append((x, max(lo, min(hi, y))))
        last_raw = y
    flush()
    return paths


# ---------------------------------------------------------------------------
# 坐标轴刻度（Heckbert nice numbers，参考 d3-scale tickIncrement）
# ---------------------------------------------------------------------------

def nice_step(span: float, target_n: float = 8.0) -> float:
    """nice 步长：1/2/5×10ⁿ 系列。"""
    if span <= 0 or not math.isfinite(span):
        raise FigureParamsError(f"刻度范围非法: {span!r}")
    raw = span / target_n
    mag = 10 ** math.floor(math.log10(raw))
    norm = raw / mag
    if norm < 1.5:
        nf = 1
    elif norm < 3:
        nf = 2
    elif norm < 7:
        nf = 5
    else:
        nf = 10
    return nf * mag


def make_ticks(lo: float, hi: float, step: float) -> list[float]:
    out: list[float] = []
    v = math.ceil(lo / step - 1e-9) * step
    while v <= hi + 1e-9 * max(1.0, abs(hi)):
        if abs(v) < abs(step) * 1e-6:
            v = 0.0
        out.append(v)
        v += step
    return out


def _fmt_tick(v: float) -> str:
    if abs(v) < 1e-12:
        return "0"
    s = f"{v:.4g}"
    if "e" in s:
        mant, exp = s.split("e")
        return f"{mant}×10{exp}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# ---------------------------------------------------------------------------
# 函数图像渲染
# ---------------------------------------------------------------------------

def _function_svg(fig: dict) -> str:
    p = fig["params"]
    size = fig["size"]
    style = fig["style"]
    w, h = size
    ml, mr, mt, mb = 46.0, 14.0, 16.0, 34.0
    xmin, xmax = p["x_range"]
    ymin, ymax = p["y_range"]
    plot_w, plot_h = w - ml - mr, h - mt - mb

    def sx(x):
        return ml + (x - xmin) / (xmax - xmin) * plot_w

    def sy(y):
        return mt + (ymax - y) / (ymax - ymin) * plot_h

    body: list[str] = [f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>']

    curves = p["curves"]
    fns = []
    for i, c in enumerate(curves):
        expr = c["expr"]
        fns.append(_compile_expr(expr))
        domain = c.get("domain") or [xmin, xmax]
        lo, hi = max(domain[0], xmin), min(domain[1], xmax)
        jump = max(3.0 * (ymax - ymin), 1e3)
        pts = _sample_curve(fns[-1], lo, hi)
        color = c.get("color") or _CURVE_COLORS[i % len(_CURVE_COLORS)]
        for path in _points_to_paths(pts, jump, ymin, ymax):
            body.append(_path([(sx(x), sy(y)) for x, y in path], color, 1.9))

    # 坐标轴（过原点；原点不在范围内则贴边）
    x_axis_y = sy(0.0) if ymin <= 0 <= ymax else (sy(ymin) if ymax < 0 else sy(ymax))
    y_axis_x = sx(0.0) if xmin <= 0 <= xmax else (sx(xmin) if xmax < 0 else sx(xmax))
    ax_x1, ax_x2 = sx(xmin), sx(xmax)
    ay_y1, ay_y2 = sy(ymax), sy(ymin)
    body.append(_line(ax_x1, x_axis_y, ax_x2, x_axis_y, _LINE_SOLID, 1.3))
    body.append(_line(y_axis_x, ay_y1, y_axis_x, ay_y2, _LINE_SOLID, 1.3))
    # 箭头（双端，教材风格）
    body.append(_polygon([(ax_x2 + 8, x_axis_y), (ax_x2, x_axis_y - 3.6),
                          (ax_x2, x_axis_y + 3.6)], _LINE_SOLID))
    body.append(_polygon([(ax_x1 - 8, x_axis_y), (ax_x1, x_axis_y - 3.6),
                          (ax_x1, x_axis_y + 3.6)], _LINE_SOLID))
    body.append(_polygon([(y_axis_x, ay_y1 - 8), (y_axis_x - 3.6, ay_y1),
                          (y_axis_x + 3.6, ay_y1)], _LINE_SOLID))
    body.append(_polygon([(y_axis_x, ay_y2 + 8), (y_axis_x - 3.6, ay_y2),
                          (y_axis_x + 3.6, ay_y2)], _LINE_SOLID))
    body.append(_text(ax_x2 + 6, x_axis_y - 8, "x", anchor="start"))
    body.append(_text(y_axis_x + 6, ay_y1 - 6, "y"))

    # 刻度
    xticks = p.get("ticks") and p["ticks"].get("x") or nice_step(xmax - xmin)
    yticks = p.get("ticks") and p["ticks"].get("y") or nice_step(ymax - ymin)
    for v in make_ticks(xmin, xmax, xticks):
        if abs(v) < 1e-12:
            continue
        body.append(_line(sx(v), x_axis_y - 2.6, sx(v), x_axis_y + 2.6, _LINE_SOLID, 1.0))
        body.append(_text(sx(v), x_axis_y + 15, _fmt_tick(v), size=11, italic=False))
    for v in make_ticks(ymin, ymax, yticks):
        if abs(v) < 1e-12:
            continue
        body.append(_line(y_axis_x - 2.6, sy(v), y_axis_x + 2.6, sy(v), _LINE_SOLID, 1.0))
        body.append(_text(y_axis_x - 8, sy(v) + 4, _fmt_tick(v), size=11,
                          italic=False, anchor="end"))
    if xmin <= 0 <= xmax and ymin <= 0 <= ymax:
        body.append(_text(y_axis_x - 6, x_axis_y + 15, "O", anchor="middle"))

    # 渐近线（灰色虚线）
    for ax in p.get("asymptotes", {}).get("x", []):
        body.append(_line(sx(ax), ay_y1, sx(ax), ay_y2, _LINE_AUX, 1.1, dash="4,4"))
    for ay in p.get("asymptotes", {}).get("y", []):
        body.append(_line(ax_x1, sy(ay), ax_x2, sy(ay), _LINE_AUX, 1.1, dash="4,4"))

    # 曲线名标注（放在各自曲线中部偏右处）
    for i, c in enumerate(curves):
        if not c.get("label"):
            continue
        domain = c.get("domain") or [xmin, xmax]
        lx = max(domain[0], xmin) + 0.62 * (min(domain[1], xmax) - max(domain[0], xmin))
        ly = fns[i](lx)
        ly = max(ymin, min(ymax, ly)) if math.isfinite(ly) else ymax - 0.08 * (ymax - ymin)
        color = c.get("color") or _CURVE_COLORS[i % len(_CURVE_COLORS)]
        body.append(_text(sx(lx) + 10, sy(ly) - 6, c["label"], anchor="start", fill=color))

    # 关键点
    for pt in p.get("points", []):
        x, y = float(pt["x"]), float(pt["y"])
        if not (xmin <= x <= xmax and ymin <= y <= ymax):
            raise FigureParamsError(f"关键点 ({x}, {y}) 超出坐标范围")
        body.append(f'<circle cx="{_fmt(sx(x))}" cy="{_fmt(sy(y))}" r="3" '
                    f'fill="#c01c28" stroke="#ffffff" stroke-width="1"/>')
        if pt.get("label"):
            body.append(_text(sx(x) + 8, sy(y) - 8, pt["label"], anchor="start",
                              fill="#c01c28"))

    # 网格（可选）
    if style.get("grid"):
        for v in make_ticks(xmin, xmax, xticks):
            body.append(_line(sx(v), ay_y1, sx(v), ay_y2, "#e3e7ee", 0.8))
        for v in make_ticks(ymin, ymax, yticks):
            body.append(_line(ax_x1, sy(v), ax_x2, sy(v), "#e3e7ee", 0.8))

    return _svg_doc(size, "\n".join(body))


# ---------------------------------------------------------------------------
# 平面三角形渲染
# ---------------------------------------------------------------------------

def _triangle2d_svg(fig: dict) -> str:
    p = fig["params"]
    size = fig["size"]
    labels = fig["labels"]
    pts = {n: (float(x), float(y)) for n, (x, y) in p["points"].items()}
    names = list(pts)
    if len(names) != 3:
        raise FigureParamsError("triangle2d 需要 3 个顶点")
    t, _k = _fit_transform(list(pts.values()), size)
    pts2 = {n: t(q) for n, q in pts.items()}
    body: list[str] = [f'<rect x="0" y="0" width="{size[0]}" height="{size[1]}" fill="#ffffff"/>']

    edges = p.get("edges") or [(names[0], names[1]), (names[1], names[2]), (names[2], names[0])]
    for a, b in edges:
        body.append(_line(*pts2[a], *pts2[b], _LINE_SOLID, 1.7))
    if p.get("circumcircle"):
        # 外接圆：两条垂直平分线交点
        (x1, y1), (x2, y2), (x3, y3) = (pts[n] for n in names)
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) < 1e-9:
            raise FigureParamsError("三角形三点共线，无外接圆")
        ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1)
              + (x3 * x3 + y3 * y3) * (y1 - y2)) / d
        uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3)
              + (x3 * x3 + y3 * y3) * (x2 - x1)) / d
        r = math.hypot(x1 - ux, y1 - uy)
        c2 = t((ux, uy))
        body.append(_circle(c2[0], c2[1], r * _k, _LINE_AUX, 1.2, dash="4,4"))
    if p.get("right_angle"):
        a = p["right_angle"]
        if a not in pts:
            raise FigureParamsError(f"right_angle 顶点 {a} 不存在")
        b = (set(names) - {a}).pop()
        others = [n for n in names if n != a]
        v1 = _sub2(pts[others[0]], pts[a])
        v2 = _sub2(pts[others[1]], pts[a])
        n1 = _norm2(v1) or 1.0
        n2 = _norm2(v2) or 1.0
        u1 = (v1[0] / n1, v1[1] / n1)
        u2 = (v2[0] / n2, v2[1] / n2)
        k = 0.12 * max(math.hypot(*v1), math.hypot(*v2))
        p0 = pts2[a]
        q1 = (p0[0] + u1[0] * k, p0[1] + u1[1] * k)
        q2 = (p0[0] + u2[0] * k, p0[1] + u2[1] * k)
        q3 = (q1[0] + u2[0] * k, q1[1] + u2[1] * k)
        body.append(_path([q1, q3, q2], _LINE_SOLID, 1.2))

    if fig["style"].get("show_labels", True):
        for n, q in pts2.items():
            cx = sum(x for x, _ in pts2.values()) / 3
            cy = sum(y for _, y in pts2.values()) / 3
            dx, dy = q[0] - cx, q[1] - cy
            L = math.hypot(dx, dy) or 1.0
            body.append(_text(q[0] + dx / L * 11, q[1] + dy / L * 11 - 2,
                              labels.get(n, _default_label(n)) if labels else _default_label(n)))
    return _svg_doc(size, "\n".join(body))


def _sub2(a: Point2, b: Point2) -> Point2:
    return (a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# 圆锥曲线渲染（椭圆/双曲线/抛物线：焦点、准线、渐近线、焦半径——等比缩放不变形）
# ---------------------------------------------------------------------------

def _conic_svg(fig: dict) -> str:
    p = fig["params"]
    size = fig["size"]
    w, h = size
    ml, mr, mt, mb = 36.0, 18.0, 18.0, 30.0
    plot_w, plot_h = w - ml - mr, h - mt - mb

    curve, axis = p["curve"], p["axis"]
    cx, cy = p["cx"], p["cy"]

    # 视野：曲线范围 ∪ 焦点 ∪ 标注点，外扩 18%（等比缩放，保证曲线形状不失真）
    xs: list[float] = [cx]
    ys: list[float] = [cy]
    if curve == "ellipse":
        rx, ry = (p["a"], p["b"]) if axis == "x" else (p["b"], p["a"])
        xs += [cx - rx, cx + rx]
        ys += [cy - ry, cy + ry]
    elif curve == "hyperbola":
        rx, ry = max(p["a"], p["focal"]) * 1.15, p["b"] * 2.0
        if axis == "x":
            xs += [cx - rx, cx + rx]
            ys += [cy - ry, cy + ry]
        else:
            xs += [cx - ry, cx + ry]
            ys += [cy - rx, cy + rx]
    else:  # parabola：横向展开 ≈ 4.2p，纵向 ≈ 4.4p，形状饱满
        u_span, v_span = 4.2 * p["p"], 4.5 * p["p"]
        horiz = p["opening"] in ("up", "down")
        xs += [cx - (u_span if horiz else v_span), cx + (u_span if horiz else v_span)]
        ys += [cy - (v_span if horiz else u_span), cy + (v_span if horiz else u_span)]
    for pt in p["points"]:
        xs += [pt["x"], pt["x"]]
        ys += [pt["y"], pt["y"]]

    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    span_x, span_y = x1 - x0, y1 - y0
    x0 -= 0.18 * span_x or 1.0
    x1 += 0.18 * span_x or 1.0
    y0 -= 0.18 * span_y or 1.0
    y1 += 0.18 * span_y or 1.0
    mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2
    scale = min(plot_w / max(x1 - x0, 1e-6), plot_h / max(y1 - y0, 1e-6))
    view_w, view_h = plot_w / scale, plot_h / scale
    left, bottom = mid_x - view_w / 2, mid_y - view_h / 2

    def sx(x):
        return ml + (x - left) * scale

    def sy(y):
        return mt + (bottom + view_h - y) * scale

    body: list[str] = [f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>']

    # 坐标轴（过原点，越界贴边）+ 箭头 + 刻度
    y_axis_x = sx(min(max(0.0, left), left + view_w))
    x_axis_y = sy(min(max(0.0, bottom), bottom + view_h))
    ax_x1, ax_x2, ay_y1, ay_y2 = sx(left), sx(left + view_w), sy(bottom + view_h), sy(bottom)
    body.append(_line(ax_x1, x_axis_y, ax_x2, x_axis_y, _LINE_SOLID, 1.3))
    body.append(_line(y_axis_x, ay_y1, y_axis_x, ay_y2, _LINE_SOLID, 1.3))
    body.append(_polygon([(ax_x2 + 8, x_axis_y), (ax_x2, x_axis_y - 3.6),
                          (ax_x2, x_axis_y + 3.6)], _LINE_SOLID))
    body.append(_polygon([(y_axis_x, ay_y1 - 8), (y_axis_x - 3.6, ay_y1),
                          (y_axis_x + 3.6, ay_y1)], _LINE_SOLID))
    body.append(_text(ax_x2 - 4, x_axis_y - 8, "x", anchor="end"))
    body.append(_text(y_axis_x + 8, ay_y1 + 10, "y", anchor="start"))
    if left <= 0 <= left + view_w and bottom <= 0 <= bottom + view_h:
        body.append(_text(y_axis_x - 6, x_axis_y + 15, "O", anchor="middle"))
    xticks = nice_step(max(abs(x0), abs(x1)) * 2)
    for v in make_ticks(left, left + view_w, xticks):
        if abs(v) < 1e-12:
            continue
        body.append(_line(sx(v), x_axis_y - 2.6, sx(v), x_axis_y + 2.6, _LINE_SOLID, 1.0))
        body.append(_text(sx(v), x_axis_y + 15, _fmt_tick(v), size=11, italic=False))
    for v in make_ticks(bottom, bottom + view_h, nice_step(max(abs(y0), abs(y1)) * 2)):
        if abs(v) < 1e-12:
            continue
        body.append(_line(y_axis_x - 2.6, sy(v), y_axis_x + 2.6, sy(v), _LINE_SOLID, 1.0))
        body.append(_text(y_axis_x - 8, sy(v) + 4, _fmt_tick(v), size=11,
                          italic=False, anchor="end"))

    color = "#1a5fb4"
    # 渐近线（双曲线，先画在曲线下层）
    if curve == "hyperbola" and p["show_aux"]:
        ratio = p["b"] / p["a"]
        if axis == "x":
            for s in (1.0, -1.0):
                ya = cy + s * ratio * (left - cx)
                yb = cy + s * ratio * (left + view_w - cx)
                body.append(_line(sx(left), sy(ya), sx(left + view_w), sy(yb),
                                  _LINE_AUX, 1.1, dash="5,4"))
        else:
            for s in (1.0, -1.0):
                xa = cx + s * ratio * (bottom - cy)
                xb = cx + s * ratio * (bottom + view_h - cy)
                body.append(_line(sx(xa), sy(bottom), sx(xb), sy(bottom + view_h),
                                  _LINE_AUX, 1.1, dash="5,4"))
    # 准线（抛物线）
    if curve == "parabola" and p["show_aux"]:
        sgn = 1.0 if p["opening"] in ("up", "right") else -1.0
        if p["opening"] in ("up", "down"):
            yd = cy - sgn * p["p"]
            body.append(_line(sx(left), sy(yd), sx(left + view_w), sy(yd),
                              _LINE_AUX, 1.1, dash="5,4"))
            body.append(_text(sx(left + view_w) - 6, sy(yd) - 5, "l", anchor="end",
                              fill=_LINE_AUX))
        else:
            xd = cx - sgn * p["p"]
            body.append(_line(sx(xd), sy(bottom), sx(xd), sy(bottom + view_h),
                              _LINE_AUX, 1.1, dash="5,4"))
            body.append(_text(sx(xd) + 10, sy(bottom + view_h) + 2, "l", anchor="start",
                              fill=_LINE_AUX))

    # 曲线本体（参数采样，等比投影）
    if curve == "ellipse":
        n = 240
        pts = []
        for i in range(n + 1):
            t = 2 * math.pi * i / n
            if axis == "x":
                x, y = cx + p["a"] * math.cos(t), cy + p["b"] * math.sin(t)
            else:
                x, y = cx + p["b"] * math.cos(t), cy + p["a"] * math.sin(t)
            pts.append((sx(x), sy(y)))
        body.append(_path(pts, color, 2.0))
    elif curve == "hyperbola":
        t_max = min(4.0, math.acosh(max(2.0, 0.8 * view_w / p["a"])))
        for branch in (1.0, -1.0):
            pts = []
            for i in range(121):
                t = -t_max + 2 * t_max * i / 120
                u, v = p["a"] * math.cosh(t), p["b"] * math.sinh(t)
                if axis == "x":
                    x, y = cx + branch * u, cy + v
                else:
                    x, y = cx + v, cy + branch * u
                pts.append((sx(x), sy(y)))
            body.append(_path(pts, color, 2.0))
    else:  # parabola
        sgn = 1.0 if p["opening"] in ("up", "right") else -1.0
        u_span = 4.2 * p["p"]
        pts = []
        for i in range(181):
            u = -u_span + 2 * u_span * i / 180
            v = sgn * u * u / (4.0 * p["p"])
            if p["opening"] in ("up", "down"):
                x, y = cx + u, cy + v
            else:
                x, y = cx + v, cy + u
            pts.append((sx(x), sy(y)))
        body.append(_path(pts, color, 2.0))

    # 焦点
    if p["show_foci"]:
        foci: list[tuple[float, float, str]] = []
        if curve == "parabola":
            sgn = 1.0 if p["opening"] in ("up", "right") else -1.0
            if p["opening"] in ("up", "down"):
                foci.append((cx, cy + sgn * p["p"], "F"))
            else:
                foci.append((cx + sgn * p["p"], cy, "F"))
        elif axis == "x":
            foci = [(cx - p["focal"], cy, "F₁"), (cx + p["focal"], cy, "F₂")]
        else:
            foci = [(cx, cy - p["focal"], "F₁"), (cx, cy + p["focal"], "F₂")]
        for fx, fy, name in foci:
            body.append(_dot_mark(sx(fx), sy(fy)))
            body.append(_text(sx(fx) - 9, sy(fy) + 14, name, fill="#c01c28"))

    # 焦半径（标注点与焦点相连，虚线——焦点三角形/焦半径题的观察对象）
    if p["show_focal_radii"] and p["points"] and p["show_foci"]:
        for pt in p["points"]:
            for fx, fy, _n in foci:
                body.append(_line(sx(fx), sy(fy), sx(pt["x"]), sy(pt["y"]),
                                  _LINE_AUX, 1.2, dash="4,4"))

    # 标注点（红点 + 字母，坐标已在校验期确认满足曲线方程）
    for pt in p["points"]:
        px, py = sx(pt["x"]), sy(pt["y"])
        body.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(py)}" r="3" '
                    f'fill="#c01c28" stroke="#ffffff" stroke-width="1"/>')
        if pt["label"]:
            body.append(_text(px + 8, py - 8, pt["label"], anchor="start", fill="#c01c28"))

    # 中心/顶点标注
    if p.get("center_label"):
        body.append(_text(sx(cx) - 10, sy(cy) + 14, p["center_label"]))
    return _svg_doc(size, "\n".join(body))


def _norm2(a: Point2) -> float:
    return math.hypot(a[0], a[1])


# ---------------------------------------------------------------------------
# 参数校验与规范化
# ---------------------------------------------------------------------------

def _num(v, name: str, lo: float | None = None, hi: float | None = None,
         positive: bool = False) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise FigureParamsError(f"参数 {name}={v!r} 不是数字") from None
    if not math.isfinite(f):
        raise FigureParamsError(f"参数 {name} 不是有限数值")
    if positive and f <= 0:
        raise FigureParamsError(f"参数 {name} 必须 > 0，收到 {f}")
    if lo is not None and f < lo:
        raise FigureParamsError(f"参数 {name} 必须 ≥ {lo}，收到 {f}")
    if hi is not None and f > hi:
        raise FigureParamsError(f"参数 {name} 必须 ≤ {hi}，收到 {f}")
    return f


def _pt2d(v, name: str) -> Point2:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise FigureParamsError(f"参数 {name}={v!r} 不是二维点")
    return (_num(v[0], f"{name}[0]"), _num(v[1], f"{name}[1]"))


def _pt3d(v, name: str) -> Vec3:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise FigureParamsError(f"参数 {name}={v!r} 不是三维点")
    return (_num(v[0], f"{name}[0]"), _num(v[1], f"{name}[1]"), _num(v[2], f"{name}[2]"))


def _validate_solid(p: dict, kind: str) -> dict:
    if kind in ("cube", "cuboid"):
        if kind == "cube":
            a = _num(p.get("a"), "a", positive=True)
            return {"a": a}
        a = _num(p.get("a"), "a", positive=True)
        b = _num(p.get("b"), "b", positive=True)
        h = _num(p.get("h"), "h", positive=True)
        return {"a": a, "b": b, "h": h}
    if kind == "triangular_prism":
        height = _num(p.get("height"), "height", positive=True)
        base = p.get("base", "equilateral")
        if base == "equilateral":
            side = _num(p.get("side"), "side", positive=True)
            return {"base": "equilateral", "side": side, "height": height}
        if base == "right":
            ab = _num(p.get("ab"), "ab", positive=True)
            bc = _num(p.get("bc"), "bc", positive=True)
            return {"base": "right", "ab": ab, "bc": bc, "height": height}
        if base == "custom":
            vs = p.get("vertices")
            if not isinstance(vs, list) or len(vs) != 3:
                raise FigureParamsError("custom 底面需要 vertices: [[x,y]×3]")
            return {"base": "custom", "vertices": [_pt2d(v, "vertices[i]") for v in vs],
                    "height": height}
        raise FigureParamsError(f"三棱柱 base 取值非法: {base!r}（equilateral/right/custom）")
    if kind == "quad_pyramid":
        base_names = p.get("base") or ["A", "B", "C", "D"]
        apex = p.get("apex", "P")
        if not isinstance(base_names, list) or len(base_names) != 4:
            raise FigureParamsError("quad_pyramid.base 需要 4 个顶点名")
        if len(set(base_names) | {apex}) != 5:
            raise FigureParamsError("四棱锥顶点名重复")
        if "base_points" in p:
            bps = [_pt2d(v, "base_points[i]") for v in p["base_points"]]
            apex_pos = _pt3d(p.get("apex_pos", [0, 0, 1]), "apex_pos")
            return {"base_points": bps, "apex_pos": apex_pos, "apex": apex,
                    "base": base_names, "height": apex_pos[2]}
        height = _num(p.get("height"), "height", positive=True)
        w = _num(p.get("base_w", 1), "base_w", positive=True)
        d = _num(p.get("base_d", 1), "base_d", positive=True)
        return {"base_w": w, "base_d": d, "height": height, "apex": apex,
                "base": base_names}
    if kind == "tri_pyramid":
        base_names = p.get("base") or ["A", "B", "C"]
        apex = p.get("apex", "P")
        if not isinstance(base_names, list) or len(base_names) != 3:
            raise FigureParamsError("tri_pyramid.base 需要 3 个顶点名")
        if len(set(base_names) | {apex}) != 4:
            raise FigureParamsError("三棱锥顶点名重复")
        if "base_points" in p:
            bps = [_pt2d(v, "base_points[i]") for v in p["base_points"]]
            apex_pos = _pt3d(p.get("apex_pos"), "apex_pos")
            return {"base_points": bps, "apex_pos": apex_pos, "apex": apex,
                    "base": base_names}
        side = _num(p.get("side", 1), "side", positive=True)
        height = _num(p.get("height", 1), "height", positive=True)
        return {"side": side, "height": height, "apex": apex, "base": base_names,
                "apex_pos": (0.0, 0.0, height)}
    if kind == "tri_frustum":
        bs = _num(p.get("bottom_side"), "bottom_side", positive=True)
        ts = _num(p.get("top_side"), "top_side", positive=True)
        h = _num(p.get("height"), "height", positive=True)
        return {"bottom_side": bs, "top_side": ts, "height": h}
    if kind == "sphere":
        r = _num(p.get("r"), "r", positive=True)
        center = _pt3d(p.get("center", [0, 0, 0]), "center")
        solid = p.get("solid")
        if solid is not None:
            if not isinstance(solid, dict):
                raise FigureParamsError("sphere.solid 必须是图形对象")
            solid = validate_figure_params(solid)
        return {"r": r, "center": center, "center_label": p.get("center_label", "O"),
                "equator": bool(p.get("equator", True)),
                "meridian": bool(p.get("meridian", False)),
                "solid": solid}
    if kind == "polyhedron":
        vs = p.get("vertices")
        fs = p.get("faces")
        if not isinstance(vs, dict) or not vs:
            raise FigureParamsError("polyhedron.vertices 需要 {名: [x,y,z]} 字典")
        if not isinstance(fs, list) or not fs:
            raise FigureParamsError("polyhedron.faces 需要面列表")
        verts = {str(n): _pt3d(v, f"vertices.{n}") for n, v in vs.items()}
        faces = []
        for f in fs:
            if not isinstance(f, list) or len(f) < 3:
                raise FigureParamsError(f"面 {f!r} 非法")
            faces.append([str(n) for n in f])
        extras = []
        for e in p.get("extra_edges", []):
            if not isinstance(e, list) or len(e) != 2:
                raise FigureParamsError(f"附加边 {e!r} 非法")
            extras.append((str(e[0]), str(e[1])))
        return {"vertices": verts, "faces": faces, "extra_edges": extras}
    raise FigureParamsError(f"未知图形类型: {kind!r}")  # pragma: no cover


def _validate_function(p: dict) -> dict:
    curves = p.get("curves")
    if not isinstance(curves, list) or not curves:
        raise FigureParamsError("function.curves 需要至少一条曲线")
    norm_curves = []
    for i, c in enumerate(curves):
        if not isinstance(c, dict) or "expr" not in c:
            raise FigureParamsError(f"曲线 {i} 缺少 expr")
        _compile_expr(c["expr"])  # 语法校验（提前失败）
        label = c.get("label")
        if label is not None:
            label = str(label)[:40]
        color = c.get("color")
        domain = c.get("domain")
        if domain is not None:
            if not isinstance(domain, (list, tuple)) or len(domain) != 2:
                raise FigureParamsError(f"曲线 {i} domain 非法")
            lo, hi = _num(domain[0], "domain[0]"), _num(domain[1], "domain[1]")
            if lo >= hi:
                raise FigureParamsError(f"曲线 {i} domain 区间非法")
            domain = [lo, hi]
        norm_curves.append({"expr": str(c["expr"]), "label": label, "color": color,
                            "domain": domain})
    xr = p.get("x_range")
    yr = p.get("y_range")
    if not isinstance(xr, (list, tuple)) or len(xr) != 2:
        raise FigureParamsError("function.x_range 需要 [xmin, xmax]")
    if not isinstance(yr, (list, tuple)) or len(yr) != 2:
        raise FigureParamsError("function.y_range 需要 [ymin, ymax]")
    xmin, xmax = _num(xr[0], "x_range[0]"), _num(xr[1], "x_range[1]")
    ymin, ymax = _num(yr[0], "y_range[0]"), _num(yr[1], "y_range[1]")
    if xmin >= xmax or ymin >= ymax:
        raise FigureParamsError("坐标范围区间非法")
    points = []
    for i, pt in enumerate(p.get("points", [])):
        if not isinstance(pt, dict):
            raise FigureParamsError(f"points[{i}] 需要 {{x, y}} 字典")
        points.append({"x": _num(pt.get("x"), f"points[{i}].x"),
                       "y": _num(pt.get("y"), f"points[{i}].y"),
                       "label": str(pt["label"])[:40] if pt.get("label") else None})
    asym = {"x": [], "y": []}
    for k in ("x", "y"):
        for v in p.get("asymptotes", {}).get(k, []):
            asym[k].append(_num(v, f"asymptotes.{k}"))
    ticks = None
    if p.get("ticks"):
        ticks = {}
        if p["ticks"].get("x") is not None:
            ticks["x"] = _num(p["ticks"]["x"], "ticks.x", positive=True)
        if p["ticks"].get("y") is not None:
            ticks["y"] = _num(p["ticks"]["y"], "ticks.y", positive=True)
    return {"curves": norm_curves, "x_range": [xmin, xmax], "y_range": [ymin, ymax],
            "points": points, "asymptotes": asym, "ticks": ticks}


def _validate_triangle2d(p: dict) -> dict:
    pts = p.get("points")
    if not isinstance(pts, dict) or len(pts) != 3:
        raise FigureParamsError("triangle2d.points 需要 3 个 {名: [x,y]}")
    norm = {str(n): _pt2d(v, f"points.{n}") for n, v in pts.items()}
    if p.get("right_angle") is not None and str(p["right_angle"]) not in norm:
        raise FigureParamsError(f"right_angle 顶点 {p['right_angle']} 不存在")
    edges = None
    if p.get("edges") is not None:
        edges = [[str(a), str(b)] for a, b in p["edges"]]
    return {"points": norm, "right_angle": str(p["right_angle"]) if p.get("right_angle")
            else None, "circumcircle": bool(p.get("circumcircle", False)),
            "edges": edges}


def _on_conic(p: dict, x: float, y: float, tol: float) -> bool:
    """判断 (x, y) 是否在圆锥曲线上（相对容差）——标注点必须在曲线上，图才配称原题图"""
    dx, dy = x - p["cx"], y - p["cy"]
    if p["curve"] == "ellipse":
        u, v = (dx, dy) if p["axis"] == "x" else (dy, dx)
        return abs((u / p["a"]) ** 2 + (v / p["b"]) ** 2 - 1.0) <= tol
    if p["curve"] == "hyperbola":
        u, v = (dx, dy) if p["axis"] == "x" else (dy, dx)
        return abs((u / p["a"]) ** 2 - (v / p["b"]) ** 2 - 1.0) <= tol
    # parabola：u 为横向坐标，v 为开口方向坐标，v = u²/(4p)
    horiz = p["opening"] in ("up", "down")
    u, v = (dx, dy) if horiz else (dy, dx)
    sign = 1.0 if p["opening"] in ("up", "right") else -1.0
    expected = sign * (u * u) / (4.0 * p["p"])
    scale = max(1.0, p["p"])
    return abs(v - expected) <= max(tol * 4.0 * scale, 0.05)


def _validate_conic(p: dict) -> dict:
    """圆锥曲线校验与规范化：椭圆/双曲线/抛物线 + 标注点（t 参数或坐标，须在曲线上）。

    真实性纪律（与课堂题图合同同思想）：标注点坐标在校验期解析为数值，
    且必须满足曲线方程——不在曲线上的点直接拒绝，绝不出"看起来像"的假原题图。
    """
    curve = p.get("curve")
    if curve not in ("ellipse", "hyperbola", "parabola"):
        raise FigureParamsError("conic.curve 必须是 ellipse / hyperbola / parabola")
    axis = p.get("axis") or "x"
    if axis not in ("x", "y"):
        raise FigureParamsError("conic.axis 必须是 x 或 y（对称轴/焦点所在轴）")
    if curve == "parabola":
        axis = "x"  # 抛物线用 opening 表达朝向，axis 仅占位
        pval = _num(p.get("p"), "conic.p（焦准距）", positive=True)
        opening = p.get("opening") or "up"
        if opening not in ("up", "down", "left", "right"):
            raise FigureParamsError("conic.opening 必须是 up/down/left/right")
        vx, vy = _pt2d(p.get("vertex") or [0.0, 0.0], "conic.vertex")
        out: dict = {"curve": curve, "axis": axis, "opening": opening, "p": pval,
                     "cx": vx, "cy": vy, "a": pval,
                     "show_foci": bool(p.get("show_foci", True)),
                     "show_aux": bool(p.get("show_aux", True)),
                     "show_focal_radii": bool(p.get("show_focal_radii", True)),
                     "points": [], "center_label": str(p.get("center_label") or "")[:8]}
    else:
        a = _num(p.get("a"), "conic.a（半长轴/实半轴）", positive=True)
        b = _num(p.get("b"), "conic.b（半短轴/虚半轴）", positive=True)
        if curve == "ellipse" and b >= a:
            raise FigureParamsError("椭圆需 a > b（a 为半长轴；焦点在 y 轴时也先给长轴 a）")
        cx, cy = _pt2d(p.get("center") or [0.0, 0.0], "conic.center")
        focal = math.sqrt(a * a - b * b) if curve == "ellipse" else math.sqrt(a * a + b * b)
        out = {"curve": curve, "axis": axis, "a": a, "b": b, "focal": focal,
               "cx": cx, "cy": cy,
               "show_foci": bool(p.get("show_foci", True)),
               "show_aux": bool(p.get("show_aux", True)),
               "show_focal_radii": bool(p.get("show_focal_radii", True)),
               "points": [], "center_label": str(p.get("center_label") or "")[:8]}

    pts_in = p.get("points") or []
    if not isinstance(pts_in, list):
        raise FigureParamsError("conic.points 必须是数组")
    for i, pt in enumerate(pts_in):
        if not isinstance(pt, dict):
            raise FigureParamsError(f"conic.points[{i}] 必须是对象")
        label = str(pt.get("label") or "")[:16]
        if "t" in pt and curve in ("ellipse", "hyperbola"):
            t = math.radians(_num(pt["t"], f"conic.points[{i}].t（参数角/参数，度）"))
            if curve == "ellipse":
                if out["axis"] == "x":
                    x, y = out["cx"] + out["a"] * math.cos(t), out["cy"] + out["b"] * math.sin(t)
                else:
                    x, y = out["cx"] + out["b"] * math.cos(t), out["cy"] + out["a"] * math.sin(t)
            else:
                branch = 1.0 if float(pt.get("branch", 1) or 1) >= 0 else -1.0
                u, v = out["a"] * math.cosh(t), out["b"] * math.sinh(t)
                if out["axis"] == "x":
                    x, y = out["cx"] + branch * u, out["cy"] + v
                else:
                    x, y = out["cx"] + v, out["cy"] + branch * u
        elif curve == "parabola" and ("x" in pt) != ("y" in pt):
            # 只给一个坐标：另一个由曲线方程算出（顶点 + 开口方向）
            sign = 1.0 if opening in ("up", "right") else -1.0
            horiz = opening in ("up", "down")
            if horiz:
                x = _num(pt["x"], f"conic.points[{i}].x")
                y = out["cy"] + sign * (x - out["cx"]) ** 2 / (4.0 * out["p"])
            else:
                y = _num(pt["y"], f"conic.points[{i}].y")
                x = out["cx"] + sign * (y - out["cy"]) ** 2 / (4.0 * out["p"])
        else:
            if "x" not in pt or "y" not in pt:
                raise FigureParamsError(
                    f"conic.points[{i}] 需要 {{x, y}}（或椭圆/双曲线给 {{t}}，抛物线给单坐标）")
            x = _num(pt["x"], f"conic.points[{i}].x")
            y = _num(pt["y"], f"conic.points[{i}].y")
        if not _on_conic(out, x, y, 0.02):
            raise FigureParamsError(
                f"标注点 {label or f'({x:.3g}, {y:.3g})'} 不在该{curve}曲线上，"
                "请核对坐标/参数或调整 a、b（图形必须与题设方程一致）")
        out["points"].append({"x": x, "y": y, "label": label})
    return out


def validate_figure_params(fig: dict) -> dict:
    """校验并规范化 figure_params：补齐默认值，非法即抛 FigureParamsError（中文消息）。"""
    if not isinstance(fig, dict):
        raise FigureParamsError("figure_params 必须是 JSON 对象")
    kind = fig.get("type")
    if kind not in _SUPPORTED_TYPES:
        raise FigureParamsError(
            f"未知图形类型 {kind!r}；支持: {', '.join(_SUPPORTED_TYPES)}")
    params = fig.get("params")
    if not isinstance(params, dict):
        raise FigureParamsError("figure_params.params 必须是对象")

    if kind == "function":
        params = _validate_function(params)
    elif kind == "triangle2d":
        params = _validate_triangle2d(params)
    elif kind == "conic":
        params = _validate_conic(params)
    else:
        params = _validate_solid(params, kind)

    view_in = fig.get("view")
    view = dict(DEFAULT_VIEW)
    explicit_elev = False
    if view_in:
        v = view_in
        if not isinstance(v, dict):
            raise FigureParamsError("view 必须是对象")
        if v.get("mode") is not None:
            if v["mode"] not in ("oblique", "axonometric"):
                raise FigureParamsError(f"view.mode 取值非法: {v['mode']!r}（oblique/axonometric）")
            view["mode"] = v["mode"]
        if v.get("yaw") is not None:
            view["yaw"] = _num(v["yaw"], "view.yaw")
        if v.get("elev") is not None:
            view["elev"] = _num(v["elev"], "view.elev", lo=-89.0, hi=89.0)
            explicit_elev = True
        if v.get("perspective") is not None:
            view["perspective"] = _num(v["perspective"], "view.perspective", positive=True)
    if view["mode"] == "axonometric" and not explicit_elev and kind in _TYPE_ELEVS:
        view["elev"] = _TYPE_ELEVS[kind]

    size = list(fig.get("size") or (FUNCTION_DEFAULT_SIZE if kind in ("function", "conic")
                                    else DEFAULT_SIZE))
    if len(size) != 2:
        raise FigureParamsError("size 需要 [宽, 高]")
    size = [int(_num(size[0], "size[0]", lo=120, hi=1200)),
            int(_num(size[1], "size[1]", lo=90, hi=900))]

    labels = fig.get("labels")
    if labels is not None:
        if not isinstance(labels, dict):
            raise FigureParamsError("labels 必须是 {顶点名: 显示名} 对象")
        labels = {str(k): str(v)[:16] for k, v in labels.items()}

    style = dict(fig.get("style") or {})
    auto_elev = (kind in ("quad_pyramid", "tri_pyramid")
                 and view["mode"] == "axonometric" and not explicit_elev)
    return {"version": 1, "type": kind, "params": params, "view": view,
            "size": tuple(size), "labels": labels, "style": style,
            "_auto_elev": auto_elev}


# ---------------------------------------------------------------------------
# 渲染入口
# ---------------------------------------------------------------------------

def _build_scene(fig: dict) -> tuple[list[Polyhedron], list[_Sphere],
                                     list[tuple[str, list[str]]]]:
    """按类型构建 3D 场景：多面体列表 + 球列表 + 锥体顶点检查。"""
    kind = fig["type"]
    p = fig["params"]
    checks: list[tuple[str, list[str]]] = []
    if kind == "cube":
        return [_poly_cube(p["a"])], [], checks
    if kind == "cuboid":
        return [_poly_cuboid(p["a"], p["b"], p["h"])], [], checks
    if kind == "triangular_prism":
        if p["base"] == "equilateral":
            base_xy = _equilateral_pts(p["side"])
        elif p["base"] == "right":
            base_xy = [(0.0, 0.0), (p["ab"], 0.0), (0.0, p["bc"])]
        else:
            base_xy = p["vertices"]
        return [_poly_tri_prism(base_xy, p["height"], ["A", "B", "C"])], [], checks
    if kind == "quad_pyramid":
        if "base_points" in p:
            bps = p["base_points"]
        else:
            w, d = p["base_w"], p["base_d"]
            bps = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
        apex_pos = p.get("apex_pos", (0.0, 0.0, p["height"]))
        poly = _poly_quad_pyramid(bps, apex_pos, p["apex"], p["base"])
        checks.append((p["apex"], p["base"]))
        return [poly], [], checks
    if kind == "tri_pyramid":
        bps = p["base_points"] if "base_points" in p else _equilateral_pts(p["side"])
        poly = _poly_tri_pyramid(bps, p["apex_pos"], p["apex"], p["base"])
        checks.append((p["apex"], p["base"]))
        return [poly], [], checks
    if kind == "tri_frustum":
        return [_poly_tri_frustum(p["bottom_side"], p["top_side"], p["height"])], [], checks
    if kind == "polyhedron":
        poly = Polyhedron(p["vertices"], p["faces"], p.get("extra_edges", []))
        return [poly], [], checks
    if kind == "sphere":
        polys, spheres, _c = [], [], checks
        if p["solid"]:
            solid_fig = p["solid"]
            solid_fig.setdefault("size", fig["size"])
            solid_fig.setdefault("view", fig["view"])
            polys, spheres, checks = _build_scene(solid_fig)
        spheres.append(_Sphere(r=p["r"], center=p["center"],
                               center_label=p.get("center_label"),
                               equator=p["equator"], meridian=p["meridian"]))
        return polys, spheres, checks
    raise FigureParamsError(f"类型 {kind} 无 3D 构建器")  # pragma: no cover


def _auto_elevation(polys: list[Polyhedron], spheres: list[_Sphere], view: View3D,
                    f: dict, checks: list[tuple[str, list[str]]]) -> float:
    """二分查找最小仰角使锥体顶点严格位于底面上方（投影对 elev 单调）。"""
    def ok(e: float) -> bool:
        v = View3D(mode="axonometric", yaw=view.yaw, elev=e,
                   perspective=view.perspective)
        try:
            _render_scene(polys, spheres, v, f["size"], f["labels"], f["style"], checks)
            return True
        except _ApexBelowError:
            return False

    if ok(view.elev):
        return view.elev
    if not ok(88.0):
        raise FigureParamsError(
            "无法找到使锥体顶点位于底面上方的视角（height 相对底面尺寸过小），"
            "请增大 height 或调整 apex_pos/view")
    lo, hi = view.elev, 88.0
    for _ in range(32):
        mid = (lo + hi) / 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


def render_figure(fig: dict) -> str:
    """figure_params -> SVG 字符串（确定性；参数非法抛 FigureParamsError）。"""
    f = validate_figure_params(fig)
    if f["type"] == "function":
        return _function_svg(f)
    if f["type"] == "triangle2d":
        return _triangle2d_svg(f)
    if f["type"] == "conic":
        return _conic_svg(f)
    view = View3D(**f["view"])
    polys, spheres, checks = _build_scene(f)
    if f["_auto_elev"]:
        elev = _auto_elevation(polys, spheres, view, f, checks)
        if abs(elev - view.elev) > 1e-6:
            view = View3D(mode="axonometric", yaw=view.yaw, elev=elev,
                          perspective=view.perspective)
            f["view"] = dict(f["view"], elev=elev)
    return _render_scene(polys, spheres, view, f["size"], f["labels"], f["style"], checks)


def to_data_uri(svg: str) -> str:
    """SVG -> data:image/svg+xml;base64 URI（沿用 question_bank.image 链路）。"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# F13 分步渲染：完整图 -> 渐进揭示帧序列（累计式，后帧在前帧上增加要素）
# ---------------------------------------------------------------------------
# 帧派生纪律（确定性、可校验）：
# - 每一帧都是一份完整可独立渲染的 figure_params（复用 validate/render 全链路）；
# - 第 1 帧只含"构图要素"（坐标系+曲线 / 几何体轮廓），不含答案性标注——
#   关键点/渐近线/外接圆/直角标记/顶点字母一律放最后一帧，供引导阶段按提示级别限帧防泄题；
# - 帧间几何布局一致（标注不参与 fit 变换），前端逐帧替换图片不会跳动。

def derive_figure_frames(fig: dict) -> list[dict]:
    """把完整 figure_params 派生为渐进揭示帧序列。

    返回 [{"figure": figure_params, "label": "帧说明"}, ...]（1~2 帧，累计式）。
    无渐进要素（如无关键点的函数图）时退化为单帧，前端静态展示。
    """
    f = validate_figure_params(fig)
    kind = f["type"]

    if kind == "function":
        p = f["params"]
        if p["points"] or p["asymptotes"]["x"] or p["asymptotes"]["y"]:
            base = deepcopy(f)
            base["params"]["points"] = []
            base["params"]["asymptotes"] = {"x": [], "y": []}
            return [
                {"figure": base, "label": "坐标系与曲线"},
                {"figure": f, "label": "标注关键点"},
            ]
        return [{"figure": f, "label": "函数图像"}]

    if kind == "triangle2d":
        p = f["params"]
        if p["circumcircle"] or p["right_angle"]:
            base = deepcopy(f)
            base["params"]["circumcircle"] = False
            base["params"]["right_angle"] = None
            return [
                {"figure": base, "label": "三角形"},
                {"figure": f, "label": "外接圆与直角标记"},
            ]
        return [{"figure": f, "label": "三角形"}]

    if kind == "conic":
        p = f["params"]
        has_annotations = bool(p["points"]) or (
            p["curve"] in ("hyperbola", "parabola") and p["show_aux"]
        )
        if not has_annotations:
            return [{"figure": f, "label": "圆锥曲线"}]
        # 帧 1 = 构图要素（曲线 + 坐标系 + 焦点）；帧 2 追加标注点/辅助线（可能含答案信息）
        base = deepcopy(f)
        base["params"]["points"] = []
        base["params"]["show_aux"] = False
        base["params"]["show_focal_radii"] = False
        return [
            {"figure": base, "label": "曲线与焦点"},
            {"figure": f, "label": "标注点与辅助线"},
        ]

    # 立体类型：顶点字母是题干已知信息（非答案性标注），单帧全标注——
    # 原"轮廓帧（无标注）→标注帧"两帧设计在引导阶段（frame_limit=1）导致学生
    # 看到的图没有任何字母、无法与题目对照（2026-08-30 截图事故）。顶点标注防泄题
    # 由 planner 的"图中要素只含该步骤及之前已知信息"纪律保证，不靠抹字母。
    if kind in _SOLID_TYPES:
        return [{"figure": f, "label": "几何体"}]


def derive_solid_wireframe(fig: dict, *, caption: str = "") -> dict | None:
    """N6 可拖动 3D：把 figure_params 物化为前端 three.js（MathFigure3D）可直接渲染的场景。

    返回 mathFigure3d.normalizeScene 兼容契约：
        {"solids": [{"kind": "polyhedron",
                     "vertices": [{"name": "A", "pos": [x, y, z]}, ...],
                     "edges": [["A", "B"], ...],
                     "faces": [["A", "B", "C"], ...]},
                    {"kind": "sphere", "center": [..], "radius": r}...],
         "caption": str}
    外接球组合（solid+sphere）同时物化两层；球以外类型无法物化返回 None（前端保持静态帧）。
    """
    try:
        f = validate_figure_params(fig)
        polys, spheres, _checks = _build_scene(f)
    except FigureParamsError:
        return None
    solids_out: list[dict] = []
    for p in polys:
        vertices = [
            {"name": n, "pos": [round(float(c), 4) for c in xyz]} for n, xyz in p.vertices.items()
        ]
        names = {v["name"] for v in vertices}
        edges: list[list[str]] = []
        seen_pairs: set[tuple[str, str]] = set()

        def _push_edge(a: str, b: str) -> None:
            if a not in names or b not in names or a == b:
                return
            pair = (min(a, b), max(a, b))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                edges.append([a, b])

        for face in p.faces:
            for i in range(len(face)):
                _push_edge(face[i], face[(i + 1) % len(face)])
        for a, b in p.extra_edges:
            _push_edge(a, b)
        if not vertices:
            continue
        solids_out.append(
            {
                "kind": "polyhedron",
                "vertices": vertices,
                "edges": edges,
                "faces": [list(face) for face in p.faces],
            }
        )
    for sp in spheres:
        solids_out.append(
            {"kind": "sphere", "center": [round(float(c), 4) for c in sp.center],
             "radius": round(float(sp.r), 4)}
        )
    if not solids_out:
        return None
    scene: dict = {"solids": solids_out[:8]}
    if caption:
        scene["caption"] = caption[:40]
    return scene


def render_figure_frames(
    fig: dict,
    *,
    step_no: int | None = None,
    caption: str = "",
    frame_limit: int | None = None,
) -> dict:
    """渲染渐进帧 -> figure 事件 data 载荷（F13 可视化讲解协议）。

    载荷结构（前端 MathFigure.vue 契约）：
        {
          "step_no": int,          # 对应讲解步骤（可选）
          "caption": str,          # 图形说明（可选）
          "frames": [{"data_uri": str, "label": str}, ...],  # 1~2 帧，data:image/svg+xml
          "figure_params": dict,   # 完整参数（调试/审计用，前端不渲染）
        }

    frame_limit：最多取前 N 帧（引导阶段按提示级别限帧防泄题，1=只给构图帧）。
    任何一帧渲染失败或不变量 fatal → 抛 FigureParamsError（调用方丢弃整图，绝不阻断讲解流）。
    """
    frames = derive_figure_frames(fig)
    if frame_limit is not None:
        frames = frames[: max(int(frame_limit), 1)]
    payload_frames: list[dict] = []
    for fr in frames:
        svg = render_figure(fr["figure"])
        problems = check_svg_invariants(fr["figure"], svg)
        fatal = [p for p in problems if p["severity"] == "fatal"]
        if fatal:
            raise FigureParamsError(
                f"帧「{fr['label']}」未通过渲染不变量检查: {fatal[0]['msg']}"
            )
        payload_frames.append({"data_uri": to_data_uri(svg), "label": fr["label"]})
    data: dict = {"frames": payload_frames, "figure_params": fig}
    # N6 可拖动交互：立体类型附带 three.js 线框场景（前端 MathFigure3D 复用渲染）
    try:
        if validate_figure_params(fig)["type"] in _SOLID_TYPES:
            scene = derive_solid_wireframe(fig, caption=caption)
            if scene:
                data["solid"] = scene
    except FigureParamsError:
        pass
    if step_no is not None:
        data["step_no"] = int(step_no)
    if caption:
        data["caption"] = str(caption)[:80]
    return data


# ---------------------------------------------------------------------------
# 结构/几何不变量检查（批量脚本与测试共用）
# ---------------------------------------------------------------------------

def check_svg_invariants(fig: dict, svg: str) -> list[dict]:
    """渲染后自动检查。返回问题列表（空=通过）；severity: fatal（弃用该图）/warning。

    fatal：XML 不合法 / 尺寸越界 / 数值非有限。
    fatal（几何核心）：锥体顶点投影必须严格位于底面上方（本次事故防复发）。
    warning：立体图无虚线隐藏边（视角退化或类型不符时提示）。
    """
    problems: list[dict] = []
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        return [{"code": "xml_parse", "severity": "fatal", "msg": f"SVG 解析失败: {e}"}]
    if not root.tag.endswith("svg"):
        problems.append({"code": "root", "severity": "fatal", "msg": "根元素不是 svg"})
        return problems
    try:
        w, h = int(float(root.get("width", 0))), int(float(root.get("height", 0)))
    except (TypeError, ValueError):
        return [{"code": "size", "severity": "fatal", "msg": "SVG 尺寸缺失"}]
    if not (100 <= w <= 1200 and 90 <= h <= 900):
        problems.append({"code": "size", "severity": "fatal", "msg": f"SVG 尺寸越界 {w}x{h}"})
    for m in re.finditer(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", svg):
        v = float(m.group(0))
        if not math.isfinite(v):
            problems.append({"code": "nonfinite", "severity": "fatal",
                             "msg": f"SVG 含非有限数值 {m.group(0)}"})
            break

    f = validate_figure_params(fig)
    kind = f["type"]
    # 锥体顶点必须在底面上方（从 <text> 字母标注的屏幕位置判断）
    if kind in ("quad_pyramid", "tri_pyramid"):
        apex = f["params"]["apex"]
        base = f["params"]["base"]
        labels = f["labels"] or {}
        def _disp(n):
            return labels.get(n, _default_label(n))

        text_pos: dict[str, float] = {}
        for el in root.iter():
            if el.tag.endswith("text"):
                txt = (el.text or "").strip()
                if txt and el.get("y"):
                    text_pos.setdefault(txt, float(el.get("y")))
        apex_y = text_pos.get(_disp(apex))
        base_ys = [text_pos.get(_disp(n)) for n in base]
        if (
            apex_y is not None
            and all(y is not None for y in base_ys)
            and apex_y >= min(base_ys) - 3
        ):
                problems.append({
                    "code": "apex_below_base", "severity": "fatal",
                    "msg": f"顶点 {apex} 的投影未位于底面上方（y={apex_y:.1f}，"
                           f"底面最高点 y={min(base_ys):.1f}）"})
    # 立体图应有虚线隐藏边
    if kind in _SOLID_TYPES and "stroke-dasharray" not in svg:
        problems.append({"code": "no_hidden_edge", "severity": "warning",
                         "msg": "立体图无虚线隐藏边（视角可能退化）"})
    # 圆锥曲线：标注点与焦点必须真实出现在渲染结果中（图形真实性底线）
    if kind == "conic":
        texts = set()
        for el in root.iter():
            if el.tag.endswith("text"):
                txt = (el.text or "").strip()
                if txt:
                    texts.add(txt)
        labels_map = f["labels"] or {}
        for pt in f["params"]["points"]:
            disp = labels_map.get(pt["label"], pt["label"])
            if disp and disp not in texts:
                problems.append({"code": "conic_point_missing", "severity": "fatal",
                                 "msg": f"标注点 {disp} 未出现在渲染结果中"})
        if f["params"]["show_foci"]:
            need = ("F",) if f["params"]["curve"] == "parabola" else ("F₁", "F₂")
            for t in need:
                if t not in texts:
                    problems.append({"code": "conic_foci_missing", "severity": "fatal",
                                     "msg": f"焦点 {t} 未出现在渲染结果中"})
    return problems


# ---------------------------------------------------------------------------
# LLM 提取参数用的 schema 说明（backfill 脚本复用，单一事实来源）
# ---------------------------------------------------------------------------

FIGURE_SCHEMA_DOC = """## figure_params JSON 结构（只输出 JSON，不要 Markdown 代码块）

{"version":1, "type":"<类型>", "params":{...}, "labels":{可选 顶点名->显示名},
 "view":{可选 视角}, "size":[400,300] 可选}

默认使用斜二测投影（人教版教材画法，顶点恒在底面上方），无需指定 view；
如需真透视可加 "view":{"mode":"axonometric","yaw":-28,"elev":16}。

支持类型与 params（长度单位可自行按比例设定，渲染器会自动缩放适配画布）：
- cube 正方体: {"a":棱长}
- cuboid 长方体: {"a":长,"b":宽,"h":高}
- triangular_prism 三棱柱 ABC-A₁B₁C₁:
   正三棱柱: {"base":"equilateral","side":底面边长,"height":侧棱长}
   直三棱柱(底面直角三角形,直角在A): {"base":"right","ab":AB,"bc":BC,"height":AA₁}
   一般: {"base":"custom","vertices":[[x,y]×3],"height":h}
- quad_pyramid 四棱锥 P-ABCD:
   {"base_w":底面宽,"base_d":底面深,"height":高,"apex":"P","base":["A","B","C","D"]}
   或 {"base_points":[[x,y]×4],"apex_pos":[x,y,z],"apex":"P","base":[...]}
   顶点 P 必须位于底面上方（z>0）；无位置信息时放在底面中心正上方。
- tri_pyramid 三棱锥 P-ABC:
   {"side":底面等边边长,"height":高,"apex":"P","base":["A","B","C"]}
   或 {"base_points":[[x,y]×3],"apex_pos":[x,y,z],"apex":"P","base":[...]}
- tri_frustum 正三棱台 ABC-A₁B₁C₁: {"bottom_side":下底边长,"top_side":上底边长,"height":高}
- sphere 球/外接球: {"r":半径,"center_label":"O","solid":{内接几何体的完整图形对象}}
   例: 长方体顶点都在球面上 -> {"type":"sphere","params":{"r":半径,"solid":{"type":"cuboid","params":{"a":2,"b":1,"h":1}}}}
   正方体棱长a外接球半径 = a·√3/2；长方体√(a²+b²+h²)/2；等边三角形外接圆半径=边长/√3。
- polyhedron 通用多面体: {"vertices":{"A":[x,y,z],...},"faces":[["A","B","C"],...]}
- function 函数图像: {"curves":[{"expr":"x**2-2*x+1","label":"y=x²-2x+1","domain":[-4,4]}],
   "x_range":[-4,4],"y_range":[-3,5],"points":[{"x":1,"y":0,"label":"(1,0)"}],
   "asymptotes":{"x":[1]},"ticks":{"x":1,"y":1}}
   expr 支持: + - * / ** % 括号, sin cos tan asin acos atan sinh cosh tanh exp log log2 log10 sqrt abs fabs floor ceil pow, 常量 pi e tau 与变量 x。乘号必须写 *。
- triangle2d 平面三角形: {"points":{"A":[0,0],"B":[3,0],"C":[0,4]},"right_angle":"A","circumcircle":false}
- conic 圆锥曲线（椭圆/双曲线/抛物线，自动画焦点，可选渐近线/准线/焦半径）:
   椭圆(焦点在x轴): {"curve":"ellipse","axis":"x","a":5,"b":3,"center_label":"O",
     "points":[{"t":50,"label":"M"}]}   // t 为参数角(度)，自动算 x=a·cos t, y=b·sin t
   椭圆(焦点在y轴): 同上加 "axis":"y"；标注点也可直接给 {"x":..,"y":..,"label":..}（必须在曲线上，否则拒绝）
   双曲线: {"curve":"hyperbola","axis":"x","a":3,"b":4,"points":[{"t":30,"label":"M"}]}
     （渐近线自动虚线；左支用 "branch":-1；t 为双曲参数，单位度）
   抛物线: {"curve":"parabola","p":2,"opening":"up","points":[{"x":4,"label":"A"}]}
     （p 为焦准距，开口 up/down/left/right；只给一个坐标，另一个按方程自动算；自动画准线 l）
   焦点三角形/焦半径题：points 给动点（椭圆用 t），F₁F₂ 与焦半径虚线自动呈现；
   注意 a 恒为半长轴/实半轴（焦点在 y 轴的椭圆也给长轴 a、短轴 b）。

顶点命名必须与题干一致（如 P-ABCD 的顶点为 P,A,B,C,D；三棱柱 ABC-A₁B₁C₁ 写 "A1","B1","C1" 会自动显示为 A₁B₁C₁）。
题目没有给出具体数值时：按题干比例设定尺寸（如四棱锥底面正方形 AB=4 -> base_w=4, base_d=4），高度取使图形美观的值（如底面边长的 0.7 倍）。
外接球题目：内接几何体边长按题干数值，球半径按公式精确计算（正方体棱长a -> r=a√3/2；长方体 a,b,h -> r=√(a²+b²+h²)/2；正三棱柱底边a侧棱h -> r=√(h²/4+a²/3)）。"""
