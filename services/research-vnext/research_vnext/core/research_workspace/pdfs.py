"""RV17 · PDF 页级文本抽取（复用已在依赖里的 `pypdfium2`）。

产出阅读器需要的**真实**数据结构：

    {page, width, height, blocks: [{text, bbox, section}]}

- bbox 是 `[x0, y0, x1, y1]`，单位为 PDF point，**坐标系已转成左上原点**
  （与 pdf.js viewport 一致），便于前端在同一坐标系里叠加选区。
- blocks 为**行级**（连续字符按换行切分），每行一个 bbox —— 精细到行，
  足以支撑"选文 → 回原文定位"（宪法 §14）。

解析失败**不得**让文献不可用：调用方把异常收成 `parse_status=failed`，
PDF 字节仍在对象存储里可读（宪法 §10）。
"""

from __future__ import annotations

import io
import re

#: 单次解析页数上限（防病态大 PDF 拖垮请求）。
DEFAULT_MAX_PAGES = 200
#: search_text 上限（防 JSONB/列膨胀）。
MAX_SEARCH_TEXT = 600_000
#: 单行文本上限（防异常 PDF 造出超长"行"）。
MAX_BLOCK_CHARS = 2000

_HEADING_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(\S.{0,80})$")
_HEADING_WORDS = re.compile(
    r"^(abstract|introduction|related work|background|method(?:s|ology)?|"
    r"experiments?|results?|discussion|conclusion(?:s)?|references|acknowledge?ments?|"
    r"摘要|引言|相关工作|方法|实验|结果|讨论|结论|参考文献|致谢)\b",
    re.IGNORECASE,
)
#: 数学/命令字符出现在行内 ⇒ 不是标题（排除 PDF 里被抽出的公式片段，如 "2 + b"）。
#: RV18 收紧：加入希腊字母与关系/算子符号 —— 实测 `"2 sin (πθ)"`、`"F F ∈ ED"`
#: 曾被当成章节标题（PDF 公式碎片）。
_MATH_OP_RE = re.compile(
    r"[=+^_\\{}<>|()\[\]%∈≥≤≈≠±×·−→←⇒∇∂∑∏∫√∞"
    r"πθαβγδεζηικλμνξρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    r"\u2126]"
)
#: 页眉式「编号 + 标题 + 页码」（如 "1 引言 1"）⇒ 不是标题。
_RUNNING_HEAD_RE = re.compile(r"^\d+(?:\.\d+)*\s+\S.*\s+\d+$")
#: **句读**（句号/分号 + 空白 + 后续内容）⇒ 是正文句子，不是标题。
#: 实测反例：`"methods. Analysis for the mixed elements is rather straightforward. …"`。
_SENTENCE_RE = re.compile(r"[.。;；]\s+\S")
#: 逗号/顿号 ⇒ 更像页眉里的作者行或正文，不是章节标题。
_LISTY_RE = re.compile(r"[,，、]")


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in text)


def _title_like(rest: str) -> bool:
    """编号之后的片段是否**像标题**（而不是正文句子 / 页眉作者行 / 公式碎片）。"""
    rest = rest.strip()
    if not rest:
        return False
    if _SENTENCE_RE.search(rest) or _LISTY_RE.search(rest):
        return False
    words = rest.split()
    if len(words) > 12:
        return False
    # 极短片段（"2 be" / "2 and"）是页眉或公式残片，不是章节名。
    # ⚠️ 中文标题天然短（`1 引言` / `2 方法` 只有 1 个"词"、2 个字），
    #    这条按**拉丁词长**的判据不能套到 CJK 上，否则真实中文章节会被误杀。
    if not _has_cjk(rest) and len(words) <= 2 and max((len(w) for w in words), default=0) <= 4:
        return False
    # 至少要有 2 个字母：`"2 sin (πθ)"` 这类公式碎片不配当章节。
    return len([c for c in rest if c.isalpha()]) >= 2


def _all_caps_like(t: str) -> bool:
    """全大写标题（如 `"FINITE ELEMENT METHODS"`）。

    RV18 收紧：要求 ≥2 个**长度 ≥2** 的字母词元 —— 实测 `"F F ∈ ED"` 曾被误判
    （它其实是公式碎片；`∈` 现在已由 `_MATH_OP_RE` 拦下，这条是第二道闸）。
    """
    if _LISTY_RE.search(t):
        return False
    tokens = [w for w in re.split(r"\s+", t) if w]
    if not (2 <= len(tokens) <= 8):
        return False
    alpha = [w for w in tokens if any(c.isalpha() for c in w)]
    if len(alpha) < 2 or any(len(w) < 2 for w in alpha):
        return False
    letters = [c for c in t if c.isalpha()]
    return t.upper() == t and len(letters) >= 4 and not t.isdigit()


def _looks_like_heading(text: str) -> bool:
    t = text.strip()
    if not (0 < len(t) <= 90):
        return False
    if t.endswith((".", "。", ",", "，")):
        return False
    if _MATH_OP_RE.search(t) or _RUNNING_HEAD_RE.match(t):
        return False

    numbered = _HEADING_NUM.match(t)
    if numbered:
        return _title_like(numbered.group(2))

    # 非编号行：整句（含句读）一律不是标题。
    if _SENTENCE_RE.search(t):
        return False

    keyword = _HEADING_WORDS.match(t)
    if keyword:
        rest = t[keyword.end():].strip()
        if not rest or rest[:1].isdigit():
            return True
        return len(rest.split()) <= 6 and not rest.endswith((",", "，", ":", "："))

    return _all_caps_like(t)


def extract_pdf(
    pdf_bytes: bytes,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict:
    """抽取每页文本块 + bbox + 章节 + 元数据。失败抛异常（调用方转 failed）。

    Returns:
        {
          "page_count": int,
          "pages": [{page, width, height, blocks:[{text,bbox,section}]}, ...],
          "page_sections": [{"title","level","page"}, ...],
          "search_text": str,
          "metadata": {"title","author"},
          "truncated": bool,
        }
    """
    import pypdfium2 as pdfium

    metadata = {"title": "", "author": ""}
    pages: list[dict] = []
    page_sections: list[dict] = []
    chunks: list[str] = []
    truncated = False

    with pdfium.PdfDocument(io.BytesIO(pdf_bytes)) as doc:
        total = len(doc)
        try:
            meta = doc.get_metadata_dict() or {}
            metadata["title"] = str(meta.get("Title") or "").strip()
            metadata["author"] = str(meta.get("Author") or "").strip()
        except Exception:  # noqa: BLE001 - 元数据缺失不影响文本抽取
            metadata = {"title": "", "author": ""}

        limit = min(total, max(1, int(max_pages)))
        truncated = total > limit
        for i in range(limit):
            page = doc[i]
            width = float(page.get_width())
            height = float(page.get_height())
            textpage = page.get_textpage()
            count = textpage.count_chars()

            blocks: list[dict] = []
            line_chars: list[str] = []
            boxes: list[tuple[float, float, float, float]] = []

            def _flush() -> None:
                if not line_chars:
                    return
                text = "".join(line_chars).strip()
                line_chars.clear()
                if not text:
                    boxes.clear()
                    return
                x0 = min(b[0] for b in boxes)
                y0 = min(b[1] for b in boxes)
                x1 = max(b[2] for b in boxes)
                y1 = max(b[3] for b in boxes)
                boxes.clear()
                # 转左上原点：y_top = height - top, y_bottom = height - bottom
                top = height - y1
                bottom = height - y0
                blocks.append(
                    {
                        "text": text[:MAX_BLOCK_CHARS],
                        "bbox": [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)],
                        "section": "",
                    }
                )

            for j in range(count):
                try:
                    ch = textpage.get_text_range(j, 1)
                except Exception:  # noqa: BLE001 - 单字符取不到就跳过
                    continue
                if ch in ("\r", "\n"):
                    _flush()
                    continue
                if ch == "\ufeff":
                    continue
                try:
                    box = textpage.get_charbox(j)
                    boxes.append((float(box[0]), float(box[1]), float(box[2]), float(box[3])))
                except Exception:  # noqa: BLE001 - 无 box 的字符仍进文本
                    pass
                line_chars.append(ch)
                if len(line_chars) >= MAX_BLOCK_CHARS:
                    _flush()
            _flush()

            # 章节识别（行级启发式），并把 section 回填到该行块。
            current_section = ""
            for b in blocks:
                if _looks_like_heading(b["text"]):
                    current_section = b["text"].strip()
                    level = 1
                    m = _HEADING_NUM.match(current_section)
                    if m:
                        level = m.group(1).count(".") + 1
                    page_sections.append(
                        {"title": current_section, "level": level, "page": i + 1}
                    )
                b["section"] = current_section

            page_text = "\n".join(b["text"] for b in blocks)
            chunks.append(page_text)
            pages.append(
                {
                    "page": i + 1,
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "blocks": blocks,
                }
            )

    search_text = "\n".join(chunks)
    if len(search_text) > MAX_SEARCH_TEXT:
        search_text = search_text[:MAX_SEARCH_TEXT]
        truncated = True

    return {
        "page_count": total,
        "pages": pages,
        "page_sections": page_sections,
        "search_text": search_text,
        "metadata": metadata,
        "truncated": truncated,
    }


__all__ = ["DEFAULT_MAX_PAGES", "extract_pdf"]
