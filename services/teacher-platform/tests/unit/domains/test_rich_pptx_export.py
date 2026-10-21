"""rich teacher slide → PPTX 的真实媒体/显式降级合同。"""
from __future__ import annotations

import io
import zipfile

from app.export import pptx_engine
from app.export.pptx_engine import build_pptx


def _package_xml(deck: dict) -> tuple[list[str], str]:
    package = build_pptx(deck)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")
    return names, xml


def test_function_plot_is_embedded_as_real_pptx_media():
    names, xml = _package_xml(
        {
            "slides": [{
                "elements": [{
                    "type": "functionPlot",
                    "left": 120,
                    "top": 100,
                    "width": 640,
                    "height": 420,
                    "expr": "y=x^2",
                    "params": {},
                    "domain": [-2, 2],
                }]
            }]
        }
    )

    assert any(name.startswith("ppt/media/") for name in names)
    assert "<p:pic" in xml


def test_dynamic_demo_is_explicitly_reported_when_pptx_is_static():
    _names, xml = _package_xml(
        {
            "slides": [{
                "elements": [{
                    "type": "dynamicDemo",
                    "left": 120,
                    "top": 100,
                    "width": 640,
                    "height": 180,
                    "demo_id": "solid/cube-section",
                    "caption": "截面形状猜一猜",
                }]
            }]
        }
    )

    assert "动态演示" in xml
    assert "solid/cube-section" in xml


def test_unknown_element_is_explicitly_reported_instead_of_silently_dropped():
    _names, xml = _package_xml(
        {
            "slides": [{
                "elements": [{
                    "type": "futureInteractive",
                    "left": 120,
                    "top": 100,
                    "width": 640,
                    "height": 180,
                }]
            }]
        }
    )

    assert "未支持的课件元素" in xml
    assert "futureInteractive" in xml


def test_figure3d_polyhedron_accepts_student_integer_edge_indices(monkeypatch):
    calls = []
    monkeypatch.setattr(pptx_engine, "_line3", lambda _ax, *points, **_kwargs: calls.append(points))
    scene = {
        "solids": [{
            "kind": "polyhedron",
            "vertices": [
                {"name": "A", "pos": [0, 0, 0]}, {"name": "B", "pos": [2, 0, 0]},
                {"name": "C", "pos": [2, 2, 0]}, {"name": "D", "pos": [0, 2, 0]},
            ],
            "edges": [[0, 1], [1, 2], [2, 3], [3, 0]],
        }],
    }

    package = build_pptx({"slides": [{"elements": [{
        "type": "figure3d", "left": 100, "top": 100, "width": 500, "height": 400, "scene": scene,
    }]}]})

    assert package
    assert len(calls) == 4


def test_figure3d_student_curves_are_rendered_instead_of_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(pptx_engine, "_line3", lambda _ax, *points, **_kwargs: calls.append(points))
    scene = {
        "solids": [],
        "curves": [{
            "kind": "polyline",
            "points": [[-2, 0, 0], [0, 1, 0], [2, 0, 0]],
            "closed": False,
            "color": "#2563eb",
        }],
        "planes": [],
    }

    package = build_pptx({"slides": [{"elements": [{
        "type": "figure3d", "left": 100, "top": 100, "width": 500, "height": 400, "scene": scene,
    }]}]})

    assert package
    assert any(len(points) == 3 for points in calls)
