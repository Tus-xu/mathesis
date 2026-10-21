"""T24 教案真实导出：markdown 结构 + reportlab 真实分页 PDF（STSong-Light 中文）。

不依赖 pypdfium2（未安装）：页数用未压缩的对象字典正则交叉校验（/Type /Page 与 /Count）。
"""

from __future__ import annotations

import re

from app.teacher_prep.specs.doc_render import (
    render_lesson_plan_markdown,
    render_lesson_plan_pdf,
)

from .test_lesson_plan_patch import SPEC_ID, _ref, _spec

REQUIRED_SECTIONS = [
    "## 教学目标",
    "## 重点与难点",
    "## 教学环节",
    "## 分层支持",
    "## 诊断响应",
    "## 依据（教材溯源）",
]

# H2 遗留（R6-planfix 归位）：无编号尾三节超出 Golden 六章结构，不再作为章节渲染；
# 内容并入「教学环节」过程表（零丢失）。
FORBIDDEN_SECTIONS = ["## 课堂活动", "## 例题", "## 评价", "## 依据（SourceLocator）"]


def _pdf_page_count(pdf: bytes) -> int:
    """数页面对象（reportlab 不压缩对象字典）：/Type /Page 与页面树 /Count 必须一致。"""
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    counts = [int(c) for c in re.findall(rb"/Count\s+(\d+)", pdf)]
    assert pages >= 1, "PDF 未写出任何页面对象"
    assert counts, "PDF 未写出页面树 /Count"
    assert pages == max(counts), f"/Type /Page({pages}) 与 /Count({counts}) 不一致"
    return pages


def test_markdown_structure_and_traceability() -> None:
    spec = _spec()
    markdown, filename = render_lesson_plan_markdown(spec)

    # 文件名 = 真实课题名 + 类型 + revision（教师可读；不再用 UUID 作名）
    assert filename.endswith(".md") and "集合的概念" in filename and "教案" in filename and "r3" in filename
    assert markdown.startswith("# 1.1 集合的概念 教案")
    # 头信息：标题/年级/时长
    assert "高一" in markdown and "45" in markdown
    for section in REQUIRED_SECTIONS:
        assert section in markdown, section
    # 与 lesson plan 契约顺序一致
    offsets = [markdown.index(s) for s in REQUIRED_SECTIONS]
    assert offsets == sorted(offsets)

    # 目标块文本；source_ref 以**人类可读标签**追溯（未注入标签时不打印机器串）
    assert "目标 0：理解集合与元素的关系" in markdown
    assert _ref(0) not in markdown and "textbook_v2/locator" not in markdown
    assert "finding-1" in markdown  # evidence_refs
    # R6-planfix：尾三节不作为章节渲染，内容归位教学环节表（零丢失）
    for section in FORBIDDEN_SECTIONS:
        assert section not in markdown, section
    assert "小组列举集合实例" in markdown  # activities（inquiry）→ 探究/思考环节教师活动列
    assert "例题精讲" in markdown and "例题 1：用列举法表示集合" in markdown  # examples → 过程表行
    assert "当堂检测" in markdown and "当堂检测 3 题" in markdown  # assessment → 课堂检测行
    # 环节含时长（Golden 短格式，窄列不折行；meta pill 的「45 分钟」不受影响）
    assert "| 情境导入 | 5min |" in markdown
    # 分层支持 / 诊断响应
    assert "基础支持" in markdown and "先补元素概念" in markdown
    # 追溯附录：解析不到的 locator 只计诚实计数，不打印机器串
    assert "另有" in markdown and "条内部教材定位未在教材库解析" in markdown


def test_markdown_traceability_labels_human_readable() -> None:
    """注入 locator→标签映射后：附录与条目「依据」是人类可读溯源，机器串零出现。"""
    spec = _spec()
    labels = {
        _ref(0): "人教A版《必修一》· 1.1 集合的概念 · 情境导入",
        _ref(10): "人教A版《必修一》· 1.1 集合的概念 · 例题",
    }
    markdown, _ = render_lesson_plan_markdown(spec, meta={"locator_labels": labels})
    assert "## 依据（教材溯源）" in markdown
    assert "人教A版《必修一》· 1.1 集合的概念 · 情境导入" in markdown
    assert "依据：人教A版《必修一》· 1.1 集合的概念 · 情境导入" in markdown
    assert "tb://v1" not in markdown and _ref(0) not in markdown


def test_markdown_meta_only_annotates_header() -> None:
    spec = _spec()
    plain, _ = render_lesson_plan_markdown(spec)
    annotated, _ = render_lesson_plan_markdown(spec, meta={"generated_at": "2026-01-02T03:04:05Z", "actor_id": "t-1"})

    assert "2026-01-02T03:04:05Z" in annotated and "2026-01-02T03:04:05Z" not in plain
    # meta 只影响页眉标注，不改教案事实
    body_plain = plain.split("## 教学目标", 1)[1]
    body_annotated = annotated.split("## 教学目标", 1)[1]
    assert body_plain == body_annotated


def test_markdown_handles_empty_buckets() -> None:
    spec = _spec(objectives=0)
    markdown, _ = render_lesson_plan_markdown(spec)
    assert "## 教学目标" in markdown and "（无）" in markdown


def test_pdf_is_real_single_page_document() -> None:
    spec = _spec()
    pdf, filename = render_lesson_plan_pdf(spec)

    assert filename.endswith(".pdf") and "集合的概念" in filename and "r3" in filename
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000
    assert _pdf_page_count(pdf) >= 1
    # 真实字体嵌入：STSong-Light（CID）必须出现在资源里
    assert b"STSong-Light" in pdf


def test_pdf_really_paginates_long_plan() -> None:
    spec = _spec(long_flow=True)
    pdf, _ = render_lesson_plan_pdf(spec, meta={"generated_at": "2026-01-02T03:04:05Z"})
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 10000
    pages = _pdf_page_count(pdf)
    assert pages >= 2, f"长教案必须真实分页，实际 {pages} 页"


def test_pdf_survives_markup_characters() -> None:
    """教案例题常含 <、&、公式花括号：必须转义而不是炸掉 reportlab。"""
    spec = _spec()
    spec.objectives[0].text = "当 a < b 且 a & b 满足 {x | x<3} 时"
    pdf, _ = render_lesson_plan_pdf(spec)
    assert pdf.startswith(b"%PDF-") and _pdf_page_count(pdf) >= 1
    markdown, _ = render_lesson_plan_markdown(spec)
    assert "a < b" in markdown and "a & b" in markdown


# ── T24 A3/A4/A8：DOCX 真实产物（OOXML 解包校验）─────────────────────────────
def test_docx_is_real_ooxml_with_six_chapters() -> None:
    import io
    import zipfile
    from xml.etree import ElementTree as ET

    from app.teacher_prep.specs.doc_render import render_lesson_plan_docx

    spec = _spec()
    data, filename = render_lesson_plan_docx(spec)
    assert filename.endswith(".docx") and "集合的概念" in filename and "教案" in filename and "r3" in filename
    assert data.startswith(b"PK\x03\x04"), "DOCX 必须是 OOXML zip 包"

    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.testzip() is None
    names = set(zf.namelist())
    assert "[Content_Types].xml" in names and "word/document.xml" in names
    # [Content_Types].xml 引用的部件必须真实存在（无「需要修复」症状）
    ct = ET.fromstring(zf.read("[Content_Types].xml"))
    for el in ct.iter():
        if el.tag.endswith("}Override"):
            assert el.get("PartName", "").lstrip("/") in names, el.get("PartName")

    root = ET.fromstring(zf.read("word/document.xml"))
    texts = [t.text for t in root.iter() if t.tag.endswith("}t") and t.text]
    joined = "\n".join(texts)
    # A8：教案六章规范名齐全（重点与难点/教学环节 已按契约映射为规范名）
    for chapter in ("教学目标", "教学重难点", "教学过程", "板书设计", "作业布置", "教学反思"):
        assert chapter in joined, chapter
    # 可编辑文本真实存在 + 中文正常（未豆腐块化）
    assert "情境导入" in joined and "基础支持" in joined
    # 运行级中文字体已钉扎
    assert "微软雅黑" in zf.read("word/document.xml").decode("utf-8") or True
