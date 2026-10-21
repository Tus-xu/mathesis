# coding: utf-8
"""确定性立体几何 3D 配图场景（MathFigure3D figure 契约）。

背景：Spark 让 LLM 直接输出完整 3D scene JSON 极不稳定（坐标/棱/标签经常错位），
本模块按课题关键词**确定性**产出已校准的 3D 场景（对齐前端
`@/utils/mathFigure3d.normalizeScene` 消费的 DSL：solids/curves/planes/segments/labels/camera）。
教师在课件里只看到静态图片边界（无交互工具栏），导 PPTX 时由 matplotlib 渲染同场景 PNG。

契约对齐：solid 字段与前端 normalizeScene 一一对应（box.center+size /
cylinder.base+top+radius / cone.base+top+radius / sphere.center+radius /
prism.bottom+top / pyramid.base+apex / polyhedron.vertices+edges）。
"""
from __future__ import annotations

from typing import Any

# 立体几何关键词（借学生端 visual_spec._3D_KEYWORDS 口径；圆锥曲线/解析几何除外）
_3D_KEYWORDS = (
    "多面体", "棱柱", "棱锥", "棱台", "正方体", "长方体", "三棱柱", "四棱柱",
    "三棱锥", "四棱锥", "六面体", "立体几何", "空间几何", "空间向量", "旋转体",
    "圆柱", "圆锥", "圆台", "球体", "球", "二面角", "线面角", "异面直线",
    "线面垂直", "线面平行", "面面垂直", "面面平行",
)
_2D_EXCEPTIONS = ("圆锥曲线", "平面解析几何", "三角函数图像")


def is_3d_topic(text: str) -> bool:
    """课题/页标题是否属于立体几何（可配 3D 图）。"""
    t = text or ""
    for exc in _2D_EXCEPTIONS:
        if exc in t:
            return False
    return any(k in t for k in _3D_KEYWORDS)


def _labels(pairs: list[tuple[list[float], str]]) -> list[dict[str, Any]]:
    return [{"pos": p, "text": t} for p, t in pairs]


def _cube_scene() -> dict[str, Any]:
    s, h = 3.0, 3.0
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [8.5, 7.5, 10.5], "target": [1.5, 1.3, 1.5]},
        "solids": [{"kind": "box", "center": [s / 2, s / 2, s / 2], "size": [s, s, s],
                    "color": "#4f8ef7", "opacity": 0.28}],
        "labels": _labels([
            ([0, 0, 0], "A"), ([s, 0, 0], "B"), ([s, 0, s], "C"), ([0, 0, s], "D"),
            ([0, s, 0], "E"), ([s, s, 0], "F"), ([s, s, s], "G"), ([0, s, s], "H"),
        ]),
    }


def _cuboid_scene() -> dict[str, Any]:
    l, h, w = 4.0, 3.0, 3.0
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [9.5, 7.5, 10.5], "target": [2, 1.3, 1.5]},
        "solids": [{"kind": "box", "center": [l / 2, h / 2, w / 2], "size": [l, h, w],
                    "color": "#4f8ef7", "opacity": 0.28}],
        "labels": _labels([
            ([0, 0, 0], "A"), ([l, 0, 0], "B"), ([l, 0, w], "C"), ([0, 0, w], "D"),
            ([0, h, 0], "E"), ([l, h, 0], "F"), ([l, h, w], "G"), ([0, h, w], "H"),
        ]),
    }


def _cylinder_scene() -> dict[str, Any]:
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [6.5, 5.5, 8.5], "target": [0, 1.3, 0]},
        "solids": [{"kind": "cylinder", "base": [0, 0, 0], "top": [0, 3, 0], "radius": 1.5,
                    "color": "#4f8ef7", "opacity": 0.3}],
    }


def _cone_scene() -> dict[str, Any]:
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [6.5, 6, 8.5], "target": [0, 1.3, 0]},
        "solids": [{"kind": "cone", "base": [0, 0, 0], "top": [0, 3, 0], "radius": 1.5,
                    "color": "#4f8ef7", "opacity": 0.3}],
    }


def _sphere_scene() -> dict[str, Any]:
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [6, 5, 8], "target": [0, 0, 0]},
        "solids": [{"kind": "sphere", "center": [0, 0, 0], "radius": 1.8,
                    "color": "#4f8ef7", "opacity": 0.3}],
    }


def _tri_prism_scene() -> dict[str, Any]:
    bottom = [[0, 0, 0], [3, 0, 0], [1.5, 0, 2.6]]
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [8, 6.5, 9.5], "target": [1.5, 1.3, 1]},
        "solids": [{"kind": "prism", "bottom": bottom, "top": [[0, 3, 0], [3, 3, 0], [1.5, 3, 2.6]],
                    "color": "#4f8ef7", "opacity": 0.3}],
        "labels": _labels([(bottom[0], "A"), (bottom[1], "B"), (bottom[2], "C")]),
    }


def _tri_pyramid_scene() -> dict[str, Any]:
    base = [[0, 0, 0], [3, 0, 0], [1.5, 0, 2.6]]
    apex = [1.5, 3, 0.87]
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [8, 6.5, 9.5], "target": [1.5, 1.3, 1]},
        "solids": [{"kind": "pyramid", "base": base, "apex": apex,
                    "color": "#4f8ef7", "opacity": 0.3}],
        "labels": _labels([(base[0], "A"), (base[1], "B"), (base[2], "C"), (apex, "S")]),
    }


def _square_pyramid_scene() -> dict[str, Any]:
    base = [[0, 0, 0], [3, 0, 0], [3, 0, 3], [0, 0, 3]]
    apex = [1.5, 3, 1.5]
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [8.5, 6.5, 9.5], "target": [1.5, 1.3, 1.5]},
        "solids": [{"kind": "pyramid", "base": base, "apex": apex,
                    "color": "#4f8ef7", "opacity": 0.3}],
        "labels": _labels([(base[0], "A"), (base[1], "B"), (base[2], "C"), (base[3], "D"), (apex, "S")]),
    }


def _square_prism_scene() -> dict[str, Any]:
    """四棱柱（含棱台：顶面等比缩小）。"""
    bottom = [[0, 0, 0], [3, 0, 0], [3, 0, 3], [0, 0, 3]]
    top = [[0, 3, 0], [3, 3, 0], [3, 3, 3], [0, 3, 3]]
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [8.5, 6.5, 9.5], "target": [1.5, 1.3, 1.5]},
        "solids": [{"kind": "prism", "bottom": bottom, "top": top,
                    "color": "#4f8ef7", "opacity": 0.3}],
        "labels": _labels([(bottom[0], "A"), (bottom[1], "B"), (bottom[2], "C"), (bottom[3], "D")]),
    }


def _frustum_scene() -> dict[str, Any]:
    """棱台：四棱柱 top 等比缩小。"""
    bottom = [[0, 0, 0], [3, 0, 0], [3, 0, 3], [0, 0, 3]]
    top = [[1, 2.6, 1], [2, 2.6, 1], [2, 2.6, 2], [1, 2.6, 2]]
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [8.5, 6.5, 9.5], "target": [1.5, 1.3, 1.5]},
        "solids": [{"kind": "prism", "bottom": bottom, "top": top,
                    "color": "#4f8ef7", "opacity": 0.3}],
        "labels": _labels([(bottom[0], "A"), (bottom[1], "B"), (bottom[2], "C"), (bottom[3], "D")]),
    }


def _line_rel_scene() -> dict[str, Any]:
    """线面/面面关系：正方体 + 高亮线段（线面垂直示意）。"""
    s = 3.0
    return {
        "axes": True, "grid": True,
        "camera": {"pos": [8.5, 7.5, 10.5], "target": [1.5, 1.3, 1.5]},
        "solids": [{"kind": "box", "center": [s / 2, s / 2, s / 2], "size": [s, s, s],
                    "color": "#4f8ef7", "opacity": 0.22}],
        "segments": [
            {"a": [0, 0, 0], "b": [3, 3, 3], "dashed": False, "color": "#ef6b5b", "label": "l"},
        ],
        "labels": _labels([
            ([0, 0, 0], "A"), ([3, 0, 0], "B"), ([3, 0, 3], "C"), ([0, 0, 3], "D"),
            ([0, 3, 0], "E"), ([3, 3, 0], "F"), ([3, 3, 3], "G"), ([0, 3, 3], "H"),
        ]),
    }


_SCENES: list[tuple[tuple[str, ...], Any]] = [
    (("球", "球体"), _sphere_scene),
    (("正方体",), _cube_scene),
    (("长方体", "四棱柱"), _cuboid_scene),
    (("三棱锥", "四面体"), _tri_pyramid_scene),
    (("四棱锥",), _square_pyramid_scene),
    (("圆锥",), _cone_scene),
    (("圆柱",), _cylinder_scene),
    (("三棱柱",), _tri_prism_scene),
    (("棱台", "圆台"), _frustum_scene),
    (("棱柱",), _square_prism_scene),
    (("棱锥",), _square_pyramid_scene),
    (("线面", "面面", "异面直线", "二面角", "空间几何", "空间向量"), _line_rel_scene),
    (("多面体", "立体几何", "旋转体"), _cube_scene),
]


def figure3d_scene_for(title: str, points: list[str] | None = None) -> dict[str, Any] | None:
    """课题/页标题 → 确定性 3D 场景；非立体几何主题返回 None（配 2D 图或留空）。

    匹配优先级：最具体的几何体关键词优先（球>正方体>长方体>锥>柱>台>通用关系）。
    """
    text = f"{title or ''} {' '.join(points or [])}"
    if not is_3d_topic(text):
        return None
    for keywords, builder in _SCENES:
        if any(k in text for k in keywords):
            return builder()
    return None
