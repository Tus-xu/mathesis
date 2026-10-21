# coding: utf-8
"""局部重生成：主题一致性护栏（防止数列页被改写成椭圆页）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import ApiError
from app.ppt_v2.service import regen


def _spec():
    return SimpleNamespace(
        metadata=SimpleNamespace(title="4.2.2 等差数列的前 n 项和公式"),
        teaching_strategy=SimpleNamespace(
            intro="从高斯配对求和引入等差数列前 n 项和",
            concept_progression=["等差数列", "倒序相加"],
            derivation_chain=["S_n = n(a_1 + a_n)/2"],
        ),
        outline=[SimpleNamespace(id="o1", goal="推导等差数列前 n 项和公式", slide_ids=["slide-05"])],
    )


def _slide():
    return SimpleNamespace(
        id="slide-05",
        type="derivation",
        title="推导与论证",
        teaching_goal="用倒序相加法推导前 n 项和公式",
        content="设等差数列 {a_n} 的首项为 a_1，公差为 d。\nS_n = a_1 + a_2 + ... + a_n",
        speaker_note="已知首项与末项用前者，已知首项与公差用后者。",
        source_refs=["教材 §4.2.2"],
    )


def test_topics_of_does_not_confuse_ellipse_with_circle():
    topics = regen._topics_of("椭圆的长轴与短轴")
    assert "ellipse" in topics
    assert "sequence" not in topics


def test_sequence_page_accepts_sequence_content():
    regen._reject_topic_drift(_spec(), _slide(), "由倒序相加可得 S_n = n(a_1 + a_n)/2")


def test_sequence_page_rejects_ellipse_content():
    with pytest.raises(ApiError) as exc:
        regen._reject_topic_drift(
            _spec(),
            _slide(),
            "设椭圆焦点 F1(-c,0)、F2(c,0)，由定义 |PF1|+|PF2|=2a，得 x²/a²+y²/b²=1",
        )
    assert exc.value.code.value == 42200
    assert "偏离本页主题" in exc.value.message


def test_generic_page_without_topic_is_not_blocked():
    spec = SimpleNamespace(metadata=SimpleNamespace(title="数学"), teaching_strategy=None, outline=[])
    slide = SimpleNamespace(id="s1", title="课堂小结", content="", speaker_note="")
    regen._reject_topic_drift(spec, slide, "椭圆的定义与标准方程")


def test_prompt_carries_deck_topic_and_real_strategy_fields():
    messages = regen._prompt(_spec(), _slide(), "补全推导步骤", {"evidence_refs": [], "source_refs": []})
    user = messages[1]["content"]
    assert "课件标题：4.2.2 等差数列的前 n 项和公式" in user
    assert "推导等差数列前 n 项和公式" in user
    assert "倒序相加" in user
    assert "approach" not in user
    assert "不得切换到无关章节" in user
