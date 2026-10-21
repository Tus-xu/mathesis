# coding: utf-8
"""可见数学文本清洗 + 图形对象键回归。"""
from __future__ import annotations

from app.teacher_prep.specs import math_figures
from app.teacher_prep.specs.math_text import normalize_ascii_math_text


def test_ascii_subscripts_and_fraction_are_readable():
    assert normalize_ascii_math_text("S_n=(n[a₁+a₁+(n-1)d])/(2)") == "Sₙ=(n[a₁+a₁+(n-1)d])/2"
    assert normalize_ascii_math_text("a_n=a₁+(n-1)d") == "aₙ=a₁+(n-1)d"
    assert normalize_ascii_math_text("S_3=6") == "S₃=6"


def test_plain_sequence_notation_without_underscores():
    assert normalize_ascii_math_text("Sn=an+an-1+…+a1") == "Sₙ=aₙ+aₙ₋₁+…+a₁"
    assert normalize_ascii_math_text("故2Sn=n(a1+an)，得Sn=n(a1+an)/2") == "故2Sₙ=n(a₁+aₙ)，得Sₙ=n(a₁+aₙ)/2"


def test_plain_english_is_not_rewritten():
    assert normalize_ascii_math_text("普通英文 an apple") == "普通英文 an apple"


def test_negative_decimal_params_roundtrip_and_render():
    key = math_figures.object_key_for("sequence", {"a1": 1.0, "d": -3.5, "n": 4.0})
    parsed = math_figures.params_from_object_key(key)
    assert parsed == ("sequence", {"a1": 1.0, "d": -3.5, "n": 4.0})
    asset, data = math_figures.asset_for("sequence", {"a1": 1.0, "d": -3.5, "n": 4.0})
    assert data.startswith(b"\x89PNG")
    assert math_figures.asset_bytes(asset) is not None
