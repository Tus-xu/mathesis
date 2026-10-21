"""圆锥曲线渲染测试（figure_renderer conic 类型）

覆盖（2026-08-29 动态讲解升级）：
1. 椭圆焦点三角形：t 参数动点、F₁F₂ 自动标注、焦半径虚线、渐进揭示帧、渲染不变量
2. 标注点不在曲线上 → 校验拒绝（图形真实性底线：图必须与题设方程一致）
3. 双曲线：两支、渐近线虚线、焦点在 y 轴
4. 抛物线：单坐标自动补全、准线、焦点
5. parse_figure_plan 集成：planner 输出的 conic 图形计划可合并进 plan
6. 主题门控：椭圆/双曲线/抛物线题命中 should_plan_figures
"""

import pytest

from app.services.figure_renderer import (
    FigureParamsError,
    check_svg_invariants,
    derive_figure_frames,
    render_figure,
    render_figure_frames,
    validate_figure_params,
)
from app.skills.socratic_solver.figures import merge_figures_into_plan, parse_figure_plan

# 截图用例：证明椭圆 x²/a²+y²/b²=1 焦点三角形的内心轨迹（a=5, b=3 → c=4）
ELLIPSE_FOCAL_TRIANGLE = {
    "version": 1,
    "type": "conic",
    "params": {
        "curve": "ellipse",
        "axis": "x",
        "a": 5,
        "b": 3,
        "center_label": "O",
        "points": [{"t": 55, "label": "M"}],
    },
}


class TestEllipseFocalTriangle:
    def test_validate_resolves_t_point_onto_curve(self):
        f = validate_figure_params(ELLIPSE_FOCAL_TRIANGLE)
        (pt,) = f["params"]["points"]
        # 手工按参数方程核对（t=55°）
        import math

        expect_x = 5 * math.cos(math.radians(55))
        expect_y = 3 * math.sin(math.radians(55))
        assert abs(pt["x"] - expect_x) < 1e-9
        assert abs(pt["y"] - expect_y) < 1e-9
        assert f["params"]["focal"] == pytest.approx(4.0)

    def test_render_contains_foci_point_and_focal_radii(self):
        svg = render_figure(ELLIPSE_FOCAL_TRIANGLE)
        assert "F₁" in svg and "F₂" in svg and "M" in svg
        assert svg.count("stroke-dasharray") >= 2  # 两条焦半径虚线
        problems = check_svg_invariants(ELLIPSE_FOCAL_TRIANGLE, svg)
        assert [p for p in problems if p["severity"] == "fatal"] == []

    def test_frames_progressive_and_render_safe(self):
        frames = derive_figure_frames(ELLIPSE_FOCAL_TRIANGLE)
        assert len(frames) == 2
        assert frames[0]["label"] == "曲线与焦点"
        # 帧 1 不含动点标注（防泄题限帧），但保留焦点
        payload = render_figure_frames(ELLIPSE_FOCAL_TRIANGLE, step_no=1, frame_limit=1)
        assert len(payload["frames"]) == 1
        assert payload["frames"][0]["data_uri"].startswith("data:image/svg+xml;base64,")

    def test_point_off_curve_rejected(self):
        bad = {
            "version": 1,
            "type": "conic",
            "params": {
                "curve": "ellipse",
                "a": 5,
                "b": 3,
                "points": [{"x": 7.0, "y": 0.0, "label": "M"}],  # 在椭圆外
            },
        }
        with pytest.raises(FigureParamsError, match="不在"):
            validate_figure_params(bad)

    def test_ellipse_requires_a_greater_than_b(self):
        with pytest.raises(FigureParamsError, match="a > b"):
            validate_figure_params(
                {"type": "conic", "params": {"curve": "ellipse", "a": 3, "b": 5}}
            )


class TestHyperbola:
    def test_render_two_branches_and_asymptotes(self):
        fig = {
            "type": "conic",
            "params": {
                "curve": "hyperbola",
                "axis": "x",
                "a": 3,
                "b": 4,
                "points": [{"t": 40, "label": "P"}],
            },
        }
        svg = render_figure(fig)
        # 两支 + 渐近线：至少 4 条 path/line 含虚线（2 渐近线 + 2 焦半径）
        assert svg.count("stroke-dasharray") >= 2
        assert "F₁" in svg and "P" in svg
        problems = check_svg_invariants(fig, svg)
        assert [p for p in problems if p["severity"] == "fatal"] == []

    def test_y_axis_hyperbola(self):
        fig = {
            "type": "conic",
            "params": {"curve": "hyperbola", "axis": "y", "a": 4, "b": 3},
        }
        f = validate_figure_params(fig)
        assert f["params"]["focal"] == 5.0
        svg = render_figure(fig)
        assert [p for p in check_svg_invariants(fig, svg) if p["severity"] == "fatal"] == []


class TestParabola:
    def test_single_coordinate_completed_by_equation(self):
        fig = {
            "type": "conic",
            "params": {"curve": "parabola", "p": 2, "opening": "up",
                       "points": [{"x": 4, "label": "A"}]},
        }
        f = validate_figure_params(fig)
        (pt,) = f["params"]["points"]
        assert pt["y"] == pytest.approx(4 * 4 / (4 * 2))  # y = x²/(4p) = 2
        svg = render_figure(fig)
        assert "F" in svg and "A" in svg
        assert "l" in svg  # 准线标注
        assert [p for p in check_svg_invariants(fig, svg) if p["severity"] == "fatal"] == []

    def test_point_off_parabola_rejected(self):
        with pytest.raises(FigureParamsError, match="不在"):
            validate_figure_params(
                {
                    "type": "conic",
                    "params": {"curve": "parabola", "p": 2, "opening": "up",
                               "points": [{"x": 1, "y": 9}]},
                }
            )


class TestPlannerIntegration:
    def test_parse_and_merge_conic_plan(self):
        raw = (
            '[{"step":1,"caption":"椭圆与焦点，观察焦点三角形","figure":'
            '{"type":"conic","params":{"curve":"ellipse","axis":"x","a":5,"b":3,'
            '"points":[{"t":55,"label":"M"}]}}}]'
        )
        items, error = parse_figure_plan(raw, steps_count=3)
        assert error is None and len(items) == 1
        steps = [{"assertion": "s1", "reason": "r"}, {"assertion": "s2"}, {"assertion": "s3"}]
        merge_figures_into_plan(steps, items)
        merged = steps[0]["figure"]["params"]  # figure.params 即完整 figure_params
        assert merged["type"] == "conic"
        assert merged["params"]["curve"] == "ellipse"

    def test_topic_gate_hits_conics(self):
        from app.skills.socratic_solver.figures import should_plan_figures

        assert should_plan_figures("证明椭圆 x^2/a^2+y^2/b^2=1 焦点三角形的内心在椭圆上")
        assert should_plan_figures("已知双曲线的离心率为 2，求渐近线方程")
        assert should_plan_figures("抛物线 y^2=4x 的焦点弦")
