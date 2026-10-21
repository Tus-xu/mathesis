r"""核心服务单元测试（FSRS / 防幻觉评分 / 学情聚合 / 管家查询匹配）

运行：.venv\Scripts\python.exe -m pytest tests/test_core_services.py -v
"""
from __future__ import annotations

import pytest

from app.services import fsrs
from app.services.growth import classify_subtype, composite_score
from app.services.hallucination_score import _cosine, c_deduction
from app.services.platform_context import match_platform_item, match_practice_intent

# ==================== FSRS ====================

class TestFsrsLevel:
    """fsrs_level 稳定度分级（迭代18 展示修复：S 分级，新题不再显示"非常稳"）"""

    @pytest.mark.parametrize(
        ("stability", "want"),
        [
            (None, "new"),
            (0.0, "new"),
            (0.4, "new"),   # 新收录错题 S=0.4 → 刚收录/待复习
            (0.99, "new"),
            (1.0, "lv1"),    # 复习 1 次
            (3.0, "lv2"),    # 复习 2 次
            (7.0, "lv3"),    # 复习 3 次
            (15.0, "lv4"),   # 复习 4 次（毕业档）
            (30.0, "lv4"),
        ],
    )
    def test_level_thresholds(self, stability, want):
        assert fsrs.fsrs_level(stability) == want

    def test_new_record_not_stable(self):
        """核心回归：新收录错题（S≈0.4）绝不能被标为 lv4 最稳。"""
        s = fsrs.estimate_stability(review_count=0, wrong_count=1)
        assert fsrs.fsrs_level(s) == "new"

    def test_review_progression(self):
        """复习推进档位联动：0-4 次复习 → new/lv1/lv2/lv3/lv4。"""
        levels = [fsrs.fsrs_level(fsrs.estimate_stability(rc, 1)) for rc in range(5)]
        assert levels == ["new", "lv1", "lv2", "lv3", "lv4"]

    def test_wrong_penalty_drops_level(self):
        """答错惩罚：4 次复习但错 3 次 → S 打折降档。"""
        s = fsrs.estimate_stability(4, wrong_count=3)  # 15 * 0.7 = 10.5
        assert fsrs.fsrs_level(s) == "lv3"


class TestFsrsMath:
    def test_retrievability_bounds(self):
        assert fsrs.retrievability(0, 0.4) == 1.0
        assert fsrs.retrievability(1, 0.0) == 0.0
        assert 0 < fsrs.retrievability(1, 1.0) < 1

    def test_days_until_inverts(self):
        """days_until 与 retrievability 互逆（R 衰减到阈值所需天数）。"""
        for s in (0.4, 1.0, 3.0, 15.0):
            t = fsrs.days_until(s, 0.6)
            assert fsrs.retrievability(t, s) == pytest.approx(0.6, abs=1e-3)

    def test_level_to_filter(self):
        assert fsrs.level_to_filter("lv4") == "stable"
        assert fsrs.level_to_filter("lv2") == "decaying"
        assert fsrs.level_to_filter("new") == "critical"  # 新收录最需复习
        assert fsrs.level_to_filter("decay") == "critical"


# ==================== 防幻觉评分 ====================

class TestHallucinationScore:
    @pytest.mark.parametrize(
        ("sim", "want"),
        [(0.8, 0.0), (0.5, 0.0), (0.4, 3.0), (0.3, 3.0), (0.2, 6.0), (0.15, 6.0), (0.1, 10.0), (0.0, 10.0)],
    )
    def test_c_deduction_bands(self, sim, want):
        assert c_deduction(sim) == want

    def test_cosine_identical(self):
        assert _cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0, abs=1e-9)

    def test_cosine_orthogonal(self):
        assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0, abs=1e-9)

    def test_cosine_degenerate(self):
        assert _cosine([], []) == 0.0
        assert _cosine([0, 0], [1, 1]) == 0.0


# ==================== 学情聚合 ====================

class TestGrowth:
    def test_composite_score_bounds(self):
        assert 0 <= composite_score(0.0, 1.0, 0) <= 100
        # 100*(0.6*0.5 + 0.25*1.0 + 0.15*1.0) = 70
        assert composite_score(0.5, 0.0, 14) == 70

    def test_composite_formula(self):
        # 100*(0.6*0.4 + 0.25*(1-0.6) + 0.15*min(1,7/14))
        want = 100 * (0.6 * 0.4 + 0.25 * 0.4 + 0.15 * 0.5)
        assert composite_score(0.4, 0.6, 7) == round(want)

    def test_classify_subtype_fallback(self):
        st, zh, parent = classify_subtype("concept", "一些与关键词无关的文本")
        assert parent == "concept" and st and zh


# ==================== 平台意图匹配 ====================

class TestPlatformMatch:
    def test_open_error_book(self):
        item = match_platform_item("打开错题本")
        assert item and item["key"] == "error-book"

    def test_jump_requires_verb(self):
        # 只有功能名、无动作词 → 不触发跳转（防误伤咨询类语句）
        assert match_platform_item("错题本") is None

    def test_practice_intent_exam(self):
        intent = match_practice_intent("来一套全真模拟")
        assert intent and intent["key"] == "exam"

    def test_practice_intent_inline_quiz_excluded(self):
        # 对话内出题（"几道"）不走练题中心跳转
        assert match_practice_intent("给我出几道导数题") is None
