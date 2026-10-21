"""E-1 回归：数学不等式文本不得误判为 HTML 标签；真实标签仍被拦截/剥除。

背景：2026-09-14 真实链路中，授权教材切片的教学目标文本（含 `0<a<1 时 f(a)>0`
类不等式链）被旧正则 `<[A-Za-z/!][^>]{0,N}>` 误判为 HTML，context/blueprint 卡
在 validate_component 落库闸门 42200，整条备课命令失败（handoff Work card E-1）。
"""

from __future__ import annotations

import pytest

from app.teacher_prep.conversation import catalog
from app.teacher_prep.text_safety import HTML_TAG_RE, strip_html_tags

# 授权教材/课堂常见的数学文本（不得命中）
MATH_TEXTS = [
    "当 0<a<1 时，f(a)>0 恒成立",
    "若 0<e<1 且 x>f(x)，则不等式成立",
    "a<b<b，且 b>c",
    "设 0<x<π/2，比较 sin x 与 x 的大小",
    "x∈(0,1)，f(x)=x² 在 x>0 时单调递增",
]

# 真实 HTML（必须命中）
HTML_TEXTS = [
    "<b>重点</b>",
    "前面<b>加粗</b>后面",
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '<span style="color:red">红色</span>',
    "<br>",
    "<br/>",
    "<!-- 隐藏注释 -->",
    "</div>游离闭合标签",
    "<a href='javascript:void(0)'>链接</a>",
]


@pytest.mark.parametrize("text", MATH_TEXTS)
def test_math_text_is_not_html(text: str):
    assert HTML_TAG_RE.search(text) is None, f"数学文本被误判为 HTML: {text}"


@pytest.mark.parametrize("text", HTML_TEXTS)
def test_real_html_is_detected(text: str):
    assert HTML_TAG_RE.search(text) is not None, f"真实 HTML 未被拦截: {text}"


def test_strip_html_tags_basic():
    assert strip_html_tags("<b>重点</b>") == "重点"
    assert strip_html_tags("第一行<br>第二行") == "第一行\n第二行"
    assert strip_html_tags("<p>段落</p>").strip() == "段落"
    assert strip_html_tags("<!-- 注释 -->正文") == "正文"


def test_strip_html_tags_keeps_math():
    for text in MATH_TEXTS:
        assert strip_html_tags(text) == text, f"数学文本被消毒改动: {text}"


def test_strip_html_tags_no_entity_unescape():
    # 实体保持字面量，避免反解出新标签
    assert strip_html_tags("&lt;b&gt;") == "&lt;b&gt;"


def test_context_card_with_math_objectives_passes_catalog():
    props = {
        "source_id": "277b4c26-6af6-4229-8c2f-82395d268c8e",
        "source_title": "人教A版2019 · 必修第一册",
        "section_id": "20bc31f5-af6f-4830-83c3-316a9f140495",
        "section_title": "2.3 二次函数与一元二次方程、不等式",
        "objectives": MATH_TEXTS[:4],
        "knowledge_points": ["二次函数", "不等式"],
    }
    validated = catalog.validate_component("context_card", props)
    assert validated is not None
    assert validated["objectives"] == props["objectives"]


def test_context_card_with_real_html_is_rejected():
    props = {"objectives": ["掌握 <b>顶点式</b> 的推导"]}
    with pytest.raises(catalog.ComponentSpecInvalid):
        catalog.validate_component("context_card", props)


def test_blueprint_item_with_math_text_passes():
    from app.teacher_prep.blueprint.schema import BlueprintItem

    item = BlueprintItem(text="当 0<a<1 时，f(a)>0 恒成立")
    assert item.text.startswith("当 0<a<1")


def test_blueprint_item_with_real_html_is_rejected():
    from app.teacher_prep.blueprint.schema import BlueprintItem

    with pytest.raises(Exception):
        BlueprintItem(text="掌握 <b>顶点式</b> 的推导")


def test_strip_html_tags_table_becomes_markdown_pipes():
    """2026-10-11 教材数字流事故回归：HTML 表格必须转管道表，单元格不得无缝拼接。"""
    html = "表2\n\n<table><tr><td>序号</td><td>1</td><td>2</td></tr><tr><td>男生身高平均数</td><td>171.3</td><td>169.8</td></tr></table>"
    out = strip_html_tags(html)
    assert "序号 | 1 | 2" in out, out
    assert "男生身高平均数 | 171.3 | 169.8" in out, out
    # 单元格边界不得消失（事故形态：171.3169.8）
    assert "171.3169.8" not in out, out
    # 无残留表格标签
    assert "<td>" not in out and "<table>" not in out, out


def test_strip_html_tags_table_th_and_caption():
    out = strip_html_tags("<table><thead><tr><th>项目</th><th>中位数</th></tr></thead><tbody><tr><td>男员工</td><td>21.6</td></tr></tbody></table>")
    assert "项目 | 中位数" in out
    assert "男员工 | 21.6" in out
    assert "21.6" in out


def test_strip_html_tags_non_table_text_unchanged():
    out = strip_html_tags("当 0<a<1 时，f(a)>0")
    assert out == "当 0<a<1 时，f(a)>0"
