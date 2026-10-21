"""PIL 文本度量 + overflow / overlap / 截断检测。

度量口径：1 inch = 96 px；font pt → px = pt * 96 / 72。
CJK 按字换行，Latin 按词换行；单行元素（标题/页脚）按整串宽度判定 overflow_w。
overlap：文本/图形 bbox 两两相交面积 > 容忍阈值 → 记录冲突对（页眉装饰条与标题、
页脚与正文故意贴近，不在检测名单）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import ImageFont

from presenton_adapter.engine.fonts import font_path_for

PX_PER_INCH = 96.0
EMU_PER_INCH = 914400.0


@dataclass(frozen=True)
class ShapeBox:
    shape_id: str
    kind: str  # title | body | formula | figure | footer | accent | chip
    x: float  # inch
    y: float
    w: float
    h: float
    text: str = ""
    font_pt: float = 14.0
    single_line: bool = False
    qa_exempt: bool = False  # 装饰元素不参与 overlap 检测


@dataclass
class ShapeMetrics:
    shape_id: str
    overflow_w: bool = False
    overflow_h: bool = False
    est_lines: int = 1
    truncated_chars: int = 0
    needed_h_in: float = 0.0


@dataclass
class PageMetrics:
    shapes: list[ShapeMetrics] = field(default_factory=list)
    overlaps: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_overflow(self) -> bool:
        return any(s.overflow_w or s.overflow_h for s in self.shapes)

    @property
    def truncated_chars(self) -> int:
        return sum(s.truncated_chars for s in self.shapes)

    @property
    def has_overlap(self) -> bool:
        return bool(self.overlaps)


def _font_for_measurement(font_pt: float):
    path = font_path_for("Microsoft YaHei") or font_path_for("SimSun")
    px = max(int(round(font_pt * PX_PER_INCH / 72.0)), 4)
    try:
        return ImageFont.truetype(str(path), px)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _text_width_px(font: ImageFont.FreeTypeFont, text: str) -> float:
    try:
        return float(font.getlength(text))
    except Exception:  # noqa: BLE001
        return float(font.size * len(text))


def _wrap_lines(font: ImageFont.FreeTypeFont, text: str, max_w_px: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        latin_run = ""
        for ch in para:
            candidate = cur + ch
            if _text_width_px(font, candidate) <= max_w_px:
                cur = candidate
                latin_run = latin_run + ch if (ch.isascii() and (ch.isalnum() or ch in " .,;:")) else ""
                continue
            # 换行点：Latin 词尾优先，否则按字断行
            if latin_run and latin_run in cur:
                cut = cur.rfind(latin_run) + len(latin_run)
                lines.append(cur[:cut])
                cur = cur[cut:] + ch
            else:
                lines.append(cur)
                cur = ch
            latin_run = latin_run + ch if (ch.isascii() and (ch.isalnum() or ch in " .,;:")) else ""
        lines.append(cur)
    return lines


def measure_shape(box: ShapeBox) -> ShapeMetrics:
    m = ShapeMetrics(shape_id=box.shape_id)
    if not box.text or box.kind in ("accent", "figure", "chip"):
        return m
    font = _font_for_measurement(box.font_pt)
    box_w_px = box.w * PX_PER_INCH
    box_h_px = box.h * PX_PER_INCH
    if box.single_line:
        m.est_lines = 1
        m.overflow_w = _text_width_px(font, box.text) > box_w_px
        m.needed_h_in = box.font_pt * 1.5 / 72.0
        m.overflow_h = m.needed_h_in > box.h + 0.02
        return m
    # 正文：按行高 ≈ font_pt * 1.45 pt 估算
    inner_w_px = max(box_w_px - 8, 10)  # 内边距
    lines = _wrap_lines(font, box.text, inner_w_px)
    m.est_lines = len(lines)
    line_h_px = box.font_pt * 1.45 * PX_PER_INCH / 72.0
    needed_px = m.est_lines * line_h_px
    m.needed_h_in = needed_px / PX_PER_INCH
    m.overflow_h = m.needed_h_in > box.h + 0.03
    # 截断：超出容量高度可容纳的行数 × 每行字符当量
    if m.overflow_h:
        fit_lines = max(int(box_h_px // line_h_px), 0)
        overflow_lines = m.est_lines - fit_lines
        per_line = max(len(box.text) // max(m.est_lines, 1), 1)
        m.truncated_chars = max(overflow_lines * per_line, 0)
    return m


def measure_page(boxes: list[ShapeBox]) -> PageMetrics:
    pm = PageMetrics()
    for b in boxes:
        pm.shapes.append(measure_shape(b))
    qa_boxes = [b for b in boxes if not b.qa_exempt and b.kind not in ("accent",)]
    for i in range(len(qa_boxes)):
        for j in range(i + 1, len(qa_boxes)):
            a, b = qa_boxes[i], qa_boxes[j]
            ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
            oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
            if ox > 0.04 and oy > 0.04:  # 容忍 0.04 inch 贴边
                pm.overlaps.append((a.shape_id, b.shape_id))
    return pm


def emu(inches: float) -> int:
    return int(round(inches * EMU_PER_INCH))


def safe_line_count(text: str, box_w_in: float, font_pt: float) -> int:
    font = _font_for_measurement(font_pt)
    return len(_wrap_lines(font, text, box_w_in * PX_PER_INCH - 8))


def estimate_height_in(text: str, box_w_in: float, font_pt: float) -> float:
    lines = safe_line_count(text, box_w_in, font_pt)
    return lines * font_pt * 1.45 / 72.0


def clamp_lines_to_fit(text: str, box_w_in: float, box_h_in: float, font_pt: float) -> str:
    """确定性截断保护：构造时保证不溢出（QA 仍独立复核，双保险）。"""
    max_h = box_h_in
    if estimate_height_in(text, box_w_in, font_pt) <= max_h:
        return text
    font = _font_for_measurement(font_pt)
    max_w = box_w_in * PX_PER_INCH - 8
    line_h = font_pt * 1.45 * PX_PER_INCH / 72.0
    fit_lines = max(int((box_h_in * PX_PER_INCH) // line_h) - 1, 1)
    out: list[str] = []
    used = 0
    for para in text.split("\n"):
        if used >= fit_lines:
            break
        cur = ""
        for ch in para:
            if used >= fit_lines:
                break
            if _text_width_px(font, cur + ch) <= max_w:
                cur += ch
            else:
                out.append(cur)
                used += 1
                cur = ch
                if used >= fit_lines:
                    break
        out.append(cur)
        used += 1
    # 省略号提示截断
    tail = out[-1]
    if used > fit_lines or estimate_height_in("\n".join(out), box_w_in, font_pt) > max_h:
        while tail and estimate_height_in("\n".join(out[:-1] + [tail + "…"]), box_w_in, font_pt) > max_h:
            tail = tail[:-1]
        out = out[:-1] + [tail + "…"]
    return "\n".join(out)


def approx_ceil(x: float) -> int:
    return int(math.ceil(x - 1e-9))
