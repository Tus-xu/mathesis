"""确定性数学图形（matplotlib）：数轴 / 函数图 / 韦恩图 / 参数化圆锥曲线。

Agg 后端 + 固定尺寸 + 固定中文字体；输出 PNG 字节（内容寻址入库）。

R8-U：新增 `ellipse_figure` —— 参数化 `math_object`（kind=ellipse）→ PNG。
导出 PPTX 必须内嵌位图（SVG 在 Presenton 容器渲染器下会破图），故与前端
`MathFigure.vue` **同构图、同参数口径**（a/b 唯一事实，c=√(a²−b²) 派生）。
"""
from __future__ import annotations

import io
import math
from functools import lru_cache
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Ellipse  # noqa: E402

from presenton_adapter.engine.fonts import font_path_for  # noqa: E402

DPI = 144

COLOR_INK = "#1E293B"
COLOR_BRAND = "#2563EB"
COLOR_MUTED = "#64748B"
COLOR_LINE = "#CBD5E1"


def _zh_font() -> str | None:
    p = font_path_for("Microsoft YaHei")
    return str(p) if p else None


def _setup() -> None:
    f = _zh_font()
    if f:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False


def number_line(x_min: float, x_max: float, *, open_end: str | None = None, label: str = "") -> bytes:
    """数轴图：x < 10 → open_end=10（空心圈）；x ≥ 2 → closed。"""
    _setup()
    lo = min(x_min, x_max) - max(2.0, abs(x_max - x_min) * 0.25)
    hi = max(x_min, x_max) + max(2.0, abs(x_max - x_min) * 0.25)
    fig, ax = plt.subplots(figsize=(6.0, 1.9), dpi=DPI)
    ax.axhline(0, color=COLOR_INK, lw=2, zorder=1)
    ticks = [t for t in range(int(lo), int(hi) + 1)]
    for t in ticks:
        ax.plot([t, t], [-0.06, 0.06], color=COLOR_INK, lw=1.4, zorder=2)
        if t % 2 == 0:
            ax.text(t, -0.22, str(t), ha="center", va="top", fontsize=9, color=COLOR_MUTED)
    a, b = min(x_min, x_max), max(x_min, x_max)
    ax.plot([a, b], [0, 0], color=COLOR_BRAND, lw=4.5, zorder=3, solid_capstyle="butt")
    ax.plot(b, 0, marker="o", ms=10, mfc="white", mec=COLOR_BRAND, mew=2, zorder=4)
    if label:
        ax.text((a + b) / 2, 0.16, label, ha="center", fontsize=11, color=COLOR_BRAND)
    ax.set_xlim(lo - 0.4, hi + 0.4)
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    return buf.getvalue()


def function_graph(expr_pts: list[tuple[float, float]], xlabel: str, ylabel: str, title: str) -> bytes:
    """函数图：数据点由调用方给定（确定性），不在引擎内随机采样。"""
    _setup()
    fig, ax = plt.subplots(figsize=(5.2, 3.4), dpi=DPI)
    xs = [p[0] for p in expr_pts]
    ys = [p[1] for p in expr_pts]
    ax.plot(xs, ys, color=COLOR_BRAND, lw=2.2)
    ax.axhline(0, color=COLOR_LINE, lw=1)
    ax.axvline(0, color=COLOR_LINE, lw=1)
    ax.set_xlabel(xlabel, fontsize=10, color=COLOR_MUTED)
    ax.set_ylabel(ylabel, fontsize=10, color=COLOR_MUTED)
    ax.set_title(title, fontsize=12, color=COLOR_INK)
    ax.grid(True, color="#EEF2F7", lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    return buf.getvalue()


def venn_two(label_a: str, label_b: str, label_ab: str, title: str) -> bytes:
    """双集合韦恩图（matplotlib 圆形近似，避免额外依赖）。"""
    _setup()
    fig, ax = plt.subplots(figsize=(4.6, 3.2), dpi=DPI)
    r = 1.0
    c1 = (0.68, 0.0)
    c2 = (1.32, 0.0)
    ax.add_patch(plt.Circle(c1, r, color=COLOR_BRAND, alpha=0.16))
    ax.add_patch(plt.Circle(c2, r, color="#8B5CF6", alpha=0.14))
    ax.text(c1[0] - 0.45, 0.0, label_a, fontsize=11, ha="center", color=COLOR_INK)
    ax.text(c2[0] + 0.45, 0.0, label_b, fontsize=11, ha="center", color=COLOR_INK)
    ax.text(1.0, 0.0, label_ab, fontsize=10, ha="center", color=COLOR_INK)
    ax.set_title(title, fontsize=12, color=COLOR_INK)
    ax.set_xlim(-0.55, 2.55)
    ax.set_ylim(-1.2, 1.55)
    ax.set_aspect("equal")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    return buf.getvalue()


def _fmt_num(v: float) -> str:
    """数值 → 标注串（整数不留小数；非整数一位小数）。"""
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def ellipse_figure(math_object: dict[str, Any]) -> bytes:
    """参数化椭圆 → PNG（确定性；与前端 MathFigure.vue 同构图、同参数口径）。

    数学红线（与 app.ppt_v2.domain.spec.MathObjectSpec 同口径）：
    - 唯一事实是 a（长半轴）、b（短半轴），必须 a > b > 0；
    - 半焦距 c = √(a²−b²) **派生**（不落库）；焦点 F₁(−c, 0) / F₂(c, 0)；
    - 等比例投影（set_aspect equal）→ 椭圆不变形，坐标轴刻度与真实数值一致。

    要素随 toggles 生效：坐标轴 / 焦点 / 辅助线（PF₁、PF₂）/ 数值标注。
    参数非法或类型不支持 → 抛 ValueError（调用方转可见失败，绝不静默出空图）。
    """
    _setup()
    if not isinstance(math_object, dict):
        raise ValueError("math_object 必须是对象")
    kind = str(math_object.get("kind") or "")
    if kind != "ellipse":
        raise ValueError(f"引擎暂不支持 math_object.kind={kind!r}（spec 仅支持 ellipse）")
    params = math_object.get("params") or {}
    try:
        a = float(params["a"])
        b = float(params["b"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"椭圆 math_object 缺少数值参数 a/b: {params!r}") from exc
    if not (a > b > 0):
        raise ValueError(f"椭圆参数必须满足 a > b > 0（数学红线），收到 a={a}, b={b}")
    c = (a * a - b * b) ** 0.5

    toggles = math_object.get("toggles") or {}
    show_foci = toggles.get("show_foci", True) is not False
    show_axes = toggles.get("show_axes", True) is not False
    show_aux = toggles.get("show_aux_lines", True) is not False
    show_labels = toggles.get("show_value_labels", False) is True

    style = math_object.get("style") or {}
    color = str(style.get("color") or COLOR_BRAND)
    stroke = float(style.get("stroke_px") or 2.0)
    dash = str(style.get("dash") or "solid")

    ink = "#334155"
    aux = "#8899B3"
    red = "#DC2626"
    lw = max(1.0, min(6.0, stroke * 0.9))
    curve_ls: Any = {"dashed": (0, (6, 4)), "dotted": (0, (1, 3))}.get(dash, "-")

    lim_x = max(a, c) * 1.26
    lim_y = b * 1.62
    fig, ax = plt.subplots(figsize=(5.1, 3.2), dpi=DPI)
    ax.set_xlim(-lim_x, lim_x)
    ax.set_ylim(-lim_y, lim_y)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    # ① 坐标轴（深色墨线 + 箭头 + 真实刻度 ±a / ±c / ±b + O / x / y）
    if show_axes:
        ax.annotate("", xy=(lim_x, 0), xytext=(-lim_x, 0),
                    arrowprops={"arrowstyle": "-|>", "color": ink, "lw": 1.5,
                                "shrinkA": 0, "shrinkB": 0})
        ax.annotate("", xy=(0, lim_y), xytext=(0, -lim_y),
                    arrowprops={"arrowstyle": "-|>", "color": ink, "lw": 1.5,
                                "shrinkA": 0, "shrinkB": 0})
        tick = lim_y * 0.032
        for tx in sorted({-a, -c, c, a}):
            ax.plot([tx, tx], [-tick, tick], color=ink, lw=1.2, zorder=2)
        for ty in (-b, b):
            ax.plot([-tick, tick], [ty, ty], color=ink, lw=1.2, zorder=2)
        ax.text(lim_x * 0.98, lim_y * 0.06, "$x$", color=ink, fontsize=13, ha="right", va="bottom")
        ax.text(lim_x * 0.035, lim_y * 0.95, "$y$", color=ink, fontsize=13, ha="left", va="top")
        ax.text(-lim_x * 0.02, -lim_y * 0.05, "$O$", color=ink, fontsize=12, ha="right", va="top")

    # ② 椭圆本体（参数实时生成；等比例 → 不变形）
    ax.add_patch(Ellipse((0.0, 0.0), 2 * a, 2 * b, fill=False, edgecolor=color,
                         linewidth=lw, linestyle=curve_ls, zorder=3))

    # ③ 辅助线 PF₁ / PF₂（参数角 58°，避开顶点与焦点）+ 点 P
    if show_aux:
        theta = math.radians(58.0)
        pxv, pyv = a * math.cos(theta), b * math.sin(theta)
        for fx in (-c, c):
            ax.plot([fx, pxv], [0.0, pyv], color=aux, lw=1.0, ls=(0, (5, 4)), zorder=2)
        ax.plot([pxv], [pyv], marker="o", ms=4, color="#1F2937", zorder=4)
        p_label = f"$P({_fmt_num(pxv)},{_fmt_num(pyv)})$" if show_labels else "$P(x,y)$"
        ax.text(pxv + lim_x * 0.035, pyv + lim_y * 0.045, p_label, fontsize=11, color="#0F172A")

    # ④ 焦点红点 F₁(−c, 0) / F₂(c, 0)（数学排版标签，禁止数字贴尾乱码）
    if show_foci:
        for fx, name in ((-c, "F_1"), (c, "F_2")):
            ax.plot([fx], [0.0], marker="o", ms=6.5, color=red, zorder=5)
            sign = "-" if fx < 0 else ""
            label = (f"${name}({sign}{_fmt_num(abs(fx))},0)$" if show_labels
                     else f"${name}({sign}c,0)$")
            ax.text(fx, -lim_y * 0.17, label, color="#0F172A", fontsize=11,
                    ha="center", va="top")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    return buf.getvalue()


# spec 支持的 kind（app.ppt_v2.domain.spec.MathObjectSpec.kind 的 Literal 集合）
_MATH_OBJECT_RENDERERS = {"ellipse": ellipse_figure}


@lru_cache(maxsize=64)
def _math_object_figure_cached(canonical: str) -> bytes:
    """R3d：同参数 math_object 图形字节缓存（内容寻址语义：参数同 → 图同）。

    逐页渲染/重复导出不再重复跑 matplotlib；参数或开关任何变化都会得到不同
    canonical 串 → 重新渲染，不复用过期图。
    """
    import json

    return math_object_figure(json.loads(canonical))


def math_object_figure(math_object: dict[str, Any]) -> bytes:
    """参数化 math_object → PNG 字节（仅 spec 已支持的 kind；未知 kind 可见失败）。"""
    kind = str((math_object or {}).get("kind") or "")
    renderer = _MATH_OBJECT_RENDERERS.get(kind)
    if renderer is None:
        raise ValueError(f"引擎不支持 math_object.kind={kind!r}（已支持: {sorted(_MATH_OBJECT_RENDERERS)}）")
    return renderer(math_object)


def math_object_figure_cached(math_object: dict[str, Any]) -> bytes:
    """带缓存入口（API 层用）：canonical JSON 为键，确定性缓存。"""
    import json

    canonical = json.dumps(math_object or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        return _math_object_figure_cached(canonical)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - 缓存层不掩盖渲染失败
        raise ValueError(f"math_object 渲染失败: {type(exc).__name__}: {exc}") from exc


def circle_distance_figure(title: str) -> bytes:
    """圆：到定点距离等于定长的点集（人教 A 版 1.1 实例(4)）。"""
    _setup()
    import numpy as np

    fig, ax = plt.subplots(figsize=(3.6, 3.4), dpi=DPI)
    t = np.linspace(0, 2 * np.pi, 240)
    ax.plot(np.cos(t), np.sin(t), color=COLOR_BRAND, lw=2.2)
    ax.plot([0], [0], marker="+", ms=14, color=COLOR_INK, mew=1.6)
    ax.text(0.04, 0.05, "定点 P", fontsize=10, color=COLOR_INK)
    ax.plot([0, np.cos(np.pi / 4)], [0, np.sin(np.pi / 4)], color=COLOR_MUTED, lw=1.2, ls="--")
    ax.text(0.42, 0.30, "定长 d", fontsize=10, color=COLOR_MUTED)
    ax.set_title(title, fontsize=11, color=COLOR_INK)
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-1.45, 1.45)
    ax.set_aspect("equal")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    return buf.getvalue()
