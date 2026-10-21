"""F13 分步渲染测试（figure_renderer.derive_figure_frames / render_figure_frames）。

覆盖：帧派生（function/triangle2d/立体/单帧退化）、帧 1 无答案性标注、
确定性（同参数同 SVG）、frame_limit 截断、帧级不变量通过、参数非法抛错。
"""

import base64
import xml.etree.ElementTree as ET

import pytest

from app.services.figure_renderer import (
    FigureParamsError,
    check_svg_invariants,
    derive_figure_frames,
    render_figure_frames,
    validate_figure_params,
)

FUNCTION_FIG = {
    "type": "function",
    "params": {
        "curves": [{"expr": "x**2-2*x-3", "label": "y=x^2-2x-3"}],
        "x_range": [-3, 5],
        "y_range": [-5, 6],
        "points": [
            {"x": -1, "y": 0, "label": "(-1,0)"},
            {"x": 3, "y": 0, "label": "(3,0)"},
            {"x": 1, "y": -4, "label": "(1,-4)"},
        ],
    },
}

FUNCTION_FIG_PLAIN = {
    "type": "function",
    "params": {
        "curves": [{"expr": "sin(x)", "label": "y=sin x"}],
        "x_range": [-7, 7],
        "y_range": [-2, 2],
    },
}

SOLID_FIG = {"type": "cube", "params": {"a": 2}}

SPHERE_FIG = {"type": "sphere", "params": {"r": 1, "center_label": "O"}}

TRIANGLE_FIG = {
    "type": "triangle2d",
    "params": {
        "points": {"A": [0, 0], "B": [3, 0], "C": [0, 4]},
        "right_angle": "A",
        "circumcircle": True,
    },
}


def _decode(data_uri: str) -> str:
    assert data_uri.startswith("data:image/svg+xml;base64,")
    return base64.b64decode(data_uri.split(",", 1)[1]).decode("utf-8")


def _texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return [(el.text or "").strip() for el in root.iter() if el.tag.endswith("text")]


# ========== 帧派生 ==========


class TestDeriveFrames:
    def test_function_two_frames(self):
        frames = derive_figure_frames(FUNCTION_FIG)
        assert len(frames) == 2
        base, full = frames[0]["figure"], frames[1]["figure"]
        # 帧 1：去关键点/渐近线（答案性标注）
        assert base["params"]["points"] == []
        assert base["params"]["asymptotes"] == {"x": [], "y": []}
        # 帧 2：完整参数（规范化后）
        assert full["params"]["points"][0]["x"] == -1
        assert frames[0]["label"] == "坐标系与曲线"
        assert frames[1]["label"] == "标注关键点"

    def test_function_frame1_renders_without_answer_labels(self):
        from app.services.figure_renderer import render_figure

        frames = derive_figure_frames(FUNCTION_FIG)
        svg1 = render_figure(frames[0]["figure"])
        svg2 = render_figure(frames[1]["figure"])
        for t in ("(-1,0)", "(3,0)", "(1,-4)"):
            assert t not in svg1, f"帧 1 不应包含答案性标注 {t}"
            assert t in svg2, f"帧 2 应包含标注 {t}"

    def test_function_plain_single_frame(self):
        frames = derive_figure_frames(FUNCTION_FIG_PLAIN)
        assert len(frames) == 1
        assert frames[0]["label"] == "函数图像"

    def test_triangle_two_frames(self):
        frames = derive_figure_frames(TRIANGLE_FIG)
        assert len(frames) == 2
        assert frames[0]["figure"]["params"]["circumcircle"] is False
        assert frames[0]["figure"]["params"]["right_angle"] is None
        assert frames[1]["figure"]["params"]["circumcircle"] is True

    def test_solid_single_frame_contract(self):
        """立体类型不再走"轮廓→标注"两帧渐进（单帧全标注合同，见上）。"""
        frames = derive_figure_frames(SOLID_FIG)
        assert len(frames) == 1
        assert frames[0]["label"] == "几何体"

    def test_solid_single_frame_labeled(self):
        """立体类型单帧全标注合同（2026-08-30 截图事故：两帧设计在 frame_limit=1 下
        学生看到的图没有任何字母）——单帧、标签"几何体"、顶点字母可见。"""
        from app.services.figure_renderer import render_figure

        frames = derive_figure_frames(SOLID_FIG)
        assert len(frames) == 1
        assert frames[0]["label"] == "几何体"
        svg = render_figure(frames[0]["figure"])
        assert "A" in _texts(svg) and "B" in _texts(svg), f"顶点字母缺失: {_texts(svg)}"

    def test_sphere_single_frame(self):
        frames = derive_figure_frames(SPHERE_FIG)
        assert len(frames) == 1
        assert frames[0]["label"] == "几何体"

    def test_solid_show_labels_off_single_frame(self):
        frames = derive_figure_frames(
            {"type": "cube", "params": {"a": 1}, "style": {"show_labels": False}}
        )
        assert len(frames) == 1

    def test_invalid_params_raise(self):
        with pytest.raises(FigureParamsError):
            derive_figure_frames({"type": "nonsense", "params": {}})

    def test_frames_are_valid_figure_params(self):
        """每帧都必须是一份可独立校验/渲染的 figure_params。"""
        for fig in (FUNCTION_FIG, SOLID_FIG, TRIANGLE_FIG, SPHERE_FIG):
            for fr in derive_figure_frames(fig):
                validate_figure_params(fr["figure"])  # 不抛即通过


# ========== 帧渲染载荷 ==========


class TestRenderFigureFrames:
    def test_payload_structure(self):
        payload = render_figure_frames(
            FUNCTION_FIG, step_no=2, caption="观察图像与 x 轴交点"
        )
        assert payload["step_no"] == 2
        assert payload["caption"] == "观察图像与 x 轴交点"
        assert len(payload["frames"]) == 2
        assert payload["figure_params"] == FUNCTION_FIG
        for fr in payload["frames"]:
            svg = _decode(fr["data_uri"])
            problems = check_svg_invariants(FUNCTION_FIG, svg)
            assert not [p for p in problems if p["severity"] == "fatal"]
            assert fr["label"]

    def test_frame_limit(self):
        payload = render_figure_frames(FUNCTION_FIG, frame_limit=1)
        assert len(payload["frames"]) == 1
        assert payload["frames"][0]["label"] == "坐标系与曲线"

    def test_frame_limit_none_gives_all(self):
        payload = render_figure_frames(FUNCTION_FIG)
        assert len(payload["frames"]) == 2

    def test_determinism(self):
        a = render_figure_frames(FUNCTION_FIG, step_no=1)
        b = render_figure_frames(FUNCTION_FIG, step_no=1)
        assert a == b

    def test_single_frame_payload(self):
        payload = render_figure_frames(SPHERE_FIG)
        assert len(payload["frames"]) == 1
        assert "step_no" not in payload

    def test_caption_truncated(self):
        payload = render_figure_frames(SPHERE_FIG, caption="长" * 100)
        assert len(payload["caption"]) <= 80
