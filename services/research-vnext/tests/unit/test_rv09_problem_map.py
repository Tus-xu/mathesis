"""RV09 · ProblemMap 领域规则（纯函数，无 DB）。

锁定四件必须成立的事：
1. 八段结构**必须都在**（哪怕为空也要登记缺口，不许省略）；
2. **信息不足不拒绝** —— 缺口进 `insufficiencies`，服务端绝不替用户补内容；
3. **自相矛盾必须拒绝** —— 机械可判的那一类（同谓词一正一负）；
4. 逐项 accept / reject / edit 可用，但**不允许改 `origin`**。
"""

from __future__ import annotations

import pytest

from research_vnext.core.domain import problem_map as PM


def _payload(**overrides) -> dict:
    base = {
        "researchQuestion": {
            "text": "如何为校园快递点设计最小等待时间的取件排队方案？",
            "origin": "USER",
            "quote": "如何为校园快递点设计最小等待时间的取件排队方案？",
        },
        "scope": [{"text": "单校内 3 个快递点的排队规则", "origin": "USER"}],
        "nonGoals": [{"text": "不涉及跨校区运输调度", "origin": "INFERENCE"}],
        "knowns": [{"text": "每小时到达人数可用泊松过程近似", "origin": "INFERENCE"}],
        "unknowns": [{"text": "高峰期真实到达率", "origin": "INFERENCE"}],
        "constraints": [{"text": "只能用课间 20 分钟实测", "origin": "USER"}],
        "successCriteria": [{"text": "平均等待时间下降 ≥20%", "origin": "INFERENCE"}],
        "falsificationCriteria": [{"text": "若排队论模型预测误差 >30% 则方案不成立", "origin": "INFERENCE"}],
        "assumptions": [{"text": "到达过程平稳", "origin": "INFERENCE"}],
        "risks": [{"text": "实测样本不足导致参数失真", "origin": "INFERENCE", "severity": "HIGH", "mitigation": "多时段采样"}],
        "requiredEvidence": [{"text": "排队论 M/M/c 推导与实测到达率", "origin": "INFERENCE", "kind": "DERIVATION"}],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# 1 · 八段结构完整性
# --------------------------------------------------------------------------


def test_all_required_sections_present_after_normalize() -> None:
    """任务书要求 1：八段（+ 问答两侧的判据）必须**全部**出现在规范化结果里。"""
    out = PM.normalize(_payload())
    for section in (
        "researchQuestion",
        "scope",
        "nonGoals",
        "knowns",
        "unknowns",
        "constraints",
        "successCriteria",
        "falsificationCriteria",
        "assumptions",
        "risks",
        "requiredEvidence",
    ):
        assert section in out.content, f"缺段: {section}"


def test_unknown_keys_are_rejected_not_passed_through() -> None:
    """封闭词表：多一个键即拒（不做宽容透传）。"""
    bad = _payload()
    bad["milestones"] = ["M1"]
    with pytest.raises(PM.ProblemMapRejected) as ei:
        PM.normalize(bad)
    assert ei.value.code == "PROBLEM_MAP_UNKNOWN_KEY"


def test_unknowns_are_pinned_to_unknown_status() -> None:
    """`unknowns` 恒为 UNKNOWN —— 模型写 KNOWN 也不改判。"""
    out = PM.normalize(
        _payload(unknowns=[{"text": "到达率", "origin": "INFERENCE", "status": "KNOWN"}])
    )
    assert out.content["unknowns"][0]["status"] == "UNKNOWN"


def test_missing_origin_downgrades_to_inference_never_to_user() -> None:
    """缺失/非法 origin 降级方向必须是 INFERENCE（不能升级成 USER/SOURCE）。"""
    out = PM.normalize(_payload(scope=[{"text": "范围未标来源"}]))
    assert out.content["scope"][0]["origin"] == "INFERENCE"


# --------------------------------------------------------------------------
# 2 · 信息不足 ≠ 拒绝
# --------------------------------------------------------------------------


def test_empty_optional_section_is_recorded_as_insufficiency_not_rejected() -> None:
    """留空是**合法**的，但必须登记缺口 —— 否则用户不知道缺了什么。"""
    out = PM.normalize(_payload(constraints=[], risks=[], requiredEvidence=[]))
    gaps = {g["section"] for g in out.insufficiencies}
    assert {"constraints", "risks", "requiredEvidence"} <= gaps
    assert out.content["constraints"] == []


def test_scope_without_nongoals_is_flagged_unbounded() -> None:
    out = PM.normalize(_payload(nonGoals=[]))
    reasons = {g["reason"] for g in out.insufficiencies}
    assert PM.INSUFFICIENCY_SCOPE_UNBOUNDED in reasons


def test_core_sections_may_not_be_empty() -> None:
    """成功/证伪判据是语义核心：为空 ⇒ 拒绝（而不是登记缺口继续）。"""
    out = PM.normalize(_payload(falsificationCriteria=[]))
    with pytest.raises(PM.ProblemMapRejected) as ei:
        PM.validate(out.content)
    assert ei.value.code == "PROBLEM_MAP_MISSING_CORE_SECTION"


def test_clarification_request_is_deterministic_and_lists_gaps() -> None:
    out = PM.normalize(_payload(assumptions=[], risks=[]))
    text = PM.clarification_request(out.content)
    assert "风险" in text and "前提假设" in text
    # 同输入必得同输出（确定性文案，不经模型）
    assert text == PM.clarification_request(out.content)


# --------------------------------------------------------------------------
# 3 · 空 / 矛盾问题
# --------------------------------------------------------------------------


def test_empty_question_is_rejected() -> None:
    out = PM.normalize(_payload(researchQuestion={"text": "帮我看看", "origin": "USER"}))
    with pytest.raises(PM.ProblemMapRejected) as ei:
        PM.validate(out.content)
    assert ei.value.code == "PROBLEM_MAP_QUESTION_INSUFFICIENT"


def test_declarative_research_question_is_accepted() -> None:
    """E2E 抓到的真实反向事故：只认疑问词会把**陈述式**研究问题判成"没提问"。

    `为单校区 3 个快递点设计取件排队方案` 一个疑问词都没有，但它显然在问一件事。
    这类假阳性比假阴性更有害：系统反过来要求用户补充一个已经说清的问题。
    """
    out = PM.normalize(
        _payload(researchQuestion={"text": "为单校区 3 个快递点设计取件排队方案", "origin": "USER"})
    )
    PM.validate(out.content)  # 不抛异常即为通过


def test_long_declarative_question_without_markers_is_accepted() -> None:
    """长度兜底：一个标记词都没有的长陈述仍应被当作研究问题。"""
    text = "半圆内接矩形的长宽比取何值时其面积取得极大"
    assert len(text) >= PM.DECLARATIVE_MIN_CHARS
    assert not any(m in text for m in PM._INTERROGATIVES), "该用例必须不含任何标记词"
    assert PM.is_question_like(text) is True
    out = PM.normalize(_payload(researchQuestion={"text": text, "origin": "USER"}))
    PM.validate(out.content)


def test_short_chitchat_is_still_rejected() -> None:
    """放宽必须**有边界**：寒暄与半个词仍然要被拦。"""
    assert PM.is_question_like("帮我看看这个数列。") is False
    assert PM.is_question_like("你好") is False


def test_mechanical_contradiction_is_detected() -> None:
    """同谓词一正一负 ⇒ 机械可判的矛盾。"""
    found = PM.detect_contradiction("这个数列收敛。这个数列不收敛。")
    assert found and found[0].kind == "SELF_NEGATION"


def test_contradiction_blocks_normalization_result() -> None:
    out = PM.normalize(
        _payload(researchQuestion={"text": "这个数列收敛吗？这个数列收敛。这个数列不收敛。", "origin": "USER"})
    )
    assert out.contradictions, "自相矛盾必须被登记"
    assert not out.complete


def test_user_message_contradiction_is_detected_even_if_model_rewrites_it() -> None:
    """E2E 发现的缺陷：只扫模型的改写，矛盾会被"洗掉"。

    真实场景：用户说"这个数列收敛。这个数列不收敛。"，
    模型把它改写成一句自洽的 `researchQuestion`。
    只扫模型输出的检测器会**完全失明** —— 因此必须同时扫用户原话。
    这里直接验证检测器的两个输入：模型改写干净时，用户原话仍能被判出矛盾。
    """
    model_rewrite = "证明该数列收敛"
    user_message = "这个数列收敛。这个数列不收敛。请证明它收敛。"
    assert PM.detect_contradiction(model_rewrite) == []
    assert PM.detect_contradiction(user_message), "用户原话里的矛盾必须能被机械检出"


def test_scope_nongoal_overlap_is_flagged() -> None:
    out = PM.normalize(
        _payload(
            scope=[{"text": "研究高峰期排队", "origin": "USER"}],
            nonGoals=[{"text": "研究高峰期排队", "origin": "USER"}],
        )
    )
    kinds = {c["kind"] for c in out.contradictions}
    assert "SCOPE_NONGOAL_OVERLAP" in kinds


# --------------------------------------------------------------------------
# 4 · 逐项 accept / reject / edit
# --------------------------------------------------------------------------


def test_reject_removes_only_that_element() -> None:
    out = PM.normalize(_payload())
    before = len(out.content["constraints"])
    out.content = PM.apply_corrections(out.content, accept=None, reject=["constraints#0"], edits=None)
    assert len(out.content["constraints"]) == before - 1


def test_edit_changes_text_only_and_records_summary() -> None:
    out = PM.normalize(_payload())
    out.content = PM.apply_corrections(
        out.content,
        accept=["constraints#0"],
        reject=None,
        edits=[{"ref": "constraints#0", "value": {"text": "只能在午休实测 10 分钟"}}],
    )
    assert out.content["constraints"][0]["text"] == "只能在午休实测 10 分钟"
    assert out.content["editSummary"] == {"accepted": 1, "rejected": 0, "edited": 1}


def test_edit_may_not_change_origin() -> None:
    """来源由服务端裁决 —— 编辑者不能把 INFERENCE 改成 USER。"""
    out = PM.normalize(_payload())
    with pytest.raises(PM.ProblemMapRejected) as ei:
        PM.apply_corrections(
            out.content,
            accept=None,
            reject=None,
            edits=[{"ref": "constraints#0", "value": {"origin": "USER"}}],
        )
    assert ei.value.code == "PROBLEM_MAP_EDIT_FORBIDDEN_FIELD"


def test_bad_ref_is_rejected() -> None:
    out = PM.normalize(_payload())
    with pytest.raises(PM.ProblemMapRejected):
        PM.apply_corrections(out.content, accept=None, reject=["constraints#99"], edits=None)
