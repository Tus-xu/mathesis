"""RV09 · ResearchMap 领域规则（纯函数，无 DB）。

核心主张：**多视角 ≠ 多步骤**。本文件逐条钉住四道硬门，
并证明"步骤清单伪装成地图"会被拒绝。
"""

from __future__ import annotations

import pytest

from research_vnext.core.domain import research_map as RM


def _p(pid: str, lens: str, kind: str, *, dep: str | None = None, stop: str = "误差 <5%") -> dict:
    return {
        "id": pid,
        "lens": lens,
        "kind": kind,
        "question": f"{lens} 要回答什么",
        "dependencies": ([{"on": dep, "text": "需要它先给出参数"}] if dep else []),
        "deliverables": [{"text": f"{lens} 结论", "kind": "MODEL", "origin": "INFERENCE"}],
        "stopConditions": [{"text": stop, "origin": "INFERENCE"}],
        "uncertainties": [{"text": f"{lens} 最可能错在边界条件", "origin": "INFERENCE"}],
    }


def _payload() -> dict:
    return {
        "rationale": "两条互不相同的取径",
        "perspectives": [
            _p("p1", "解析推导", "THEORETICAL"),
            _p("p2", "数值实验", "COMPUTATIONAL", dep="p1", stop="样本 > 1000 或误差不再下降"),
        ],
    }


# --------------------------------------------------------------------------
# 1 · 数量
# --------------------------------------------------------------------------


def test_two_perspectives_ok() -> None:
    out = RM.normalize(_payload())
    assert len(out.content["perspectives"]) == 2


def test_single_perspective_is_not_a_map() -> None:
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize({"perspectives": [_p("p1", "解析推导", "THEORETICAL")]})
    assert ei.value.code == "RESEARCH_MAP_PERSPECTIVE_COUNT"


def test_five_perspectives_is_a_checklist_not_a_map() -> None:
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(
            {
                "perspectives": [
                    _p("p1", "解析推导", "THEORETICAL"),
                    _p("p2", "数值实验", "COMPUTATIONAL", dep="p1"),
                    _p("p3", "文献对照", "LITERATURE", dep="p2"),
                    _p("p4", "几何直观", "GEOMETRIC", dep="p3"),
                    _p("p5", "构造反例", "CONSTRUCTION", dep="p4"),
                ]
            }
        )
    assert ei.value.code == "RESEARCH_MAP_PERSPECTIVE_COUNT"


# --------------------------------------------------------------------------
# 2 · 可区分
# --------------------------------------------------------------------------


def test_same_lens_twice_is_rejected() -> None:
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(
            {
                "perspectives": [
                    _p("p1", "解析推导", "THEORETICAL"),
                    _p("p2", "解析推导", "COMPUTATIONAL", dep="p1"),
                ]
            }
        )
    assert ei.value.code == "RESEARCH_MAP_PERSPECTIVES_NOT_DISTINGUISHABLE"


def test_all_same_kind_is_rejected() -> None:
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(
            {
                "perspectives": [
                    _p("p1", "解析推导", "THEORETICAL"),
                    _p("p2", "符号计算", "THEORETICAL", dep="p1"),
                ]
            }
        )
    assert ei.value.code == "RESEARCH_MAP_SINGLE_LENS"


def test_identical_stop_conditions_across_three_perspectives_is_checklist_shaped() -> None:
    """三份"做完为止"式停止条件 = 复制粘贴的清单。"""
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(
            {
                "perspectives": [
                    _p("p1", "解析推导", "THEORETICAL", stop="做完为止"),
                    _p("p2", "数值实验", "COMPUTATIONAL", dep="p1", stop="做完为止"),
                    _p("p3", "文献对照", "LITERATURE", dep="p2", stop="做完为止"),
                ]
            }
        )
    assert ei.value.code == "RESEARCH_MAP_CHECKLIST_SHAPED"


# --------------------------------------------------------------------------
# 3 · 交付物与停止条件
# --------------------------------------------------------------------------


def test_perspective_without_deliverable_is_rejected() -> None:
    p = _p("p1", "解析推导", "THEORETICAL")
    p["deliverables"] = []
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize({"perspectives": [p, _p("p2", "数值实验", "COMPUTATIONAL", dep="p1")]})
    assert ei.value.code == "RESEARCH_MAP_PERSPECTIVE_NO_DELIVERABLE"


def test_perspective_without_stop_condition_is_rejected() -> None:
    p = _p("p1", "解析推导", "THEORETICAL")
    p["stopConditions"] = []
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize({"perspectives": [p, _p("p2", "数值实验", "COMPUTATIONAL", dep="p1")]})
    assert ei.value.code == "RESEARCH_MAP_PERSPECTIVE_NO_STOP_CONDITION"


# --------------------------------------------------------------------------
# 4 · 依赖
# --------------------------------------------------------------------------


def test_no_dependency_between_perspectives_is_rejected() -> None:
    """互不相干的多个视角仍然是清单。"""
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(
            {"perspectives": [_p("p1", "解析推导", "THEORETICAL"), _p("p2", "数值实验", "COMPUTATIONAL")]}
        )
    assert ei.value.code == "RESEARCH_MAP_NO_DEPENDENCY"


def test_dangling_dependency_is_rejected() -> None:
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(
            {
                "perspectives": [
                    _p("p1", "解析推导", "THEORETICAL", dep="p9"),
                    _p("p2", "数值实验", "COMPUTATIONAL", dep="p1"),
                ]
            }
        )
    assert ei.value.code == "RESEARCH_MAP_DANGLING_DEPENDENCY"


def test_dependency_cycle_is_rejected() -> None:
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(
            {
                "perspectives": [
                    _p("p1", "解析推导", "THEORETICAL", dep="p2"),
                    _p("p2", "数值实验", "COMPUTATIONAL", dep="p1"),
                ]
            }
        )
    assert ei.value.code == "RESEARCH_MAP_DEPENDENCY_CYCLE"


def test_external_dependency_on_problem_map_section_is_allowed() -> None:
    """依赖可以指向问题地图的段（视角的取舍标准来自地图）。"""
    p1 = _p("p1", "解析推导", "THEORETICAL")
    p1["dependencies"] = [{"on": "problemMap:successCriteria", "text": "对齐成功判据"}]
    p2 = _p("p2", "数值实验", "COMPUTATIONAL", dep="p1")
    out = RM.normalize({"perspectives": [p1, p2]})
    assert out.content["edges"]


# --------------------------------------------------------------------------
# 5 · 拒绝清单伪装
# --------------------------------------------------------------------------


def test_steps_key_is_rejected_as_checklist() -> None:
    payload = _payload()
    payload["steps"] = ["第一步", "第二步", "第三步"]
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize(payload)
    assert ei.value.code == "RESEARCH_MAP_IS_CHECKLIST"


def test_missing_perspectives_is_rejected() -> None:
    with pytest.raises(RM.ResearchMapRejected) as ei:
        RM.normalize({"nodes": [{"id": "n1"}], "edges": []})
    assert ei.value.code == "RESEARCH_MAP_NO_PERSPECTIVES"


# --------------------------------------------------------------------------
# 6 · 图由服务端推导
# --------------------------------------------------------------------------


def test_graph_is_derived_and_never_dangling() -> None:
    out = RM.normalize(_payload())
    ids = {n["id"] for n in out.content["nodes"]}
    assert ids >= {"p1", "p2"}
    for edge in out.content["edges"]:
        assert edge["from"] in ids and edge["to"] in ids, "服务端推导的图不允许出现悬空边"


def test_refresh_graph_follows_grounded_origins() -> None:
    """origin 被服务端降级之后，图节点必须跟着更新（否则降级会被推导绕过）。"""
    out = RM.normalize(_payload())
    out.content["perspectives"][0]["deliverables"][0]["origin"] = "INFERENCE"
    refreshed = RM.refresh_graph(out.content)
    node = next(n for n in refreshed["nodes"] if n["id"] == "p1.d1")
    assert node["origin"] == "INFERENCE"


def test_dependency_order_is_topological() -> None:
    out = RM.normalize(_payload())
    rm = RM
    perspectives = [
        rm.Perspective(
            id=p["id"], lens=p["lens"], kind=p["kind"], question=p["question"],
            dependencies=p["dependencies"], deliverables=[], stop_conditions=[], uncertainties=[],
        )
        for p in out.content["perspectives"]
    ]
    assert rm.dependency_order(perspectives) == ["p1", "p2"]
