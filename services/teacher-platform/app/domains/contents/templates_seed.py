# coding: utf-8
"""deck_templates 真实种子（M2-B 生死 Gate 前置，审计 Top10-5）。

六套教学意图模板（02-ARCHITECTURE §4.2：概念新授/定理推导/例题讲解/复习讲评/探究活动的
槽位骨架按九种页型给 TemplateSlots；IFC-001 布局引擎按槽位出坐标/字号/颜色）。
缩略图 = 服务端确定性渲染 SVG（非前端色块占位——L4 判定①），入 MinIO teacher-thumbs，
列表经 presign GET 下发。

幂等：按模板名查重，存在即跳过（不覆盖教师可能的引用）。
"""
from __future__ import annotations

import io
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DeckTemplate

# 九种页型的默认槽位骨架（TemplateSlots，IFC-001 TypedDict 形状）
def _slots(page_type: str, *, role: str, max_text: int = 5, formula: int = 1, visual: int = 0) -> dict[str, Any]:
    return {
        "page_type": page_type,
        "layout_type": page_type,
        "pedagogical_role": role,
        "required_slots": ["title"],
        "optional_slots": ["points", "formula", "visual"],
        "max_text": max_text,
        "formula_capacity": formula,
        "visual_capacity": visual,
        "locked_elements": ["title"],
    }


_STYLE_THEMES: dict[str, dict[str, str]] = {
    # 六套模板视觉差异（ailp 派生色系；背景由 PIL 渐变 + 角部几何母题生成）
    "学术蓝":   {"bg1": "#eef4ff", "bg2": "#dfe9ff", "accent": "#4f46e5", "title": "#1e3a8a", "body": "#1f2a52", "motif": "circle"},
    "黑板绿":   {"bg1": "#f0fdf4", "bg2": "#dcfce7", "accent": "#15803d", "title": "#14532d", "body": "#1c3a2a", "motif": "lines"},
    "几何灰":   {"bg1": "#f8fafc", "bg2": "#e2e8f0", "accent": "#475569", "title": "#0f172a", "body": "#1e293b", "motif": "triangle"},
    "经典藏青": {"bg1": "#f1f5f9", "bg2": "#e2e8f0", "accent": "#1e3a5f", "title": "#0c2a4d", "body": "#16324f", "motif": "circle"},
    "极简白":   {"bg1": "#ffffff", "bg2": "#f8fafc", "accent": "#334155", "title": "#0f172a", "body": "#1e293b", "motif": "none"},
    "暖橙活力": {"bg1": "#fff7ed", "bg2": "#ffedd5", "accent": "#c2410c", "title": "#7c2d12", "body": "#431407", "motif": "circle"},
}


def _bg_datauri(style: str) -> str:
    """PIL 渐变背景 + 角部几何母题 → PNG dataURI（1280×720，低饱和不抢内容）。"""
    import base64

    theme = _STYLE_THEMES.get(style) or _STYLE_THEMES["极简白"]
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (1280, 720))
        top = tuple(int(theme["bg1"][i:i + 2], 16) for i in (1, 3, 5))
        bot = tuple(int(theme["bg2"][i:i + 2], 16) for i in (1, 3, 5))
        d = ImageDraw.Draw(img)
        for yy in range(0, 720, 2):
            r = yy / 719
            row = tuple(int(top[i] + (bot[i] - top[i]) * r) for i in range(3))
            d.rectangle([0, yy, 1280, yy + 2], fill=row)
        motif = theme["motif"]
        acc = theme["accent"]
        if motif == "circle":
            d.ellipse([1080, -140, 1420, 200], outline=acc, width=3)
            d.ellipse([1120, -100, 1380, 160], outline=acc, width=2)
        elif motif == "lines":
            for i in range(4):
                d.line([1120 + i * 36, 0, 1240 + i * 36, 150], fill=acc, width=2)
        elif motif == "triangle":
            d.polygon([(1120, 150), (1220, -20), (1320, 150)], outline=acc, width=3)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _template_slots() -> dict[str, Any]:
    """全模板通用九页型槽位（v1：教学意图差异在 role 文案与容量；差异深化随基准库对比迭代）。"""
    return {
        "_default": _slots("concept_intro", role="通用内容页"),
        "concept_intro": _slots("concept_intro", role="概念新授：定义要素与辨析", visual=1),
        "visual_definition": _slots("visual_definition", role="图形化定义：图示优先", visual=1),
        "derivation": _slots("derivation", role="定理/公式推导：步骤链", max_text=4, formula=1),
        "formula_focus": _slots("formula_focus", role="公式聚焦：式子放大居中", max_text=3, formula=1),
        "worked_example": _slots("worked_example", role="例题精讲：题干+分步解答", max_text=5, formula=1),
        "compare": _slots("compare", role="对比辨析：双栏对照"),
        "common_error": _slots("common_error", role="易错辨析：错因+纠正", max_text=4),
        "practice": _slots("practice", role="课堂练习：题面呈现", formula=1),
        "summary": _slots("summary", role="课堂小结：知识结构回顾", max_text=4),
    }


# 六套模板（契约命名：学术蓝/黑板绿/几何灰/经典藏青/极简白/暖橙活力）
_TEMPLATES = [
    {"name": "学术蓝", "style": "academic", "color": "#1d4ed8", "bg": "#eff6ff"},
    {"name": "黑板绿", "style": "chalkboard", "color": "#14532d", "bg": "#f0fdf4"},
    {"name": "几何灰", "style": "minimal", "color": "#334155", "bg": "#f8fafc"},
    {"name": "经典藏青", "style": "classic", "color": "#1e3a5f", "bg": "#f1f5f9"},
    {"name": "极简白", "style": "minimal", "color": "#0f172a", "bg": "#ffffff"},
    {"name": "暖橙活力", "style": "academic", "color": "#c2410c", "bg": "#fff7ed"},
]


def _thumb_svg(name: str, color: str, bg: str) -> bytes:
    """确定性模板预览 SVG（16:9，标题条+要点行+公式区示意）。"""
    lines = "".join(
        f'<rect x="70" y="{170 + i * 74}" width="{430 - i * 60}" height="30" rx="8" fill="{color}" opacity="{0.55 - i * 0.12}"/>'
        for i in range(3)
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">'
        f'<rect width="640" height="360" fill="{bg}"/>'
        f'<rect x="40" y="36" width="560" height="56" rx="10" fill="{color}"/>'
        f'<text x="56" y="72" font-family="sans-serif" font-size="26" fill="#ffffff" font-weight="700">{name} · 椭圆及其标准方程</text>'
        f'{lines}'
        f'<rect x="360" y="170" width="220" height="150" rx="12" fill="none" stroke="{color}" stroke-width="2"/>'
        f'<ellipse cx="470" cy="245" rx="88" ry="54" fill="none" stroke="{color}" stroke-width="2.5"/>'
        f'<circle cx="382" cy="245" r="4" fill="{color}"/><circle cx="558" cy="245" r="4" fill="{color}"/>'
        f'<text x="56" y="330" font-family="sans-serif" font-size="15" fill="{color}" opacity="0.8">x²/a² + y²/b² = 1 (a&gt;b&gt;0)</text>'
        f'</svg>'
    )
    return svg.encode("utf-8")


async def ensure_deck_templates(session: AsyncSession) -> list[DeckTemplate]:
    """幂等种子：按名查重；缩略图入 MinIO（失败不阻塞种子——thumb 留空走前端骨架）。"""
    from ...storage.minio import get_storage

    existing = {n for (n,) in (await session.execute(select(DeckTemplate.name))).all()}
    created: list[DeckTemplate] = []
    dirty = False  # 补挂 _theme 的老模板行：循环后统一 commit（此前补挂从不落库的 bug）
    storage = None
    for t in _TEMPLATES:
        theme = _STYLE_THEMES.get(t["name"]) or _STYLE_THEMES["极简白"]  # 按模板名（中文）取主题
        if t["name"] in existing:
            # 升级已有模板：slots 无 _theme 时补挂（2026-09-08 视觉差异化）
            row = (await session.execute(
                select(DeckTemplate).where(DeckTemplate.name == t["name"])
            )).scalar_one()
            if row is not None and not (row.slots or {}).get("_theme"):
                slots = dict(row.slots or {})
                slots["_theme"] = {**theme, "bg_datauri": _bg_datauri(t["name"])}
                row.slots = slots
                dirty = True
            continue
        slots = _template_slots()
        slots["_theme"] = {**theme, "bg_datauri": _bg_datauri(t["name"])}
        row = DeckTemplate(
            name=t["name"], style=t["style"], slide_count=12,
            thumb="", slots=slots, source="builtin",
        )
        session.add(row)
        await session.flush()
        try:
            if storage is None:
                storage = get_storage()
            key = f"deck-templates/{row.id}.svg"
            storage.put_bytes("teacher-thumbs", key, _thumb_svg(t["name"], t["color"], t["bg"]), "image/svg+xml")
            row.thumb = key
        except Exception:
            row.thumb = ""  # MinIO 不可用：诚实空 thumb（前端骨架兜底），不阻塞
        created.append(row)
    if created or dirty:
        await session.commit()
    return created


def slots_for_page_type(slots_json: Any, page_type: str) -> dict[str, Any]:
    """从模板 slots JSON 取页型骨架（页型缺失回 _default；再缺失给宽松默认）。"""
    if isinstance(slots_json, dict):
        s = slots_json.get(page_type) or slots_json.get("_default")
        if isinstance(s, dict):
            return s
    return {"layout_type": page_type, "pedagogical_role": "通用", "required_slots": [],
            "optional_slots": ["points", "formula", "visual"], "max_text": 5,
            "formula_capacity": 1, "visual_capacity": 1, "locked_elements": ["title"]}
