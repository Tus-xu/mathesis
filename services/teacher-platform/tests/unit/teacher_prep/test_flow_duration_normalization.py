"""课时时长确定性归一化单测（数据一致性红线：Σ环节时长 == 课时预算）。

纯函数 app.teacher_prep.specs.durations：largest_remainder + normalize_flow_durations。
"""

from __future__ import annotations

from app.teacher_prep.specs.durations import largest_remainder, normalize_flow_durations


def test_largest_remainder_sums_to_total():
    for weights, total in [
        ([10, 20, 10, 10, 5, 5], 45),
        ([1.0], 45),
        ([7, 7, 7], 20),
        ([0, 0, 0], 12),
        ([100, 1], 10),
    ]:
        out = largest_remainder([float(w) for w in weights], total)
        assert len(out) == len(weights)
        assert sum(out) == total
        assert all(v >= 1 for v in out)


def test_largest_remainder_deterministic_and_proportional():
    a = largest_remainder([10.0, 20.0, 10.0, 10.0, 5.0, 5.0], 45)
    b = largest_remainder([10.0, 20.0, 10.0, 10.0, 5.0, 5.0], 45)
    assert a == b
    # 比例保持：权重 20 的环节拿到最大值
    assert a[1] == max(a)


def test_normalize_flow_durations_to_budget():
    body = {
        "lesson_flow": [
            {"stage": "introduction", "duration_minutes": 10},
            {"stage": "new_content", "duration_minutes": 20},
            {"stage": "example", "duration_minutes": 10},
            {"stage": "exercise", "duration_minutes": 10},
            {"stage": "summary", "duration_minutes": 5},
            {"stage": "homework", "duration_minutes": 5},
        ]
    }
    out = normalize_flow_durations(body, 45)
    flow = out["lesson_flow"]
    assert sum(s["duration_minutes"] for s in flow) == 45
    assert all(s["duration_minutes"] >= 1 for s in flow)
    # 原 body 不被就地修改（防污染已确认蓝图）
    assert sum(s["duration_minutes"] for s in body["lesson_flow"]) == 60


def test_normalize_flow_preserves_other_fields():
    body = {
        "lesson_flow": [{"stage": "a", "duration_minutes": 30, "title": "T", "items": [{"text": "x"}]}],
        "title": "keep",
    }
    out = normalize_flow_durations(body, 45)
    assert out["title"] == "keep"
    assert out["lesson_flow"][0]["title"] == "T"
    assert out["lesson_flow"][0]["items"] == [{"text": "x"}]
    assert out["lesson_flow"][0]["duration_minutes"] == 45


def test_normalize_flow_skips_when_more_stages_than_budget():
    body = {"lesson_flow": [{"stage": f"s{i}", "duration_minutes": 10} for i in range(50)]}
    out = normalize_flow_durations(body, 45)
    assert out is body  # 环节数 > 预算：原样返回（诚实，不硬塞）


def test_normalize_flow_handles_null_durations():
    body = {"lesson_flow": [{"stage": "a", "duration_minutes": None}, {"stage": "b", "duration_minutes": None}]}
    out = normalize_flow_durations(body, 45)
    assert sum(s["duration_minutes"] for s in out["lesson_flow"]) == 45


def test_normalize_flow_empty_and_missing():
    assert normalize_flow_durations({}, 45) == {}
    assert normalize_flow_durations({"lesson_flow": []}, 45)["lesson_flow"] == []


def test_normalize_flow_idempotent():
    body = {"lesson_flow": [{"stage": "a", "duration_minutes": 30}, {"stage": "b", "duration_minutes": 30}]}
    once = normalize_flow_durations(body, 45)
    twice = normalize_flow_durations(once, 45)
    assert once["lesson_flow"] == twice["lesson_flow"]
