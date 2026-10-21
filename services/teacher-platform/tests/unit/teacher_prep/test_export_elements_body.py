"""G14 导出边界一致性：`slide.elements` 非空时正文 = 元素展开（画布=导出=落库）。

背景（R3c 实测缺陷）：画布按 `elements` 渲染（知识卡/公式/步骤），导出链只吃
`content` → 同一页两套事实（画布≠导出、regen 修订教师看不见）。导出边界把
语义元素确定性展开为正文行（与前端画布可见文本同口径），figure 不进正文
（图形由 slide.math_object 内嵌）、教研批注不上屏、步骤带编号。
"""

from __future__ import annotations

from app.tasks.jobs.export_artifact_v2 import _element_body_lines, _materialize_elements_body


def test_elements_overwrite_content_for_export() -> None:
    spec = {
        "slides": [
            {
                "id": "slide-01",
                "content": "旧骨架正文（教研建议）",
                "elements": [
                    {"role": "knowledge_card", "title": "知识铺垫", "items": ["直线由一点与斜率唯一确定"]},
                    {"role": "formula", "latex": "y - y_0 = k(x - x_0)"},
                ],
            }
        ]
    }
    rewritten = _materialize_elements_body(spec)
    assert rewritten == 1
    body = spec["slides"][0]["content"]
    assert "知识铺垫" in body
    assert "直线由一点与斜率唯一确定" in body
    assert "$y - y_0 = k(x - x_0)$" in body
    assert "旧骨架正文" not in body


def test_steps_items_are_numbered_like_canvas() -> None:
    spec = {
        "slides": [
            {
                "id": "slide-02",
                "content": "旧正文",
                "elements": [{"role": "steps", "items": ["设直线过定点", "代入斜率公式", "整理得点斜式"]}],
            }
        ]
    }
    _materialize_elements_body(spec)
    lines = spec["slides"][0]["content"].split("\n")
    assert lines == ["1. 设直线过定点", "2. 代入斜率公式", "3. 整理得点斜式"]


def test_figure_and_teacher_annotations_stay_out_of_body() -> None:
    spec = {
        "slides": [
            {
                "id": "slide-03",
                "content": "旧正文",
                "elements": [
                    {"role": "figure", "figure_ref": "math_object"},
                    {"role": "bullets", "items": ["引导学生观察图形，归纳结论", "椭圆的定义"]},
                    {"role": "note", "text": "注意适用条件"},
                ],
            }
        ]
    }
    _materialize_elements_body(spec)
    body = spec["slides"][0]["content"]
    assert "引导学生" not in body  # 教研批注不进导出正文
    assert "椭圆的定义" in body
    assert "注意适用条件" in body  # note 是画布可见文本 → 导出同文
    assert "math_object" not in body


def test_content_only_slide_untouched() -> None:
    spec = {"slides": [{"id": "slide-04", "content": "纯文本旧 spec", "elements": []}]}
    assert _materialize_elements_body(spec) == 0
    assert spec["slides"][0]["content"] == "纯文本旧 spec"


def test_element_order_matches_canvas_roles() -> None:
    lines = _element_body_lines({"role": "formula", "latex": "x=1"})
    assert lines == ["$x=1$"]
    # 表格行原样保留单元格分隔
    assert _element_body_lines({"role": "table", "items": ["点斜式 | 斜截式"]}) == ["点斜式 | 斜截式"]
    # 步骤已带编号则不重复编号
    assert _element_body_lines({"role": "steps", "items": ["1. 已编号"]}) == ["1. 已编号"]
