"""R22 皮肤移植回归测试：plan 路径正文卡片化（对齐 golden-B-r211 观感）。

验收锚点：
- 页上零裸 TEXT_BOX：文字一律内嵌 AUTO_SHAPE（旧标杆 148 个 AUTO_SHAPE / 0 个 TEXT_BOX）；
- 正文块有浅底圆角卡（EFF6FF/F1F5F9），行级 bullet 保持透明承载；
- 已被 resolver 发卡的文本（lead/def-banner/prompt 等）不叠第二层卡；
- 皮肤纯视觉层：QA 度量盒（boxes）与 resolver 输出一致（坐标纪律不破）。
"""
from __future__ import annotations

import io

from pptx import Presentation


def _make_request(slides: list[dict]):
    from app.contracts.deck import TeachingDeckSpec
    from app.visual_planning import build_visual_plan

    from presenton_adapter.engine.builder import PageRequest

    spec_dict = {
        "schema_version": "2.0.0", "id": "skin-regression", "revision": 1,
        "metadata": {"title": "二次函数的图象与性质", "language": "zh-CN"},
        "textbook_sources": [{
            "textbook_id": "tb-1", "section_id": "sec-1", "page_refs": [],
            "chunk_refs": ["textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1"],
            "locator": "textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1",
        }],
        "slides": slides,
        "assets": [], "outline": [], "selected_examples": [], "theme": None,
        "speaker_notes": {}, "change_log": [],
    }
    spec = TeachingDeckSpec.model_validate(spec_dict)
    plan = build_visual_plan(spec)
    return [
        PageRequest(deck_id="t", deck_title="二次函数的图象与性质", page_no=i, total_pages=len(slides),
                    slide=slide_dict, visual_plan=plan["slides"][i - 1],
                    deck_style_tokens=plan["style_tokens"])
        for i, slide_dict in enumerate(spec_dict["slides"], 1)
    ], plan


_NUMBERED_SLIDE = {
    "id": "s2", "type": "introduction", "teaching_goal": "提问", "title": "情境问题",
    "content": "- 问题一：球飞行高度何时最高？\n- 问题二：利润何时最大？\n- 问题三：如何用式子刻画？",
    "source_refs": [], "assets": [], "visual_intent": "提问", "layout_hint": "introduction",
    "speaker_note": "", "revision": 0, "status": "confirmed", "qa_status": "pending",
}
_GENERIC_SLIDE = {
    "id": "s3", "type": "inquiry", "teaching_goal": "探究", "title": "合作探究",
    "content": "请用表达式刻画抛掷高度随时间的变化规律，并说明系数的含义与符号约定。",
    "source_refs": [], "assets": [], "visual_intent": "探究", "layout_hint": "inquiry",
    "speaker_note": "", "revision": 0, "status": "confirmed", "qa_status": "pending",
}


def _shape_stats(fragment_bytes: bytes) -> dict:
    prs = Presentation(io.BytesIO(fragment_bytes))
    slide = prs.slides[0]
    textboxes = 0
    auto_with_text = 0
    filled_roundrect = 0
    for sh in slide.shapes:
        st = str(sh.shape_type)
        has_text = sh.has_text_frame and sh.text_frame.text.strip()
        if "TEXT_BOX" in st:
            textboxes += 1
        elif "AUTO_SHAPE" in st:
            if has_text:
                auto_with_text += 1
            xml = sh._element.xml
            if "roundRect" in xml and "solidFill" in xml and not has_text:
                filled_roundrect += 1
    return {"textboxes": textboxes, "auto_with_text": auto_with_text, "cards": filled_roundrect}


def test_plan_path_has_no_bare_textbox_and_texts_live_in_shapes() -> None:
    """皮肤口径：页上零裸 TEXT_BOX，文字全部内嵌 AUTO_SHAPE（golden-B-r211 观感）。"""
    from presenton_adapter.engine.builder import build_page

    reqs, _plan = _make_request([_NUMBERED_SLIDE, _GENERIC_SLIDE])
    for req in reqs:
        built = build_page(req)
        stats = _shape_stats(built.fragment_bytes)
        assert stats["textboxes"] == 0, f"page {req.page_no} 仍有裸 TEXT_BOX: {stats}"
        assert stats["auto_with_text"] >= 5, f"page {req.page_no} 带字形状过少: {stats}"


def test_plan_path_body_blocks_get_light_cards() -> None:
    """正文块卡片化：numbered 条目有浅底圆角卡；generic 正文块有卡。"""
    from presenton_adapter.engine.builder import build_page

    reqs, _ = _make_request([_NUMBERED_SLIDE, _GENERIC_SLIDE])
    numbered_stats = _shape_stats(build_page(reqs[0]).fragment_bytes)
    # 3 个条目卡 + chip 底 + 3 个编号点底（圆点 roundRect 带填充）≥ 5
    assert numbered_stats["cards"] >= 4, f"numbered 页卡片过少: {numbered_stats}"
    generic_stats = _shape_stats(build_page(reqs[1]).fragment_bytes)
    assert generic_stats["cards"] >= 1, f"generic 正文块未卡片化: {generic_stats}"


def test_skin_cards_do_not_leak_into_qa_boxes() -> None:
    """卡是装饰底（qa_exempt）：QA 度量盒与 resolver 元素一一对应，不混入卡片盒。"""
    from presenton_adapter.engine.builder import build_page

    reqs, _ = _make_request([_NUMBERED_SLIDE])
    built = build_page(reqs[0])
    card_boxes = [b for b in built.boxes if b.shape_id.endswith("-card")]
    assert card_boxes == [], "皮肤卡不得进入 QA 度量盒（坐标纪律：resolver 唯一产生地）"


def test_builder_version_bumped_for_skin() -> None:
    """皮肤是渲染语义变更：builder_version 升版 → 内容寻址/页 checkpoint 正确失效重渲染。"""
    from presenton_adapter.engine import builder

    assert builder.BUILDER_VERSION.endswith("1.4.0"), builder.BUILDER_VERSION
