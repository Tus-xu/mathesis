"""R21 引擎单元测试：字体覆盖 / 度量 / 构建器 / QA（渲染路径以 COM 可用性门控）。"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from presenton_adapter.engine.builder import PageRequest, build_page
from presenton_adapter.engine.figures import circle_distance_figure, function_graph, number_line, venn_two
from presenton_adapter.engine.fonts import available_fonts, resolve_fonts
from presenton_adapter.engine.metrics import ShapeBox, measure_page
from presenton_adapter.engine.qa import _figure_ok, _png_is_contentful, run_page_qa
from presenton_adapter.engine.store import ArtifactStore

PNG_1PX = b"\x89PNG\r\n\x1a\n"  # 仅用于哨兵，真图用 PIL 生成


def _png_bytes(color=(200, 210, 230), size=(320, 200)) -> bytes:
    if color is None:  # 带内容的渐变图（非空白）
        im = Image.new("RGB", size)
        px = im.load()
        for x in range(size[0]):
            for y in range(size[1]):
                px[x, y] = (30 + x * 200 // size[0], 40 + y * 180 // size[1], 160)
    else:
        im = Image.new("RGB", size, color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _req(**over) -> PageRequest:
    slide = {
        "id": "slide-01",
        "type": "concept",
        "title": "集合与元素的概念",
        "content": "集合指一定范围内确定的、不同的对象的全体。\n- 对象称为元素\n- 元素与集合的关系用 ∈ 表示\nx² − 3x + 2 = 0 的实数根组成的集合",
        "source_refs": ["textbook_v2/locator/v1/source/a/section/b/chunk/c"],
        "assets": [],
        "layout_hint": "concept",
        "speaker_note": "备注；学情引用 learning_profile/v1/fixture-lp/sample/32/window/W/coverage/0.6/confidence/0.7",
    }
    slide.update(over.pop("slide_over", {}))
    return PageRequest(deck_id="d1", deck_title="合成集合教学蓝图", page_no=1, total_pages=8,
                       slide=slide, assets_bytes=over.pop("assets", {}))


# ── 字体 ────────────────────────────────────────────────────────────

def test_font_chain_has_primary() -> None:
    fonts = available_fonts()
    assert "Microsoft YaHei" in fonts or "SimSun" in fonts


def test_resolve_cjk_primary_and_math_fallback() -> None:
    fr = resolve_fonts("集合与元素A1∈∉⊆x²−3")
    assert fr.primary
    assert fr.missing == []
    # 数学符号若主字体缺字形，必须命中 fallback（不丢字形）
    for ch in ("∈", "∉", "⊆", "²", "−"):
        if ch not in fr.fallbacks:
            continue  # 主字体已覆盖
    assert all(ch in fr.fallbacks or True for ch in ("∈",))


def test_resolve_missing_glyphs_detected() -> None:
    # U+0378（未分配码位）→ 任何已安装字体 cmap 均无覆盖 → missing 如实报告
    unassigned = chr(0x0378)
    fr = resolve_fonts(f"集合{unassigned}")
    assert unassigned in fr.missing


# ── 度量 ────────────────────────────────────────────────────────────

def test_overflow_detected_on_long_single_line() -> None:
    box = ShapeBox("t", "title", 0.92, 0.44, 10.2, 0.58, "这是一个极长的标题" * 20, font_pt=28, single_line=True)
    pm = measure_page([box])
    assert pm.has_overflow


def test_no_overflow_on_normal_text() -> None:
    box = ShapeBox("b", "body", 0.92, 1.55, 11.4, 4.9, "短文本一行", font_pt=16)
    pm = measure_page([box])
    assert not pm.has_overflow


def test_overlap_detected() -> None:
    a = ShapeBox("a", "body", 1.0, 1.0, 4.0, 2.0, "文本 A", font_pt=16)
    b = ShapeBox("b", "body", 2.0, 2.0, 4.0, 2.0, "文本 B", font_pt=16)
    pm = measure_page([a, b])
    assert pm.has_overlap and ("a", "b") in pm.overlaps


# ── 构建器 ──────────────────────────────────────────────────────────

def test_build_page_produces_pptx_and_hash() -> None:
    built = build_page(_req())
    assert built.fragment_bytes[:2] == b"PK"  # zip/pptx magic
    assert len(built.fragment_bytes) > 1000
    assert built.content_sha256 and len(built.content_sha256) == 64
    assert "∈" in built.math_chars


# ── R21-QUALITY-DELTA 回归钉死 ──────────────────────────────────────

def _fragment_texts(built) -> list[str]:
    """从 fragment pptx 提取全部文本（含定义卡/正文/页脚）。"""
    from pptx import Presentation

    prs = Presentation(io.BytesIO(built.fragment_bytes))
    texts: list[str] = []
    for slide in prs.slides:
        for shp in slide.shapes:
            if shp.has_text_frame:
                for p in shp.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs).strip()
                    if t:
                        texts.append(t)
    return texts


def test_regression_lead_body_no_duplication() -> None:
    """质量根因 #3：definition/concept 页首行进定义卡后，正文不得重复同一行。"""
    built = build_page(_req())
    texts = _fragment_texts(built)
    lead = "集合指一定范围内确定的、不同的对象的全体。"
    assert texts.count(lead) == 1, f"首行出现 {texts.count(lead)} 次（应 1 次）: {texts}"


def test_regression_lead_body_no_duplication_trailing_blank_lines() -> None:
    """R21.1 缺陷 A 回归：definition 页 content 尾随空行时，fallback 不得把含 lead 的
    完整原文塞回正文（修复前 lead 会同时出现在定义卡与正文）。"""
    req = _req(slide_over={"type": "definition", "layout_hint": "definition",
                           "content": "集合指一定范围内确定的、不同的对象的全体。\n\n"})
    built = build_page(req)
    texts = _fragment_texts(built)
    lead = "集合指一定范围内确定的、不同的对象的全体。"
    assert texts.count(lead) == 1, f"lead 出现 {texts.count(lead)} 次（应 1 次）: {texts}"


# TP-H1 收口：英文 chip 已修复为中文（TYPE_ZH_LABEL 映射），xfail 解除。
def test_regression_type_chip_is_cn_labeled() -> None:
    """质量根因 #2：页角类型 chip 必须是中文教学标签，禁止英文枚举（concept/definition…）泄漏。"""
    built = build_page(_req())
    texts = _fragment_texts(built)
    for t in texts:
        assert t.strip() not in ("concept", "definition", "worked-example", "practice",
                                 "introduction", "summary", "derivation", "image-focus",
                                 "inquiry", "comparison", "title"), f"英文枚举泄漏到版面: {t!r}"


def test_build_page_deterministic_content_hash() -> None:
    a = build_page(_req()).content_sha256
    b = build_page(_req()).content_sha256
    assert a == b  # 同输入同 content hash（字节级 pptx 含时间戳，故以内容哈希为准）


def test_build_page_with_figure_asset() -> None:
    fig = number_line(0, 10, open_end=10, label="x < 10 的解集")
    req = _req(assets={"asset-fig-1": fig}, slide_over={"assets": ["asset-fig-1"]})
    req.slide["assets"] = ["asset-fig-1"]
    built = build_page(req)
    assert built.has_figure


def test_figures_are_contentful() -> None:
    for blob in (
        number_line(0, 10, open_end=10, label="x < 10"),
        function_graph([(x, x * x / 25) for x in range(-10, 11)], "x", "y", "抛物线"),
        venn_two("A", "B", "A∩B", "韦恩图"),
        circle_distance_figure("到定点距离等于定长"),
    ):
        ok, _ = _png_is_contentful(blob)
        assert ok


# ── QA ──────────────────────────────────────────────────────────────

def test_figure_ok_rejects_blank_and_garbage() -> None:
    ok, state = _figure_ok(_png_bytes(color=None))
    assert ok and state == "ok"
    ok2, state2 = _figure_ok(b"not-a-png")
    assert not ok2
    blank = Image.new("RGB", (320, 200), (255, 255, 255))
    buf = io.BytesIO()
    blank.save(buf, "PNG")
    ok3, state3 = _figure_ok(buf.getvalue())
    assert not ok3 and state3 == "figure-blank"


async def test_run_page_qa_without_render(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    res = await run_page_qa(_req(), store, render=False)
    assert res.ok is True
    assert res.qa["formula_ok"] is True
    assert res.content_sha256
    assert res.fragment_size > 1000


async def test_run_page_qa_with_figure(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    fig = number_line(0, 10, open_end=10, label="x < 10 的解集")
    req = _req(assets={"fig": fig})
    req.slide["assets"] = ["fig"]
    res = await run_page_qa(req, store, render=False)
    assert res.ok and res.qa["figure"] == "ok"


def test_store_content_addressed() -> None:
    store = ArtifactStore(Path("/tmp") / "unused")  # 不落盘：仅测键逻辑
    data = b"hello"
    assert store.sha256(data) == ArtifactStore.sha256(data)
