# coding: utf-8
"""PPTX 导出引擎（M2-B 生死 Gate，架构 C-7 定案路线）。

deck slides JSON（V3Element 形状，1280×720 逻辑坐标）→ python-pptx 16:9 文档：
- text 元素 → 文本框；$..$ 定界段 → OMML 原生公式段落（混排，导出后仍可编辑）
- formula 元素 → LaTeX → latex2mathml → mathml2omml → m:oMath 原生公式对象
  （PowerPoint 双击可进公式编辑器——红线 31：禁止公式图片化）
- geometry 元素（preset_id）→ matplotlib 确定性渲染 PNG 嵌入（conic/function 系；
  未支持的 preset 诚实渲染占位框 + 参数文字，不静默空白）
- notes → 演讲者备注

坐标换算：1px(逻辑) = 0.75pt（1280×720px → 960×540pt = 13.333×7.5in 16:9）。
"""
from __future__ import annotations

import io
import re
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

PX_TO_PT = 0.75
SLIDE_W_PX, SLIDE_H_PX = 1280, 720
CN_FONT = "微软雅黑"

_NS_A14 = "http://schemas.microsoft.com/office/drawing/2010/main"
_NS_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _hex_rgb(color: str | None) -> RGBColor | None:
    if not color or not str(color).startswith("#") or len(str(color)) != 7:
        return None
    try:
        return RGBColor.from_string(str(color)[1:].upper())
    except ValueError:
        return None


def _set_cn_font(run, name: str = CN_FONT) -> None:
    """中西文都钉中文字体（防残酷测试「字体变了」——M2-B FAIL 项）。"""
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", name)


def _latex_to_omml_xml(latex: str) -> str:
    """LaTeX → MathML → OMML（<m:oMath>…</m:oMath> 字符串）。失败抛 ValueError（调用方降级文本）。"""
    import latex2mathml.converter as l2m
    import mathml2omml

    mathml = l2m.convert(latex)
    omml = mathml2omml.convert(mathml)
    if "oMath" not in omml:
        raise ValueError("OMML 转换产物异常")
    # mathml2omml 输出不带命名空间声明——挂上 m 命名空间（pptx parse_xml 需要）
    if "xmlns:m" not in omml:
        omml = omml.replace("<m:oMath", f'<m:oMath xmlns:m="{_NS_M}"', 1)
    return omml


def _formula_paragraph_xml(latex: str, font_size: int, color: str | None) -> str:
    """构造含原生公式的 <a:p> XML（PowerPoint OMML 载体 = a14:m 包装）。
    lnSpc 150%：防相邻公式段分式上下标互相叠压（真机渲染验证过的最小行距）。"""
    omml = _latex_to_omml_xml(latex)
    return (
        f'<a:p xmlns:a="{_NS_A}">'
        f'<a:pPr><a:lnSpc><a:spcPct val="180000"/></a:lnSpc>'
        f'<a:spcBef><a:spcPts val="600"/></a:spcBef>'
        f'<a:spcAft><a:spcPts val="600"/></a:spcAft></a:pPr>'
        f'<a14:m xmlns:a14="{_NS_A14}">'
        f'{omml}'
        f'</a14:m>'
        f'<a:endParaRPr lang="zh-CN" sz="{int(font_size) * 100}"/>'
        f'</a:p>'
    )


def _render_geometry_png(preset_id: str, params: dict[str, float]) -> bytes | None:
    """确定性几何渲染（matplotlib，Agg）。支持的 preset 出真图；未支持返回 None（调用方占位）。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(5.2, 4.8), dpi=150)
    try:
        color = "#1e3a5f"
        if preset_id == "conic/ellipse":
            a = float(params.get("a", 5) or 5)
            b = float(params.get("b", 3) or 3)
            c = (a * a - b * b) ** 0.5 if a > b else 0.0
            t = np.linspace(0, 2 * np.pi, 361)
            ax.plot(a * np.cos(t), b * np.sin(t), color=color, lw=2)
            ax.plot([-c, c], [0, 0], "o", color="#c2410c", ms=5)
            ax.annotate(f"F1(-{c:g},0)", (-c, 0), xytext=(-c, -b * 0.35), fontsize=8, ha="center", color="#c2410c")
            ax.annotate(f"F2({c:g},0)", (c, 0), xytext=(c, -b * 0.35), fontsize=8, ha="center", color="#c2410c")
            lo = max(a, b) * 1.3
            ax.set_xlim(-lo, lo)
            ax.set_ylim(-lo, lo)
        elif preset_id == "conic/hyperbola":
            a = float(params.get("a", 3) or 3)
            b = float(params.get("b", 2) or 2)
            c = (a * a + b * b) ** 0.5
            x1 = np.linspace(a * 1.05, a * 4, 200)
            x2 = -x1
            ax.plot(x1, b * np.sqrt(x1 * x1 / (a * a) - 1), color=color, lw=2)
            ax.plot(x1, -b * np.sqrt(x1 * x1 / (a * a) - 1), color=color, lw=2)
            ax.plot(x2, b * np.sqrt(x2 * x2 / (a * a) - 1), color=color, lw=2)
            ax.plot(x2, -b * np.sqrt(x2 * x2 / (a * a) - 1), color=color, lw=2)
            ax.plot([-c, c], [0, 0], "o", color="#c2410c", ms=5)
            lim = a * 3
            ax.set_xlim(-lim, lim)
            ax.set_ylim(-lim, lim)
        elif preset_id == "conic/parabola":
            p = float(params.get("p", 2) or 2)
            y = np.linspace(-4, 4, 200)
            ax.plot(y * y / (2 * p), y, color=color, lw=2)
            ax.plot([p / 2], [0], "o", color="#c2410c", ms=5)
            ax.set_xlim(-1, 10)
            ax.set_ylim(-5, 5)
        elif preset_id.startswith("function/"):
            x = np.linspace(-6, 6, 300)
            if preset_id == "function/quadratic":
                aa = float(params.get("a", 1) or 1)
                bb = float(params.get("b", 0) or 0)
                cc = float(params.get("c", 0) or 0)
                ax.plot(x, aa * x * x + bb * x + cc, color=color, lw=2)
            elif preset_id == "function/sine":
                ax.plot(x, np.sin(x), color=color, lw=2)
            else:
                ax.plot(x, np.exp(x) / (1 + np.exp(x)), color=color, lw=2)
            ax.axhline(0, color="#94a3b8", lw=0.8)
            ax.axvline(0, color="#94a3b8", lw=0.8)
            ax.set_xlim(-6, 6)
            ax.set_ylim(-6, 6)
        else:
            return None
        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", transparent=True)
        return buf.getvalue()
    finally:
        plt.close(fig)


def _render_function_plot_png(expr: str, domain: list | tuple | None, params: dict | None) -> bytes | None:
    """把教师画布的 functionPlot 表达式确定性静态化为 PNG。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import sympy as sp
    except ImportError:
        return None
    try:
        body = re.sub(r"^\s*y\s*=\s*", "", str(expr or "")).replace("^", "**")
        body = re.sub(r"\\(sin|cos|tan|exp|log|ln|sqrt)", r"\1", body)
        x = sp.symbols("x")
        names = {name: getattr(sp, name) for name in ("sin", "cos", "tan", "exp", "log", "sqrt")}
        parsed = sp.sympify(body, locals={"x": x, **names})
        substitutions = {}
        for name, value in (params or {}).items():
            if isinstance(value, dict):
                value = value.get("value")
            if isinstance(value, (int, float)):
                substitutions[sp.symbols(str(name))] = float(value)
        fn = sp.lambdify(x, parsed.subs(substitutions), "numpy")
        bounds = list(domain) if isinstance(domain, (list, tuple)) and len(domain) == 2 else [-6.0, 6.0]
        lo, hi = float(bounds[0]), float(bounds[1])
        if not lo < hi:
            return None
        xx = np.linspace(lo, hi, 401)
        yy = np.asarray(fn(xx), dtype=float)
        if yy.ndim != 1:
            return None
        yy = np.where(np.isfinite(yy), yy, np.nan)
        fig, ax = plt.subplots(figsize=(5.2, 4.8), dpi=150)
        try:
            ax.plot(xx, yy, color="#1e3a5f", lw=2)
            ax.axhline(0, color="#94a3b8", lw=0.8)
            ax.axvline(0, color="#94a3b8", lw=0.8)
            ax.grid(True, color="#dbe2ee", lw=0.6)
            ax.set_xlim(lo, hi)
            finite = yy[np.isfinite(yy)]
            if finite.size:
                span = max(float(np.nanmax(finite) - np.nanmin(finite)), 1.0)
                mid = float((np.nanmax(finite) + np.nanmin(finite)) / 2)
                ax.set_ylim(mid - span * 0.65, mid + span * 0.65)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", transparent=True)
            return buf.getvalue()
        finally:
            plt.close(fig)
    except Exception:
        return None


def _add_placeholder(slide, el: dict, label: str, detail: str = "") -> None:
    left, top = int(el["left"] * PX_TO_PT), int(el["top"] * PX_TO_PT)
    w, h = int(el["width"] * PX_TO_PT), int(el["height"] * PX_TO_PT)
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Emu(int(left * 12700)), Emu(int(top * 12700)),
        Emu(int(w * 12700)), Emu(int(h * 12700)),
    )
    shape.line.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
    tf = shape.text_frame
    tf.text = label
    if detail:
        p2 = tf.add_paragraph()
        p2.text = detail
    for para in tf.paragraphs:
        for run in para.runs:
            run.font.size = run.font.size or Pt(14)
            _set_cn_font(run)


def _add_function_plot(slide, el: dict) -> None:
    png = _render_function_plot_png(el.get("expr") or "", el.get("domain"), el.get("params"))
    if png is not None:
        left, top = int(el["left"] * PX_TO_PT), int(el["top"] * PX_TO_PT)
        w, h = int(el["width"] * PX_TO_PT), int(el["height"] * PX_TO_PT)
        slide.shapes.add_picture(
            io.BytesIO(png), Emu(int(left * 12700)), Emu(int(top * 12700)),
            Emu(int(w * 12700)), Emu(int(h * 12700)),
        )
    else:
        _add_placeholder(slide, el, "[函数图像导出失败]", str(el.get("expr") or ""))


def _add_board_json(slide, el: dict) -> None:
    payload = el.get("board_json") or el.get("boardJson") or {}
    if isinstance(payload, dict) and payload.get("type") == "plot2d":
        _add_function_plot(slide, {
            **el,
            "expr": payload.get("expr") or "",
            "params": payload.get("params") or {},
            "domain": payload.get("domain"),
        })
        return
    _add_placeholder(slide, el, "[结构化图形未静态化]", str(payload.get("type") or "boardJson"))


def _add_external_image(slide, el: dict) -> None:
    src = str(el.get("src") or "")
    if src.startswith("data:image/") and ";base64," in src:
        import base64

        try:
            img_bytes = base64.b64decode(src.split(",", 1)[1])
            left, top = int(el["left"] * PX_TO_PT), int(el["top"] * PX_TO_PT)
            w, h = int(el["width"] * PX_TO_PT), int(el["height"] * PX_TO_PT)
            slide.shapes.add_picture(
                io.BytesIO(img_bytes), Emu(int(left * 12700)), Emu(int(top * 12700)),
                Emu(int(w * 12700)), Emu(int(h * 12700)),
            )
            return
        except Exception:
            pass
    _add_placeholder(slide, el, "[图片需在课件端加载]", src[:120])


def _render_figure3d_png(scene: dict | None) -> bytes | None:
    """figure3d 确定性场景（MathFigure3D DSL）→ matplotlib 3D 静态图 PNG。

    与前端 MathFigure3D 同场景（solids/segments/labels/grid），导出为静态图——
    教师课件只取图片边界，PPTX 内不承载 WebGL 交互。
    """
    if not isinstance(scene, dict):
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    except ImportError:
        return None

    def _p(v):
        return [float(x) for x in v[:3]]

    face_color = (0.31, 0.56, 0.97, 0.16)
    edge_color = "#1d4ed8"

    fig = plt.figure(figsize=(5.2, 4.8), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    try:
        # 面统一收集后一次性 add（退化面——三点重合——会锁死坐标轴缩放，跳过）；
        # 线段逐条 ax.plot（新版 mpl 的 Line3DCollection 对不等长线段自动缩放会抛异常）
        faces: list[list[list[float]]] = []
        for s in scene.get("solids") or []:
            kind = str(s.get("kind") or "")
            if kind == "box":
                c, size = _p(s["center"]), [float(x) for x in s.get("size", [3, 3, 3])]
                lo = [c[i] - size[i] / 2 for i in range(3)]
                hi = [c[i] + size[i] / 2 for i in range(3)]
                corners = [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
                idx = [0, 1, 3, 2, 4, 5, 7, 6, 0, 2, 6, 4, 1, 3, 7, 5]
                for i in range(0, 16, 4):
                    faces.append([corners[idx[i + j]] for j in range(4)])
                for i in range(4):
                    _line3(ax, corners[i], corners[i + 4])
            elif kind == "sphere":
                center, r = _p(s["center"]), float(s.get("radius", 1.8))
                u = np.linspace(0, 2 * np.pi, 40)
                for lat in np.linspace(-np.pi / 2, np.pi / 2, 7)[1:-1]:
                    z0 = r * np.sin(lat)
                    rr = r * np.cos(lat)
                    _line3(ax, *[[center[0] + rr * np.cos(a), center[1] + z0, center[2] + rr * np.sin(a)] for a in u])
                for lon in np.linspace(0, np.pi, 6)[1:-1]:
                    _line3(ax, *[[center[0] + r * np.cos(a) * np.cos(lon), center[1] + r * np.sin(a), center[2] + r * np.sin(a) * np.cos(lon)] for a in u])
            elif kind in ("cylinder", "cone"):
                base, top = _p(s["base"]), _p(s.get("top") or [s["base"][0], s["base"][1] + 3, s["base"][2]])
                r = float(s.get("radius", 1.5))
                u = np.linspace(0, 2 * np.pi, 36)
                bpts = [[base[0] + r * np.cos(a), base[1], base[2] + r * np.sin(a)] for a in u]
                tpts = [[top[0] + r * np.cos(a), top[1], top[2] + r * np.sin(a)] for a in u]
                _line3(ax, *bpts)
                if kind == "cylinder":
                    _line3(ax, *tpts)
                    for i in range(0, 36, 6):
                        _line3(ax, bpts[i], tpts[i])
                else:
                    for i in range(0, 36, 3):
                        _line3(ax, tpts[0], bpts[i])
            elif kind in ("prism", "pyramid"):
                # 场景契约：棱柱 bottom+top；棱锥 base+apex（与 MathFigure3D DSL 一致）
                bottom = [_p(p) for p in (s.get("bottom") if kind == "prism" else s.get("base") or [])]
                if kind == "prism":
                    top = [_p(p) for p in (s.get("top") or [])]
                else:
                    top = [_p(s["apex"])] * len(bottom)
                if len(bottom) >= 3 and len(top) == len(bottom):
                    faces.append(bottom)
                    if len({tuple(p) for p in top}) >= 3:
                        faces.append(top)
                    n = len(bottom)
                    for i in range(n):
                        j = (i + 1) % n
                        _line3(ax, bottom[i], bottom[j])
                        _line3(ax, top[i], top[j])
                        _line3(ax, bottom[i], top[i])
                        # 侧面：棱柱为四边形，棱锥为三角形
                        if kind == "prism":
                            faces.append([bottom[i], bottom[j], top[j], top[i]])
                        else:
                            faces.append([bottom[i], bottom[j], top[i]])
            elif kind == "polyhedron":
                verts = [_p(v["pos"]) for v in (s.get("vertices") or [])]
                name2i = {str(v.get("name")): i for i, v in enumerate(s.get("vertices") or [])}
                def _edge_index(value):
                    if isinstance(value, int) and 0 <= value < len(verts):
                        return value
                    return name2i.get(str(value))
                for e in (s.get("edges") or []):
                    a, b = _edge_index(e[0]), _edge_index(e[1])
                    if a is not None and b is not None:
                        _line3(ax, verts[a], verts[b])
        # 学生端 visual_spec 会把圆锥曲线/轨迹物化为 polyline；这些曲线没有
        # solids 也必须进入 PPTX，否则平面几何页会得到透明空图。
        for curve in scene.get("curves") or []:
            if not isinstance(curve, dict):
                continue
            raw_points = curve.get("points")
            if not isinstance(raw_points, (list, tuple)):
                continue
            points = []
            for point in raw_points[:200]:
                try:
                    points.append(_p(point))
                except (TypeError, ValueError, IndexError):
                    continue
            if len(points) < 2:
                continue
            curve_color = str(curve.get("color") or edge_color)
            _line3(ax, *points, color=curve_color, lw=2.0)
            if curve.get("closed"):
                _line3(ax, points[-1], points[0], color=curve_color, lw=2.0)
        # 辅助平面与其边界同样属于学生端场景语义；边界线让 matplotlib 在
        # 只有 plane、没有实体时也能正确建立坐标范围。
        plane_faces = []
        plane_colors = []
        plane_alphas = []
        for plane in scene.get("planes") or []:
            if not isinstance(plane, dict):
                continue
            raw_points = plane.get("points")
            if not isinstance(raw_points, (list, tuple)) or len(raw_points) < 3:
                continue
            points = []
            for point in raw_points[:4]:
                try:
                    points.append(_p(point))
                except (TypeError, ValueError, IndexError):
                    continue
            if len(points) < 3:
                continue
            plane_faces.append(points)
            plane_colors.append(str(plane.get("color") or "#f59e0b"))
            try:
                plane_alphas.append(min(max(float(plane.get("opacity", 0.22)), 0.08), 0.5))
            except (TypeError, ValueError):
                plane_alphas.append(0.22)
            _line3(ax, *[*points, points[0]], color=plane_colors[-1], lw=1.1)
        if faces:
            ax.add_collection3d(Poly3DCollection(faces, facecolor=face_color, alpha=0.5))
        for points, color, alpha in zip(plane_faces, plane_colors, plane_alphas):
            ax.add_collection3d(Poly3DCollection([points], facecolor=color, edgecolor=color, alpha=alpha))
        for seg in scene.get("segments") or []:
            a, b = _p(seg["a"]), _p(seg["b"])
            _line3(ax, a, b, color="#dc2626", dashed=bool(seg.get("dashed")))
            if seg.get("label"):
                ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2, str(seg["label"]), fontsize=11)
        for lb in scene.get("labels") or []:
            p = _p(lb["pos"])
            ax.text(p[0], p[1], p[2], str(lb.get("text") or ""), fontsize=11, ha="center", va="center",
                    color="#0f172a")
        cam = scene.get("camera") or {}
        ax.view_init(elev=25, azim=-140)
        if isinstance(cam.get("pos"), (list, tuple)) and len(cam["pos"]) == 3:
            p = _p(cam["pos"])
            ax.view_init(elev=25, azim=np.degrees(np.arctan2(p[0], p[2])))
        ax.set_axis_off()
        if scene.get("grid", True):
            ax.grid(True, color="#dbe2ee", lw=0.6)
        ax.set_box_aspect((1, 1, 1))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05, transparent=True)
        return buf.getvalue()
    except Exception:
        return None
    finally:
        plt.close(fig)


def _line3(ax, *pts, color="#1d4ed8", lw=1.6, dashed=False) -> None:
    """三维折线：逐点 plot（新版 mpl 的 Line3DCollection 对不等长线段自动缩放会抛异常）。"""
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    zs = [float(p[2]) for p in pts]
    ax.plot(xs, ys, zs, color=color, lw=lw, ls="--" if dashed else "-")


def _split_inline_math(text: str) -> list[tuple[str, str]]:
    """一行 → [('text'|'math', 片段)]。$..$ 显式定界优先；无 $ 时整行按文本处理。"""
    if "$" not in text:
        return [("text", text)]
    out: list[tuple[str, str]] = []
    for i, part in enumerate(re.split(r"\$([^$]+)\$", text)):
        if part:
            out.append(("math" if i % 2 else "text", part))
    return out


def _style_run(run, font_size: int, color: str | None, bold: bool | None = None) -> None:
    run.font.size = Pt(max(10, font_size))
    rgb = _hex_rgb(color)
    if rgb is not None:
        run.font.color.rgb = rgb
    if bold:
        run.font.bold = True
    _set_cn_font(run)


def _add_text(shape_frame, html: str, font_size: int, color: str | None, bold: bool | None) -> None:
    """文本混排（红线 31/H12）：$..$ 定界段 → OMML 原生公式段落（导出后仍可编辑）；
    其余普通文本 run（字号/颜色/加粗/中文钉扎）。

    段落策略：普通行 → python-pptx 段落 API；公式段 → parse_xml <a:p> 按序 append
    （两类都 append 到 txBody 尾部，顺序天然保持；首段复用自带空段）。
    """
    tf = shape_frame.text_frame
    tf.word_wrap = True
    txBody = tf._txBody
    first_used = False

    def _plain_para():
        nonlocal first_used
        paras = tf.paragraphs
        if not first_used and len(paras) == 1 and not paras[0].runs:
            first_used = True
            return paras[0]
        first_used = True
        return tf.add_paragraph()

    for line in str(html).split("\n"):
        segments = _split_inline_math(line)
        if not any(k == "math" for k, _ in segments):
            run = _plain_para().add_run()
            run.text = line
            _style_run(run, font_size, color, bold)
            continue
        for kind, seg in segments:
            if kind == "text" and seg:
                run = _plain_para().add_run()
                run.text = seg
                _style_run(run, font_size, color, bold)
            elif kind == "math" and seg.strip():
                try:
                    txBody.append(parse_xml(_formula_paragraph_xml(seg, font_size, color)))
                    first_used = True
                except Exception:
                    # 公式转换失败：降级为普通文本 run（源码可见但导出永不崩——诚实呈现）
                    run = _plain_para().add_run()
                    run.text = seg
                    _style_run(run, font_size, color, bold)
                    first_used = True


def _add_formula(slide, el: dict) -> None:
    """原生 OMML 公式（可编辑）；转换失败降级为文本并如实标注（不图片化、不静默）。"""
    left, top = int(el["left"] * PX_TO_PT), int(el["top"] * PX_TO_PT)
    w, h = int(el["width"] * PX_TO_PT), max(int(el["height"] * PX_TO_PT), 24)
    box = slide.shapes.add_textbox(Emu(int(left * 12700)), Emu(int(top * 12700)), Emu(int(w * 12700)), Emu(int(h * 12700)))
    latex = str(el.get("latex") or "")
    font_size = int(el.get("font_size") or 24)
    color = el.get("color")
    try:
        para_xml = _formula_paragraph_xml(latex, font_size, color)
    except Exception:
        _add_text(box, f"{latex}（公式转换失败，已降级文本）", font_size, color, False)
        return
    txBody = box.text_frame._txBody
    old_p = txBody.find(qn("a:p"))
    new_p = parse_xml(para_xml)
    if old_p is not None:
        txBody.replace(old_p, new_p)
    else:
        txBody.append(new_p)


def _add_geometry(slide, el: dict) -> None:
    if el.get("board_json") or el.get("boardJson"):
        _add_board_json(slide, el)
        return
    png = _render_geometry_png(str(el.get("preset_id") or ""), dict(el.get("params") or {}))
    left, top = int(el["left"] * PX_TO_PT), int(el["top"] * PX_TO_PT)
    w, h = int(el["width"] * PX_TO_PT), int(el["height"] * PX_TO_PT)
    if png is not None:
        slide.shapes.add_picture(io.BytesIO(png), Emu(int(left * 12700)), Emu(int(top * 12700)), Emu(int(w * 12700)), Emu(int(h * 12700)))
    else:
        # 未支持的 preset：诚实占位框 + 参数文字（不静默空白）
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(int(left * 12700)), Emu(int(top * 12700)), Emu(int(w * 12700)), Emu(int(h * 12700)))
        shape.line.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
        tf = shape.text_frame
        tf.text = f"[图形 {el.get('preset_id')}]"
        note = "  ".join(f"{k}={v:g}" for k, v in (el.get("params") or {}).items())
        if note:
            p2 = tf.add_paragraph()
            p2.text = note
            p2.runs[0].font.size = Pt(12)
        for para in tf.paragraphs:
            for run in para.runs:
                run.font.size = run.font.size or Pt(14)
                _set_cn_font(run)


def build_pptx(deck: dict[str, Any]) -> bytes:
    """deck dict（{title, slides:[{elements, notes, …}]}）→ PPTX bytes。"""
    prs = Presentation()
    prs.slide_width = Emu(int(13.333 * 914400))
    prs.slide_height = Emu(int(7.5 * 914400))
    blank = prs.slide_layouts[6]

    for slide_data in deck.get("slides") or []:
        slide = prs.slides.add_slide(blank)
        for el in slide_data.get("elements") or []:
            etype = el.get("type")
            if etype == "text":
                box = slide.shapes.add_textbox(
                    Emu(int(el["left"] * PX_TO_PT * 12700)), Emu(int(el["top"] * PX_TO_PT * 12700)),
                    Emu(int(el["width"] * PX_TO_PT * 12700)), Emu(int(el["height"] * PX_TO_PT * 12700)),
                )
                _add_text(box, el.get("html") or "", int(el.get("font_size") or 20), el.get("color"), el.get("bold"))
            elif etype == "formula":
                _add_formula(slide, el)
            elif etype == "image":
                # dataURI 背景/图片可嵌入；外部 key 或 URL 无法在导出进程取图时显式占位。
                if str(el.get("src") or "").startswith("data:image/png;base64,") and int(el.get("left") or 0) == 0 and int(el.get("top") or 0) == 0:
                    import base64

                    b64 = str(el["src"]).split(",", 1)[1]
                    img_bytes = base64.b64decode(b64)
                    slide.shapes.add_picture(
                        io.BytesIO(img_bytes), Emu(0), Emu(0),
                        Emu(int(13.333 * 914400)), Emu(int(7.5 * 914400)),
                    )
                else:
                    _add_external_image(slide, el)
            elif etype == "geometry":
                _add_geometry(slide, el)
            elif etype == "functionPlot":
                _add_function_plot(slide, el)
            elif etype == "dynamicDemo":
                _add_placeholder(
                    slide,
                    el,
                    "[动态演示：PPTX 为静态课件]",
                    f"{el.get('demo_id') or '未命名演示'} · {el.get('caption') or '请在课堂端打开互动演示'}",
                )
            elif etype == "boardJson":
                _add_board_json(slide, el)
            elif etype == "anchorPhoto":
                _add_external_image(slide, el)
            elif etype == "figure3d":
                png = _render_figure3d_png(el.get("scene"))
                left, top = int(el["left"] * PX_TO_PT), int(el["top"] * PX_TO_PT)
                w, h = int(el["width"] * PX_TO_PT), int(el["height"] * PX_TO_PT)
                if png is not None:
                    slide.shapes.add_picture(io.BytesIO(png), Emu(int(left * 12700)), Emu(int(top * 12700)), Emu(int(w * 12700)), Emu(int(h * 12700)))
                else:
                    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(int(left * 12700)), Emu(int(top * 12700)), Emu(int(w * 12700)), Emu(int(h * 12700)))
                    shape.line.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
                    tf = shape.text_frame
                    tf.text = "[立体图形]"
                    for para in tf.paragraphs:
                        for run in para.runs:
                            run.font.size = run.font.size or Pt(14)
                            _set_cn_font(run)
            elif etype == "pageNo":
                box = slide.shapes.add_textbox(
                    Emu(int((SLIDE_W_PX - 60) * PX_TO_PT * 12700)), Emu(int((SLIDE_H_PX - 40) * PX_TO_PT * 12700)),
                    Emu(int(40 * PX_TO_PT * 12700)), Emu(int(20 * PX_TO_PT * 12700)),
                )
                _add_text(box, str(el.get("no") or ""), 12, "#94a3b8", False)
            else:
                # 未知类型也必须显式落到 PPTX，避免新增前端元素在导出时静默消失。
                _add_placeholder(slide, el, "[未支持的课件元素]", str(etype or "unknown"))
        notes = slide_data.get("notes")
        if notes:
            slide.notes_slide.notes_text_frame.text = str(notes)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
