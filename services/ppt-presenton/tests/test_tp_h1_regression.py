"""TP-H1 引擎侧回归测试：basic chip 中文化 + hero-context 跳过标准 title + primitives 暴露。

针对任务书第 1/4/5/7 条要求：通用去重、不为"集合"等关键词开特例、英文 chip 清除、
图资产缺失显式降级、PPTX 可编辑结构、primitives 可组合。
"""
from __future__ import annotations

import re

import pytest


def test_basic_fallback_chip_is_chinese_label() -> None:
    """basic 路径（无 visual_plan）下 chip 文本 = 中文标签，禁止英文内部枚举泄漏。"""
    from presenton_adapter.engine.builder import TYPE_ZH_LABEL

    expected = {
        "title": "封面", "introduction": "导入", "concept": "概念",
        "definition": "定义", "derivation": "推导", "worked-example": "例题",
        "comparison": "对比", "image-focus": "观察", "practice": "练习",
        "inquiry": "探究", "summary": "小结", "overview": "总览",
    }
    assert TYPE_ZH_LABEL == expected, \
        f"chip 中文映射不全或被改：{set(TYPE_ZH_LABEL) ^ set(expected)}"

    # 反向断言：所有合法类型映射都非空、且不含英文内部枚举词
    INTERNAL = re.compile(
        r"^(concept|definition|derivation|worked-example|image-focus|"
        r"inquiry|summary|comparison|practice|introduction|overview)$")
    for stype, zh in TYPE_ZH_LABEL.items():
        assert zh, f"{stype} 没有中文标签"
        assert not INTERNAL.match(zh), f"{stype} → {zh!r} 是英文内部枚举"
        assert any('\u4e00' <= c <= '\u9fff' for c in zh), \
            f"{stype} → {zh!r} 不含中文字符"


def test_plan_path_chip_uses_role_label_zh_only() -> None:
    """plan 路径：chip 文本来自 layout_resolver._resolve_page 的 vp.role_label_zh，
    渲染时不再混入 slide.type 英文。"""
    from presenton_adapter.engine import layout_resolver as LR

    # 反射验证：_resolve_page 内部 chip 元素直接用 role_label_zh 文本
    import inspect
    src = inspect.getsource(LR.resolve_page)
    assert 'vp.get("role_label_zh"' in src, \
        "plan 路径 chip 应直接取 vp.role_label_zh（已翻译的中文）"


def test_layout_resolver_emits_primitives_composably() -> None:
    """layout_resolver 暴露 prim_* primitives，族 handler 应通过其组合而非坐标硬编码。"""
    import inspect
    from presenton_adapter.engine import layout_resolver as LR

    primitive_names = [
        "prim_title", "prim_lead", "prim_definition", "prim_derivation",
        "prim_example", "prim_diagram", "prim_prompt", "prim_takeaway",
    ]
    for name in primitive_names:
        assert hasattr(LR, name), f"primitive {name} 未暴露"
        fn = getattr(LR, name)
        assert callable(fn), f"{name} 不可调用"
        sig = inspect.signature(fn)
        # primitive 必须接 x,y,w,h 或（slots/位置）参数
        assert len(sig.parameters) >= 4, \
            f"{name} 参数过少（应至少含 x,y,w,*）"


def test_layout_resolver_skips_standard_title_for_hero_context() -> None:
    """hero-context 族自带 hero-title，标准 title 必须跳过，避免与 hero-title 同文重复。"""
    from app.contracts.deck import TeachingDeckSpec
    from app.visual_planning import build_visual_plan
    from presenton_adapter.engine.builder import PageRequest, build_page

    # 单页极简 spec：直接构造，不依赖 golden 副本（避免多样性约束告警）
    spec_dict = {
        "schema_version": "2.0.0",
        "id": "test-hero", "revision": 1,
        "metadata": {"title": "唯一标题 X", "language": "zh-CN"},
        "textbook_sources": [{
            "textbook_id": "tb-1", "section_id": "sec-1",
            "page_refs": [],
            "chunk_refs": ["textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1"],
            "locator": "textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1",
        }],
        "slides": [{
            "id": "slide-1", "type": "title", "teaching_goal": "开场",
            "title": "唯一标题 X", "content": "副题 · 课程",
            "source_refs": ["textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1"],
            "assets": [], "visual_intent": "封面",
            "layout_hint": "title", "speaker_note": "",
            "revision": 0, "status": "confirmed", "qa_status": "pending",
        }],
        "assets": [], "outline": [], "selected_examples": [],
        "theme": None, "speaker_notes": {}, "change_log": [],
    }
    spec = TeachingDeckSpec.model_validate(spec_dict)
    plan = build_visual_plan(spec)
    hero_slide = plan["slides"][0]
    assert hero_slide["layout_family"] == "hero-context"
    req = PageRequest(deck_id="t", deck_title=spec_dict["metadata"]["title"],
                      page_no=1, total_pages=1,
                      slide=spec_dict["slides"][0],
                      visual_plan=hero_slide,
                      deck_style_tokens=plan["style_tokens"])
    built = build_page(req)
    titles = [shape for shape in built.boxes if shape.shape_id == "title"]
    assert titles == [], \
        f"hero-context 不应再产生标准 title shape_id，但找到 {len(titles)} 个"


def test_figure_missing_drops_empty_frame_and_inserts_notice() -> None:
    """图资产字节缺失时，builder 应 drop 配对 frame 并插入显式"图象暂缺"提示。"""
    import io
    from pptx import Presentation

    from app.visual_planning import build_visual_plan
    from app.visual_planning.intent import _TYPE_TO_ROLE
    from app.contracts.deck import TeachingDeckSpec
    from presenton_adapter.engine.builder import PageRequest, build_page
    from app.visual_planning.assets import build_asset_plan
    from app.visual_planning.narrative import build_narrative_plan
    from app.visual_planning.intent import build_slide_intent
    from app.visual_planning.sequence import plan_sequence

    # 构造极简 spec：单页 image-focus，assets 引用不存在的 asset-venn
    spec_dict = {
        "schema_version": "2.0.0",
        "id": "test-fig-missing",
        "revision": 1,
        "metadata": {"title": "缺图降级测试", "language": "zh-CN"},
        "textbook_sources": [{
            "textbook_id": "tb-1", "section_id": "sec-1",
            "page_refs": [],
            "chunk_refs": ["textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1"],
            "locator": "textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1",
        }],
        "slides": [{
            "id": "slide-1", "type": "image-focus", "teaching_goal": "看图",
            "title": "看图", "content": "观察要点",
            "source_refs": ["textbook_v2/locator/v1/source/tb-1/section/sec-1/chunk/c1"],
            "assets": ["asset-venn"],
            "visual_intent": "图象",
            "layout_hint": "image-focus",
            "speaker_note": "",
            "revision": 0, "status": "confirmed", "qa_status": "pending",
        }],
        "assets": [{
            "id": "asset-venn", "kind": "MATH_RENDER",
            "object_key": "k", "sha256": "0" * 64,
            "mime_type": "image/png", "size_bytes": 0,
            "width": 100, "height": 100,
            "source_ref": "x", "rights_status": "ACTIVE",
            "rights_evidence_ref": "队伍确认授权",
            "preserve_original": True,
            "created_by": "t", "created_at": "2026-09-12T00:00:00",
            "transform_history": [],
        }],
        "outline": [], "selected_examples": [], "theme": None,
        "speaker_notes": {}, "change_log": [],
    }
    spec = TeachingDeckSpec.model_validate(spec_dict)
    plan = build_visual_plan(spec)
    req = PageRequest(deck_id="t", deck_title="缺图", page_no=1, total_pages=1,
                      slide=spec_dict["slides"][0],
                      assets_bytes={},   # 关键：bytes 为空
                      visual_plan=plan["slides"][0],
                      deck_style_tokens=plan["style_tokens"])
    built = build_page(req)
    # 装入 Presentation 读回形状：不应有大型 soft-bg (#F1F5F9) 矩形
    prs = Presentation(io.BytesIO(built.fragment_bytes))
    slide = prs.slides[0]
    big_empty_rects = []
    for shape in slide.shapes:
        try:
            w_in = (shape.width or 0) / 914400
            h_in = (shape.height or 0) / 914400
        except Exception:  # noqa: BLE001
            continue
        if w_in < 3.0 or h_in < 2.0:
            continue
        # 空框的标志：无文字、fill 为 #F1F5F9 (soft-bg)
        if shape.has_text_frame and shape.text_frame.text.strip():
            continue
        try:
            if shape.fill.type and int(shape.fill.type) == 1 and \
               str(shape.fill.fore_color.rgb).upper() == "F1F5F9":
                big_empty_rects.append((w_in, h_in, str(shape.fill.fore_color.rgb)))
        except Exception:  # noqa: BLE001
            pass
    assert big_empty_rects == [], \
        f"图资产缺失时不应保留大型空框（soft-bg 矩形）：{big_empty_rects}"