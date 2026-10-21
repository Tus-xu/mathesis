"""双师课堂 geometry 块（MathFigure3D DSL）的后端收敛逻辑单测。

只测纯函数（安全求值 / 曲线采样 / 场景规范化），不依赖数据库与 LLM。
"""

import math

from app.domains.classroom.stage_router import (
    _normalize_figure,
    _safe_math_eval,
    _sample_curve,
)


class TestSafeMathEval:
    def test_function_of_x(self):
        # y = x^2 - 2x,  x=3
        assert _safe_math_eval("x^2 - 2*x", {"x": 3}) == 9 - 6 == 3.0

    def test_trig_and_constants(self):
        assert abs(_safe_math_eval("sin(pi/2)", {}) - 1.0) < 1e-9
        assert abs(_safe_math_eval("cos(0) + e*0", {}) - 1.0) < 1e-9

    def test_power_and_mod(self):
        assert _safe_math_eval("2**10", {}) == 1024.0
        assert _safe_math_eval("7 % 3", {}) == 1.0

    def test_division_by_zero_returns_none(self):
        assert _safe_math_eval("1/0", {}) is None
        assert _safe_math_eval("sqrt(-1)", {}) is None

    def test_unsafe_expr_returns_none(self):
        assert _safe_math_eval("__import__('os').system('id')", {}) is None
        assert _safe_math_eval("os.system('id')", {}) is None
        assert _safe_math_eval("[1,2,3]", {}) is None
        assert _safe_math_eval("x + unknown", {"x": 1}) is None
        assert _safe_math_eval("", {}) is None
        assert _safe_math_eval(None, {}) is None
        assert _safe_math_eval("x[0]", {"x": [1]}) is None


class TestSampleCurve:
    def test_parametric_circle(self):
        pts = _sample_curve(
            {
                "kind": "parametric",
                "expr": ["2*cos(t)", "2*sin(t)", "0"],
                "t0": 0,
                "t1": 2 * math.pi,
                "samples": 120,
            }
        )
        assert pts is not None
        assert len(pts) == 121
        for (x, y, z) in pts:
            assert abs(x) <= 2.001 and abs(y) <= 2.001 and z == 0.0

    def test_function_curve(self):
        pts = _sample_curve(
            {"kind": "function", "expr": "x^2-4", "x0": -3, "x1": 3, "samples": 60}
        )
        assert pts is not None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        assert min(xs) == -3.0 and max(xs) == 3.0
        # 最大值出现在 x=±3：9-4=5
        assert max(ys) == 5.0

    def test_bad_kind_returns_none(self):
        assert _sample_curve({"kind": "polyline", "points": [[0, 0, 0], [1, 1, 0]]}) is None
        assert _sample_curve({}) is None


class TestNormalizeFigure:
    def test_solid_from_coordinates_builds_polyhedron(self):
        """坐标驱动的 3D 图：顶点命名与题意一致、含底面棱/顶点棱/虚线对角线。"""
        from app.domains.classroom.visual_spec import solid_from_coordinates

        fig = solid_from_coordinates(
            {
                "A": [0, 0, 0], "B": [2, 0, 0], "C": [2, 2, 0], "D": [0, 2, 0],
                "S": [0, 0, 2], "E": [0, 0, 1],
            }
        )
        assert fig is not None
        solid = fig["solids"][0]
        assert solid["kind"] == "polyhedron"
        names = [v["name"] for v in solid["vertices"]]
        assert {"A", "B", "C", "D", "S"} <= set(names)
        edge_flat = [tuple(sorted(e)) for e in solid["edges"]]
        assert ("A", "S") in edge_flat and ("A", "D") in edge_flat  # 顶点到底面
        assert ("A", "B") in edge_flat  # 底面边
        assert fig.get("segments"), "应有底面对角线等虚线辅助线"

    def test_solid_from_coordinates_insufficient(self):
        from app.domains.classroom.visual_spec import solid_from_coordinates

        assert solid_from_coordinates({"A": [0, 0, 0], "B": [1, 0, 0]}) is None
        assert solid_from_coordinates("junk") is None
        assert solid_from_coordinates(None) is None

    def test_prism_full_scene(self):
        fig = _normalize_figure(
            {
                "caption": "四棱柱 ABCD-EFGH",
                "solids": [
                    {
                        "kind": "polyhedron",
                        "vertices": [
                            {"name": f"p{i}", "pos": [0, (i % 2) - 0.5, 0 if i < 4 else 3]}
                            for i in range(8)
                        ],
                        "edges": [["p0", "p1"], ["p4", "p5"], [0, 4]],
                        "opacity": 0.4,
                    }
                ],
                "segments": [{"a": [0, 0, 0], "b": [2, 2, 0], "dashed": True, "label": "AC"}],
                "grid": True,
            }
        )
        assert fig is not None
        assert len(fig["solids"]) == 1
        assert fig["solids"][0]["kind"] == "polyhedron"
        assert len(fig["solids"][0]["edges"]) == 3  # 名称与下标引用都被保留
        assert fig["segments"][0]["dashed"] is True
        assert fig["segments"][0]["label"] == "AC"
        assert fig["caption"] == "四棱柱 ABCD-EFGH"

    def test_empty_figure_returns_none(self):
        assert _normalize_figure({}) is None
        assert _normalize_figure(None) is None
        assert _normalize_figure("junk") is None

    def test_bad_coords_dropped(self):
        fig = _normalize_figure(
            {
                "solids": [
                    {"kind": "box", "center": [9999, 0, 0], "size": [1, 1, 1]},
                    {"kind": "sphere", "center": [0, 0, 0], "radius": 2},
                ],
                "segments": [{"a": [0, 0], "b": [1, 1, 0]}],  # a 缺一维 → 丢弃
                "curves": [{"kind": "parametric", "expr": ["1/t", "0", "0"], "t0": -1, "t1": 1}],
            }
        )
        assert fig is not None
        assert len(fig["solids"]) == 1
        assert fig["solids"][0]["kind"] == "sphere"
        assert "segments" not in fig  # 非法线段被剔除，键不存在
        # 曲线采样：t=0 附近发散点（|值|>50）被剔除，其余有限点保留且被收敛
        pts = fig["curves"][0]["points"]
        assert all(abs(p[0]) <= 50 for p in pts)
        assert len(pts) != len(range(121))  # 意味着确有发散点被剔除

    def test_unknown_solid_kind_dropped(self):
        fig = _normalize_figure({"solids": [{"kind": "warp_drive", "size": [1, 1, 1]}]})
        assert fig is None

    def test_curve_points_passthrough(self):
        fig = _normalize_figure(
            {"curves": [{"kind": "polyline", "points": [[0, 0, 0], [1, 2, 0], [2, 0, 1]]}]}
        )
        assert fig is not None
        assert fig["curves"][0]["points"][1] == [1, 2, 0]

    def test_planes_and_camera(self):
        fig = _normalize_figure(
            {
                "planes": [{"points": [[0, 0, 1], [4, 0, 1], [0, 3, 1]], "color": "#f59e0b"}],
                "camera": {"pos": [5, 5, 6], "target": [0, 1, 0]},
            }
        )
        assert fig is not None
        assert fig["planes"][0]["points"][1] == [4, 0, 1]
        assert fig["camera"]["pos"] == [5, 5, 6]

    # ---- 退化/最小尺寸保护 ----

    def test_tiny_cylinder_radius_promoted(self):
        """LLM 偶尔输出半径 0.1 的圆柱，必须拉到 _MIN_RADIUS 保护线。"""
        fig = _normalize_figure(
            {
                "solids": [
                    {"kind": "cylinder", "base": [0, 0, 0], "top": [0, 0, 2], "radius": 0.1}
                ]
            }
        )
        assert fig is not None
        assert fig["solids"][0]["radius"] >= 0.55

    def test_tiny_box_size_promoted(self):
        fig = _normalize_figure(
            {"solids": [{"kind": "box", "center": [0, 0, 0], "size": [0.05, 0.05, 0.05]}]}
        )
        assert fig is not None
        assert all(v >= 0.8 for v in fig["solids"][0]["size"])

    def test_cylinder_no_top_still_kept(self):
        """cylinder 缺 top 应自动补一个 +y 方向的，避免整张图变成一根线。"""
        fig = _normalize_figure(
            {"solids": [{"kind": "cylinder", "base": [0, 0, 0], "radius": 1.0}]}
        )
        assert fig is not None
        # 必须自动补一个 top
        assert "top" in fig["solids"][0]
        assert fig["solids"][0]["top"][1] > 0  # y 方向有正位移

    def test_polyhedron_missing_edges_auto_filled(self):
        """LLM 经常只给顶点不写 edges；这种情况应自动连最近的 k 个顶点作为兜底。"""
        # 8 顶点立方体（[0,0,0] ~ [1,1,1]），完全不给 edges
        verts = []
        for x in (0, 1):
            for y in (0, 1):
                for z in (0, 1):
                    verts.append({"name": f"v{len(verts)}", "pos": [x, y, z]})
        fig = _normalize_figure({"solids": [{"kind": "polyhedron", "vertices": verts}]})
        assert fig is not None
        # 8 顶点立方体应有 12 条棱，自动连边后数量符合预期
        assert len(fig["solids"][0]["edges"]) == 12

    def test_polyhedron_too_tiny_scaled_up(self):
        """顶点全在 [-0.1, 0.1] 范围时整体应被放大到目标跨度。"""
        verts = [
            {"name": f"v{i}", "pos": [0.1 * (i - 4), 0, 0]} for i in range(8)
        ]
        fig = _normalize_figure({"solids": [{"kind": "polyhedron", "vertices": verts}]})
        assert fig is not None
        xs = [v["pos"][0] for v in fig["solids"][0]["vertices"]]
        # 跨度应接近 _TARGET_SPAN（3.0），不再是 0.7
        assert (max(xs) - min(xs)) >= 2.5

    def test_prism_keeps_relative_shape_when_scaled(self):
        """prism 上下底面同时被缩放后相对位置应保持（高度比例不丢失）。"""
        # 底面三点在 xz 平面（y=0），顶面三点相对底面 +y 方向平移 0.005
        fig = _normalize_figure(
            {
                "solids": [
                    {
                        "kind": "prism",
                        "bottom": [[0.01, 0, 0], [0.02, 0, 0], [0.015, 0, 0.017]],
                        "top": [[0.01, 0.005, 0], [0.02, 0.005, 0], [0.015, 0.005, 0.017]],
                    }
                ]
            }
        )
        assert fig is not None
        b = fig["solids"][0]["bottom"]
        t = fig["solids"][0]["top"]
        # 关键：底面 y 应都是同一个值（底面水平，相对位置不变）
        assert len({round(p[1], 3) for p in b}) == 1
        assert len({round(p[1], 3) for p in t}) == 1
        # 上下底面 y 差（柱高）应与原数据比例保持
        h_after = t[0][1] - b[0][1]
        assert h_after > 0  # 柱体有效
        # 缩放后跨度应接近 _TARGET_SPAN
        all_pts = b + t
        xs = [p[0] for p in all_pts]
        zs = [p[2] for p in all_pts]
        assert (max(xs) - min(xs)) >= 2.0
        assert (max(zs) - min(zs)) >= 2.0

    def test_polyhedron_with_letter_names_keeps_edges(self):
        """LLM 给字母 name（A,B,C...）时，edges 引用必须能正确映射。"""
        verts = [
            {"name": "A", "pos": [0, 0, 0]}, {"name": "B", "pos": [3, 0, 0]},
            {"name": "C", "pos": [3, 3, 0]}, {"name": "D", "pos": [0, 3, 0]},
            {"name": "E", "pos": [0, 0, 3]}, {"name": "F", "pos": [3, 0, 3]},
            {"name": "G", "pos": [3, 3, 3]}, {"name": "H", "pos": [0, 3, 3]},
        ]
        edges_in = [
            ["A", "B"], ["B", "C"], ["C", "D"], ["D", "A"],
            ["E", "F"], ["F", "G"], ["G", "H"], ["H", "E"],
            ["A", "E"], ["B", "F"], ["C", "G"], ["D", "H"],
        ]
        fig = _normalize_figure(
            {"solids": [{"kind": "polyhedron", "vertices": verts, "edges": edges_in}]}
        )
        assert fig is not None
        solid = fig["solids"][0]
        # 12 条边全部保留（不能因为字母 name 而被过滤）
        assert len(solid["edges"]) == 12
        # 顶点的 name 统一被改写为 v0..v7
        names = [v["name"] for v in solid["vertices"]]
        assert names == [f"v{i}" for i in range(8)]
        # 原 name 应作为 labels 保留（A-H 字母各显示一次）
        labels = solid.get("labels", [])
        label_texts = sorted(lb["text"] for lb in labels)
        assert label_texts == sorted("ABCDEFGH")

    def test_polyhedron_with_empty_or_duplicate_names_falls_back(self):
        """LLM 给空 name 或重复 name 时，必须用 v0..vN 兜底并保留边。"""
        # 8 顶点全空 name，12 条 edges 用整数下标引用（v0..v7 的索引）
        verts = [
            {"name": "", "pos": [0, 0, 0]}, {"name": "", "pos": [3, 0, 0]},
            {"name": "", "pos": [3, 3, 0]}, {"name": "", "pos": [0, 3, 0]},
            {"name": "", "pos": [0, 0, 3]}, {"name": "", "pos": [3, 0, 3]},
            {"name": "", "pos": [3, 3, 3]}, {"name": "", "pos": [0, 3, 3]},
        ]
        # 用整数下标 edges，验证整数下标路径
        edges = [[0, 1], [1, 2], [2, 3], [3, 0],
                 [4, 5], [5, 6], [6, 7], [7, 4],
                 [0, 4], [1, 5], [2, 6], [3, 7]]
        fig = _normalize_figure(
            {"solids": [{"kind": "polyhedron", "vertices": verts, "edges": edges}]}
        )
        assert fig is not None
        solid = fig["solids"][0]
        # 12 条边全部保留（整数下标路径）
        assert len(solid["edges"]) == 12
        # 顶点 name 兜底为 v0..v7
        names = [v["name"] for v in solid["vertices"]]
        assert names == [f"v{i}" for i in range(8)]

    def test_polyhedron_with_index_edges_preserved(self):
        """LLM 给整数下标 edges（如 [0, 4]）时，必须能正确解析。"""
        verts = [
            {"name": f"v{i}", "pos": [i % 2, (i // 2) % 2, i // 4]}
            for i in range(8)
        ]
        fig = _normalize_figure(
            {
                "solids": [
                    {
                        "kind": "polyhedron",
                        "vertices": verts,
                        "edges": [[0, 1], [4, 5], [0, 4]],
                    }
                ]
            }
        )
        assert fig is not None
        # 整数下标 edges 全部被映射为 v0..vN
        edges = fig["solids"][0]["edges"]
        assert ["v0", "v1"] in edges
        assert ["v4", "v5"] in edges
        assert ["v0", "v4"] in edges
