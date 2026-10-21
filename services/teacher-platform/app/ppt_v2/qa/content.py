"""T24 deck **内容** QA（R8-T）：读真实 deck spec 元素 + 真实 PPTX 文本，判定内容级缺陷。

为什么需要：既有 `detector` 只做视觉维度（overflow / overlap / font / formula / graphic /
contrast / duplicate / off_slide / blank / density），对「正文混入教研批注 / 工程黑话 /
模板前缀 / 封面内部术语 / 裸 LaTeX 上屏 / 跨页逐字重复」零判定——导致 `ppt_v2_page_qa`
对内容垃圾页一律 pass，质量门形同虚设（2026-09-23 R8-E2E 实测：10 页全 pass，但正文里
有「引导学生…」「本节结论：」「（AI 生成变式）」「教材直连 · SourceLocator 溯源见备注」）。

判据（每页；规则确定性、可单测）：
- `title_empty`（p0）：deck spec 页面标题为空；
- `content_thin`（p1）：spec 元素数 < 2 或 role 类别 < 2；
- `teacher_annotation`（p0）：PPTX 可见正文出现教师动作描述（「引导学生…」）；
- `internal_term`（p0）：PPTX 可见正文出现工程黑话 / 模板前缀（SourceLocator / 教材直连 /
  「本节结论：」/「（AI 生成变式）」）；
- `bare_latex`（p0）：PPTX 可见正文出现未渲染的 LaTeX 源码（`$...$` / `\\frac` / `^{`…）；
- `cross_page_duplicate`（p0）：spec 内容行在同一 deck 的其它页逐字重复。

为什么标题/密度/跨页重复走 spec、批注/黑话/LaTeX 走 PPTX：引擎会在每页加页眉
（kicker / 课件名页脚 / 页码 / role 标签；R6-planfix 起页脚为纯课件名，无系统口径尾巴），
按 PPTX 全文判跨页重复会把页眉误判为重复；
而批注/黑话/LaTeX 不会出现在页眉里，按真实渲染文本判定才是「上屏即事故」的证据。

本模块只依赖 python-pptx 与共享文本原语（app.teacher_prep.text_safety），**不 import 生成侧代码**。
"""

from __future__ import annotations

import re
from typing import Any

from app.teacher_prep.text_safety import has_internal_term, is_teacher_annotation

CONTENT_DETECTOR_VERSION = "t24-content-qa-1.0.0"

# 与视觉 detector 同口径（`DUPLICATE_MIN_CHARS`）：短于该长度的行不参与重复判定。
DUPLICATE_MIN_CHARS = 6

# 裸 LaTeX 上屏痕迹：`$...$` 行内公式、`\命令`、`^{` / `_{` 上标下标语法。
_BARE_LATEX_RE = re.compile(r"\$[^$]+\$|\\[a-zA-Z]{2,}|[\^_]\{")

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """跨页重复归一化键：去空白（与视觉 detector 的 `_check_duplicate` 同口径）。"""
    return _WS_RE.sub("", text or "")


def _defect(kind: str, severity: str, message: str) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "message": message, "shape_ids": []}


# ─────────────────────────────────────────────────────────────────────────────
# PPTX 文本抽取（逐段一行；与 detector 的 shape 收集互不耦合）
# ─────────────────────────────────────────────────────────────────────────────


def slide_lines(slide: Any) -> list[str]:
    """一页 PPTX 的可见文本行（逐段；空行丢弃）。"""
    lines: list[str] = []
    for shape in slide.shapes:
        try:
            if not shape.has_text_frame:
                continue
        except Exception:  # noqa: BLE001 - picture/group 无 text_frame
            continue
        try:
            for para in shape.text_frame.paragraphs:
                text = (para.text or "").strip()
                if text:
                    lines.append(text)
        except Exception:  # noqa: BLE001
            continue
    return lines


def deck_lines(presentation: Any) -> dict[int, list[str]]:
    """整份 deck 的逐页可见文本（page_no 从 1 起）。"""
    return {index + 1: slide_lines(slide) for index, slide in enumerate(presentation.slides)}


# ─────────────────────────────────────────────────────────────────────────────
# deck spec 页面视图（title + elements）
# ─────────────────────────────────────────────────────────────────────────────


def slide_view(slide: Any) -> dict[str, Any]:
    """deck spec 的 SlideSpec → QA 视图（title + elements 摘要；无坐标）。"""
    elements: list[dict[str, Any]] = []
    for el in getattr(slide, "elements", None) or []:
        elements.append(el.model_dump() if hasattr(el, "model_dump") else dict(el))
    return {"title": str(getattr(slide, "title", "") or ""), "elements": elements}


def spec_pages_from_slides(slides: Any) -> dict[int, dict[str, Any]]:
    """deck spec 的 slides → {page_no: slide_view}。"""
    return {index + 1: slide_view(slide) for index, slide in enumerate(slides or [])}


def _element_contents(spec_page: Any) -> list[str]:
    """一页 spec 元素的全部内容文本（text + latex + items）。"""
    out: list[str] = []
    for el in (spec_page or {}).get("elements") or []:
        if not isinstance(el, dict):
            continue
        for key in ("text", "latex"):
            value = str(el.get(key) or "").strip()
            if value:
                out.append(value)
        for item in el.get("items") or []:
            item_text = str(item or "").strip()
            if item_text:
                out.append(item_text)
    return out


def _element_density(spec_page: Any) -> tuple[int, set[str], bool]:
    """一页 spec 的元素数与 role 类别数（封面无元素 → 不判密度）。"""
    roles: set[str] = set()
    count = 0
    for el in (spec_page or {}).get("elements") or []:
        if not isinstance(el, dict):
            continue
        role = str(el.get("role") or "")
        if not role:
            continue
        roles.add(role)
        count += 1
    return count, roles, count > 0


def _page_title(spec_page: Any) -> str:
    return str((spec_page or {}).get("title") or "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# 逐维判定
# ─────────────────────────────────────────────────────────────────────────────


def _cross_page_duplicate_keys(contents_by_page: dict[int, list[str]]) -> dict[int, set[str]]:
    """同一内容行在多页逐字出现 → 每页记录命中的归一化键。"""
    pages_of: dict[str, set[int]] = {}
    for page_no, lines in contents_by_page.items():
        for line in lines:
            key = _norm(line)
            if len(key) >= DUPLICATE_MIN_CHARS:
                pages_of.setdefault(key, set()).add(page_no)
    out: dict[int, set[str]] = {}
    for key, pages in pages_of.items():
        if len(pages) > 1:
            for page_no in pages:
                out.setdefault(page_no, set()).add(key)
    return out


def content_defects_for_page(
    *,
    page_no: int,
    pptx_lines: list[str],
    spec_page: Any = None,
    cross_dup_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """单页内容缺陷（确定性）。spec_page 缺失时只判 PPTX 文本维。"""
    defects: list[dict[str, Any]] = []
    has_spec = isinstance(spec_page, dict) and bool(spec_page)

    if has_spec:
        if not _page_title(spec_page):
            defects.append(_defect("title_empty", "p0", "页面标题为空"))
        count, roles, has_elements = _element_density(spec_page)
        if has_elements and (count < 2 or len(roles) < 2):
            defects.append(
                _defect(
                    "content_thin",
                    "p1",
                    f"内容单薄：元素 {count} 个、role 类别 {len(roles)} 类（要求 ≥2 个且 ≥2 类）",
                )
            )

    for line in pptx_lines:
        if is_teacher_annotation(line):
            defects.append(_defect("teacher_annotation", "p0", f"教研批注当正文：{line[:48]!r}"))
            break
    for line in pptx_lines:
        if has_internal_term(line):
            defects.append(_defect("internal_term", "p0", f"工程黑话/模板前缀泄漏：{line[:48]!r}"))
            break
    for line in pptx_lines:
        if _BARE_LATEX_RE.search(line):
            defects.append(_defect("bare_latex", "p0", f"裸 LaTeX 上屏：{line[:48]!r}"))
            break

    if cross_dup_keys:
        for line in _element_contents(spec_page):
            key = _norm(line)
            if len(key) >= DUPLICATE_MIN_CHARS and key in cross_dup_keys:
                defects.append(
                    _defect("cross_page_duplicate", "p0", f"与其它页逐字重复：{line[:48]!r}")
                )
                break
    return defects


def detect_content_defects(
    *,
    pptx_lines: dict[int, list[str]],
    spec_pages: dict[int, dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """整份 deck 的内容缺陷（page_no → defects）。跨页重复按 spec 内容行判定。"""
    contents_by_page = {
        page_no: _element_contents(spec_pages.get(page_no)) for page_no in pptx_lines
    }
    cross_dup = _cross_page_duplicate_keys(contents_by_page)
    return {
        page_no: content_defects_for_page(
            page_no=page_no,
            pptx_lines=lines,
            spec_page=spec_pages.get(page_no),
            cross_dup_keys=cross_dup.get(page_no),
        )
        for page_no, lines in pptx_lines.items()
    }


__all__ = [
    "CONTENT_DETECTOR_VERSION",
    "DUPLICATE_MIN_CHARS",
    "content_defects_for_page",
    "deck_lines",
    "detect_content_defects",
    "slide_lines",
    "slide_view",
    "spec_pages_from_slides",
]
