"""R17 组件契约测试：allowlist、schema 校验、HTML/可执行表达式禁令。

验收（DECISIONS.md §7/§8）：
- 服务器只能下发已注册组件类型（未知组件名 → 422）；
- props schema 校验（extra=forbid：多余字段拒绝）；
- 字符串值禁止任意 HTML/CSS 与可执行表达式（落库前强制）。
"""

from __future__ import annotations

import pytest

from app.teacher_prep.conversation.catalog import (
    ALLOWED_COMPONENT_TYPES,
    COMPONENT_CATALOG,
    ComponentSpecInvalid,
    validate_component,
)


def test_allowlist_contains_expected_types():
    assert ALLOWED_COMPONENT_TYPES == {
        "user_text",
        "assistant_text",
        "context_card",
        "blueprint_card",
        "evidence_rail",
        "job_status_card",
        "selection_card",
        "basic_mode_card",
        "diagnostic_card",  # R18 学前诊断任务卡（教师审阅视图）
        "error_card",
        "node_revision_card",  # R3 AI 定向修改完成卡（单环节修订结果）
        "decision_card",  # R2 改选/决策确认动作（非章节胶囊云）
    }


def test_blueprint_card_confirm_fields_optional_and_validated():
    """R2 确认卡新增字段可选（存量行缺字段仍可渲染），违规形状仍被拒。"""
    legacy = validate_component(
        "blueprint_card",
        {"blueprint_id": "b", "revision": 1, "status": "proposal", "title": "t"},
    )
    assert legacy is not None and legacy["info"] == []
    full = validate_component(
        "blueprint_card",
        {
            "blueprint_id": "b",
            "revision": 1,
            "status": "proposal",
            "title": "t",
            "info": [{"label": "课题", "value": "集合的概念"}],
            "intent_bullets": ["从实际问题抽象集合概念"],
            "next_step": "确认后生成教学流程。",
        },
    )
    assert full["info"][0]["label"] == "课题"
    with pytest.raises(ComponentSpecInvalid):
        validate_component(
            "blueprint_card",
            {"blueprint_id": "b", "revision": 1, "status": "proposal", "title": "t", "unknown_field": 1},
        )


def test_decision_card_validates_and_rejects_unknown_action():
    card = validate_component(
        "decision_card",
        {
            "title": "确认改选章节",
            "options": [
                {"id": "a", "label": "确认改选为 1.2.1", "action": "select_section", "section_id": "s"},
                {"id": "b", "label": "保持不变", "action": "send_text", "text": "保持当前章节不变"},
            ],
        },
    )
    assert card is not None and len(card["options"]) == 2
    with pytest.raises(ComponentSpecInvalid):
        validate_component(
            "decision_card",
            {"title": "t", "options": [{"id": "x", "label": "y", "action": "run_shell"}]},
        )


def test_unknown_component_rejected():
    with pytest.raises(ComponentSpecInvalid) as ei:
        validate_component("evil_component", {"text": "x"})
    assert "allowlist" in str(ei.value)


def test_none_component_none_props_ok():
    assert validate_component(None, None) is None


def test_props_without_type_rejected():
    with pytest.raises(ComponentSpecInvalid):
        validate_component(None, {"text": "x"})


def test_extra_fields_rejected():
    with pytest.raises(ComponentSpecInvalid):
        validate_component("user_text", {"text": "hi", "onclick": "alert(1)"})


def test_html_in_text_rejected():
    with pytest.raises(ComponentSpecInvalid) as ei:
        validate_component("assistant_text", {"text": "请点击 <script>alert(1)</script>"})
    assert "HTML" in str(ei.value)


def test_html_in_nested_props_rejected():
    with pytest.raises(ComponentSpecInvalid):
        validate_component(
            "blueprint_card",
            {
                "blueprint_id": "b-1",
                "revision": 1,
                "status": "proposal",
                "title": "<b>标题</b>",
                "buckets": [],
            },
        )


def test_executable_expression_rejected():
    # 2026-10-10 契约修订：裸 `{{ config.x }}` 已改为折叠放行（见
    # test_doubled_braces_outside_math_collapsed_not_rejected）；
    # 本测试继续守住 _EXPR_RE 的真实可执行表达式禁令。
    with pytest.raises(ComponentSpecInvalid):
        validate_component("assistant_text", {"text": "计算 eval(user_input)"})
    with pytest.raises(ComponentSpecInvalid):
        validate_component("assistant_text", {"text": "读取 document.cookie"})


def test_javascript_uri_rejected():
    with pytest.raises(ComponentSpecInvalid):
        validate_component("assistant_text", {"text": "点我 javascript:alert(1)"})


# ── 2026-09-14 P0：LaTeX 嵌套花括号不得被误判为模板插值 ─────────────────────
# 教材 direct-chunk 正文天然含 LaTeX，`\overline{{{x}}}` / `\sum_{i=1}^{n}` 会产出
# 与模板插值同形的 `{{`。误判导致含公式的 evidence_rail / blueprint 卡片落库被拒，
# 备课 run 恒失败（实测 `refs[6].snippet` 误判 10 次；全库上下文命中 `{{{x}}` 1066 次）。
_LATEX_SNIPPETS = [
    r"$\overline{{{x}}} = \frac {1}{n} \sum_ {j = 1} ^ {2} n _ {j} \overline {{{x}}} _ {j}$",
    r"$$\overline{{{x}}} = \frac{1}{n}\sum_{j=1}^{2} n_{j}\overline{{{x}}}_{j}, s^{2} = ...$$",
    r"$N^{*}=\{1,2,3,\cdots\}$ ， $A=\{1,2,3\}$",
    r"当 $y_{0}<2$ 时， $x+\frac{1}{x}\geqslant y_{0}$",
]


@pytest.mark.parametrize("snippet", _LATEX_SNIPPETS)
def test_latex_math_braces_not_treated_as_template(snippet: str):
    """数学片段内的 `{{` 是惰性文本，不得触发「可执行表达式」拒绝。"""
    props = validate_component("evidence_rail", {"refs": [{"ref": "chunk/1", "kind": "example", "title": "t", "snippet": snippet}]})
    assert props["refs"][0]["snippet"] == snippet


def test_latex_math_braces_allowed_in_context_card_objectives():
    """context_card.objectives 同样承载教材原文（含 LaTeX）。"""
    props = validate_component("context_card", {"objectives": [r"$\overline{{{x}}}$ 的定义"]})
    assert props["objectives"] == [r"$\overline{{{x}}}$ 的定义"]


def test_doubled_braces_outside_math_collapsed_not_rejected():
    """2026-10-10 契约修订：数学片段外的 `{{…}}` 折叠为单括号后放行，不再拒绝。

    依据：本 catalog 字段全部由前端纯文本插值渲染（无 v-html、Vue 不二次编译
    数据字符串），双花括号是惰性文本；同形误杀曾使 9.3 统计案例蓝图 5/5 次
    尝试 FAILED、run 行孤儿化（无限转圈）。真注入向量（HTML/DOM sink/事件
    处理器/eval）仍由 _EXPR_RE/_HTML_TAG_RE 在把关，见 test_dom_sink_*。
    """
    props = validate_component("assistant_text", {"text": "普通文本 {{ config.secret }} 后面"})
    assert props["text"] == "普通文本 { config.secret } 后面"

    props = validate_component("assistant_text", {"text": " {{{x}}} 与 s^{{2}} "})
    assert props["text"] == " {x} 与 s^{2} "

    # 数学片段内的双括号保持原样（教材语料合法形态，历史契约不回归）
    props = validate_component("evidence_rail", {"refs": [{"ref": "c/1", "kind": "example", "title": "t", "snippet": r"$$\overline{{{x}}}$$"}]})
    assert props["refs"][0]["snippet"] == r"$$\overline{{{x}}}$$"

    # 折叠不得制造新注入面：{{document.cookie}} 折叠后仍被 _EXPR_RE 拒绝
    with pytest.raises(ComponentSpecInvalid):
        validate_component("assistant_text", {"text": "{{document.cookie}}"})


def test_dom_sink_inside_math_still_rejected():
    """数学片段剥离只豁免模板插值；URL scheme / DOM sink / 事件处理器仍在原文上检查。"""
    for evil in (r"$x$ javascript:alert(1)", r"$x$ <script>alert(1)</script>", r"$x$ onclick=alert(1)", r"$x$ eval(1)"):
        with pytest.raises(ComponentSpecInvalid):
            validate_component("assistant_text", {"text": evil})


def test_evidence_rail_accepts_real_chapter_ref_count():
    """真实单章去重后可达 51 条（> 旧上限 50）——容量必须覆盖。"""
    refs = [{"ref": f"chunk/{i}", "kind": "example", "title": f"t{i}", "snippet": "s"} for i in range(51)]
    props = validate_component("evidence_rail", {"refs": refs})
    assert len(props["refs"]) == 51


def test_evidence_rail_capacity_is_bounded():
    """放宽容量不等于无界：仍须有上限。"""
    refs = [{"ref": f"chunk/{i}", "kind": "example", "title": "t", "snippet": "s"} for i in range(121)]
    with pytest.raises(ComponentSpecInvalid):
        validate_component("evidence_rail", {"refs": refs})


def test_valid_roundtrip_normalizes():
    props = validate_component("user_text", {"text": "  用必修一讲单调性  "})
    assert props == {"text": "  用必修一讲单调性  "}


def test_blueprint_card_valid():
    props = validate_component(
        "blueprint_card",
        {
            "blueprint_id": "b-1",
            "revision": 2,
            "status": "confirmed",
            "title": "函数单调性",
            "buckets": [
                {"bucket": "objectives", "title": "教学目标", "items": [{"text": "理解单调性", "source_ref": None}]}
            ],
            "context_snapshot_hash": "0" * 64,
        },
    )
    assert props["revision"] == 2


def test_bad_enum_rejected():
    with pytest.raises(ComponentSpecInvalid):
        validate_component(
            "blueprint_card",
            {"blueprint_id": "b-1", "revision": 1, "status": "evil", "title": "t", "buckets": []},
        )


def test_registry_schemas_are_strict():
    """注册表内所有 props schema 必须 extra=forbid（不放过任意字段）。"""
    from pydantic import BaseModel

    for name, cls in COMPONENT_CATALOG.items():
        assert issubclass(cls, BaseModel), name
        assert cls.model_config.get("extra") == "forbid", f"{name} 未启用 extra=forbid"
