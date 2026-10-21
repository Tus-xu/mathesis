"""RV07 · `origin` 服务端真值裁决：只降级、依据必须可核、纯函数。

背景：RV08 `surface/MessageComponent.vue` 会把 `origin` 渲染成"来源/用户/模型推断"徽标，
而该字段目前**由模型自述**（`proposal_handlers._norm_originated` 只挡非法值）。
本模块把 `origin=SOURCE` 变成必须由服务端证据核验的结论。
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence.origin_grounding import (  # noqa: E402
    REASON_INVALID,
    REASON_SOURCE_UNVERIFIED_REF,
    REASON_SOURCE_WITHOUT_EVIDENCE,
    REASON_USER_NOT_IN_MESSAGE,
    REASON_USER_QUOTE_MISSING,
    ground_content_origins,
    ground_element_origin,
    grounded_card_ids,
    is_grounded_card,
)

CARD_A = "c0000000-0000-0000-0000-00000000000a"
CARD_B = "c0000000-0000-0000-0000-00000000000b"


def _card(card_id: str, *, status: str = "ACTIVE", stale_state: str = "FRESH") -> dict:
    return {"id": card_id, "status": status, "stale_state": stale_state}


# --------------------------------------------------------------------------
# 依据筛选
# --------------------------------------------------------------------------


def test_active_fresh_card_is_grounded() -> None:
    assert is_grounded_card(_card(CARD_A)) is True


def test_stale_card_is_not_grounded() -> None:
    assert is_grounded_card(_card(CARD_A, stale_state="STALE")) is False


def test_non_active_card_is_not_grounded() -> None:
    assert is_grounded_card(_card(CARD_A, status="PROPOSED")) is False


def test_grounded_ids_filters_and_dedupes() -> None:
    ids = grounded_card_ids(
        [_card(CARD_A), _card(CARD_B, stale_state="STALE"), _card(CARD_A), "not-a-dict"]
    )
    assert ids == {CARD_A}


# --------------------------------------------------------------------------
# 单元素裁决
# --------------------------------------------------------------------------


def test_source_without_evidence_refs_is_downgraded() -> None:
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE"}, grounded_ids={CARD_A}
    )
    assert origin == "INFERENCE"
    assert reason == REASON_SOURCE_WITHOUT_EVIDENCE


def test_source_with_unverified_ref_is_downgraded() -> None:
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE", "evidence_refs": [CARD_B]},
        grounded_ids={CARD_A},
    )
    assert origin == "INFERENCE"
    assert reason == REASON_SOURCE_UNVERIFIED_REF


def test_source_with_one_bad_ref_among_good_ones_is_downgraded() -> None:
    origin, _ = ground_element_origin(
        {"text": "x", "origin": "SOURCE", "evidence_refs": [CARD_A, CARD_B]},
        grounded_ids={CARD_A},
    )
    assert origin == "INFERENCE"


def test_source_with_all_refs_grounded_survives() -> None:
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE", "evidence_refs": [CARD_A]},
        grounded_ids={CARD_A},
    )
    assert origin == "SOURCE"
    assert reason == ""


def test_evidence_refs_camel_case_alias_is_honoured() -> None:
    origin, _ = ground_element_origin(
        {"text": "x", "origin": "SOURCE", "evidenceRefs": [CARD_A]}, grounded_ids={CARD_A}
    )
    assert origin == "SOURCE"


def test_unknown_origin_falls_back_to_inference() -> None:
    origin, reason = ground_element_origin({"text": "x", "origin": "GUESS"}, grounded_ids=set())
    assert origin == "INFERENCE"
    assert reason == REASON_INVALID


def test_inference_is_left_untouched() -> None:
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "INFERENCE"}, grounded_ids=set()
    )
    assert origin == "INFERENCE"
    assert reason == ""


def test_user_origin_is_not_judged_without_user_text() -> None:
    origin, reason = ground_element_origin({"text": "x", "origin": "USER"}, grounded_ids=set())
    assert origin == "USER"
    assert reason == ""


def test_user_origin_downgraded_when_quote_not_in_user_message() -> None:
    """提供了 `quote` 但不在用户原文中 ⇒ 降级（`USER_NOT_IN_MESSAGE`）。"""
    origin, reason = ground_element_origin(
        {"text": "改写后的陈述", "origin": "USER", "quote": "我没说过这句"},
        grounded_ids=set(),
        user_text="用户真正说过的内容",
    )
    assert origin == "INFERENCE"
    assert reason == REASON_USER_NOT_IN_MESSAGE


def test_user_origin_kept_when_quote_is_verbatim_in_user_message() -> None:
    """`quote` 逐字命中用户原文 ⇒ 保留 `USER`（`text` 允许是改写）。"""
    origin, reason = ground_element_origin(
        {"text": "最小化代价函数（改写）", "origin": "USER", "quote": "最小化代价函数"},
        grounded_ids=set(),
        user_text="请帮我最小化代价函数并给出推导",
    )
    assert origin == "USER"
    assert reason == ""


def test_user_origin_downgraded_when_quote_missing() -> None:
    """声明 `USER` 却不给 `quote` ⇒ fail-closed 降级（`USER_QUOTE_MISSING`）。"""
    origin, reason = ground_element_origin(
        {"text": "用户明确说的", "origin": "USER"},
        grounded_ids=set(),
        user_text="请分析",
    )
    assert origin == "INFERENCE"
    assert reason == REASON_USER_QUOTE_MISSING


def test_user_origin_downgraded_when_quote_is_blank() -> None:
    """`quote` 为纯空白 ⇒ 视为缺失，同样降级。"""
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "USER", "quote": "   \n\t "},
        grounded_ids=set(),
        user_text="请分析",
    )
    assert origin == "INFERENCE"
    assert reason == REASON_USER_QUOTE_MISSING


def test_user_origin_is_not_judged_without_user_text_even_without_quote() -> None:
    """拿不到用户原文 ⇒ 不裁决（保持既有行为），即使没有 `quote` 也保留 `USER`。"""
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "USER"}, grounded_ids=set(), user_text=None
    )
    assert origin == "USER"
    assert reason == ""


def test_grounding_never_upgrades_inference_to_source() -> None:
    """**只降级不升级**：即使有可用依据，也不得把推断提升为来源。"""
    origin, _ = ground_element_origin(
        {"text": "x", "origin": "INFERENCE", "evidence_refs": [CARD_A]},
        grounded_ids={CARD_A},
    )
    assert origin == "INFERENCE"


# --------------------------------------------------------------------------
# content 级改写
# --------------------------------------------------------------------------


def test_ground_content_origins_downgrades_and_reports() -> None:
    content = {
        "assumptions": [
            {"text": "a", "origin": "SOURCE", "evidence_refs": [CARD_A]},
            {"text": "b", "origin": "SOURCE"},
            {"text": "c", "origin": "INFERENCE"},
        ],
        "objective": "纯标量字段不受影响",
    }
    out, report = ground_content_origins(content, grounded_ids={CARD_A})

    assert [item["origin"] for item in out["assumptions"]] == ["SOURCE", "INFERENCE", "INFERENCE"]
    assert out["objective"] == content["objective"]
    assert report["downgraded"] == 1
    assert report["reasons"] == {REASON_SOURCE_WITHOUT_EVIDENCE: 1}


def test_ground_content_origins_does_not_mutate_input() -> None:
    original = {"nodes": [{"text": "b", "origin": "SOURCE"}]}
    snapshot = {"nodes": [{"text": "b", "origin": "SOURCE"}]}
    ground_content_origins(original, grounded_ids=set())
    assert original == snapshot


def test_ground_content_origins_leaves_lists_without_origin_alone() -> None:
    content = {"constraints": ["纯字符串", 42]}
    out, report = ground_content_origins(content, grounded_ids=set())
    assert out["constraints"] == ["纯字符串", 42]
    assert report["downgraded"] == 0
    assert report["reasons"] == {}


def test_ground_content_origins_skips_server_authored_keys() -> None:
    """`server_authored_keys` 命中的字段**整段**跳过裁决（服务端赋 origin，不由模型决定）。"""
    content = {
        "problemStatement": [{"text": "服务端构造", "origin": "USER"}],  # 无 quote
        "knowns": [{"text": "模型声明", "origin": "USER"}],  # 无 quote ⇒ 应降级
    }
    out, report = ground_content_origins(
        content,
        grounded_ids=set(),
        user_text="请分析",
        server_authored_keys=frozenset({"problemStatement"}),
    )
    assert out["problemStatement"][0]["origin"] == "USER", "服务端构造字段免裁决"
    assert out["knowns"][0]["origin"] == "INFERENCE", "其余字段照常裁决"
    assert report["downgraded"] == 1


def test_ground_content_origins_reports_user_downgrade_count() -> None:
    """报告里的 `downgraded` 计数必须覆盖 `USER` 锚定失败的降级。"""
    content = {
        "knowns": [
            {"text": "a", "origin": "USER", "quote": "请分析"},  # 命中 ⇒ 不降级
            {"text": "b", "origin": "USER"},  # 缺 quote ⇒ 降级
            {"text": "c", "origin": "USER", "quote": "编的"},  # 不在原文 ⇒ 降级
        ]
    }
    out, report = ground_content_origins(content, grounded_ids=set(), user_text="请分析")
    assert [item["origin"] for item in out["knowns"]] == ["USER", "INFERENCE", "INFERENCE"]
    assert report["downgraded"] == 2
    assert report["reasons"] == {REASON_USER_QUOTE_MISSING: 1, REASON_USER_NOT_IN_MESSAGE: 1}


# --------------------------------------------------------------------------
# 提案级 ref 回落（真实链路：元素只有 {text, origin}，ref 在 Proposal 上）
# --------------------------------------------------------------------------


def test_proposal_refs_rescue_element_without_own_refs() -> None:
    """元素级没有 ref 时回落提案级 —— 否则真实链路上 SOURCE 会被全量误杀。"""
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE"},
        grounded_ids={CARD_A},
        proposal_refs=[CARD_A],
    )
    assert origin == "SOURCE"
    assert reason == ""


def test_proposal_refs_still_must_be_grounded() -> None:
    """提案级 ref 指向未授权/非 ACTIVE 的卡时，照样降级。"""
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE"},
        grounded_ids={CARD_A},
        proposal_refs=[CARD_B],
    )
    assert origin == "INFERENCE"
    assert reason == REASON_SOURCE_UNVERIFIED_REF


def test_empty_proposal_refs_downgrade_without_evidence_reason() -> None:
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE"},
        grounded_ids={CARD_A},
        proposal_refs=[],
    )
    assert origin == "INFERENCE"
    assert reason == REASON_SOURCE_WITHOUT_EVIDENCE


def test_element_level_refs_win_over_proposal_refs() -> None:
    """元素自带 ref 时以元素为准：不能被提案级的"好 ref"救活。"""
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE", "evidence_refs": [CARD_B]},
        grounded_ids={CARD_A},
        proposal_refs=[CARD_A],
    )
    assert origin == "INFERENCE"
    assert reason == REASON_SOURCE_UNVERIFIED_REF


def test_proposal_refs_accept_bare_string() -> None:
    origin, reason = ground_element_origin(
        {"text": "x", "origin": "SOURCE"},
        grounded_ids={CARD_A},
        proposal_refs=CARD_A,
    )
    assert (origin, reason) == ("SOURCE", "")


def test_ground_content_origins_threads_proposal_refs() -> None:
    content = {"knowns": [{"text": "a", "origin": "SOURCE"}]}
    out, report = ground_content_origins(
        content, grounded_ids={CARD_A}, proposal_refs=[CARD_A]
    )
    assert out["knowns"][0]["origin"] == "SOURCE"
    assert report["downgraded"] == 0


def test_ground_content_origins_without_proposal_refs_still_fails_closed() -> None:
    """不传 proposal_refs 时保持原语义：无依据即降级（fail closed）。"""
    content = {"knowns": [{"text": "a", "origin": "SOURCE"}]}
    out, report = ground_content_origins(content, grounded_ids={CARD_A})
    assert out["knowns"][0]["origin"] == "INFERENCE"
    assert report["reasons"] == {REASON_SOURCE_WITHOUT_EVIDENCE: 1}
