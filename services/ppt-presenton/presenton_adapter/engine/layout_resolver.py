"""Engine layout resolver —— family + tokens → 元素几何（坐标唯一合法产生地）。

ResolvedVisualPlan 只含语义槽位；本模块把 (family, slots, tokens) 编译为
确定性元素列表（rect/text/picture），builder 据此绘制。层级：
  VisualPlan(语义) → 本模块(坐标) → builder(OOXML) → QA(metrics 独立复核)

坐标纪律：所有字号取自 deck tokens 的档位映射；几何常量集中本文件；
任何槽位文本溢出由 clamp_lines_to_fit 防护（QA 仍独立复核，双保险）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from presenton_adapter.engine import metrics as M
from presenton_adapter.engine.metrics import ShapeBox

SLIDE_W = 13.333
SLIDE_H = 7.5
MARGIN = 0.90
HEADER_GAP = 0.42
BLOCK_GAP = 0.34
GUTTER = 0.52
CONTENT_W = SLIDE_W - 2 * MARGIN  # 11.53

# 参数化数学对象（slide.math_object）在 figure 槽位中的资产占位 id。
# 8200 视觉计划用它把「页承载 math_object」表达为 figure 槽位；8201 渲染时把它
# 换成引擎确定性渲染出的 PNG 字节（presenton_adapter.api.render_page 注入）。
MATH_OBJECT_ASSET_ID = "__math_object__"
FIG_MIN_W = 4.6     # 图形面板最小宽度（inch）
FIG_MAX_W = 5.8     # 图形面板最大宽度（inch）
FIG_MIN_TEXT_W = 4.8  # 有图时文本栏最小宽度（inch）

# 语义色（与 8200 tokens COLOR_ROLES 同源，本地扁平映射）
C_INK = "1E293B"
C_MUTED = "64748B"
C_SUBTLE = "94A3B8"
C_BRAND = "2563EB"
C_BRAND_BG = "EFF6FF"
C_SURFACE = "FFFFFF"
C_SOFT_BG = "F1F5F9"
C_LINE = "E2E8F0"

FONT_PRIMARY = "Microsoft YaHei"

MATH_CHARS = set("∈∉∋⊆⊇⊂⊃∪∩∅≤≥≠≈±×÷²³∑∫√∞∥⊥∠°′″∀∃→←↔⇒⇔·−")

# ── LaTeX → Unicode 过渡排版（R16 Math Visual 前的临时方案，确定性纯函数）──────
# 只处理常见 inline 模式；未识别命令原样保留（诚实，不猜测不破坏）。
_LATEX_SYMBOLS = {
    "\\leq": "≤", "\\le": "≤", "\\geq": "≥", "\\ge": "≥", "\\neq": "≠", "\\ne": "≠",
    "\\pm": "±", "\\mp": "∓", "\\times": "×", "\\div": "÷", "\\cdot": "·",
    "\\approx": "≈", "\\equiv": "≡", "\\in": "∈", "\\notin": "∉",
    "\\subseteq": "⊆", "\\subset": "⊂", "\\supseteq": "⊇", "\\supset": "⊃",
    "\\cup": "∪", "\\cap": "∩", "\\emptyset": "∅", "\\varnothing": "∅",
    "\\infty": "∞", "\\sqrt": "√", "\\perp": "⊥", "\\parallel": "∥",
    "\\angle": "∠", "\\degree": "°", "\\Rightarrow": "⇒", "\\rightarrow": "→",
    "\\leftarrow": "←", "\\leftrightarrow": "↔", "\\Leftrightarrow": "⇔",
    "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ", "\\theta": "θ",
    "\\lambda": "λ", "\\mu": "μ", "\\pi": "π", "\\rho": "ρ", "\\sigma": "σ",
    "\\omega": "ω", "\\Delta": "Δ", "\\Omega": "Ω", "\\sum": "∑", "\\int": "∫",
}
_SUP_MAP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
            "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "n": "ⁿ", "i": "ⁱ"}
_SUB_MAP = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
            "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "n": "ₙ", "i": "ᵢ"}


def latex_to_unicode(text: str) -> str:
    """常见 inline LaTeX → 可读 Unicode 纯文本（确定性；无相关记号时零开销直返）。"""
    if not text or not re.search(r"[$\\^_]", text):
        return text
    out = text
    out = re.sub(r"\\(?:d)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", out)
    out = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", out)
    out = re.sub(r"\^\s*\{([^{}]+)\}", lambda m: "".join(_SUP_MAP.get(c, c) for c in m.group(1)), out)
    out = re.sub(r"_\s*\{([^{}]+)\}", lambda m: "".join(_SUB_MAP.get(c, c) for c in m.group(1)), out)
    out = re.sub(r"\^\s*([0-9+.\-])", lambda m: _SUP_MAP.get(m.group(1), m.group(0)), out)
    out = re.sub(r"_\s*([0-9+.\-])", lambda m: _SUB_MAP.get(m.group(1), m.group(0)), out)
    for cmd in sorted(_LATEX_SYMBOLS, key=len, reverse=True):  # 最长优先：\in 不得吃掉 \infty
        out = out.replace(cmd, _LATEX_SYMBOLS[cmd])
    out = out.replace("\\left", "").replace("\\right", "")
    out = re.sub(r"\$\s*([^$]*?)\s*\$", r"\1", out)
    return out


@dataclass
class Element:
    kind: str  # rect | text | picture
    shape_id: str
    qa_kind: str  # title | body | formula | figure | footer | chip | kicker
    x: float
    y: float
    w: float
    h: float
    text: str = ""
    font_pt: float = 14.0
    bold: bool = False
    color: str = C_INK
    fill: str | None = None
    stroke: str | None = None
    radius: float | None = None
    align: str = "left"      # left | center | right
    anchor: str = "top"      # top | middle
    single_line: bool = False
    line_spacing: float = 1.4
    asset_ids: list[str] = dc_field(default_factory=list)
    qa_exempt: bool = False


def _ts(tokens: dict[str, Any] | None, key: str, default: float) -> float:
    try:
        return float((tokens or {}).get("type_scale_pt", {}).get(key, default))
    except Exception:  # noqa: BLE001
        return default


# ── 图形槽位（figure）：参数化 math_object / 绑定资产 → 右侧图形面板 ──────────


def _figure_ids(slots: dict[str, Any]) -> list[str]:
    """figure 槽位的资产 id（含 math_object 占位 id）；无图 → 空列表。"""
    ids = (slots.get("figure") or {}).get("asset_ids") or []
    return [str(i) for i in ids if i]


def _split_for_figure(slots: dict[str, Any]) -> tuple[float, float, float]:
    """有图 → (文本栏宽, 图形面板 x, 图形面板宽)；无图 → (CONTENT_W, 0, 0)。

    图占右侧，文本栏保底 FIG_MIN_TEXT_W，保证不压字（宽度收窄而非裁内容）。
    """
    if not _figure_ids(slots):
        return CONTENT_W, 0.0, 0.0
    fig_w = max(FIG_MIN_W, min(FIG_MAX_W, CONTENT_W - FIG_MIN_TEXT_W - GUTTER))
    text_w = CONTENT_W - fig_w - GUTTER
    return text_w, MARGIN + text_w + GUTTER, fig_w


def _figure_panel(slots: dict[str, Any], x: float, w: float, y_top: float, y_bottom: float) -> list[Element]:
    """图形面板（底框 + picture）；无图或高度不足 → 空列表。"""
    ids = _figure_ids(slots)
    h = y_bottom - y_top
    if not ids or w < 1.0 or h < 1.2:
        return []
    return [
        Element("rect", "diag-frame", "figure", x, y_top, w, h,
                fill=C_SOFT_BG, stroke=C_LINE, radius=0.10, qa_exempt=True),
        Element("picture", "diag", "figure", x + 0.18, y_top + 0.18,
                w - 0.36, h - 0.36, asset_ids=list(ids)),
    ]


def resolve_page(req_page_no: int, total_pages: int, deck_title: str,
                 slide: dict[str, Any], vp: dict[str, Any],
                 tokens: dict[str, Any] | None) -> list[Element]:
    """per-slide visual plan → 元素列表（含页眉页脚/装饰）。"""
    fam = vp["layout_family"]
    # LaTeX → Unicode 过渡排版：槽位文本与标题统一净化（确定性；引擎内唯一应用点）
    for s in vp.get("slots", []):
        if isinstance(s, dict):
            if s.get("text"):
                s["text"] = latex_to_unicode(str(s["text"]))
            if s.get("items"):
                s["items"] = [latex_to_unicode(str(it)) for it in s["items"]]
    slide = {**slide, "title": latex_to_unicode(str(slide.get("title", "")))}
    slots = {s["slot"]: s for s in vp.get("slots", [])}
    # R8-U：页承载参数化 math_object → 强制 figure 槽位（图形由引擎渲染为 PNG 内嵌）。
    # 幂等：计划已带 figure 槽位时不覆盖；无 math_object 的页行为不变。
    if isinstance(slide.get("math_object"), dict) and "figure" not in slots:
        slots["figure"] = {"slot": "figure", "asset_ids": [MATH_OBJECT_ASSET_ID]}
    els: list[Element] = []
    title_pt = _ts(tokens, "title", 32.0)
    support_pt = _ts(tokens, "support", 15.0)
    caption_pt = _ts(tokens, "caption", 12.0)

    # ── 页眉：kicker 阶段标签 + 角色标签 + accent bar；页脚（全族统一）──
    # R21.1 修复：phase 与 role 同文案时去重（"情境导入 · 情境导入"）
    phase_zh = str(vp.get("phase_zh", "") or "")
    role_zh = str(vp.get("role_label_zh", "") or "")
    if not phase_zh or phase_zh == role_zh:
        kicker = role_zh or phase_zh
    else:
        kicker = f"{phase_zh} · {role_zh}"
    els.append(Element("rect", "accent", "accent", 0.60, 0.50, 0.16, 0.52, fill=C_BRAND, qa_exempt=True))
    els.append(Element("text", "kicker", "kicker", 0.92, 0.47, 7.0, 0.34, text=kicker,
                       font_pt=support_pt, bold=True, color=C_BRAND, single_line=True, qa_exempt=False))
    els.append(Element("rect", "chip-bg", "chip", 10.92, 0.44, 1.82, 0.42,
                       fill=C_BRAND_BG, radius=0.5, qa_exempt=True))
    els.append(Element("text", "chip", "chip", 10.92, 0.44, 1.82, 0.42,
                       text=str(vp.get("role_label_zh", "")), font_pt=11, bold=True, color=C_BRAND,
                       align="center", anchor="middle", single_line=True, qa_exempt=True))
    els.append(Element("text", "footer", "footer", 0.60, 7.06, 10.4, 0.30,
                       text=deck_title, font_pt=caption_pt, color=C_SUBTLE,
                       single_line=True, qa_exempt=True))
    els.append(Element("text", "pageno", "footer", 12.02, 7.06, 0.72, 0.30,
                       text=f"{req_page_no:02d} / {total_pages:02d}", font_pt=caption_pt,
                       color=C_SUBTLE, align="right", single_line=True, qa_exempt=True))

    # 标题（统一：左上，32pt+）
    # TP-H1：hero-context 族自带 hero-title，标准 title 与之重复 → 跳过；
    #       其他族无自渲染 title，仍由本处统一产生。
    fam_emits_own_title = fam in {"hero-context"}
    if not fam_emits_own_title:
        title = str(slots.get("title", {}).get("text", slide.get("title", "")))
        title_lines = M.safe_line_count(title, CONTENT_W, title_pt)
        t_h = max(0.62, title_lines * title_pt * 1.35 / 72.0 + 0.08)
        els.append(Element("text", "title", "title", MARGIN, 0.94, CONTENT_W, t_h, text=title,
                           font_pt=title_pt, bold=True, color=C_INK,
                           single_line=(title_lines == 1), line_spacing=1.15))
        body_top = 0.94 + t_h + HEADER_GAP * 0.75
    else:
        body_top = 0.94 + 1.50 + HEADER_GAP * 0.75  # hero 占用顶部 ~2.4 inch；body 起点给到 ~2.4

    handler = _HANDLERS.get(fam)
    if handler is None:
        handler = _h_generic  # 未知族 → 通用降级（fallback_policy.family_unsupported）
    els.extend(handler(slots, body_top, tokens, vp))
    return els


# ─────────── 族布局函数（坐标在此产生） ───────────


def _h_hero(slots, top, tokens, vp):
    display = _ts(tokens, "display", 44.0)
    support = _ts(tokens, "support", 15.0)
    body = _ts(tokens, "body", 18.0)
    # TP-H1：原 hero-kicker y=2.42 h=0.36 (ends 2.78) 与 hero-title y=2.62 (start 2.62)
    # 垂直重叠 0.16 inch → QA 报 P0 overlap。改为 title 下移 0.23 inch，间隙清晰。
    els = [
        Element("rect", "hero-band", "figure", 0.0, 2.30, SLIDE_W, 2.90, fill=C_BRAND_BG, qa_exempt=True),
        Element("text", "hero-kicker", "kicker", 2.40, 2.45, SLIDE_W - 4.8, 0.30,
                text=slots.get("kicker", {}).get("text", ""), font_pt=support, bold=True,
                color=C_BRAND, align="center", single_line=True, qa_exempt=True),
        Element("text", "hero-title", "title", 1.20, 2.85, SLIDE_W - 2.4, 1.30,
                text=slots.get("title", {}).get("text", ""), font_pt=display, bold=True,
                color=C_INK, align="center", anchor="middle", single_line=True),
        Element("text", "hero-sub", "body", 2.40, 4.25, SLIDE_W - 4.8, 0.55,
                text=slots.get("subtitle", {}).get("text", ""), font_pt=body, color=C_MUTED,
                align="center", anchor="middle", single_line=True),
        Element("text", "hero-meta", "support", 2.40, 5.75, SLIDE_W - 4.8, 0.40,
                text=slots.get("meta", {}).get("text", ""), font_pt=support, color=C_MUTED,
                align="center", single_line=True),
    ]
    return els


def _h_numbered(slots, top, tokens, vp):
    items = slots.get("numbered-items", {}).get("items", [])
    els: list[Element] = []
    body_pt = _ts(tokens, "body", 18.0)
    text_col_w, fig_x, fig_w = _split_for_figure(slots)
    n = max(len(items), 1)
    cols = 1 if fig_w else (2 if n > 3 else 1)
    rows = (n + cols - 1) // cols
    cell_h = min(0.92, (SLIDE_H - top - 0.6) / max(rows, 1))
    grid_w = text_col_w - (cols - 1) * GUTTER
    cell_w = grid_w / cols
    text_w = cell_w - 0.68
    for i, item in enumerate(items[:12]):
        r, c = divmod(i, cols)
        x = MARGIN + c * (cell_w + GUTTER)
        y = top + r * (cell_h + BLOCK_GAP * 0.6)
        text = str(item)
        # 长提问自适应：字号从 body 档逐步收缩至装入格高（不截断、不丢内容）；
        # 极端超长再走 clamp 兜底（QA 独立复核，双保险）。
        pt = body_pt
        while pt > 13.0 and M.estimate_height_in(text, text_w, pt) > cell_h - 0.04:
            pt -= 1.0
        if M.estimate_height_in(text, text_w, pt) > cell_h - 0.02:
            text = M.clamp_lines_to_fit(text, text_w, cell_h - 0.04, pt)
        els.append(Element("rect", f"dot-bg-{i}", "figure", x, y, 0.52, 0.52,
                           fill=C_BRAND_BG, radius=0.5, qa_exempt=True))
        els.append(Element("text", f"dot-{i}", "figure", x, y, 0.52, 0.52, text=str(i + 1),
                           font_pt=body_pt, bold=True, color=C_BRAND, align="center",
                           anchor="middle", single_line=True, qa_exempt=True))
        els.append(Element("text", f"item-{i}", "body", x + 0.68, y, text_w, cell_h,
                           text=text, font_pt=pt, color=C_INK, anchor="middle"))
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_observe(slots, top, tokens, vp):
    body_pt = _ts(tokens, "body", 18.0)
    fig_w = 6.0
    text_w = CONTENT_W - fig_w - GUTTER
    fig_ids = list(slots.get("figure", {}).get("asset_ids", []))
    els: list[Element] = []
    if fig_ids:
        # 有图：图框 + picture 占位
        els.append(Element("rect", "fig-frame", "figure", MARGIN, top, fig_w,
                           SLIDE_H - top - 0.75, fill=C_SOFT_BG, stroke=C_LINE,
                           radius=0.10, qa_exempt=True))
        els.append(Element("picture", "figure", "figure", MARGIN + 0.25, top + 0.25,
                           fig_w - 0.5, SLIDE_H - top - 1.25,
                           asset_ids=fig_ids))
    else:
        # TP-H1：图资产缺失 → 禁止空框伪装。改为显式"缺图"提示卡 + 占位说明
        notice_h = 1.40
        els.append(Element("rect", "fig-missing-card", "figure", MARGIN, top,
                           fig_w, notice_h, fill=C_BRAND_BG, stroke=C_LINE,
                           radius=0.10, qa_exempt=True))
        els.append(Element("text", "fig-missing-title", "kicker",
                           MARGIN + 0.30, top + 0.18, fig_w - 0.60, 0.36,
                           text="图象暂缺", font_pt=_ts(tokens, "support", 15.0),
                           bold=True, color=C_BRAND, align="center",
                           anchor="middle", single_line=True))
        els.append(Element("text", "fig-missing-body", "support",
                           MARGIN + 0.30, top + 0.62, fig_w - 0.60, notice_h - 0.72,
                           text="本页为图象资产缺失降级样例。请补传教材原图或教师上传图后重渲染。",
                           font_pt=_ts(tokens, "support", 15.0), color=C_MUTED,
                           align="center", anchor="middle"))
    obs = slots.get("observations", {}).get("items", [])
    y = top
    for i, item in enumerate(obs[:6]):
        h = 0.72
        els.append(Element("rect", f"obs-line-{i}", "figure", MARGIN + fig_w + GUTTER, y,
                           0.10, h, fill=C_BRAND, qa_exempt=True))
        els.append(Element("text", f"obs-{i}", "body", MARGIN + fig_w + GUTTER + 0.26, y,
                           text_w - 0.36, h, text=str(item), font_pt=body_pt, color=C_INK,
                           anchor="middle"))
        y += h + BLOCK_GAP * 0.8
    return els


def _h_concept(slots, top, tokens, vp):
    """concept-focus = prim_lead + prim_takeaway + (可选) prim_diagram。"""
    body_pt = _ts(tokens, "body", 18.0)
    stmt = slots.get("concept-statement", {}).get("text", "")
    fig_ids = list(slots.get("figure", {}).get("asset_ids", []))
    text_w = CONTENT_W - 4.40 - GUTTER if fig_ids else CONTENT_W
    bl = M.safe_line_count(stmt, max(text_w - 0.60, 1.0), _ts(tokens, "emphasis", 22.0))
    stmt_h = max(1.05, bl * _ts(tokens, "emphasis", 22.0) * 1.5 / 72.0 + 0.30)
    available = SLIDE_H - top - 1.05
    lead_max_h = max(1.30, available - 1.5)  # 给 takeaway 至少 1.5 inch
    lead_h = min(stmt_h, lead_max_h)
    els: list[Element] = []
    els.extend(prim_lead(stmt, MARGIN, top, text_w, lead_h, tokens))
    if fig_ids:
        fig_x = MARGIN + text_w + GUTTER
        fig_w = CONTENT_W - text_w - GUTTER
        # 图存在时由 builder 决定 has_bytes；primitive 接受 asset_ids + has_bytes。
        # 此处只产出 plan 元素，不在此处校验字节；校验由 builder._build_page_from_plan 完成。
        els.append(Element("rect", "diag-frame", "figure", fig_x, top, fig_w,
                           SLIDE_H - top - 0.75, fill=C_SOFT_BG, stroke=C_LINE,
                           radius=0.10, qa_exempt=True))
        els.append(Element("picture", "diag", "figure", fig_x + 0.20, top + 0.20,
                           fig_w - 0.40, SLIDE_H - top - 1.15,
                           asset_ids=fig_ids))
    points = slots.get("points-2col", {}).get("items", [])
    takeaway_top = top + lead_h + BLOCK_GAP
    takeaway_h = SLIDE_H - takeaway_top - 1.05
    # 密度自适应：≤3 条要点的概念页用单栏居中（避免大面积空白）
    n_cols = 1 if (not fig_ids and len(points) <= 3) else 2
    els.extend(prim_takeaway(points, MARGIN, takeaway_top, CONTENT_W, takeaway_h,
                              tokens, n_cols=n_cols))
    return els


def _h_definition(slots, top, tokens, vp):
    """definition-emphasis = prim_definition（全宽横幅 + 辅助说明）；有图 → 右侧图形面板。"""
    banner = slots.get("definition-banner", {}).get("text", "")
    supporting = slots.get("supporting", {}).get("items", [])
    text_w, fig_x, fig_w = _split_for_figure(slots)
    els = prim_definition(banner, supporting, MARGIN, top, text_w,
                          SLIDE_H - top - 1.05, tokens)
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_equation(slots, top, tokens, vp):
    """equation-focus = 等式焦点框 + prim_derivation 行；有图 → 右侧图形面板。"""
    eq_pt = _ts(tokens, "equation", 26.0)
    body_pt = _ts(tokens, "body", 18.0)
    eq = slots.get("equation", {}).get("text", "")
    text_w, fig_x, fig_w = _split_for_figure(slots)
    eq_w = text_w - 1.2 if fig_w else CONTENT_W - 1.2
    eq_x = MARGIN + (0.6 if not fig_w else 0.0)
    bl = M.safe_line_count(eq, eq_w, eq_pt)
    eh = max(1.10, bl * eq_pt * 1.6 / 72.0 + 0.42)
    els = [
        Element("rect", "eq-frame", "figure", eq_x, top + 0.25, eq_w, eh,
                fill=C_SURFACE, stroke=C_LINE, radius=0.08, qa_exempt=True),
        Element("text", "eq", "formula", eq_x, top + 0.25, eq_w, eh,
                text=eq, font_pt=eq_pt, bold=True, color=C_BRAND, align="center", anchor="middle"),
    ]
    y = top + eh + 0.25 + BLOCK_GAP
    for i, ann in enumerate(slots.get("annotations", {}).get("items", [])[:5]):
        is_math = any(ch in MATH_CHARS for ch in str(ann))
        # annotations 长度自适应：单行且不过长才 single_line，否则允许换行
        ann_text = str(ann)
        ann_lines = M.safe_line_count(ann_text, text_w, _ts(tokens, "eq-inline", 20.0) if is_math else body_pt)
        hh = max(0.56, ann_lines * (eq_pt if is_math else body_pt) * 1.5 / 72.0 + 0.16)
        if y + hh > SLIDE_H - 1.05:
            break
        els.append(Element("text", f"ann-{i}", "formula" if is_math else "body",
                           MARGIN, y, text_w, hh, text=ann_text,
                           font_pt=_ts(tokens, "eq-inline", 20.0) if is_math else body_pt,
                           bold=is_math, color=C_BRAND if is_math else C_INK,
                           single_line=(ann_lines == 1)))
        y += hh + 0.14
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_steps(slots, top, tokens, vp):
    """progressive-derivation = prim_derivation（逐步推导，步步可回看）；有图 → 右侧面板。"""
    text_w, fig_x, fig_w = _split_for_figure(slots)
    els = prim_derivation(slots.get("steps", {}).get("items", []),
                          MARGIN, top, text_w,
                          SLIDE_H - top - 1.05, tokens)
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_worked(slots, top, tokens, vp):
    """worked-example = prim_example（题干 + 分步解答）；有图 → 右侧面板。"""
    problem = slots.get("problem", {}).get("text", "")
    steps = slots.get("solution-steps", {}).get("items", [])
    text_w, fig_x, fig_w = _split_for_figure(slots)
    els = prim_example(problem, steps, MARGIN, top, text_w,
                       SLIDE_H - top - 1.05, tokens)
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_practice(slots, top, tokens, vp):
    """practice-prompt = prim_prompt（提问 + 提示）；有图 → 右侧面板。"""
    prompt = slots.get("prompt", {}).get("text", "")
    hints = slots.get("hints", {}).get("items", [])
    text_w, fig_x, fig_w = _split_for_figure(slots)
    els = prim_prompt(prompt, hints, MARGIN, top, text_w,
                      SLIDE_H - top - 1.05, tokens)
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_twocol(slots, top, tokens, vp):
    body_pt = _ts(tokens, "body", 18.0)
    text_w, fig_x, fig_w = _split_for_figure(slots)
    col_w = (text_w - GUTTER) / 2
    els: list[Element] = []
    for col, slot_name in enumerate(("column-left", "column-right")):
        x = MARGIN + col * (col_w + GUTTER)
        items = slots.get(slot_name, {}).get("items", [])
        y = top
        for i, it in enumerate(items[:6]):
            els.append(Element("text", f"{slot_name}-{i}", "body", x, y, col_w, 0.56,
                               text="• " + str(it), font_pt=body_pt, color=C_INK, single_line=True))
            y += 0.64
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_checkpoint(slots, top, tokens, vp):
    body_pt = _ts(tokens, "body", 18.0)
    q = slots.get("question", {}).get("text", "")
    text_w, fig_x, fig_w = _split_for_figure(slots)
    qh = 0.95
    els = [
        Element("text", "check-q", "body", MARGIN, top, text_w, qh, text=q,
                font_pt=_ts(tokens, "emphasis", 22.0), bold=True, color=C_INK),
    ]
    y = top + qh + BLOCK_GAP
    for i, it in enumerate(slots.get("check-items", {}).get("items", [])[:4]):
        els.append(Element("text", f"chk-{i}", "body", MARGIN, y, text_w, 0.56,
                           text=str(i + 1) + ". " + str(it), font_pt=body_pt, color=C_MUTED,
                           single_line=True))
        y += 0.64
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_summary(slots, top, tokens, vp):
    """summary-map = prim_takeaway（双栏脉络收束，不复读标题）；有图 → 右侧面板。"""
    items = slots.get("map-2col", {}).get("items", [])
    text_w, fig_x, fig_w = _split_for_figure(slots)
    els = prim_takeaway(items, MARGIN, top, text_w,
                        SLIDE_H - top - 1.05, tokens, n_cols=1 if fig_w else 2,
                        prefix="")  # 不加 bullet；改用左侧 accent bar 视觉
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


def _h_generic(slots, top, tokens, vp):
    body_pt = _ts(tokens, "body", 18.0)
    text_w, fig_x, fig_w = _split_for_figure(slots)
    els: list[Element] = []
    y = top
    for slot in ("body", "concept-statement", "supporting"):
        s = slots.get(slot)
        if not s:
            continue
        text = s.get("text") or "\n".join(s.get("items", []))
        if text:
            els.append(Element("text", f"gen-{slot}", "body", MARGIN, y, text_w,
                               SLIDE_H - y - 0.8, text=text, font_pt=body_pt, color=C_INK))
        break
    els.extend(_figure_panel(slots, fig_x, fig_w, top, SLIDE_H - 1.05))
    return els


_HANDLERS = {
    "hero-context": _h_hero,
    "question-prompt": _h_numbered,
    "observe-and-infer": _h_observe,
    "concept-focus": _h_concept,
    "definition-emphasis": _h_definition,
    "equation-focus": _h_equation,
    "progressive-derivation": _h_steps,
    "worked-example": _h_worked,
    "practice-prompt": _h_practice,
    "two-column-evidence": _h_twocol,
    "checkpoint": _h_checkpoint,
    "summary-map": _h_summary,
}


def boxes_from_elements(els: list[Element]) -> list[ShapeBox]:
    """元素 → QA 度量盒（metrics 独立复核用）。"""
    out: list[ShapeBox] = []
    for e in els:
        if e.kind == "picture" or e.qa_exempt:
            out.append(ShapeBox(e.shape_id, e.qa_kind, e.x, e.y, e.w, e.h, "", qa_exempt=True))
            continue
        out.append(ShapeBox(e.shape_id, e.qa_kind, e.x, e.y, e.w, e.h, e.text,
                            font_pt=e.font_pt, single_line=e.single_line, qa_exempt=e.qa_exempt))
    return out


# ─────────── TP-H1 可组合语义 layout primitives ───────────
# 设计：8 个 primitive 对应任务书要求的语义词汇；
#   title / lead / definition / derivation / example / diagram / prompt / takeaway
# 族 handler 通过 primitive 组合产生版面元素；primitive 是 OOXML 元素的纯函数。
# 关键纪律：primitive 不感知知识点/具体业务；尺寸由调用方传入。
# 坐标仍仅在本文件产生（layout_resolver 是引擎内"坐标唯一合法产生地"）。



def prim_title(slots, x: float, y: float, w: float, tokens) -> list[Element]:
    """prim_title —— 普通页主标题（policy ≥ 32pt）。"""
    title_pt = _ts(tokens, "title", 32.0)
    text = str(slots.get("title", {}).get("text", ""))
    lines = M.safe_line_count(text, w, title_pt)
    h = max(0.62, lines * title_pt * 1.35 / 72.0 + 0.08)
    return [Element("text", "title", "title", x, y, w, h, text=text,
                    font_pt=title_pt, bold=True, color=C_INK,
                    single_line=(lines == 1), line_spacing=1.15)]


def prim_lead(text: str, x: float, y: float, w: float, h: float,
              tokens, *, fill: str = C_BRAND_BG, accent_x: float | None = None) -> list[Element]:
    """prim_lead —— 高亮"主陈述/概念陈述"卡（rect + text）。"""
    emphasis = _ts(tokens, "emphasis", 22.0)
    # 高度自适应内容
    bl = M.safe_line_count(text, max(w - 0.60, 1.0), emphasis)
    hh = max(1.05, bl * emphasis * 1.5 / 72.0 + 0.30)
    hh = min(hh, h)
    tab_x = accent_x if accent_x is not None else x
    return [
        Element("rect", "lead-bg", "figure", x, y, w, hh, fill=fill, radius=0.10, qa_exempt=True),
        Element("rect", "lead-tab", "accent", tab_x, y, 0.12, hh, fill=C_BRAND, qa_exempt=True),
        Element("text", "lead-text", "body", x + 0.30, y, w - 0.60, hh,
                text=text, font_pt=emphasis, bold=True, color=C_INK, anchor="middle"),
    ]


def prim_definition(banner: str, supporting: list[str], x: float, y: float, w: float,
                    h: float, tokens) -> list[Element]:
    """prim_definition —— 全宽定义横幅 + 辅助说明下置。"""
    emphasis = _ts(tokens, "emphasis", 22.0)
    body_pt = _ts(tokens, "body", 18.0)
    bl = M.safe_line_count(banner, w - 0.8, emphasis)
    bh = max(1.05, bl * emphasis * 1.5 / 72.0 + 0.30)
    bh = min(bh, h)
    els = [
        Element("rect", "def-banner", "body", x, y, w, bh,
                fill=C_BRAND_BG, radius=0.08, qa_exempt=True),
        Element("rect", "def-tab", "accent", x, y, 0.12, bh, fill=C_BRAND, qa_exempt=True),
        Element("text", "def-text", "body", x + 0.42, y, w - 0.84, bh,
                text=banner, font_pt=emphasis, bold=True, color=C_INK, anchor="middle"),
    ]
    # 支持列表下行
    yy = y + bh + BLOCK_GAP
    for i, it in enumerate(supporting[:5]):
        hh = 0.56
        if yy + hh > y + h:
            break
        els.append(Element("text", f"def-sup-{i}", "body", x, yy, w, hh,
                           text="· " + str(it), font_pt=body_pt, color=C_INK,
                           single_line=True))
        yy += hh + 0.14
    return els


def prim_derivation(steps: list[str], x: float, y: float, w: float, h: float,
                    tokens) -> list[Element]:
    """prim_derivation —— 步骤纵向推进，步步可回看；数学行自动强调。"""
    body_pt = _ts(tokens, "body", 18.0)
    eq_pt = _ts(tokens, "eq-inline", 20.0)
    items = list(steps)[:6]
    if not items:
        return []
    row_h = min(0.85, (h) / len(items))
    els: list[Element] = []
    yy = y
    for i, st in enumerate(items):
        is_math = any(ch in MATH_CHARS for ch in str(st))
        els.append(Element("rect", f"der-dot-{i}", "figure", x, yy + row_h / 2 - 0.19, 0.38, 0.38,
                           fill=C_BRAND, radius=0.5, qa_exempt=True))
        els.append(Element("text", f"der-n-{i}", "figure", x, yy + row_h / 2 - 0.19, 0.38, 0.38,
                           text=str(i + 1), font_pt=14, bold=True, color=C_SURFACE,
                           align="center", anchor="middle", single_line=True, qa_exempt=True))
        els.append(Element("text", f"der-{i}", "formula" if is_math else "body",
                           x + 0.60, yy, w - 0.60, row_h, text=str(st),
                           font_pt=eq_pt if is_math else body_pt,
                           bold=is_math, color=C_BRAND if is_math else C_INK, anchor="middle"))
        yy += row_h + 0.12
    return els


def prim_example(problem: str, steps: list[str], x: float, y: float, w: float, h: float,
                 tokens) -> list[Element]:
    """prim_example —— 题干成框 + 解答分步；例题/示范。"""
    body_pt = _ts(tokens, "body", 18.0)
    eq_pt = _ts(tokens, "eq-inline", 20.0)
    els: list[Element] = []
    ph = 0.95
    if problem:
        els.append(Element("rect", "ex-prob-card", "body", x, y, w, ph,
                           fill=C_SOFT_BG, stroke=C_LINE, radius=0.10, qa_exempt=True))
        els.append(Element("text", "ex-prob", "body", x + 0.30, y, w - 0.60, ph,
                           text=problem, font_pt=body_pt, bold=True, color=C_INK,
                           anchor="middle"))
    yy = y + ph + BLOCK_GAP
    for i, st in enumerate(list(steps)[:6]):
        if yy + 0.60 > y + h:
            break
        is_math = any(ch in MATH_CHARS for ch in str(st))
        hh = 0.60
        els.append(Element("rect", f"ex-bar-{i}", "accent", x, yy + 0.06, 0.10, hh - 0.12,
                           fill=C_BRAND, qa_exempt=True))
        els.append(Element("text", f"ex-{i}", "formula" if is_math else "body",
                           x + 0.32, yy, w - 0.32, hh, text=str(st),
                           font_pt=eq_pt if is_math else body_pt, bold=is_math,
                           color=C_BRAND if is_math else C_INK, anchor="middle"))
        yy += hh + 0.10
    return els


def prim_diagram(asset_ids: list[str], x: float, y: float, w: float, h: float,
                 tokens, *, has_bytes: bool = True) -> list[Element]:
    """prim_diagram —— 图象资产位；有字节 → frame+picture；缺失 → 显式"缺图"卡。

    has_bytes 由调用方判断（builder 知道 assets_bytes 是否含 id）；
    primitive 不假设有图，仅按 has_bytes 分支渲染。
    """
    if has_bytes and asset_ids:
        return [
            Element("rect", "diag-frame", "figure", x, y, w, h,
                    fill=C_SOFT_BG, stroke=C_LINE, radius=0.10, qa_exempt=True),
            Element("picture", "diag", "figure", x + 0.20, y + 0.20,
                    w - 0.40, h - 0.40, asset_ids=list(asset_ids)),
        ]
    notice_h = min(1.40, h)
    return [
        Element("rect", "diag-missing-card", "figure", x, y, w, notice_h,
                fill=C_BRAND_BG, stroke=C_LINE, radius=0.10, qa_exempt=True),
        Element("text", "diag-missing-title", "kicker",
                x + 0.30, y + 0.18, w - 0.60, 0.36,
                text="图象暂缺", font_pt=_ts(tokens, "support", 15.0),
                bold=True, color=C_BRAND, align="center", anchor="middle",
                single_line=True),
        Element("text", "diag-missing-body", "support",
                x + 0.30, y + 0.62, w - 0.60, notice_h - 0.72,
                text="本页为图象资产缺失降级样例。请补传教材原图或教师上传图后重渲染。",
                font_pt=_ts(tokens, "support", 15.0), color=C_MUTED,
                align="center", anchor="middle"),
    ]


def prim_prompt(text: str, hints: list[str], x: float, y: float, w: float, h: float,
                tokens) -> list[Element]:
    """prim_prompt —— 提问/任务卡 + 提示。"""
    body_pt = _ts(tokens, "body", 18.0)
    support_pt = _ts(tokens, "support", 15.0)
    els: list[Element] = []
    if not text:
        return els
    ph = min(1.30, h * 0.55)
    els.append(Element("rect", "prompt-card", "body", x, y, w, ph,
                       fill=C_BRAND_BG, radius=0.10, qa_exempt=True))
    els.append(Element("text", "prompt", "body", x + 0.30, y, w - 0.60, ph,
                       text=text, font_pt=body_pt, bold=True, color=C_INK,
                       anchor="middle"))
    yy = y + ph + BLOCK_GAP
    for i, hint in enumerate(list(hints)[:4]):
        if yy + 0.50 > y + h:
            break
        els.append(Element("text", f"prompt-hint-{i}", "support",
                           x, yy, w, 0.50, text="提示 · " + str(hint),
                           font_pt=support_pt, color=C_MUTED, single_line=True))
        yy += 0.58
    return els


def prim_takeaway(items: list[str], x: float, y: float, w: float, h: float,
                  tokens, *, n_cols: int = 2, prefix: str = "• ") -> list[Element]:
    """prim_takeaway —— 要点列表（双栏/单栏）；用于课后小结、要点收束。"""
    body_pt = _ts(tokens, "body", 18.0)
    if not items:
        return []
    if n_cols <= 1:
        cols = [list(items)]
        col_w = w
    else:
        half = (len(items) + 1) // 2
        cols = [list(items[:half]), list(items[half:])]
        col_w = (w - (n_cols - 1) * GUTTER) / n_cols
    els: list[Element] = []
    y_max = y + h - 0.05
    for ci, group in enumerate(cols):
        cx = x + ci * (col_w + GUTTER)
        yy = y
        for i, it in enumerate(group[:6]):
            ln = M.safe_line_count(str(it), col_w - 0.10, body_pt)
            hh = max(0.40, ln * body_pt * 1.5 / 72.0 + 0.16)
            if yy + hh > y_max:
                break
            els.append(Element("text", f"take-{ci}-{i}", "body", cx, yy, col_w, hh,
                               text=prefix + str(it), font_pt=body_pt, color=C_INK))
            yy += hh + 0.14
    return els
