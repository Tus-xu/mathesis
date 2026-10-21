"""RV10 · Prompt v4 契约：route_arena / research_blueprint 写进提示词 + 裁决词封禁。

为什么这是单元测试而不是文档：提示词是**行为契约**——
它教模型什么、禁什么，直接决定提案的通过率与诚实性。
服务端校验器与提示词必须同源演进（RV08 L-7 的教训：
"服务端有校验器、提示词没有契约"会让模型只能猜）。
"""

from __future__ import annotations

import pytest

from research_vnext.core.llm.prompts import registry as R


@pytest.fixture(autouse=True)
def _reset():
    R.reset_releases()
    yield
    R.reset_releases()


def _published_system() -> str:
    return R.require_published(R.ASSISTANT_PROPOSE_ID).system


def test_v4_teaches_route_arena_contract() -> None:
    system = _published_system()
    assert "route_arena" in system
    # 每条路线的必备字段都要出现在提示词里（与服务端校验器同源）
    for key in ("method", "hypotheses", "variables", "identifiability", "metrics", "falsification", "resources", "evidenceRefs", "tradeOffs", "unknowns"):
        assert key in system, key


def test_v4_teaches_research_blueprint_contract() -> None:
    system = _published_system()
    assert "research_blueprint" in system
    for key in ("routeId", "experimentPlan", "evidenceRequirements", "verificationGates", "budget", "deadline", "stopConditions"):
        assert key in system, key


def test_v4_bans_verdict_words() -> None:
    """总分/胜者/排名必须在提示词里被显式封禁——与 ROUTE_ARENA_VERDICT_FORBIDDEN 同源。"""
    system = _published_system()
    for word in ("winner", "totalScore", "ranking"):
        assert word in system, word


def test_v4_states_no_total_score_no_winner() -> None:
    system = _published_system()
    assert "没有总分" in system
    assert "选择由研究者做出" in system


def test_v4_declares_untrusted_data_block() -> None:
    prompt = R.require_published(R.ASSISTANT_PROPOSE_ID)
    assert R.UNTRUSTED_OPEN in prompt.template
    assert R.UNTRUSTED_CLOSE in prompt.template
    assert "{{context_manifest}}" in prompt.template


def test_v4_release_note_records_why() -> None:
    """v3 为什么下线必须可查（发布台账的 note）。"""
    entry = R.RELEASE_LEDGER[R.ASSISTANT_PROPOSE_ID][3]
    assert entry.state == R.RELEASE_REVOKED
    assert "v4" in entry.note
