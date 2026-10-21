"""BKT 接口预留测试（任务8：决策降级，接口可用性验证）"""

import pytest

from app.services.bkt import (
    BKT_MIN_ANSWERS,
    BKTParams,
    bkt_mastery_after_block,
    bkt_update,
    should_use_bkt,
)


class TestDecision:
    def test_threshold(self):
        assert should_use_bkt(19) is False
        assert should_use_bkt(20) is True
        assert should_use_bkt(0) is False
        assert BKT_MIN_ANSWERS == 20


class TestBktUpdate:
    def test_correct_raises_mastery(self):
        m = 0.5
        for _ in range(10):
            m = bkt_update(m, True)
        assert m > 0.9

    def test_wrong_lowers_mastery(self):
        m = 0.9
        for _ in range(5):
            m = bkt_update(m, False)
        assert m < 0.5

    def test_clamped(self):
        assert 0 <= bkt_update(0.99, False) <= 1
        assert 0 <= bkt_update(0.01, True) <= 1

    def test_invalid_prior_raises(self):
        with pytest.raises(ValueError):
            bkt_update(1.5, True)

    def test_invalid_params_raise(self):
        with pytest.raises(ValueError):
            BKTParams(p_t=2.0)

    def test_block_update(self):
        m = bkt_mastery_after_block(0.5, correct_count=3, total=4)
        assert 0 < m < 1
        # 对错顺序无关性不成立（顺序更新），但空块应原样返回
        assert bkt_mastery_after_block(0.5, 0, 0) == 0.5
