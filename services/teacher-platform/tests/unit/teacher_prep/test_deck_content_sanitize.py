"""R8-T：deck 内容净化 + 内容 QA 判定单测（确定性、幂等、零 LLM）。

覆盖验收：
- 教研批注（「引导学生…」）判定高置信、不误伤正常板书内容；
- 教案「教师活动」列**不被误伤**（批注剔除只作用于 deck 页面正文）；
- 模板前缀（「本节结论：」）剥离且不误伤普通句；
- 工程黑话（「（AI 生成变式）」/SourceLocator/教材直连/「本节结论：」）零泄漏；
- 净化确定性、幂等（同输入两次结果一致）；
- 跨页逐字重复去重；
- 内容 QA 判定：批注/黑话/裸 LaTeX/跨页重复/内容单薄/标题空 → 真实 status。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.ppt_v2.qa.content import content_defects_for_page, detect_content_defects
from app.teacher_prep.specs.projector import (
    ProjectionRunLineage,
    project_deck_spec,
    project_lesson_plan_spec,
)
from app.teacher_prep.text_safety import (
    has_internal_term,
    is_teacher_annotation,
    sanitize_deck_text,
    strip_internal_terms,
    strip_orphan_punctuation_lines,
    strip_template_prefix,
)

from .tp_context_fixtures import build, seed_synthetic_textbook, selection

# ── 1. 教研批注判定 ──────────────────────────────────────────────────────────


def test_is_teacher_annotation_flags_high_confidence_sentences() -> None:
    for text in (
        "引导学生回忆椭圆的定义与标准方程，回顾利用方程研究几何图形性质的一般方法",
        "结合教材观察与思考问题，先引导学生从图形直观猜想椭圆范围，再用代数方法推导验证",
        "1. 让学生先动手画椭圆，再总结定义",
        "布置教材本节课后作业，明确作业要求",
        "教师通过追问启发学生思考",
    ):
        assert is_teacher_annotation(text), f"未识别教研批注: {text}"


def test_is_teacher_annotation_does_not_flag_real_content() -> None:
    for text in (
        "探究如何利用椭圆的代数方程确定椭圆的边界范围",
        "掌握利用椭圆标准方程研究椭圆几何性质（范围、对称性、顶点）的方法",
        "椭圆的对称性",
        "观察椭圆的扁平程度",
        r"平面内，动点$M$与定点$F$的距离和为常数的轨迹是椭圆",
        "完成课本练习第 2、3 题",
    ):
        assert not is_teacher_annotation(text), f"误伤真实内容: {text}"


# ── 2. 模板前缀 / 工程黑话 ───────────────────────────────────────────────────


def test_strip_template_prefix_keeps_body() -> None:
    assert strip_template_prefix("本节结论：掌握利用椭圆标准方程研究几何性质的方法") == (
        "掌握利用椭圆标准方程研究几何性质的方法"
    )
    assert strip_template_prefix("本节小结：范围与对称性") == "范围与对称性"
    assert strip_template_prefix("结论：$c^2=a^2-b^2$") == "$c^2=a^2-b^2$"
    # 普通句不被误伤
    assert strip_template_prefix("椭圆的对称性") == "椭圆的对称性"
    assert strip_template_prefix("掌握椭圆的范围") == "掌握椭圆的范围"


def test_strip_internal_terms_zero_leak() -> None:
    # 整行都是黑话 → 清空
    assert strip_internal_terms("教材直连 · SourceLocator 溯源见备注") == ""
    assert strip_internal_terms("SourceLocator") == ""
    # 夹带黑话 → 只剥黑话，保留正文
    assert strip_internal_terms("教材直连 · 椭圆的范围") == "椭圆的范围"
    # 括号形态黑话（AI 生成变式 / 教师修订）由 sanitize_deck_text 统一剥除
    assert sanitize_deck_text("掌握椭圆（AI 生成变式）") == "掌握椭圆"
    assert sanitize_deck_text("掌握椭圆（教师修订）") == "掌握椭圆"
    assert sanitize_deck_text("掌握椭圆（教师A10编辑验证-1）") == "掌握椭圆"


def test_sanitize_deck_text_is_deterministic_and_idempotent() -> None:
    raw = "本节结论：掌握椭圆（AI 生成变式）"
    once = sanitize_deck_text(raw)
    assert once == "掌握椭圆"
    assert sanitize_deck_text(raw) == once  # 确定性
    assert sanitize_deck_text(once) == once  # 幂等


def test_has_internal_term_detects_all_leak_forms() -> None:
    assert has_internal_term("教材直连 · SourceLocator 溯源见备注")
    assert has_internal_term("本节结论：掌握椭圆")
    assert has_internal_term("已知动点 M 的轨迹是椭圆（AI 生成变式）")
    assert has_internal_term("椭圆的定义（教师修订）")
    assert not has_internal_term("掌握利用椭圆标准方程研究几何性质的方法")


# ── 2b. 孤立标点行（R8-F P1：句子剔除后句末标点残留）─────────────────────────


def test_strip_orphan_punctuation_lines_drops_punct_only_lines() -> None:
    # 多行：仅由标点组成的行被丢弃，正文行原样保留
    raw = "通过直观观察和代数推导，探究得到椭圆的范围结论和对称性质。\n。"
    assert strip_orphan_punctuation_lines(raw) == (
        "通过直观观察和代数推导，探究得到椭圆的范围结论和对称性质。"
    )
    # 常见标点/空白行形态
    for orphan in ("。", "；", "，、", "  。  ", "！？", "...", "· ·", ""):
        assert strip_orphan_punctuation_lines(orphan) == ""
    # 含真实文字的行不被误伤
    assert strip_orphan_punctuation_lines("椭圆的对称性。") == "椭圆的对称性。"


def test_sanitize_deck_text_drops_orphan_punctuation() -> None:
    # 「本节结论：。」剥前缀后只剩标点 → 整条丢弃（不得作为孤立标点进入版面）
    assert sanitize_deck_text("本节结论：。") == ""
    assert sanitize_deck_text("。") == ""
    # 正文 + 残留孤立标点行 → 只保留正文
    assert sanitize_deck_text("探究椭圆的范围。\n。") == "探究椭圆的范围。"
    # 幂等
    once = sanitize_deck_text("探究椭圆的范围。\n。")
    assert sanitize_deck_text(once) == once


# ── 3. 内容 QA 判定 ──────────────────────────────────────────────────────────


def _spec_page(title: str, elements: list[dict]) -> dict:
    return {"title": title, "elements": elements}


def _bullets(items: list[str]) -> dict:
    return {"role": "bullets", "items": items}


def _steps(items: list[str]) -> dict:
    return {"role": "steps", "items": items}


def test_qa_flags_annotation_jargon_and_bare_latex() -> None:
    defects = content_defects_for_page(
        page_no=2,
        pptx_lines=[
            "学习目标",
            "引导学生回忆椭圆的定义与标准方程",
            r"观察椭圆 $\frac{x^2}{a^2}+\frac{y^2}{b^2}=1$ 的形状",
            "已知动点 M 的轨迹是椭圆（AI 生成变式）",
        ],
        spec_page=_spec_page(
            "学习目标",
            [_bullets(["掌握椭圆的范围", "理解离心率的意义"]), {"role": "note", "text": "课堂提示"}],
        ),
    )
    kinds = {d["kind"] for d in defects}
    assert "teacher_annotation" in kinds
    assert "bare_latex" in kinds
    assert "internal_term" in kinds
    assert all(d["severity"] == "p0" for d in defects)


def test_qa_flags_cover_internal_term() -> None:
    defects = content_defects_for_page(
        page_no=1,
        pptx_lines=["高中数学 · 课堂教学", "3.1.2 椭圆的简单几何性质", "教材直连 · SourceLocator 溯源见备注"],
        spec_page=_spec_page("3.1.2 椭圆的简单几何性质", []),
    )
    assert any(d["kind"] == "internal_term" and d["severity"] == "p0" for d in defects)


def test_qa_flags_thin_page_and_empty_title() -> None:
    defects = content_defects_for_page(
        page_no=3,
        pptx_lines=["椭圆的对称性"],
        spec_page=_spec_page("", [_bullets(["椭圆的对称性"])]),
    )
    kinds = {d["kind"] for d in defects}
    assert "title_empty" in kinds
    assert "content_thin" in kinds


def test_qa_cross_page_duplicate_dedup() -> None:
    dup_line = "已知动点 M 的轨迹是椭圆，求椭圆的离心率和标准方程"
    spec_pages = {
        1: _spec_page("封面", []),
        2: _spec_page("例题精讲", [_steps([dup_line, "第二行内容足够长用于占位"])]),
        3: _spec_page("课堂小结", [_bullets([dup_line, "另一行内容足够长用于占位"])]),
    }
    pptx_lines = {
        1: ["封面"],
        2: ["例题精讲", dup_line],
        3: ["课堂小结", dup_line],
    }
    defects = detect_content_defects(pptx_lines=pptx_lines, spec_pages=spec_pages)
    assert any(d["kind"] == "cross_page_duplicate" for d in defects[2])
    assert any(d["kind"] == "cross_page_duplicate" for d in defects[3])
    assert not any(d["kind"] == "cross_page_duplicate" for d in defects[1])


def test_qa_clean_deck_has_no_content_defects() -> None:
    spec_pages = {
        1: _spec_page("3.1.2 椭圆的简单几何性质", []),
        2: _spec_page("学习目标", [_bullets(["掌握椭圆的范围", "理解离心率的意义"]), {"role": "note", "text": "本页聚焦几何性质"}]),
        3: _spec_page("例题精讲", [_steps(["已知椭圆的标准方程", "求离心率"]), _bullets(["注意焦点位置"])]),
    }
    pptx_lines = {
        1: ["高中数学 · 课堂教学", "3.1.2 椭圆的简单几何性质", "人教A版2019 · 选择性必修第一册"],
        2: ["学习目标", "掌握椭圆的范围", "理解离心率的意义"],
        3: ["例题精讲", "已知椭圆的标准方程", "求离心率", "注意焦点位置"],
    }
    defects = detect_content_defects(pptx_lines=pptx_lines, spec_pages=spec_pages)
    assert all(not d for d in defects.values()), defects


# ── 4. 投影边界：deck 剔除批注 / 教案教师活动列不误伤 ────────────────────────


def _flow_body(ctx) -> dict:
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "3.1.2 椭圆的简单几何性质",
        "objectives": [{"text": "掌握椭圆的范围、对称性、特殊点", "source_ref": None}],
        "prior_knowledge": [],
        "concepts": [{"text": "椭圆的标准方程", "source_ref": None}],
        "theorems": [{"text": "本节结论：掌握利用椭圆标准方程研究几何性质的方法", "source_ref": None}],
        "examples": [{"text": "已知动点 M 的轨迹是椭圆，求离心率", "source_ref": None}],
        "inquiries": [{"text": "观察椭圆的扁平程度", "source_ref": None}],
        "key_points": [{"text": "椭圆的对称性", "source_ref": None}],
        "difficult_points": [{"text": "离心率的刻画", "source_ref": None}],
        "lesson_flow": [
            {
                "stage": "introduction",
                "title": "动手画椭圆，总结定义",
                "duration_minutes": 5,
                "items": [{"text": "引导学生回忆椭圆的定义与标准方程", "source_ref": None}],
                "summary": "复习椭圆的定义与标准方程，明确学习目标。",
                "hints": [{"text": "教师通过追问启发学生", "source_ref": None}],
            },
            {
                "stage": "new_content",
                "title": "探究椭圆的范围与对称性",
                "duration_minutes": 10,
                "items": [{"text": "探究如何利用椭圆的代数方程确定椭圆的边界范围", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }


def _project_deck(ctx, body: dict):
    return project_deck_spec(
        spec_id=str(uuid.uuid4()),
        blueprint_id=str(uuid.uuid4()),
        blueprint_revision=2,
        blueprint_body=body,
        context=ctx,
        profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="m"),
        run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


async def test_deck_drops_annotations_and_jargon(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection())
    deck = _project_deck(ctx, _flow_body(ctx))

    visible: list[str] = []
    for slide in deck.slides:
        visible.append(slide.title)
        visible.extend(slide.content.split("\n"))
        for el in slide.elements:
            if el.text:
                visible.append(el.text)
            if el.title:
                visible.append(el.title)
            visible.extend(el.items)

    joined = "\n".join(t for t in visible if t)
    assert "引导学生" not in joined
    assert "教师通过" not in joined
    assert "本节结论" not in joined
    assert "AI 生成变式" not in joined
    # 封面副标题是教师可读的教材标识（来自 context.source），不是内部术语
    assert deck.slides[0].content
    assert not has_internal_term(deck.slides[0].content)
    # 真实教学内容保留（批注剔除不删内容）
    assert any("探究如何利用椭圆的代数方程确定椭圆的边界范围" in t for t in visible)


async def test_lesson_plan_keeps_teacher_activity(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection())
    body = _flow_body(ctx)
    lp = project_lesson_plan_spec(
        spec_id=str(uuid.uuid4()),
        blueprint_id=str(uuid.uuid4()),
        blueprint_revision=2,
        blueprint_body=body,
        context=ctx,
        profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="m"),
        run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    teacher_activity = [
        it.text for stage in lp.lesson_flow for it in stage.columns.teacher_activity
    ]
    # 教案「教师活动」列本来就该写教师行为——绝不能被 deck 净化误伤
    assert any("引导学生回忆椭圆的定义与标准方程" in t for t in teacher_activity)
