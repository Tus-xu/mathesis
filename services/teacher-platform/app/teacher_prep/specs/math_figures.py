"""确定性数学图形（R3e 产物投影层）：参数化 math_object → PNG（课件资产 + 教案板书同源）。

铁律（KERNEL-RUBRIC §1.2 / FROZEN-CONTRACT §F.4）：
- **确定性**：同参数 → 同图；渲染是纯函数（无时间戳/随机），sha256 可跨进程复现；
- **只存独立参数**：椭圆 {a, b}（c=√(a²−b²) 派生）、直线 {k, x0, y0}（截距 b=y0−k·x0 派生）、
  直线系 {b}（样本斜率是展示常量）、二次函数 {a, b, c}（顶点/对称轴/交点全派生）；
  派生值不落库；
- **同源**：课件图形资产与教案板书中列共用本模块（同一参数解析函数 → 同一图）；
- **不造第二套椭圆绘制器**：ellipse 复用 FROZEN `app.export.pptx_engine._render_geometry_png`
  （KEEP > ADAPT）；本模块新增的只有直线 / 直线系 / 二次函数（此前无任何实现）。

图形态（PPT.png 精神：坐标系 + 关键点 + 辅助线 + 公式卡）：
- line：坐标系 + 过 P₁(x₁,y₁) 斜率为 k 的直线 + 斜率三角形 + 红色定点 + 叠式分式公式卡；
- line_family：坐标系 + 过定点 (0,b) 的直线系 y=kx+b（k 取展示常量）+ 公式卡；
- quadratic：坐标系 + 抛物线 y=ax²+bx+c + 对称轴 x=-b/(2a) + 顶点标注 + 开口方向箭头
  + x 轴交点（Δ≥0）+ 一般式/顶点式公式卡（系数与图形同源）；
- formula 卡用 matplotlib mathtext 真排版（叠式分式/上下标），不是源码贴字。
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from typing import Any

# 渲染字节缓存（进程内纯函数 memo）：投影算 sha 与落 MinIO 的字节必须逐字节一致。
_BYTES_CACHE: dict[str, bytes] = {}

FIGURE_NS = "math-figures/v5"

# G6：v2 起图形标注改为「点偏移（pt）+ 直线异侧」排版（字节变、参数同）——
# 对象键必须随版本走（同键旧字节被新字节覆盖会让旧 deck 的 sha256 校验失败、图形降级）；
# 解析端同时接受历史 v1 键（旧 deck 的 object_key 仍可读参重渲，渲染结果按其落库 sha 裁决）。
_FIGURE_KEY_RE = re.compile(r"math-figures/v\d+/([a-z_]+)__([A-Za-z0-9_.\-]+)\.png$")

COLOR_INK = "#334155"
COLOR_BRAND = "#2563EB"
COLOR_RED = "#DC2626"
COLOR_AUX = "#8899B3"
COLOR_MUTED = "#64748B"
COLOR_CARD_BG = "#EFF4FF"
COLOR_CARD_EDGE = "#BFD3F2"

# 直线系展示常量（与8201 椭圆图 θ=58° 同性质：展示常量，不是数学事实）
_FAMILY_KS = (-2.0, -1.0, 1.0, 2.0)


def _fmt(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:g}"


def _setup() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


# ── 参数 ↔ 资产对象键（确定性可重渲）────────────────────────────────────────


def object_key_for(kind: str, params: dict[str, float]) -> str:
    """内容寻址对象键：把独立参数编进键 → 任何进程可从键重渲同一张图。"""
    parts = [f"{name}_{_fmt(float(params[name]))}" for name in sorted(params)]
    return f"{FIGURE_NS}/{kind}__" + "__".join(parts) + ".png"


def params_from_object_key(key: str) -> tuple[str, dict[str, float]] | None:
    m = _FIGURE_KEY_RE.match(key or "")
    if not m:
        return None
    kind = m.group(1)
    params: dict[str, float] = {}
    for part in m.group(2).split("__"):
        name, _, value = part.rpartition("_")
        try:
            params[name] = float(value)
        except ValueError:
            return None
    return kind, params


# ── 渲染 ────────────────────────────────────────────────────────────────────


def _fig_to_png(fig: Any) -> bytes:
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    return buf.getvalue()


def _formula_card(
    fig: Any,
    ax: Any,
    lines: list[str],
    *,
    x: float = 0.985,
    y: float = 0.965,
    ha: str = "right",
    va: str = "top",
) -> None:
    """图内公式卡（mathtext 真排版：叠式分式/上下标）。

    默认右上角；曲线会经过右上角的图（数列/指数/对数）由调用方指定空闲角落，
    避免公式卡压住真实曲线或数据点。
    """
    import matplotlib.pyplot as plt

    text = "\n".join(lines)
    ax.text(
        x, y, text, transform=ax.transAxes, ha=ha, va=va,
        fontsize=11.5, color="#0F172A", linespacing=1.55,
        fontname="Microsoft YaHei",
        bbox={"boxstyle": "round,pad=0.55", "facecolor": COLOR_CARD_BG,
              "edgecolor": COLOR_CARD_EDGE, "linewidth": 1.0},
    )
    del fig, plt


def line_figure(params: dict[str, float]) -> bytes:
    """过 P₁(x₁,y₁)、斜率 k 的直线图（坐标系 + 定点 + 斜率三角 + 公式卡）。"""
    _setup()
    import matplotlib.pyplot as plt

    try:
        k = float(params["k"])
        x0 = float(params["x0"])
        y0 = float(params["y0"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"直线 math_object 缺少数值参数 k/x0/y0: {params!r}") from exc
    if not math.isfinite(k):
        raise ValueError(f"直线斜率必须有限: k={k}")

    span = 5.0
    lo_x, hi_x = min(-4.0, x0 - span), max(4.0, x0 + span)
    ys = [y0 + k * (x - x0) for x in (lo_x, hi_x)]
    lo_y, hi_y = min(ys + [y0, 0.0]) - 2.2, max(ys + [y0, 0.0]) + 2.2

    fig, ax = plt.subplots(figsize=(5.8, 3.7))
    ax.set_xlim(lo_x, hi_x)
    ax.set_ylim(lo_y, hi_y)
    ax.axis("off")

    # ① 坐标轴（箭头 + 刻度 + O/x/y）
    ax.annotate("", xy=(hi_x, 0), xytext=(lo_x, 0),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.annotate("", xy=(0, hi_y), xytext=(0, lo_y),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.text(hi_x - 0.15, 0.28, "$x$", color=COLOR_INK, fontsize=12, ha="right")
    ax.text(0.22, hi_y - 0.35, "$y$", color=COLOR_INK, fontsize=12, ha="left")
    ax.text(-0.18, -0.42, "$O$", color=COLOR_INK, fontsize=11, ha="right")

    # ② 直线本体
    ax.plot([lo_x, hi_x], ys, color=COLOR_BRAND, lw=2.2, zorder=3)

    # ③ 斜率三角形（Δx=1 单位的相似三角；虚线辅助 + 标注）
    # G6 产物复审修复（标注-直线零重叠、标注挂靠三角边、P₁ 贴红点）：
    # 偏移一律用「pt 点偏移」（annotate + textcoords="offset points"），与数据纵横比无关；
    # - Δx 标签贴横边中点、放在直线**另一侧**（k<0 → 边上方；k>0 → 边下方）→ 永不被直线贯穿；
    # - Δy 标签贴竖边中点右侧：直线在该高度恒在 x₀+0.5 处（竖边左侧）→ 右侧恒无直线；
    # - P₁ 标签贴红点小偏移，放在直线空闲象限（k<0 → 左下；k>0 → 左上），不再偏离红点。
    dx = 1.0 if k != 0 else 0.0
    px2, py2 = x0 + dx, y0 + k * dx
    ax.annotate("", xy=(px2, y0), xytext=(x0, y0),
                arrowprops={"arrowstyle": "<->", "color": COLOR_AUX, "lw": 1.1,
                            "linestyle": "--", "shrinkA": 0, "shrinkB": 0}, zorder=2)
    ax.annotate("", xy=(px2, py2), xytext=(px2, y0),
                arrowprops={"arrowstyle": "<->", "color": COLOR_AUX, "lw": 1.1,
                            "linestyle": "--", "shrinkA": 0, "shrinkB": 0}, zorder=2)
    label_bg = {"boxstyle": "square,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.9}
    if dx:
        dx_side = 1.0 if k < 0 else -1.0  # 直线相对横边所在侧的反侧
        ax.annotate(
            r"$\Delta x=1$", xy=((x0 + px2) / 2, y0),
            xytext=(0, dx_side * 7), textcoords="offset points",
            ha="center", va="bottom" if dx_side > 0 else "top",
            fontsize=9.5, color=COLOR_MUTED, zorder=6, bbox=label_bg,
        )
        ax.annotate(
            r"$\Delta y=k$", xy=(px2, y0 + 0.3 * k * dx),
            xytext=(7, 0), textcoords="offset points",
            ha="left", va="center",
            fontsize=9.5, color=COLOR_MUTED, zorder=6, bbox=label_bg,
        )

    # ④ 定点 P₁(x₁,y₁) 红点（数学排版标签；紧贴红点，置于直线异侧）
    ax.plot([x0], [y0], marker="o", ms=6.5, color=COLOR_RED, zorder=5)
    # k<0：直线沿左上/右下穿过 → 标签放左上（远离 x 轴与直线）；k>0 → 左下。
    # 旧实现 k<0 放左下，P₁ 靠近 x 轴时标签会压在坐标轴上（产物复审实测）。
    p_side = 1.0 if k < 0 else -1.0
    ax.annotate(
        r"$P_1(x_1,y_1)$", xy=(x0, y0),
        xytext=(-8, p_side * 10), textcoords="offset points",
        ha="right", va="bottom" if p_side > 0 else "top",
        fontsize=11, color="#0F172A", zorder=6, bbox=label_bg,
    )

    # ⑤ 公式卡（叠式分式点斜式 + 斜率公式，mathtext 真排版）
    _formula_card(fig, ax, [
        r"$y - y_1 = k(x - x_1)$",
        r"$k = \frac{y_2 - y_1}{x_2 - x_1}$",
    ])
    return _fig_to_png(fig)


def line_family_figure(params: dict[str, float]) -> bytes:
    """直线系 y = kx + b（过定点 (0,b)；k 取展示常量）+ 公式卡。"""
    _setup()
    import matplotlib.pyplot as plt

    try:
        b = float(params["b"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"直线系 math_object 缺少数值参数 b: {params!r}") from exc

    fig, ax = plt.subplots(figsize=(5.8, 3.7))
    lo_x, hi_x = -4.4, 4.4
    lo_y = min(b - 2.2, -4.0)
    hi_y = max(b + 2.2, 4.0)
    ax.set_xlim(lo_x, hi_x)
    ax.set_ylim(lo_y, hi_y)
    ax.axis("off")

    ax.annotate("", xy=(hi_x, 0), xytext=(lo_x, 0),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.annotate("", xy=(0, hi_y), xytext=(0, lo_y),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.text(hi_x - 0.15, 0.28, "$x$", color=COLOR_INK, fontsize=12, ha="right")
    ax.text(0.22, hi_y - 0.35, "$y$", color=COLOR_INK, fontsize=12, ha="left")

    for k in _FAMILY_KS:
        xs = [lo_x, hi_x]
        ys = [k * x + b for x in xs]
        ax.plot(xs, ys, color=COLOR_BRAND, lw=1.6, alpha=0.75, zorder=3)

    # 公共定点 (0,b) 红点
    ax.plot([0.0], [b], marker="o", ms=6.5, color=COLOR_RED, zorder=5)
    ax.text(0.18, b + 0.3, r"$(0,b)$", fontsize=11, color="#0F172A", ha="left", va="bottom")

    _formula_card(fig, ax, [
        r"$y = kx + b$",
        r"$k \in \mathbb{R}$ 时恒过 $(0,b)$",
    ])
    return _fig_to_png(fig)


def ellipse_png(params: dict[str, float]) -> bytes:
    """椭圆图：复用 FROZEN legacy 内核的确定性几何渲染（不建第二套椭圆绘制器）。"""
    from app.export.pptx_engine import _render_geometry_png  # noqa: PLC2701 - KEEP/ADAPT 复用

    data = _render_geometry_png("conic/ellipse", {"a": float(params["a"]), "b": float(params["b"])})
    if not data:
        raise RuntimeError("椭圆图形渲染不可用（legacy 几何渲染器未出图）")
    return data


# ── 二次函数（抛物线）：系数确定性解析 + matplotlib 渲染 ─────────────────────
# 契约约束：MathObjectSpec.kind 不含 quadratic（8200 deck.py 锁 ellipse）→ 二次函数
# **不挂 math_object**，只走 MATH_RENDER 资产通道（asset_for → ensure_asset_stored →
# resolve 注入 figure 槽 → 8201 取字节；渲染端零改动）。参数只存独立系数 {a, b, c}。
QUADRATIC_BASELINE: dict[str, float] = {"a": 1.0, "b": 0.0, "c": 0.0}  # 课堂基线 y = x²

# 系数数字 token（最长优先）：2 / 0.5 / (1)/(2)（\frac 展开形）/ 1/2（裸分式）
_QUAD_MAG = r"(?:\(\d+(?:\.\d+)?\)/\(\d+(?:\.\d+)?\)|\d+(?:\.\d+)?/\d+(?:\.\d+)?|\d+(?:\.\d+)?)"
# y = ax² + bx + c 一般式（缺项可省；a 缺省为 1；b 项允许 x/分母 变体）
# 组：1=signA 2=magA 3=signB 4=magB 5=b 的 x 分母 6=signC 7=magC
_QUAD_EQ_RE = re.compile(
    r"y\s*=\s*"
    rf"([+-]?)\s*({_QUAD_MAG})?\s*\*?\s*x\s*(?:²|\^\s*\{{?2\}}?)"
    rf"(?:\s*([+-])\s*({_QUAD_MAG})?\s*\*?\s*x(?:\s*/\s*(\d+(?:\.\d+)?))?(?!\s*(?:²|\^)))?"
    rf"(?:\s*([+-])\s*({_QUAD_MAG}))?"
)


def _quad_mag_value(mag: str | None) -> float | None:
    """系数数字 token → 数值（2 / 0.5 / (1)/(2) / 1/2）；空 token → None。"""
    if mag is None:
        return None
    m = re.fullmatch(r"\((\d+(?:\.\d+)?)\)/\((\d+(?:\.\d+)?)\)", mag)
    if m:
        return float(m.group(1)) / float(m.group(2))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", mag)
    if m:
        return float(m.group(1)) / float(m.group(2))
    return float(mag)


def quadratic_coeffs_from_text(text: str) -> dict[str, float] | None:
    """从一段文本解析 y=ax²+bx+c 的系数 {a, b, c}（缺项补 0；支持分式系数）。

    只认**显式数字**系数（x² 缺系数按 1；symbolic 的 y=ax²+bx+c → a=1, b=0, c=0 命中
    基础抛物线）；首个一般式命中即返回（文档序确定）。解析不出任何一般式 → None
    （不猜测；调用方按课堂基线 y = x² 降级）。顶点式 y=a(x−h)²+k 不在本口径内。
    """
    if not text:
        return None
    from .math_text import latex_to_unicode  # noqa: PLC0415 - 与本模块一致：重依赖函数内惰性导入

    s = str(text)
    for a, bb in (("−", "-"), ("＋", "+"), ("－", "-")):  # 全角/Unicode 运算符归一
        s = s.replace(a, bb)
    s = re.sub(r"\s+", "", latex_to_unicode(s))
    for m in _QUAD_EQ_RE.finditer(s):
        sign_a, mag_a, sign_b, mag_b, x_den, sign_c, mag_c = m.groups()
        a = _quad_mag_value(mag_a)
        a = 1.0 if a is None else a
        if sign_a == "-":
            a = -a
        if a == 0:
            continue  # 显式 0 系数不是抛物线 → 看下一段
        if sign_b is None:
            b = 0.0  # 缺 bx 项
        else:
            b = _quad_mag_value(mag_b)
            b = 1.0 if b is None else b
            if x_den:
                b = b / float(x_den)
            if sign_b == "-":
                b = -b
        c = 0.0 if sign_c is None else _quad_mag_value(mag_c)
        c = 0.0 if c is None else c
        if sign_c == "-":
            c = -c
        return {"a": round(a, 6), "b": round(b, 6), "c": round(c, 6)}
    return None


def _quad_general_tex(a: float, b: float, c: float) -> str:
    """y = ax² + bx + c 的 mathtext 串（系数 1 省略、符号规范；与图形同参数）。"""
    lead = "" if a == 1 else ("-" if a == -1 else _fmt(a))
    out = f"{lead}x^2"
    if b:
        mag = "" if abs(b) == 1 else _fmt(abs(b))
        out += f" + {mag}x" if b > 0 else f" - {mag}x"
    if c:
        out += f" + {_fmt(c)}" if c > 0 else f" - {_fmt(abs(c))}"
    return out


def _quad_vertex_tex(a: float, h: float, k: float) -> str:
    """顶点式 y = a(x − h)² + k 的 mathtext 串（h = -b/(2a)，k = f(h)；派生值不上键）。

    h = 0 时退化为 y = ax²（不产出「(x − 0)²」这类非教材写法）。
    """
    lead = "" if a == 1 else ("-" if a == -1 else _fmt(a))
    if h == 0:
        out = f"{lead}x^2"
    else:
        inner = f"x - {_fmt(h)}" if h > 0 else f"x + {_fmt(abs(h))}"
        out = f"{lead}({inner})^2"
    if k:
        out += f" + {_fmt(k)}" if k > 0 else f" - {_fmt(abs(k))}"
    return out


def quadratic_figure(params: dict[str, float]) -> bytes:
    """二次函数 y = ax² + bx + c 抛物线图（坐标系 + 顶点/对称轴 + 开口方向 + x 轴交点 + 公式卡）。

    数学红线（确定性）：顶点 x = -b/(2a)（a≠0 恒有定义）；开口方向由 a 的符号决定；
    Δ = b² − 4ac > 0 → 两个 x 轴交点；Δ = 0 → 唯一交点即顶点切点（yv = -Δ/(4a) = 0，
    顶点标注已表达该坐标，不重复贴点）；Δ < 0 → 无交点（不画不标，绝不编造）。
    """
    _setup()
    import matplotlib.pyplot as plt

    try:
        a = float(params["a"])
        b = float(params["b"])
        c = float(params["c"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"二次函数 math_object 缺少数值参数 a/b/c: {params!r}") from exc
    if a == 0 or not all(math.isfinite(v) for v in (a, b, c)):
        raise ValueError(f"二次函数系数必须非零有限（a≠0）: a={a}, b={b}, c={c}")

    def f(x: float) -> float:
        return a * x * x + b * x + c

    xv = -b / (2.0 * a)  # 顶点横坐标
    yv = f(xv)
    disc = b * b - 4.0 * a * c  # Δ
    opens_up = a > 0

    roots: list[float] = []
    if disc > 0:
        sq = math.sqrt(disc)
        roots = sorted({round((-b - sq) / (2.0 * a), 6), round((-b + sq) / (2.0 * a), 6)})

    # 视窗：覆盖顶点 / y 截距 / x 轴交点，最小跨度 6（确定性）
    pts_x = [0.0, xv, *roots]
    lo_x = min(pts_x) - 2.0
    hi_x = max(pts_x) + 2.0
    if hi_x - lo_x < 6.0:
        mid = (lo_x + hi_x) / 2.0
        lo_x, hi_x = mid - 3.0, mid + 3.0
    n = 240
    xs = [lo_x + (hi_x - lo_x) * i / (n - 1) for i in range(n)]
    ys = [f(x) for x in xs]
    lo_y = min(ys + [0.0, yv, c]) - 1.4
    hi_y = max(ys + [0.0, yv, c]) + 1.4

    fig, ax = plt.subplots(figsize=(5.8, 3.7))
    ax.set_xlim(lo_x, hi_x)
    ax.set_ylim(lo_y, hi_y)
    ax.axis("off")
    label_bg = {"boxstyle": "square,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.9}

    # ① 坐标轴（箭头 + O/x/y；与 line 同款）
    ax.annotate("", xy=(hi_x, 0), xytext=(lo_x, 0),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.annotate("", xy=(0, hi_y), xytext=(0, lo_y),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.text(hi_x - 0.15, 0.28, "$x$", color=COLOR_INK, fontsize=12, ha="right")
    ax.text(0.22, hi_y - 0.35, "$y$", color=COLOR_INK, fontsize=12, ha="left")
    ax.text(-0.18, -0.42, "$O$", color=COLOR_INK, fontsize=11, ha="right")

    # ② 抛物线本体
    ax.plot(xs, ys, color=COLOR_BRAND, lw=2.2, zorder=3)

    # ③ 对称轴 x = -b/(2a)（虚线，向开口方向延伸）+ 开口方向箭头与文字
    axis_y_end = hi_y if opens_up else lo_y
    ax.plot([xv, xv], [yv, axis_y_end], color=COLOR_AUX, lw=1.1, ls=(0, (5, 4)), zorder=2)
    y_a0 = yv + 0.12 * (axis_y_end - yv)
    y_a1 = yv + 0.30 * (axis_y_end - yv)
    ax.annotate("", xy=(xv, y_a1), xytext=(xv, y_a0),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_RED, "lw": 1.8,
                            "shrinkA": 0, "shrinkB": 0}, zorder=4)
    x_side = 9 if xv <= (lo_x + hi_x) / 2.0 else -9
    ax.annotate("开口向上" if opens_up else "开口向下", xy=(xv, y_a1),
                xytext=(x_side, 0), textcoords="offset points",
                ha="left" if x_side > 0 else "right", va="center",
                fontsize=10, color=COLOR_RED, zorder=6, bbox=label_bg)

    # ④ 顶点红点 + 坐标标注（置于开口反向侧：远离两臂，也不与开口文字同高）
    ax.plot([xv], [yv], marker="o", ms=6.5, color=COLOR_RED, zorder=5)
    ax.annotate(f"顶点 ({_fmt(xv)}, {_fmt(yv)})", xy=(xv, yv),
                xytext=(0, -12 if opens_up else 12), textcoords="offset points",
                ha="center", va="top" if opens_up else "bottom",
                fontsize=10.5, color="#0F172A", zorder=6, bbox=label_bg)

    # ⑤ x 轴交点（Δ > 0 两点；标签一律放交点**左上/左下**侧（右缘零溢出、不撞 x 轴
    # 箭头与 x 记号），纵向放**背离曲线一侧**：开口向上 → 轴线下方，开口向下 → 轴线上方；
    # Δ = 0 唯一交点即顶点切点，见④）
    for r in roots:
        ax.plot([r], [0.0], marker="o", ms=6.5, color=COLOR_RED, zorder=5)
        dy = -7 if opens_up else 7
        ax.annotate(f"({_fmt(r)}, 0)", xy=(r, 0.0),
                    xytext=(-9, dy), textcoords="offset points",
                    ha="right", va="top" if dy < 0 else "bottom",
                    fontsize=9.5, color="#0F172A", zorder=6, bbox=label_bg)

    # ⑥ 公式卡（一般式 + 顶点式，mathtext 真排版；系数与图形同源；两式同形则只留一般式）
    card = [f"$y = {_quad_general_tex(a, b, c)}$"]
    vertex_tex = f"$y = {_quad_vertex_tex(a, xv, yv)}$"
    if vertex_tex != card[0]:
        card.append(vertex_tex)
    _formula_card(fig, ax, card)
    return _fig_to_png(fig)


# ── 幂函数图象族：教材「五个常见幂函数的图象与性质」页的确定性配图 ─────────────
# 走 MATH_RENDER 资产通道（同 line/quadratic：8200 deck.py 的 MathObjectSpec.kind 契约
# 不含 power → 不挂 slide.math_object，只进 slide.assets）。
POWER_FAMILY_N = 5.0
_POWER_CURVES: tuple[tuple[str, str, str], ...] = (
    # (mathtext 标签, 颜色, 曲线键)
    (r"$y=x$", "#2563EB", "x"),
    (r"$y=x^2$", "#DC2626", "x2"),
    (r"$y=x^3$", "#16A34A", "x3"),
    (r"$y=x^{1/2}$", "#EA580C", "sqrt"),
    (r"$y=x^{-1}$", "#7C3AED", "inv"),
)


def power_figure(params: dict[str, float]) -> bytes:
    """五个常见幂函数图象族（y=x / y=x² / y=x³ / y=x^(1/2) / y=x⁻¹）。

    教材标准呈现：同一坐标系下的图象族对比。数学红线（确定性、零编造）：
    - y=x^(1/2) 定义域 x≥0 → 只画右分支；
    - y=x⁻¹ 在 x=0 无定义 → 左右两支**断开**绘制（绝不跨原点连线伪造连续）。
    """
    _setup()
    import matplotlib.pyplot as plt

    n = int(round(float(params.get("n", POWER_FAMILY_N))))
    if n != 5:
        raise ValueError(f"幂函数图象族当前只支持五个常见幂函数（n=5），收到 n={n}")

    lo, hi = -2.2, 2.2
    top, bottom = 5.2, -2.6
    fig, ax = plt.subplots(figsize=(5.8, 3.9))
    ax.set_xlim(lo, hi)
    ax.set_ylim(bottom, top)
    ax.axis("off")

    # ① 坐标轴（箭头 + 刻度 + O/x/y；与 line_figure 同风格）
    ax.annotate("", xy=(hi, 0), xytext=(lo, 0),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.annotate("", xy=(0, top), xytext=(0, bottom),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.text(hi - 0.12, 0.18, "$x$", color=COLOR_INK, fontsize=12, ha="right")
    ax.text(0.14, top - 0.34, "$y$", color=COLOR_INK, fontsize=12, ha="left")
    ax.text(-0.16, -0.34, "$O$", color=COLOR_INK, fontsize=11, ha="right")
    for t in (-2, -1, 1, 2):
        ax.plot([t, t], [-0.08, 0.08], color=COLOR_INK, lw=1.0)
        ax.text(t, -0.44, f"${t}$", color=COLOR_MUTED, fontsize=10, ha="center")
    for t in (1, 2, 3, 4):
        ax.plot([-0.06, 0.06], [t, t], color=COLOR_INK, lw=1.0)
        ax.text(-0.18, t, f"${t}$", color=COLOR_MUTED, fontsize=10, ha="right", va="center")

    # ② 五条曲线（定义域感知：右分支 / 断开绘制）
    xs = [lo + (hi - lo) * i / 240 for i in range(241)]
    pos = [x for x in xs if x >= 0.0]
    seen: set[str] = set()
    for label, color, key in _POWER_CURVES:
        if key == "x":
            segs = [(xs, list(xs))]
        elif key == "x2":
            segs = [(xs, [x * x for x in xs])]
        elif key == "x3":
            segs = [(xs, [x ** 3 for x in xs])]
        elif key == "sqrt":
            segs = [(pos, [math.sqrt(x) for x in pos])]
        else:  # inv：x=0 无定义 → 两支断开
            left = [x for x in xs if x <= -0.18]
            right = [x for x in xs if x >= 0.18]
            segs = [(left, [1.0 / x for x in left]), (right, [1.0 / x for x in right])]
        for seg_x, seg_y in segs:
            ax.plot(seg_x, seg_y, color=color, lw=2.0,
                    label=label if label not in seen else None)
            seen.add(label)

    ax.legend(loc="lower right", frameon=True, fontsize=10.0, borderpad=0.5, ncol=2,
              labelspacing=0.38, handlelength=1.7, columnspacing=1.1,
              facecolor=COLOR_CARD_BG, edgecolor=COLOR_CARD_EDGE)
    return _fig_to_png(fig)


# ── 数列 / 集合 / 常见函数图象（R22：从“只给代数课配图”扩到真实课题）──────────
# 全部走 MATH_RENDER 资产通道（不挂 slide.math_object；8200 契约只锁 ellipse）。
# 图形只表达确定性数学事实：点列/容斥/函数曲线，不画装饰性插画。


def _new_axes(lo_x: float, hi_x: float, lo_y: float, hi_y: float, *, figsize=(5.8, 3.7)):
    _setup()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_xlim(lo_x, hi_x)
    ax.set_ylim(lo_y, hi_y)
    ax.axis("off")
    return fig, ax


def _draw_axes(ax: Any, lo_x: float, hi_x: float, lo_y: float, hi_y: float) -> None:
    ax.annotate("", xy=(hi_x, 0), xytext=(lo_x, 0),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.annotate("", xy=(0, hi_y), xytext=(0, lo_y),
                arrowprops={"arrowstyle": "-|>", "color": COLOR_INK, "lw": 1.4,
                            "shrinkA": 0, "shrinkB": 0})
    ax.text(hi_x - 0.12, 0.22, "$x$", color=COLOR_INK, fontsize=12, ha="right")
    ax.text(0.16, hi_y - 0.32, "$y$", color=COLOR_INK, fontsize=12, ha="left")
    ax.text(-0.16, -0.34, "$O$", color=COLOR_INK, fontsize=11, ha="right")


def _sequence_figure(params: dict[str, float], *, geometric: bool) -> bytes:
    try:
        a1 = float(params["a1"])
        n = int(round(float(params["n"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"数列图形缺少参数 a1/n: {params!r}") from exc
    ratio_name = "q" if geometric else "d"
    try:
        ratio = float(params[ratio_name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"数列图形缺少参数 {ratio_name}: {params!r}") from exc
    if n < 3 or n > 10:
        raise ValueError(f"数列图形展示项数 n 必须在 3..10（避免挤爆画布）: n={n}")
    if geometric and (ratio <= 0 or ratio == 1):
        raise ValueError(f"等比数列公比必须 q>0 且 q≠1: q={ratio}")

    xs = list(range(1, n + 1))
    if geometric:
        ys = [a1 * (ratio ** (i - 1)) for i in xs]
    else:
        ys = [a1 + (i - 1) * ratio for i in xs]
    lo_x, hi_x = 0.2, n + 0.8
    lo_y = min(0.0, min(ys)) - 1.0
    hi_y = max(0.0, max(ys)) + 1.4
    fig, ax = _new_axes(lo_x, hi_x, lo_y, hi_y)
    _draw_axes(ax, lo_x, hi_x, lo_y, hi_y)

    ax.vlines(xs, 0, ys, colors=COLOR_AUX, lw=1.0, linestyles=(0, (3, 3)), zorder=2)
    ax.plot(xs, ys, color=COLOR_BRAND, lw=1.7, marker="o", ms=5.8, zorder=4)
    label_bg = {"boxstyle": "square,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.9}
    for i, (x, y) in enumerate(zip(xs, ys), start=1):
        if i in (1, n):
            ax.annotate(f"$a_{{{i}}}={_fmt(y)}$", xy=(x, y), xytext=(0, 10 if y >= 0 else -10),
                        textcoords="offset points", ha="center",
                        va="bottom" if y >= 0 else "top",
                        fontsize=10, color="#0F172A", zorder=6, bbox=label_bg)
        ax.text(x, -0.5 if lo_y < 0 else 0.1, f"${x}$", color=COLOR_MUTED,
                fontsize=9, ha="center", va="top" if lo_y < 0 else "bottom")

    if geometric:
        card = [f"$a_n = a_1 q^{{n-1}}$", f"$a_1={_fmt(a1)},\\ q={_fmt(ratio)}$"]
    else:
        card = [f"$a_n = a_1+(n-1)d$", f"$S_n = \\frac{{n(a_1+a_n)}}{{2}}$"]
    if ys[-1] >= ys[0]:
        # 递增：左上角（下移避开 y 轴字母；曲线在左端低位）
        _formula_card(fig, ax, card, x=0.02, y=0.80, ha="left", va="top")
    else:
        # 递减：右上角（曲线在右端低位）
        _formula_card(fig, ax, card, x=0.98, y=0.80, ha="right", va="top")
    return _fig_to_png(fig)


def sequence_figure(params: dict[str, float]) -> bytes:
    """等差数列点列（a1/d/n）：项值点 + 虚线茎 + 通项/前 n 项和公式卡。"""
    return _sequence_figure(params, geometric=False)


def sequence_geo_figure(params: dict[str, float]) -> bytes:
    """等比数列点列（a1/q/n）：指数增长/衰减点 + 通项公式卡。"""
    return _sequence_figure(params, geometric=True)


def venn_figure(params: dict[str, float]) -> bytes:
    """两集合韦恩图（n=2）：A/B/A∩B + 容斥公式卡。"""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    n = int(round(float(params.get("n", 2.0))))
    if n != 2:
        raise ValueError(f"Venn 图形当前只支持两集合（n=2），收到 n={n}")
    fig, ax = plt.subplots(figsize=(5.8, 3.4))
    ax.set_xlim(-2.4, 2.4)
    ax.set_ylim(-1.7, 1.7)
    ax.set_aspect("equal")
    ax.axis("off")
    left = Circle((-0.62, 0.0), 1.05, facecolor=COLOR_BRAND, edgecolor=COLOR_BRAND, alpha=0.18, lw=1.8)
    right = Circle((0.62, 0.0), 1.05, facecolor=COLOR_RED, edgecolor=COLOR_RED, alpha=0.16, lw=1.8)
    ax.add_patch(left)
    ax.add_patch(right)
    ax.text(-1.55, 0.0, "$A$", ha="center", va="center", fontsize=18, color=COLOR_BRAND)
    ax.text(1.55, 0.0, "$B$", ha="center", va="center", fontsize=18, color=COLOR_RED)
    ax.text(0.0, 0.0, "$A\\cap B$", ha="center", va="center", fontsize=13, color="#0F172A")
    ax.text(0.0, -1.42, "阴影区域 = 两个集合的交集", ha="center", va="center",
            fontsize=10.5, color=COLOR_MUTED, fontname="Microsoft YaHei")
    _formula_card(fig, ax, [r"$|A\cup B|=|A|+|B|-|A\cap B|$"])
    return _fig_to_png(fig)


def exponential_figure(params: dict[str, float]) -> bytes:
    """指数函数 y=a^x（a>0, a≠1）：曲线 + 单调性公式卡。"""
    a = float(params.get("a", 2.0))
    if a <= 0 or a == 1:
        raise ValueError(f"指数函数底数必须 a>0 且 a≠1: a={a}")
    lo_x, hi_x = -2.2, 2.2
    xs = [lo_x + (hi_x - lo_x) * i / 200 for i in range(201)]
    ys = [a ** x for x in xs]
    lo_y = min(0.0, min(ys)) - 0.8
    hi_y = max(ys) + 1.0
    fig, ax = _new_axes(lo_x, hi_x, lo_y, hi_y)
    _draw_axes(ax, lo_x, hi_x, lo_y, hi_y)
    ax.plot(xs, ys, color=COLOR_BRAND, lw=2.2, zorder=3)
    ax.plot([0.0], [1.0], marker="o", ms=6.0, color=COLOR_RED, zorder=5)
    ax.annotate("$(0,1)$", xy=(0.0, 1.0), xytext=(6, 6), textcoords="offset points",
                fontsize=10, color="#0F172A")
    trend = "在 R 上单调递增" if a > 1 else "在 R 上单调递减"
    if a > 1:
        _formula_card(fig, ax, [f"$y={_fmt(a)}^x$", trend], x=0.02, y=0.96, ha="left", va="top")
    else:
        _formula_card(fig, ax, [f"$y={_fmt(a)}^x$", trend], x=0.98, y=0.96, ha="right", va="top")
    return _fig_to_png(fig)


def logarithm_figure(params: dict[str, float]) -> bytes:
    """对数函数 y=log_a x（a>0, a≠1）：定义域 x>0 + 曲线 + 公式卡。"""
    a = float(params.get("a", 2.0))
    if a <= 0 or a == 1:
        raise ValueError(f"对数函数底数必须 a>0 且 a≠1: a={a}")
    lo_x, hi_x = -0.3, 4.2
    xs = [0.02 + (hi_x - 0.02) * i / 220 for i in range(221)]
    ys = [math.log(x) / math.log(a) for x in xs]
    lo_y = max(-4.0, min(ys) - 0.8)
    hi_y = min(4.0, max(ys) + 0.8)
    fig, ax = _new_axes(lo_x, hi_x, lo_y, hi_y)
    _draw_axes(ax, lo_x, hi_x, lo_y, hi_y)
    ax.plot(xs, ys, color=COLOR_BRAND, lw=2.2, zorder=3)
    ax.plot([1.0], [0.0], marker="o", ms=6.0, color=COLOR_RED, zorder=5)
    ax.annotate("$(1,0)$", xy=(1.0, 0.0), xytext=(6, -10), textcoords="offset points",
                fontsize=10, color="#0F172A")
    trend = "在 (0,+∞) 上单调递增" if a > 1 else "在 (0,+∞) 上单调递减"
    if a > 1:
        # 递增：曲线在右上方，右下角x轴下方空闲
        _formula_card(fig, ax, [f"$y=\\log_{{{_fmt(a)}}}x$", trend], x=0.98, y=0.04, ha="right", va="bottom")
    else:
        _formula_card(fig, ax, [f"$y=\\log_{{{_fmt(a)}}}x$", trend], x=0.98, y=0.96, ha="right", va="top")
    return _fig_to_png(fig)


def trig_figure(params: dict[str, float]) -> bytes:
    """三角函数 y=sin x / y=cos x：一个周期 2π 内的双曲线 + 公式卡。"""
    amp = float(params.get("amp", 1.0))
    if amp <= 0:
        raise ValueError(f"三角函数振幅必须 >0: amp={amp}")
    lo_x, hi_x = -2 * math.pi, 2 * math.pi
    xs = [lo_x + (hi_x - lo_x) * i / 320 for i in range(321)]
    sin_y = [amp * math.sin(x) for x in xs]
    cos_y = [amp * math.cos(x) for x in xs]
    view = amp * 2.0
    fig, ax = _new_axes(lo_x, hi_x, -view, view)
    _draw_axes(ax, lo_x, hi_x, -view, view)
    ax.plot(xs, sin_y, color=COLOR_BRAND, lw=2.0, label=r"$y=\sin x$")
    ax.plot(xs, cos_y, color=COLOR_RED, lw=2.0, label=r"$y=\cos x$")
    for x in (-2 * math.pi, -math.pi, math.pi, 2 * math.pi):
        ax.plot([x, x], [-0.1, 0.1], color=COLOR_INK, lw=1.0)
        label = {-2 * math.pi: r"$-2\pi$", -math.pi: r"$-\pi$",
                 math.pi: r"$\pi$", 2 * math.pi: r"$2\pi$"}[x]
        ax.text(x, -0.32, label, ha="center", va="top", fontsize=9.5, color=COLOR_MUTED)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 0.01), ncol=2, frameon=True,
              fontsize=10.5, facecolor=COLOR_CARD_BG, edgecolor=COLOR_CARD_EDGE)
    _formula_card(fig, ax, [r"$T=2\pi$", r"$-1\leqslant \sin x,\cos x\leqslant 1$"],
                  x=0.98, y=0.99, ha="right", va="top")
    return _fig_to_png(fig)


def math_object_png(kind: str, params: dict[str, float]) -> bytes:
    """参数化数学对象 → PNG 字节（未知 kind 可见失败，绝不静默空图）。"""
    key = object_key_for(kind, params)
    cached = _BYTES_CACHE.get(key)
    if cached is not None:
        return cached
    if kind == "line":
        data = line_figure(params)
    elif kind == "line_family":
        data = line_family_figure(params)
    elif kind == "ellipse":
        data = ellipse_png(params)
    elif kind == "quadratic":
        data = quadratic_figure(params)
    elif kind == "power":
        data = power_figure(params)
    elif kind == "sequence":
        data = sequence_figure(params)
    elif kind == "sequence_geo":
        data = sequence_geo_figure(params)
    elif kind == "venn":
        data = venn_figure(params)
    elif kind == "exponential":
        data = exponential_figure(params)
    elif kind == "logarithm":
        data = logarithm_figure(params)
    elif kind == "trig":
        data = trig_figure(params)
    else:
        raise ValueError(f"math_figures 不支持 kind={kind!r}")
    _BYTES_CACHE[key] = data
    return data


# ── 课件图形资产（MATH_RENDER AssetRef；字节进 MinIO teacher-assets）──────────


def asset_for(kind: str, params: dict[str, float]) -> tuple[Any, bytes]:
    """(AssetRef, PNG 字节)：id/object_key/sha256 全部内容寻址（同参数恒同资产）。"""
    from app.ppt_v2.domain.spec import AssetRef

    data = math_object_png(kind, params)
    sha = hashlib.sha256(data).hexdigest()
    key = object_key_for(kind, params)
    asset = AssetRef(
        id=f"math-{sha[:16]}",
        kind="MATH_RENDER",
        object_key=key,
        sha256=sha,
        mime_type="image/png",
        size_bytes=len(data),
        rights_status="ACTIVE",
        rights_evidence_ref="服务端确定性渲染（math_object 参数派生）",
        preserve_original=True,
    )
    return asset, data


def asset_bytes(asset: Any) -> bytes | None:
    """按 AssetRef 重渲图形字节并核对 sha256（跨进程确定性验证；不符返回 None）。"""
    key = str(getattr(asset, "object_key", "") or "")
    parsed = params_from_object_key(key)
    if parsed is None:
        return None
    kind, params = parsed
    try:
        data = math_object_png(kind, params)
    except Exception:  # noqa: BLE001 - 渲染失败由调用方按缺资产诚实降级
        return None
    if hashlib.sha256(data).hexdigest() != str(getattr(asset, "sha256", "")).lower():
        return None
    return data


def ensure_asset_stored(asset: Any) -> bool:
    """MATH_RENDER 资产字节落对象存储（teacher-assets/{object_key}；内容寻址幂等）。

    导出链 `_inline_assets` 从对象存储读字节下发引擎（sha256 校验）；投影持久化时
    必须把图形字节写入，否则直线/直线系课的图进不了 PPTX（8201 只自渲 ellipse）。
    失败返回 False（调用方按缺资产诚实降级，不阻断投影落库）。
    """
    data = asset_bytes(asset)
    if data is None:
        return False
    try:
        from app.storage.minio import get_storage

        get_storage().put_bytes("teacher-assets", str(asset.object_key), data, "image/png")
        return True
    except Exception:  # noqa: BLE001 - 存储不可用不阻断投影；导出侧按缺资产降级
        return False


__all__ = [
    "FIGURE_NS",
    "QUADRATIC_BASELINE",
    "POWER_FAMILY_N",
    "object_key_for",
    "params_from_object_key",
    "quadratic_coeffs_from_text",
    "line_figure",
    "line_family_figure",
    "ellipse_png",
    "quadratic_figure",
    "power_figure",
    "math_object_png",
    "asset_for",
    "asset_bytes",
    "ensure_asset_stored",
]
