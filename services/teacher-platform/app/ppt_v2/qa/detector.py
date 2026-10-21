"""T24 逐页视觉 QA 检测器：只读真实 PPTX + 真实渲染 PNG，独立于生成侧。

为什么必须独立：TP-H1 VISUAL-RUBRIC §4 明确「rubric 通过 ≠ deck QA 通过，二者正交」——
若复用生成侧（builder / layout_resolver / visual_planning）的 box 列表，错排会被同源逻辑
一起漏掉。本模块因此只依赖 python-pptx（读 OOXML 几何）、Pillow（读渲染像素）、
fontTools cmap（读真实已安装字体的字形覆盖），不 import 任何生成侧代码。

阈值全部取自 TP-H1 VISUAL-RUBRIC.md（2026-09-12 冻结，不得改）：
页面 13.333×7.5in、overlap 0.04in、body 12pt、白像素 ≥235、ink<6%、4×4 网格 CV>1.4、
blank stddev<6.0、EMU→inch 914400。contrast（WCAG AA <3.0）是 T24 本轮新增维，TP-H1 无此维。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat
from pptx import Presentation

# fontTools 由 pyproject 已显式声明的 matplotlib 传递提供（matplotlib 硬依赖 fontTools）。
# 为什么 try/except：依赖缺失时应降级为「字形未检查」，绝不能把正常字形误判成缺字形。
try:  # pragma: no cover - 依赖存在时走正常分支
    from fontTools.ttLib import TTCollection, TTFont
except Exception:  # noqa: BLE001
    TTCollection = None  # type: ignore[assignment]
    TTFont = None  # type: ignore[assignment]

# ── 冻结契约（8100 主干直接调用，签名与常量不得改）────────────────────────────
DETECTOR_VERSION = "t24-visual-qa-1.0.0"
RUBRIC = "tp-h1-visual-rubric"
SCHEMA = "tp-h1-visual-qa/v1"
P0_KINDS = ("overflow", "overlap", "off_slide", "duplicate", "graphic", "contrast")
P1_KINDS = ("font", "formula")
INFO_KINDS = ("density",)
# blank 按 TP-H1 rubric §2（P1-4 page_blank）归 p1；上面两个元组为冻结字面量，按任务书原样保留。

# ── TP-H1 冻结阈值 ────────────────────────────────────────────────────────────
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
EMU_PER_INCH = 914400
OVERLAP_TOL_IN = 0.04
BOUND_TOL_IN = 0.02
TINY_FONT_BODY_PT = 12.0
DENSITY_LOW_PCT = 6.0
DENSITY_CLUMP_CV = 1.4
BLANK_STDDEV_MIN = 6.0
WHITE_PIXEL_MIN = 235
DUPLICATE_MIN_CHARS = 6
CONTRAST_MIN_RATIO = 3.0  # WCAG AA（大字级）；T24 新增维
DEFAULT_TEXT_RGB = (0, 0, 0)  # 未显式设色时按默认模板主题 dk1（深色）处理 → 不误报
WHITE_RGB = (255, 255, 255)
GRAPHIC_MIN_W_IN = 3.0
GRAPHIC_MIN_H_IN = 2.0
GRAPHIC_LABEL_COVER = 0.30

# 镜像 services/ppt-presenton/presenton_adapter/engine/fonts.py 的 FONT_CHAIN（含链序）。
# 为什么不 import 8201：8100 进程不得依赖 8201 源码/生成侧模块（会把 Presenton 引擎依赖拖进
# 8100 主干）；链序漂移由 tests/unit/ppt_v2/test_visual_qa_detector.py 的漂移守卫看护。
FONT_CHAIN: tuple[tuple[str, str], ...] = (
    ("Microsoft YaHei", "msyh.ttc"),
    ("Microsoft YaHei UI", "msyh.ttc"),
    ("SimSun", "simsun.ttc"),
    ("Segoe UI Symbol", "seguisym.ttf"),
    ("Cambria Math", "cambria.ttc"),
    ("Arial", "arial.ttf"),
    ("Calibri", "calibri.ttf"),
)
WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# 数学字符（希腊字母 / 运算符 / 箭头 / 上下标 / 数学字母数字符号 / Latin-1 幂号 / 几何三角）
MATH_CHAR_RE = re.compile(
    "[\u00b1\u00b2\u00b3\u00b9\u00b7\u00d7\u00f7\u0370-\u03ff\u2070-\u209f"
    "\u2190-\u22ff\u25b3\u27c0-\u27ef\u2980-\u29ff\u2a00-\u2aff\U0001d000-\U0001d7ff]"
)
# 截断把表达式尾巴吃掉时留下的两类痕迹（括号不闭合 / 结尾悬挂算符）。
# 为什么只收算符不收 ∅ 这类「值」：∅ 合法地收尾（A ∩ B = ∅），收了会误报。
DELIM_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"), ("（", "）"), ("［", "］"), ("｛", "｝"))
DANGLING_TAIL_CHARS = "∈∉⊆⊂⊇⊃∪∩=≤≥≠±×÷−+<>∑∫√·"
# 区间/集合记号（`[0,+∞)`、`(-∞,0)`、`[a,b)`、`[1,2]`）天然左闭右开：其 `)`/`]` 与
# `(`/`[` 数量本就不配平，直接做括号平衡会把**合法数学书写**误报成「表达式被截断」。
# 实测（R8c，deck 5b109adb 幂函数课堂小结）：`[0,+∞)` 的 `)` 触发 p1 → QA 卡门拦截导出，
# 教师彻底拿不到 PPTX。故做括号平衡前先把区间记号整体剔除（配对完整的记号不影响平衡
# 判定的正确性；真正被截断的 `(a,b` 因缺闭合符不会被本正则命中，仍会被如实检出）。
_INTERVAL_NOTATION_RE = re.compile(
    r"[\[\(]\s*[+-]?\s*(?:∞|\d+(?:\.\d+)?|[a-zA-Z])"
    r"\s*,\s*[+-]?\s*(?:∞|\d+(?:\.\d+)?|[a-zA-Z])\s*[\]\)]"
)


@dataclass
class _Shape:
    sid: str
    x: float
    y: float
    w: float
    h: float
    text: str
    sizes: tuple[float, ...]
    colors: tuple[tuple[int, int, int], ...]
    fill: tuple[int, int, int] | None
    has_picture: bool
    shape_type: str


@dataclass
class _Defect:
    kind: str
    severity: str
    message: str
    shape_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "shape_ids": list(self.shape_ids),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 字形覆盖（真实已安装字体 cmap）
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _chain_files() -> tuple[tuple[str, Path], ...]:
    return tuple((family, WINDOWS_FONTS / filename) for family, filename in FONT_CHAIN if (WINDOWS_FONTS / filename).exists())


@lru_cache(maxsize=32)
def _cmap(path_str: str) -> frozenset[int]:
    try:
        if path_str.lower().endswith(".ttc"):
            collection = TTCollection(path_str, lazy=True)  # type: ignore[misc]
            codes: set[int] = set()
            for font in collection.fonts:
                cmap = font.getBestCmap()
                if cmap:
                    codes.update(cmap.keys())
            return frozenset(codes)
        font = TTFont(path_str, lazy=True)  # type: ignore[misc]
        cmap = font.getBestCmap()
        return frozenset(cmap.keys()) if cmap else frozenset()
    except Exception:  # noqa: BLE001
        return frozenset()


@lru_cache(maxsize=64)
def _chain_coverages() -> tuple[frozenset[int], ...]:
    return tuple(_cmap(str(path)) for _, path in _chain_files())


@lru_cache(maxsize=512)
def missing_glyphs(text: str) -> tuple[str, ...]:
    """整条字体链都无 cmap 覆盖的字符（去重、保序）。

    为什么按整条链并集判定：fallback 顺序只决定渲染时选哪张字体，不改变「是否缺字形」。
    """
    if TTCollection is None or TTFont is None:  # pragma: no cover - 依赖缺失
        return ()
    coverages = _chain_coverages()
    if not coverages:  # 本机无链上字体 → 无法判定，宁可不报也不把正常字形判成缺失
        return ()
    missing: list[str] = []
    seen: set[str] = set()
    for ch in text:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        code = ord(ch)
        if not any(code in cmap for cmap in coverages):
            missing.append(ch)
    return tuple(missing)


# ─────────────────────────────────────────────────────────────────────────────
# 形状与像素提取
# ─────────────────────────────────────────────────────────────────────────────


def emu_to_in(emu: int | None) -> float:
    return int(emu or 0) / EMU_PER_INCH


def _rgb_tuple(value: Any) -> tuple[int, int, int]:
    # python-pptx 的 RGBColor 是 str 子类（"1E293B"），不能直接 int()
    hexcode = str(value).lstrip("#").strip().upper()
    code = int(hexcode, 16)
    return ((code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF)


def _explicit_run_color(run: Any, paragraph: Any) -> tuple[int, int, int] | None:
    for holder in (run.font, paragraph.font):
        try:
            color = holder.color
            if color is not None and color.type is not None and int(color.type) == 1:  # MSO_COLOR_TYPE.RGB
                return _rgb_tuple(color.rgb)
        except Exception:  # noqa: BLE001
            continue
    return None


def _shape_fill_rgb(shape: Any) -> tuple[int, int, int] | None:
    try:
        fill = shape.fill
        if fill.type is None or int(fill.type) != 1:  # 1 = MSO_FILL.SOLID
            return None
        return _rgb_tuple(fill.fore_color.rgb)
    except Exception:  # noqa: BLE001 - picture/group 无 fill
        return None


def _collect_shapes(slide: Any) -> list[_Shape]:
    shapes: list[_Shape] = []
    for index, shape in enumerate(slide.shapes):
        try:
            x = emu_to_in(shape.left)
            y = emu_to_in(shape.top)
            w = emu_to_in(shape.width)
            h = emu_to_in(shape.height)
        except Exception:  # noqa: BLE001
            continue
        sid = shape.name or f"shape-{index}"
        text = ""
        sizes: list[float] = []
        colors: list[tuple[int, int, int]] = []
        try:
            if shape.has_text_frame:
                paragraphs = list(shape.text_frame.paragraphs)
                text = "\n".join(p.text for p in paragraphs if p.text)
                for paragraph in paragraphs:
                    run_sizes = [r.font.size.pt for r in paragraph.runs if r.font.size]
                    if not run_sizes and paragraph.text and paragraph.font.size:
                        run_sizes = [paragraph.font.size.pt]  # run 未显式设字号时回落段级
                    sizes.extend(run_sizes)
                    for run in paragraph.runs:
                        color = _explicit_run_color(run, paragraph)
                        if color is not None:
                            colors.append(color)
        except Exception:  # noqa: BLE001
            pass
        shapes.append(
            _Shape(
                sid=sid,
                x=x,
                y=y,
                w=w,
                h=h,
                text=text,
                sizes=tuple(sizes),
                colors=tuple(colors),
                fill=_shape_fill_rgb(shape),
                has_picture=(shape.shape_type is not None and int(shape.shape_type) == 13),  # 13 = PICTURE
                shape_type=str(shape.shape_type),
            )
        )
    return shapes


def _is_decorative(s: _Shape) -> bool:
    """按位置 + 几何启发式排除装饰元素（沿用 TP-H1 脚本，与 shape.name 命名约定无关）。"""
    if s.y > 7.0:  # 底栏 footer / pageno
        return True
    if s.y < 1.2 and (s.w < 2.5 or s.h < 0.5):  # 顶栏 kicker / chip
        return True
    if s.x > 10.5 and s.y < 1.0:  # 右上阶段 chip
        return True
    if s.x < 1.0 and s.w < 0.25:  # accent bar
        return True
    if s.w < 0.15 or s.h < 0.15:  # 极窄竖条
        return True
    if 0.3 < s.w < 0.6 and 0.3 < s.h < 0.6 and not s.text:  # 编号点 / 步骤点
        return True
    return bool(s.w > 12 and 2.0 < s.y < 6.0)  # hero-band


def _overlap_in(a: _Shape, b: _Shape) -> tuple[float, float]:
    ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    return max(ox, 0.0), max(oy, 0.0)


def _pixel_values(image: Image.Image) -> list[int]:
    # Pillow 14 移除了 getdata；优先用替代 API，避免整仓测试被 DeprecationWarning 刷屏
    getter = getattr(image, "get_flattened_data", None)
    return list(getter() if getter is not None else image.getdata())


def _white_pct(img: Image.Image) -> float:
    gray = img.convert("L")
    pixels = _pixel_values(gray)
    white = sum(1 for v in pixels if v >= WHITE_PIXEL_MIN)
    return 100.0 * white / len(pixels)


def _density_grid_cv(img: Image.Image, n: int = 4) -> float:
    gray = img.convert("L")
    width, height = gray.size
    cell_w, cell_h = max(width // n, 1), max(height // n, 1)
    densities: list[float] = []
    for row in range(n):
        for col in range(n):
            crop = gray.crop((col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h))
            ink = 100.0 - 100.0 * sum(1 for v in _pixel_values(crop) if v >= WHITE_PIXEL_MIN) / (cell_w * cell_h)
            densities.append(ink)
    mean = sum(densities) / len(densities) or 1e-6
    variance = sum((d - mean) ** 2 for d in densities) / len(densities)
    return (variance**0.5) / mean


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        v = value / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])


def _contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    hi, lo = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _effective_background(target: _Shape, filled: list[_Shape]) -> tuple[int, int, int]:
    """文本的有效背景 = 同页覆盖该文本框面积比重最大的填色矩形；无覆盖 → 白色。

    比重相同时取 z 顺序更靠后（更上层）的矩形：后画的才是眼睛看到的背景。
    """
    area = max(target.w * target.h, 1e-9)
    best_cov, best = 0.0, None
    for rect in filled:
        if rect.fill is None:
            continue
        ox, oy = _overlap_in(target, rect)
        if ox <= 0 or oy <= 0:
            continue
        coverage = (ox * oy) / area
        if coverage >= best_cov:
            best_cov, best = coverage, rect
    return best.fill if best is not None else WHITE_RGB


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


# ─────────────────────────────────────────────────────────────────────────────
# plan（ResolvedVisualPlan / deck spec 投影）读取
# ─────────────────────────────────────────────────────────────────────────────


def _plan_pages(plan: Any) -> dict[int, dict[str, Any]]:
    if plan is None:
        return {}
    if isinstance(plan, (str, Path)):
        path = Path(plan)
        if not path.exists():
            return {}
        plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        return {}
    pages: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(plan.get("slides") or [], 1):
        if not isinstance(raw, dict):
            continue
        page_no = raw.get("page_no") or raw.get("page") or raw.get("page_index") or index
        try:
            pages[int(page_no)] = raw
        except (TypeError, ValueError):
            pages[index] = raw
    return pages


def _plan_declares_asset(page_plan: dict[str, Any]) -> bool:
    """plan 是否声明了本页应有资产（asset_bindings / figure slot / assets）。"""
    if not page_plan:
        return False
    if page_plan.get("asset_bindings") or page_plan.get("assets") or page_plan.get("asset_ids"):
        return True
    for slot in page_plan.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        if str(slot.get("slot") or slot.get("name") or "").lower() == "figure":
            return True
        if slot.get("asset_ids") or slot.get("asset_id"):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 逐维检测
# ─────────────────────────────────────────────────────────────────────────────


def _check_bounds(shapes: list[_Shape]) -> list[_Defect]:
    defects: list[_Defect] = []
    for s in shapes:
        if s.x < -BOUND_TOL_IN or s.y < -BOUND_TOL_IN:
            defects.append(
                _Defect("off_slide", "p0", f"{s.sid} 起点越出页面：({s.x:.2f},{s.y:.2f}) 为负坐标", [s.sid])
            )
        if s.x + s.w > SLIDE_W_IN + BOUND_TOL_IN or s.y + s.h > SLIDE_H_IN + BOUND_TOL_IN:
            defects.append(
                _Defect(
                    "overflow",
                    "p0",
                    f"{s.sid} 溢出：终点 ({s.x + s.w:.2f},{s.y + s.h:.2f}) 超出页面 {SLIDE_W_IN}×{SLIDE_H_IN} in",
                    [s.sid],
                )
            )
    return defects


def _check_overlap(text_shapes: list[_Shape]) -> list[_Defect]:
    defects: list[_Defect] = []
    for index, a in enumerate(text_shapes):
        for b in text_shapes[index + 1 :]:
            ox, oy = _overlap_in(a, b)
            if ox > OVERLAP_TOL_IN and oy > OVERLAP_TOL_IN:
                defects.append(
                    _Defect(
                        "overlap",
                        "p0",
                        f"文本框重叠：{a.sid!r} 与 {b.sid!r} 双轴交叠 {ox:.2f}×{oy:.2f} in（> {OVERLAP_TOL_IN} in）",
                        [a.sid, b.sid],
                    )
                )
    return defects


def _check_duplicate(text_shapes: list[_Shape]) -> list[_Defect]:
    defects: list[_Defect] = []
    seen: dict[str, str] = {}
    for s in text_shapes:
        key = re.sub(r"\s+", "", s.text)
        if len(key) < DUPLICATE_MIN_CHARS + 1:
            continue
        if key in seen:
            defects.append(
                _Defect(
                    "duplicate",
                    "p0",
                    f"同页重复文本：{seen[key]!r} 与 {s.sid!r} 去空白后完全相同（{s.text[:40]!r}）",
                    [seen[key], s.sid],
                )
            )
        else:
            seen[key] = s.sid
    return defects


def _check_graphic(
    shapes: list[_Shape], *, declares_asset: bool, has_picture: bool, page_plan: dict[str, Any]
) -> list[_Defect]:
    """空框伪装：plan 声明了资产、页内却无 picture，只剩大面积填色无文字矩形。

    为什么需要「兄弟文本覆盖 ≥30%」豁免：card + title + body 的标注卡合法，
    没有这条会把正常卡片判成空框。
    """
    if not declares_asset or has_picture:
        return []
    defects: list[_Defect] = []
    for rect in shapes:
        if rect.has_picture or rect.text or rect.fill is None:
            continue
        if rect.w < GRAPHIC_MIN_W_IN or rect.h < GRAPHIC_MIN_H_IN:
            continue
        if rect.fill == WHITE_RGB:
            continue
        has_label = False
        for other in shapes:
            if other is rect or not other.text:
                continue
            ox, oy = _overlap_in(rect, other)
            if ox > 0 and oy > 0 and (ox * oy) >= GRAPHIC_LABEL_COVER * (rect.w * rect.h):
                has_label = True
                break
        if has_label:
            continue
        defects.append(
            _Defect(
                "graphic",
                "p0",
                f"{rect.sid} 是 {rect.w:.1f}×{rect.h:.1f} in 的 {_hex(rect.fill)} 空框：本页 "
                f"{page_plan.get('slide_id', '?')} 的 plan 声明了资产却未渲染出 picture（空框伪装）",
                [rect.sid],
            )
        )
    return defects


def _check_contrast(text_shapes: list[_Shape], filled: list[_Shape]) -> list[_Defect]:
    defects: list[_Defect] = []
    for s in text_shapes:
        background = _effective_background(s, filled)
        colors = s.colors or (DEFAULT_TEXT_RGB,)
        worst = min(colors, key=lambda c: _contrast_ratio(c, background))
        ratio = _contrast_ratio(worst, background)
        if ratio < CONTRAST_MIN_RATIO:
            defects.append(
                _Defect(
                    "contrast",
                    "p0",
                    f"{s.sid} 文本 {_hex(worst)} 对有效背景 {_hex(background)} 的对比度 {ratio:.2f}:1 "
                    f"< {CONTRAST_MIN_RATIO}:1（WCAG AA）",
                    [s.sid],
                )
            )
    return defects


def _check_font(text_shapes: list[_Shape], page_text: str) -> list[_Defect]:
    defects: list[_Defect] = []
    for s in text_shapes:
        if s.sizes and 0 < min(s.sizes) < TINY_FONT_BODY_PT:
            defects.append(
                _Defect(
                    "font",
                    "p1",
                    f"{s.sid} 正文字号 {min(s.sizes):g}pt < {TINY_FONT_BODY_PT:g}pt",
                    [s.sid],
                )
            )
    missing = [c for c in missing_glyphs(page_text) if not MATH_CHAR_RE.match(c)]
    if missing:
        defects.append(
            _Defect("font", "p1", f"整条字体链无字形覆盖：{len(missing)} 个字符 {''.join(missing)[:40]!r}", [])
        )
    return defects


def _math_truncation(text: str) -> str | None:
    if not MATH_CHAR_RE.search(text):
        return None
    # 先中和区间/集合记号：只脱掉它的括号、保留内容（`[0,+∞)` → `0,+∞`），使左闭右开
    # 的括号不再参与配平。若整体删除，`(1,2] ∪ [3,+∞)` 反会残留孤立的 `∪` 被误判为
    # 「结尾悬挂算符」——保留内容则无此副作用。
    text = _INTERVAL_NOTATION_RE.sub(lambda m: m.group(0)[1:-1], text)
    for opener, closer in DELIM_PAIRS:
        if text.count(opener) != text.count(closer):
            return f"括号不闭合（{opener} {text.count(opener)} 个 / {closer} {text.count(closer)} 个）"
    tail = text.rstrip()
    if tail and tail[-1] in DANGLING_TAIL_CHARS:
        return f"结尾悬挂算符 {tail[-1]!r}"
    return None


def _check_formula(text_shapes: list[_Shape]) -> list[_Defect]:
    defects: list[_Defect] = []
    math_text = "\n".join(s.text for s in text_shapes if s.text and MATH_CHAR_RE.search(s.text))
    if not math_text:
        return defects
    math_missing = [c for c in missing_glyphs(math_text) if MATH_CHAR_RE.match(c)]
    if math_missing:
        defects.append(
            _Defect(
                "formula",
                "p1",
                f"数学字形无覆盖：{len(math_missing)} 个字符 {''.join(math_missing)[:40]!r}（公式会渲染成豆腐块）",
                [],
            )
        )
    for s in text_shapes:
        reason = _math_truncation(s.text)
        if reason:
            defects.append(
                _Defect(
                    "formula",
                    "p1",
                    f"{s.sid} 数学表达式被截断（{reason}），已丢失数学字符：{s.text[-40:]!r}",
                    [s.sid],
                )
            )
    return defects


def _check_png(png_path: Path) -> tuple[list[_Defect], dict[str, float]]:
    with Image.open(png_path) as handle:
        image = handle.convert("RGB")
    stddev = float(ImageStat.Stat(image.convert("L")).stddev[0])
    cv = _density_grid_cv(image)
    ink_pct = 100.0 - _white_pct(image)
    defects: list[_Defect] = []
    if stddev < BLANK_STDDEV_MIN:
        defects.append(_Defect("blank", "p1", f"渲染 PNG 灰度方差 {stddev:.1f} < {BLANK_STDDEV_MIN}（空白页）", []))
    if ink_pct < DENSITY_LOW_PCT:
        defects.append(
            _Defect(
                "density",
                "info",
                f"ink 占比 {ink_pct:.1f}% < {DENSITY_LOW_PCT}%（信息性，不卡门）",
                [],
            )
        )
    elif cv > DENSITY_CLUMP_CV and ink_pct < 30:
        defects.append(
            _Defect(
                "density",
                "info",
                f"4×4 网格 CV={cv:.2f} > {DENSITY_CLUMP_CV} 且 ink {ink_pct:.1f}% < 30%（小团文字+大面积空白；信息性，不卡门）",
                [],
            )
        )
    return defects, {"ink_pct": round(ink_pct, 2), "cv": round(cv, 2)}


def _merge_by_kind(defects: list[_Defect]) -> list[_Defect]:
    """单页内每个 kind 最多保留 1 条 defect：同 kind 合并消息与 shape_ids，不丢信息。"""
    merged: dict[str, _Defect] = {}
    order: list[str] = []
    for defect in defects:
        if defect.kind not in merged:
            merged[defect.kind] = _Defect(defect.kind, defect.severity, defect.message, list(defect.shape_ids))
            order.append(defect.kind)
            continue
        kept = merged[defect.kind]
        kept.message = f"{kept.message}；{defect.message}"
        for sid in defect.shape_ids:
            if sid not in kept.shape_ids:
                kept.shape_ids.append(sid)
    return [merged[kind] for kind in order]


def _page_qa(
    slide: Any,
    page_no: int,
    png_path: Path,
    page_plan: dict[str, Any],
    content_defects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    shapes = _collect_shapes(slide)
    has_picture = any(s.has_picture for s in shapes)
    text_shapes = [s for s in shapes if s.text and not _is_decorative(s)]
    filled = [s for s in shapes if s.fill is not None]

    defects: list[_Defect] = []
    defects += _check_bounds(shapes)
    defects += _check_overlap(text_shapes)
    defects += _check_duplicate(text_shapes)
    defects += _check_graphic(
        shapes,
        declares_asset=_plan_declares_asset(page_plan),
        has_picture=has_picture,
        page_plan=page_plan,
    )
    defects += _check_contrast(text_shapes, filled)
    defects += _check_font(text_shapes, "\n".join(s.text for s in text_shapes if s.text))
    defects += _check_formula(text_shapes)
    # 内容维（R8-T）：教研批注 / 工程黑话 / 模板前缀 / 裸 LaTeX / 内容单薄 / 跨页重复。
    for item in content_defects or []:
        defects.append(
            _Defect(
                str(item.get("kind") or "content"),
                str(item.get("severity") or "p1"),
                str(item.get("message") or ""),
                list(item.get("shape_ids") or []),
            )
        )

    metrics: dict[str, Any] = {"ink_pct": 0.0, "cv": 0.0, "shape_count": len(shapes), "has_picture": has_picture}
    if png_path.exists():
        png_defects, png_metrics = _check_png(png_path)
        defects += png_defects
        metrics.update(png_metrics)

    kept = _merge_by_kind(defects)
    gating = [d for d in kept if d.severity in ("p0", "p1")]
    if gating:
        status = "fail"
    elif not png_path.exists():
        # 无渲染证据 → 像素级维度（blank/density）未验证，不能报 pass
        status = "unknown"
    else:
        status = "pass"
    return {
        "page_no": page_no,
        "status": status,
        "defects": [d.as_dict() for d in kept],
        "metrics": metrics,
    }


def _summarize(pages: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    p0_count = p1_count = 0
    failed_pages: list[int] = []
    for page in pages:
        if page["status"] == "fail":
            failed_pages.append(page["page_no"])
        for defect in page["defects"]:
            by_kind[defect["kind"]] = by_kind.get(defect["kind"], 0) + 1
            if defect["severity"] == "p0":
                p0_count += 1
            elif defect["severity"] == "p1":
                p1_count += 1
    return {"p0_count": p0_count, "p1_count": p1_count, "failed_pages": failed_pages, "by_kind": by_kind}


def detect_deck_qa(
    pptx_path: Any,
    pages_dir: Any,
    *,
    plan: Any = None,
    spec_pages: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """逐页检测整份 deck：真实 PPTX + 真实渲染 PNG（page-NN.png），纯函数、无网络无 DB。

    视觉维（TP-H1 rubric）+ 内容维（R8-T：批注/黑话/模板前缀/裸 LaTeX/内容单薄/跨页重复）。
    `spec_pages` 由调用方（qa 编排层）从 deck spec 读出，是内容维的**必需输入**：没有 spec
    就没有「元素/role/跨页重复」的权威判定源，此时只跑视觉维（不静默报内容通过）。
    """
    from app.ppt_v2.qa import content as content_qa

    pages_dir = Path(pages_dir)
    plan_pages = _plan_pages(plan)
    presentation = Presentation(str(pptx_path))
    content_map = (
        content_qa.detect_content_defects(
            pptx_lines=content_qa.deck_lines(presentation),
            spec_pages=spec_pages or {},
        )
        if spec_pages
        else {}
    )
    pages = [
        _page_qa(
            slide,
            index + 1,
            pages_dir / f"page-{index + 1:02d}.png",
            plan_pages.get(index + 1, {}),
            content_map.get(index + 1),
        )
        for index, slide in enumerate(presentation.slides)
    ]
    return {
        "schema": SCHEMA,
        "detector_version": DETECTOR_VERSION,
        "pages": pages,
        "summary": _summarize(pages),
    }


def detect_page_qa(
    pptx_path: Any,
    page_no: int,
    png_path: Any,
    *,
    plan: Any = None,
    spec_pages: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """单页检测（供 POST /qa/retry 用）：返回与 pages[] 同一元素形状 + schema/detector_version。

    跨页重复维需要整册上下文，单页重试不判定；内容维同样需要 spec_pages（无则只跑视觉维）。
    """
    from app.ppt_v2.qa import content as content_qa

    presentation = Presentation(str(pptx_path))
    slides = list(presentation.slides)
    if not 1 <= int(page_no) <= len(slides):
        raise ValueError(f"page_no={page_no} 超出 deck 页数 {len(slides)}")
    page_plan = _plan_pages(plan).get(int(page_no), {})
    content_defects = (
        content_qa.content_defects_for_page(
            page_no=int(page_no),
            pptx_lines=content_qa.slide_lines(slides[int(page_no) - 1]),
            spec_page=(spec_pages or {}).get(int(page_no)),
        )
        if spec_pages
        else []
    )
    element = _page_qa(
        slides[int(page_no) - 1],
        int(page_no),
        Path(png_path),
        page_plan,
        content_defects,
    )
    return {"schema": SCHEMA, "detector_version": DETECTOR_VERSION, **element}
