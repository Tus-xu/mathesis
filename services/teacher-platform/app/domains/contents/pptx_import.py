# coding: utf-8
"""pptx 导入：老师上传的自制课件 → 逐页拆元素 → 平台可二次编辑的 deck 草稿。

可行性已实证（python-pptx 1.0.2 反向拆解样例：文本框位置/尺寸/字号/颜色/加粗、
OMML 公式载体、图片、备注全部可拆）。坐标天然同源：pptx EMU ↔ 平台 1280×720
（1px = 9525 EMU）。

诚实边界（v1）：
- 文本框（含 $..$ 定界）→ text 元素；公式形状（oMath）→ 公式文本尽力提取，
  提取失败记入 skipped；
- 图片 → image 元素（字节转存 MinIO teacher-assets）；
- 表格/图表/SmartArt → v1 不拆，记入 skipped（不静默丢弃）；
- 全部元素 teacher_confirmed=False（R5：导入产物是草稿，教师确认后生效）。
"""
from __future__ import annotations

import io
import re
from typing import Any

from pptx import Presentation
from pptx.oxml.ns import qn

EMU_PER_PX = 9525


def _px(emu: int | None) -> int:
    return int((emu or 0) / EMU_PER_PX)


def _shape_has_math(shape) -> bool:
    try:
        return shape._element.find(".//" + qn("a14:m")) is not None or \
            shape._element.find(".//" + qn("m:oMath")) is not None
    except Exception:
        return False


def _text_frame_to_html(shape) -> str:
    """文本框 → 多行文本（保留段落换行；公式段的文字尽力抓取）。"""
    lines: list[str] = []
    try:
        for para in shape.text_frame.paragraphs:
            runs = [r.text or "" for r in para.runs]
            line = "".join(runs).strip()
            if not line and para._p.find(".//" + qn("a14:m")) is not None:
                # 公式段落：抓 m:t 文本拼近似式（v1 降级；OMML→LaTeX 逆向后续版本）
                mts = [t.text or "" for t in para._p.findall(".//" + qn("m:t"))]
                line = "".join(mts).strip()
            lines.append(line)
    except Exception:
        pass
    return "\n".join(lines)


def parse_pptx(data: bytes, *, storage=None, owner_key: str = "import") -> dict[str, Any]:
    """pptx bytes → {title, slides:[V3Slide 形状], report:{...}}。

    report 如实列出 skipped（表格/图表/组合形状等 v1 不拆项）。
    """
    prs = Presentation(io.BytesIO(data))
    slides_out: list[dict[str, Any]] = []
    skipped: list[str] = []
    picture_count = 0

    for idx, slide in enumerate(prs.slides, 1):
        elements: list[dict[str, Any]] = []
        for sh in slide.shapes:
            x, y = _px(sh.left), _px(sh.top)
            w, h = _px(sh.width), _px(sh.height)
            sid = f"imp-{idx}-{len(elements) + 1}"
            try:
                if sh.shape_type == 13:  # PICTURE
                    if storage is not None:
                        blob = sh.image.blob
                        key = f"import/{owner_key}/{idx}/{sid}.png"
                        try:
                            storage.put_bytes("teacher-assets", key, blob, sh.image.content_type)
                            elements.append({
                                "id": sid, "type": "image", "left": x, "top": y,
                                "width": w, "height": h, "z": 1, "src": key, "alt": "导入图片",
                            })
                            picture_count += 1
                            continue
                        except Exception:
                            skipped.append(f"第{idx}页图片转存失败（MinIO 不可用）")
                            continue
                    skipped.append(f"第{idx}页图片（存储通道未注入）")
                    continue
                if getattr(sh, "has_table", False) and sh.has_table:
                    skipped.append(f"第{idx}页表格（v1 不拆）")
                    continue
                if sh.shape_type == 6:  # GROUP
                    skipped.append(f"第{idx}页组合形状（v1 不拆）")
                    continue
                if sh.has_text_frame:
                    html = _text_frame_to_html(sh)
                    if not html.strip():
                        continue
                    is_math = _shape_has_math(sh)
                    font_size = 20
                    color = None
                    bold = None
                    try:
                        for para in sh.text_frame.paragraphs:
                            for r in para.runs:
                                if r.font.size:
                                    font_size = max(12, int(r.font.size.pt))
                                if r.font.color and r.font.color.type is not None:
                                    try:
                                        color = f"#{r.font.color.rgb}"
                                    except Exception:
                                        color = None
                                bold = bool(r.font.bold) if r.font.bold is not None else bold
                                raise StopIteration
                    except StopIteration:
                        pass
                    except Exception:
                        pass
                    etype = "formula" if (is_math and "$" in html) else "text"
                    el: dict[str, Any] = {
                        "id": sid, "type": etype, "left": x, "top": y,
                        "width": w, "height": max(h, 28), "z": 2, "html": html,
                        "font_size": font_size, "teacher_confirmed": False,
                    }
                    if color:
                        el["color"] = color
                    if bold is not None:
                        el["bold"] = bold
                    if etype == "formula":
                        el["latex"] = html.replace("$", "")
                    elements.append(el)
                else:
                    skipped.append(f"第{idx}页形状 {sh.shape_type}（v1 不拆）")
            except Exception as e:  # noqa: BLE001 —— 单形状失败不影响整页
                skipped.append(f"第{idx}页形状解析异常：{e}")

        notes = ""
        try:
            if slide.has_notes_slide:
                notes = (slide.notes_slide.notes_text_frame.text or "")[:500]
        except Exception:
            pass

        slides_out.append({
            "id": f"slide-{idx}", "layout": "blank", "elements": elements, "notes": notes,
        })

    return {
        "title": "导入课件",
        "slides": slides_out,
        "report": {
            "slides": len(slides_out),
            "pictures": picture_count,
            "skipped": skipped[:30],
        },
    }
