"""双师课堂·数学正确性测试（不依赖数据库，用 --noconftest 跑）。

覆盖：
- 通用数学验证器 verify_function_claims：导数/临界点/单调区间/极值反求的一致性与错误拦截。
- 通用 LaTeX 结构校验。
- visual_spec：二维采样、主题 3D 判定（函数题禁用 3D）。
- 图形纪律：stage_router 的 _fallback_solids_for_title / _ensure_solids / _normalize_plot2d。
- 验收题标准答案真值交叉验证（从 tests/fixtures 读取，不依赖生产金标准代码）。

设计原则：
- 本文件只测试「通用校验器的自洽性」和「标准答案的真值」，
  不测试任何固定课件、不调用任何金标准生产函数。
- 端到端黑盒验收见 test_classroom_acceptance.py（需 PG）。
"""

import math

from app.domains.classroom import math_verifier as mv
from app.domains.classroom import visual_spec as vs

# 验收题标准答案（仅测试可读，生产目录禁止引用）
from tests.fixtures.acceptance_problems import (
    ACCEPTANCE_A,
    ACCEPTANCE_B,
    ACCEPTANCE_C,
    compute_a_truth,
    compute_b_truth,
    compute_c_distance,
)

# ==================== 通用验证器：verify_function_claims ====================


class TestVerifyFunctionClaims:
    def test_a_monotonicity_verified(self):
        """验收题 A 的标准断言通过通用验证器。"""
        claims = ACCEPTANCE_A["verification_claims"]
        res = mv.verify_function_claims(
            f_expr=claims["f_expr"],
            fprime_expr=claims["fprime_expr"],
            critical_points=claims["critical_points"],
            increasing_intervals=claims["increasing_intervals"],
            decreasing_intervals=claims["decreasing_intervals"],
            max_eval_point=claims["max_eval_point"],
            max_eval_second_deriv=claims["max_eval_second_deriv"],
        )
        assert res["status"] == "verified", f"验收题 A 标准断言应通过: {res}"

    def test_b_parameter_reverse_verified(self):
        """验收题 B 的标准断言通过通用验证器（含参函数 + 参数代入）。"""
        claims = ACCEPTANCE_B["verification_claims"]
        res = mv.verify_function_claims(
            f_expr=claims["f_expr"],
            fprime_expr=claims["fprime_expr"],
            substitutions=claims["substitutions"],
            max_eval_point=claims["max_eval_point"],
            max_eval_second_deriv=claims["max_eval_second_deriv"],
        )
        assert res["status"] == "verified", f"验收题 B 标准断言应通过: {res}"

    def test_wrong_derivative_fails(self):
        """错误的导数被拦截。"""
        res = mv.verify_function_claims(f_expr="x**3-3*x", fprime_expr="3*x**2+3")
        assert res["status"] == "failed"

    def test_wrong_critical_point_fails(self):
        """错误的临界点被拦截。"""
        res = mv.verify_function_claims(
            f_expr="x**3-3*x",
            fprime_expr="3*x**2-3",
            critical_points=[2.0],  # 2 不是 f'=0 的根
        )
        assert res["status"] == "failed"

    def test_wrong_monotone_interval_fails(self):
        """错误的单调区间被拦截。"""
        res = mv.verify_function_claims(
            f_expr="x**3-3*x",
            fprime_expr="3*x**2-3",
            increasing_intervals=[(-1.0, 1.0)],  # (-1,1) 实际递减
        )
        assert res["status"] == "failed"

    def test_unparseable_f_needs_review(self):
        """无法解析的 f 返回 needs_review。"""
        res = mv.verify_function_claims(f_expr="not a function!!!")
        assert res["status"] == "needs_review"


# ==================== 通用 LaTeX 结构校验 ====================


class TestLatexStructure:
    def test_valid_latex(self):
        ok, _ = mv.latex_structure_check(r"$f'(x) = 3(x-1)(x+1)$")
        assert ok

    def test_unmatched_dollar(self):
        ok, detail = mv.latex_structure_check(r"$f(x) = x^2")
        assert not ok and "$" in detail

    def test_unmatched_brace(self):
        ok, detail = mv.latex_structure_check(r"$\frac{1}{2$")
        assert not ok

    def test_dangerous_command(self):
        ok, _ = mv.latex_structure_check(r"\begin{matrix}1\end{matrix}")
        assert not ok


# ==================== 通用页级验证 verify_slide ====================


class TestVerifySlide:
    def test_math_bearing_without_claims_needs_review(self):
        """V4 §7：数学承载页（含 latex/例题/图形）缺结构化断言 → needs_review，不得 verified。"""
        slide = {"blocks": [{"kind": "latex", "latex": "3(x-1)(x+1)"}]}
        res = mv.verify_slide(slide)
        assert res["status"] == "needs_review"
        assert "结构化断言" in res["detail"]

    def test_pure_text_slide_verified_without_claims(self):
        """纯文字页（导入/小结）无需结构化断言，LaTeX 合法即 verified。"""
        slide = {"blocks": [{"kind": "text", "text": "回顾旧知"}, {"kind": "note", "text": "易错点"}]}
        assert mv.verify_slide(slide)["status"] == "verified"

    def test_math_claims_verified(self):
        """正确 math_claims 通过（函数类断言自洽校验）。"""
        slide = {
            "blocks": [{"kind": "latex", "latex": "f(x)=x^3-3x"}, {"kind": "plot2d", "expr": "x^3-3*x"}],
            "math_claims": {
                "f_expr": "x**3-3*x",
                "fprime_expr": "3*(x-1)*(x+1)",
                "critical_points": [-1.0, 1.0],
                "increasing_intervals": [[-100, -1], [1, 100]],
                "decreasing_intervals": [[-1, 1]],
                "max_eval_point": -1.0,
                "max_eval_second_deriv": -6.0,
            },
        }
        assert mv.verify_slide(slide)["status"] == "verified"

    def test_geometry_claims_verified(self):
        """几何类结构化断言（坐标/平面/距离自洽）通过。"""
        slide = {
            "blocks": [{"kind": "geometry", "figure": {"solids": []}}],
            "geometry_claims": {
                "coordinates": {"S": [0, 0, 2], "B": [2, 0, 0], "D": [0, 2, 0], "E": [0, 0, 1]},
                "plane_points": {"SBD": ["S", "B", "D"]},
                "distance": {"point": "E", "plane": "SBD", "value": "1/sqrt(3)"},
            },
        }
        res = mv.verify_slide(slide)
        assert res["status"] == "verified", res

    def test_geometry_claims_wrong_distance_autofixes(self):
        """几何距离是坐标表派生量：模型算术错误 → 按坐标表自动校准（如实记录校准值）。"""
        slide = {
            "blocks": [{"kind": "geometry", "figure": {"solids": []}}],
            "geometry_claims": {
                "coordinates": {"S": [0, 0, 2], "B": [2, 0, 0], "D": [0, 2, 0], "E": [0, 0, 1]},
                "plane_points": {"SBD": ["S", "B", "D"]},
                "distance": {"point": "E", "plane": "SBD", "value": 9.999},
            },
        }
        res = mv.verify_slide(slide)
        assert res["status"] == "verified", res
        assert res.get("autofix"), "应给出按坐标表计算的校准值"
        assert abs(res["autofix"]["value"] - (1 / (3 ** 0.5))) < 1e-3

    def test_geometry_claims_wrong_perpendicular_fails(self):
        """线面垂直与坐标表矛盾（非派生可校准项）→ failed。"""
        slide = {
            "blocks": [{"kind": "geometry", "figure": {"solids": []}}],
            "geometry_claims": {
                "coordinates": {"S": [0, 0, 2], "A": [0, 0, 0], "B": [2, 0, 0], "D": [0, 2, 0]},
                "plane_points": {"SAB": ["S", "A", "B"]},
                "perpendicular": {"line": ["B", "D"], "plane": "SAB"},  # BD 不垂直平面SAB(y=0)
            },
        }
        assert mv.verify_slide(slide)["status"] == "failed"

    def test_bad_latex_needs_review(self):
        """非法 LaTeX 被标记 needs_review。"""
        slide = {"blocks": [{"kind": "latex", "latex": "3(x-1)(x+1 $"}]}
        assert mv.verify_slide(slide)["status"] == "needs_review"

    def test_math_claims_failed(self):
        """错误的 math_claims 被拦截。"""
        slide = {
            "blocks": [],
            "math_claims": {"f_expr": "x**3-3*x", "fprime_expr": "3*x**2+3"},
        }
        assert mv.verify_slide(slide)["status"] == "failed"

    def test_no_prebuilt_passthrough(self):
        """verify_slide 不信任任何预置 verified 标记——所有内容都经过相同校验。"""
        # 即使 slide 自带 verification_result=verified，verify_slide 仍应正常校验
        # （不绕过、不放行）
        slide_clean = {
            "blocks": [{"kind": "latex", "latex": "x^2"}],
            "verification_result": {"status": "verified", "detail": "自称已验证"},
        }
        result = mv.verify_slide(slide_clean)
        # 数学承载页缺结构化断言 → needs_review（不被 prebuilt 放行）
        assert result["status"] == "needs_review"
        # 不应包含 prebuilt 检查项
        check_items = [c.get("item") for c in result.get("checks", [])]
        assert "prebuilt" not in check_items

        # 带 prebuilt 但 LaTeX 非法 → 仍应 needs_review（不被 prebuilt 放行）
        slide_bad = {
            "blocks": [{"kind": "latex", "latex": "x^2 $"}],
            "verification_result": {"status": "verified", "detail": "自称已验证"},
        }
        assert mv.verify_slide(slide_bad)["status"] == "needs_review"


# ==================== 图形纪律测试（病根修复：函数题禁用 3D） ====================


class TestVisualDiscipline:
    """验证 stage_router 图形纪律（V4 契约：禁止标题关键词驱动的默认 3D 图形）。
    函数/数列/概率/平面几何主题不注入 3D solids；即便立体几何主题，
    图形也只能由题意数据（LLM 输出的 geometry.solids）驱动，不注入默认真体。"""

    def test_fallback_solids_always_empty(self):
        """标题关键词不再驱动任何默认 3D 注入（函数题与立体几何题一律为空）。"""
        from app.domains.classroom.stage_router import _fallback_solids_for_title

        assert _fallback_solids_for_title("函数的单调性") == []
        assert _fallback_solids_for_title("导数与极值") == []
        assert _fallback_solids_for_title("数列求和") == []
        assert _fallback_solids_for_title("概率分布") == []
        assert _fallback_solids_for_title("四棱锥") == []
        assert _fallback_solids_for_title("多面体家族") == []
        assert _fallback_solids_for_title("旋转体") == []

    def test_ensure_solids_skips_function_topic(self):
        from app.domains.classroom.stage_router import _ensure_solids

        fig = {"axes": True, "grid": True, "curves": [{"kind": "polyline", "points": [[0, 0, 0], [1, 1, 0]]}]}
        result = _ensure_solids(fig, "函数的单调性")
        assert "solids" not in result, "函数题不应被注入 3D solids"

    def test_ensure_solids_injects_3d_topic(self):
        """V4：即使立体几何主题，_ensure_solids 也只透传，不注入默认 solids。"""
        from app.domains.classroom.stage_router import _ensure_solids

        fig = {"axes": True, "grid": True}
        result = _ensure_solids(fig, "四棱锥")
        assert "solids" not in result, "V4 禁止按标题注入默认 3D 实体（需由题意数据驱动）"
        # 无参形式调用也必须保持透传语义
        assert "solids" not in _ensure_solids({"axes": True}, "")

    def test_normalize_plot2d_valid(self):
        from app.domains.classroom.stage_router import _normalize_plot2d

        raw = {
            "kind": "plot2d",
            "expr": "x^3 - 3*x",
            "x0": -3,
            "x1": 3,
            "marks": [{"x": -1, "label": "极大"}, {"x": 1, "label": "极小"}],
            "regions": [
                {"x0": -3, "x1": -1, "color": "#ef4444", "label": "增"},
                {"x0": -1, "x1": 1, "color": "#3b82f6", "label": "减"},
            ],
            "caption": "f(x)=x^3-3x",
        }
        out = _normalize_plot2d(raw)
        assert out is not None
        assert out["kind"] == "plot2d"
        assert out["expr"] == "x^3 - 3*x"
        assert len(out["marks"]) == 2
        assert len(out["regions"]) == 2

    def test_normalize_plot2d_invalid_expr(self):
        from app.domains.classroom.stage_router import _normalize_plot2d

        assert _normalize_plot2d({"kind": "plot2d", "expr": "y + z"}) is None
        assert _normalize_plot2d({"kind": "plot2d", "expr": ""}) is None

    def test_is_3d_topic_classification(self):
        assert vs.is_3d_topic("函数的单调性") is False
        assert vs.is_3d_topic("导数与极值") is False
        assert vs.is_3d_topic("数列求和") is False
        assert vs.is_3d_topic("四棱锥") is True
        assert vs.is_3d_topic("多面体家族") is True


# ==================== 验收题标准答案真值交叉验证 ====================


class TestAcceptanceTruthCrossCheck:
    """用 sympy 独立计算验收题真值，与 fixtures 中的 standard_answer 交叉验证。
    这证明标准答案本身是正确的（验收基准可信）。"""

    def test_a_truth_matches(self):
        """验收题 A：f(x)=x^3-3x 的真值与 standard_answer 一致。"""
        computed = compute_a_truth()
        expected = ACCEPTANCE_A["standard_answer"]
        assert computed["fprime_factorized"] == expected["fprime_factorized"]
        assert computed["critical_points"] == expected["critical_points"]
        assert abs(computed["f_at_minus1"] - expected["f_at_minus1"]) < 1e-9
        assert abs(computed["f_at_1"] - expected["f_at_1"]) < 1e-9
        # f''(-1) < 0 → 极大值
        assert computed["fpp_at_minus1"] < 0
        # f''(1) > 0 → 极小值
        assert computed["fpp_at_1"] > 0

    def test_b_truth_matches(self):
        """验收题 B：f(x)=x^3-ax^2+x+1 在 x=-1 极值 → a=-2，极大值。"""
        computed = compute_b_truth()
        expected = ACCEPTANCE_B["standard_answer"]
        assert abs(computed["a_solved"] - expected["a_solved"]) < 1e-9
        assert abs(computed["fpp_at_minus1"] - expected["f_pp_at_minus1"]) < 1e-9
        assert computed["is_maximum"] is True
        # 关键：导数为零只是必要条件，需二阶导验证
        assert expected["necessary_not_sufficient"] is True

    def test_c_distance_truth(self):
        """验收题 C：E 到平面 SBD 的距离 = 1/√3 = √3/3。"""
        computed = compute_c_distance()
        expected = ACCEPTANCE_C["standard_answer"]["proof_2_distance"]["E_to_plane"]
        assert abs(computed["distance_numeric"] - expected["value"]) < 1e-9
        # 1/√3 = √3/3 ≈ 0.5774
        assert abs(computed["distance_numeric"] - 1 / math.sqrt(3)) < 1e-9
        assert abs(computed["distance_numeric"] - math.sqrt(3) / 3) < 1e-9

    def test_c_normal_vector_correct(self):
        """验收题 C：平面 SBD 法向量计算正确。"""
        computed = compute_c_distance()
        # 法向量 (4,4,4) 方向与 (1,1,1) 一致
        n = computed["normal_vector"]
        assert n[0] == n[1] == n[2] > 0


# ==================== 验收题 C 变式题标准答案验证 ====================


class TestAcceptanceCVariants:
    """验收题 C 的 3 道变式题标准答案数值校验。
    变式题与原题同一知识域但不等价：改变边长、命名、设问。"""

    def test_variants_count(self):
        from tests.fixtures.acceptance_problems import ACCEPTANCE_C_VARIANTS

        assert len(ACCEPTANCE_C_VARIANTS) == 3

    def test_variant_v1_distance(self):
        """变式 v1：PA=AB=4，A 到平面 PBD 距离 = 4/√3 = 4√3/3。"""
        from tests.fixtures.acceptance_problems import ACCEPTANCE_C_VARIANTS

        v1 = ACCEPTANCE_C_VARIANTS[0]
        d = v1["standard_answer"]["proof_2_distance"]["A_to_plane"]["value"]
        assert abs(d - 4 / math.sqrt(3)) < 1e-9

    def test_variant_v2_distance(self):
        """变式 v2：AB⊥平面 SAD，B 到平面 SAD 距离 = |AB| = 2。"""
        from tests.fixtures.acceptance_problems import ACCEPTANCE_C_VARIANTS

        v2 = ACCEPTANCE_C_VARIANTS[1]
        d = v2["standard_answer"]["proof_2_distance"]["B_to_plane"]["value"]
        assert abs(d - 2.0) < 1e-9

    def test_variant_v3_distance(self):
        """变式 v3：菱形 60°，A 到平面 VBD 距离 = 2√3/√7 = 2√21/7。"""
        from tests.fixtures.acceptance_problems import ACCEPTANCE_C_VARIANTS

        v3 = ACCEPTANCE_C_VARIANTS[2]
        d = v3["standard_answer"]["proof_2_distance"]["A_to_plane"]["value"]
        assert abs(d - 2 * math.sqrt(3) / math.sqrt(7)) < 1e-9

    def test_variants_not_equivalent_to_original(self):
        """变式题与原题不等价：边长/命名/设问不同。"""
        from tests.fixtures.acceptance_problems import ACCEPTANCE_C_VARIANTS

        original_topic = ACCEPTANCE_C["topic"]
        for v in ACCEPTANCE_C_VARIANTS:
            assert v["topic"] != original_topic
            assert v["id"] != "C"
