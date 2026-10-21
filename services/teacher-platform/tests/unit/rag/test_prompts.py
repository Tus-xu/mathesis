"""提示词模板渲染自测：14 个模板全量渲染 + KNR evidence 契约断言。

B1 渲染入口 = app.prompts.registry.PromptRegistry；本测试即模板的「编译检查」——
任何模板变量改名/漏声明 evidence 会让这里红。
"""

import pytest

from app.prompts.registry import PromptRegistry

GENERATION_TEMPLATES = [
    "lesson/outline.j2",
    "lesson/section.j2",
    "deck/outline.j2",
    "deck/slide.j2",
    "grading/feedback.j2",
    "grading/review_slides.j2",
    "grading/variant.j2",
]

ALL_TEMPLATES = [
    "butler/system.j2",
    "butler/intent_fc.j2",
    "butler/web_answer.j2",
    "voice/latex_parse.j2",
    *GENERATION_TEMPLATES,
    "lesson/cliche_rewrite.j2",
    "recognition/rebuild.j2",
    "recognition/kp_suggest.j2",
]


def _evidence() -> dict:
    return {
        "textbook_facts": [{"content": "椭圆定义…", "locator": "人教A版选必一 P89"}],
        "curriculum_constraints": [{"content": "经历…过程", "locator": "课标 §1.2"}],
        "examples": [{"content": "已知椭圆…", "locator": "quiz:123"}],
        "class_context": "高二(3)班正确率 62%",
        "teacher_preferences": "偏好几何直观导入",
    }


@pytest.fixture(scope="module")
def reg() -> PromptRegistry:
    return PromptRegistry()


def test_all_14_templates_present(reg: PromptRegistry):
    assert len(ALL_TEMPLATES) == 14
    for name in ALL_TEMPLATES:
        src, filename, _uptodate = reg._env.loader.get_source(reg._env, name)  # noqa: SLF001
        assert filename, name


def test_meta_headers_complete(reg: PromptRegistry):
    for name in ALL_TEMPLATES:
        meta = reg.meta(name)
        assert meta.get("purpose") and meta.get("pipeline"), name
        assert meta.get("version"), name
        assert isinstance(meta.get("evidence"), bool), name


def test_generation_templates_declare_evidence(reg: PromptRegistry):
    reg.assert_evidence_contract(GENERATION_TEMPLATES)  # 不抛即过


def test_render_butler_system(reg: PromptRegistry):
    out = reg.render(
        "butler/system.j2",
        context={"route": "课件工坊", "deck_title": "椭圆的标准方程", "slide_index": 3,
                 "class_name": "高二(3)班", "textbook": "人教A版选必一", "chapter": "第三章",
                 "selection": "e=0.8"},
        memories=["偏好几何直观导入"],
        turn_reference=None,
    )
    assert "教研组" in out and "课件工坊" in out


def test_render_intent_fc(reg: PromptRegistry):
    out = reg.render("butler/intent_fc.j2")
    assert "math_chat" in out and "chitchat" in out


def test_render_web_answer(reg: PromptRegistry):
    out = reg.render(
        "butler/web_answer.j2",
        sources=[{"index": 1, "title": "椭圆定义", "site": "例站", "snippet": "第一定义为…"}],
        evidence=[{"index": 2, "title": "教材", "ref": "P89", "snippet": "…"}],
        query="椭圆第一定义和第二定义的区别？",
    )
    assert "[n=1]" in out and "椭圆" in out


def test_render_latex_parse(reg: PromptRegistry):
    out = reg.render("voice/latex_parse.j2", spoken="根号下 x 平方加 y 平方", l1_hints="\\sqrt{}")
    assert '"latex"' in out and "根号下 x 平方加 y 平方" in out


def test_render_lesson_outline(reg: PromptRegistry):
    out = reg.render(
        "lesson/outline.j2",
        form={"textbook_version": "人教A版选必一", "chapter": "第三章", "topic": "椭圆的标准方程",
              "lesson_no": 1, "duration": 45, "lesson_type": "新授", "key_points": None,
              "extra_requirements": None, "example_source": None},
        evidence=_evidence(),
    )
    assert "椭圆的标准方程" in out and "45" in out and "missing_evidence" in out


def test_render_lesson_section(reg: PromptRegistry):
    out = reg.render(
        "lesson/section.j2",
        form={"topic": "椭圆的标准方程", "textbook_version": "人教A版选必一", "chapter": "第三章",
              "duration": 45, "lesson_type": "新授"},
        outline={"goals": "…", "key_points": {"重点": "…", "难点": "…"}},
        board={"name": "新知探究", "minutes": 12, "goal": "…"},
        evidence=_evidence(),
    )
    assert "新知探究" in out and "12" in out


def test_render_cliche_rewrite(reg: PromptRegistry):
    out = reg.render(
        "lesson/cliche_rewrite.j2",
        form={"topic": "椭圆的标准方程", "textbook_version": "人教A版", "chapter": "第三章",
              "lesson_type": "新授"},
        hits=[{"text": "激发学生学习兴趣", "pattern": "激发(学生)?(学习)?兴趣", "tier": "hard",
               "note": "不可检验"}],
        passage="本节课激发学生学习兴趣。",
    )
    assert "激发学生学习兴趣" in out


def test_render_deck_outline(reg: PromptRegistry):
    out = reg.render(
        "deck/outline.j2",
        form={"topic": "椭圆的标准方程", "textbook_version": "人教A版选必一", "chapter": "第三章",
              "lesson_type": "新授", "page_budget": 24, "class_name": "高二(3)班",
              "extra_requirements": None},
        slot_schemas=["worked_example：pedagogical_role=例题讲解 required_slots=[stem,steps]"],
        evidence=_evidence(),
    )
    assert "worked_example" in out and "24" in out


def test_render_deck_slide(reg: PromptRegistry):
    out = reg.render(
        "deck/slide.j2",
        form={"topic": "椭圆的标准方程"},
        page={"ord": 5, "page_type": "derivation", "title": "标准方程推导",
              "intent": "…", "carries": ["a-b-c 关系"]},
        slot_schemas=["derivation：…"],
        evidence=_evidence(),
    )
    assert "derivation" in out and "page_type" in out


def test_render_grading_feedback(reg: PromptRegistry):
    out = reg.render(
        "grading/feedback.j2",
        question={"stem": "设椭圆…求离心率", "kp_code": "G3.1", "error_rate": "42%"},
        cluster={"count": 12, "samples": ["e=a/c…"]},
        evidence=_evidence(),
    )
    assert "聚类" in out


def test_render_review_slides(reg: PromptRegistry):
    out = reg.render(
        "grading/review_slides.j2",
        assignment={"title": "周测三", "class_name": "高二(3)班", "submitted": 38, "total": 40},
        question={"ord": 7, "kp_code": "G3.1", "error_rate": "42%", "stem": "…"},
        clusters=[{"count": 12, "severity": "conceptual", "summary": "…", "feedback": "…"}],
        evidence={"class_context": {"distribution": "A:40% B:30%", "related_error_rates": "无"}},
    )
    assert "chart" in out


def test_render_variant(reg: PromptRegistry):
    out = reg.render(
        "grading/variant.j2",
        question={"stem": "椭圆 x^2/4+y^2/3=1 的离心率", "kp_code": "G3.1",
                  "difficulty": None, "answer": "1/2", "q_type": "choice",
                  "solution_sketch": None, "analysis": None},
        direction="换数值",
        evidence=_evidence(),
    )
    assert "check_answer" in out and "互斥" in out


def test_render_rebuild_and_kp_suggest(reg: PromptRegistry):
    r1 = reg.render("recognition/rebuild.j2", stem="如图…", figure_description="椭圆+焦点",
                    preset_catalog=["ellipse：a,b"])
    assert "preset" in r1 and "confidence" in r1
    r2 = reg.render("recognition/kp_suggest.j2", kp_subtree=None, stem="求离心率", q_type="choice")
    assert "alternates" in r2
