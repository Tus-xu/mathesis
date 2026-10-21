"""RV02 · 状态机单元测试（先红后绿）。

覆盖任务书要求：
- 9 个状态：DRAFT/ACTIVE/WAITING_CONFIRMATION/RUNNING/PARTIAL/READY_FOR_REVIEW/COMPLETED/FAILED/CANCELLED
- **不允许跳过 confirmation**
- **不允许把 PARTIAL 当 COMPLETED**
"""

from __future__ import annotations

import pytest

from research_vnext.core.domain.errors import InvalidTransition
from research_vnext.core.domain.state_machine import (
    SessionState,
    can_transition,
    is_terminal,
    requires_confirmation_evidence,
)


ALL = list(SessionState)


def test_all_nine_states_exist() -> None:
    assert {s.value for s in ALL} == {
        "DRAFT",
        "ACTIVE",
        "WAITING_CONFIRMATION",
        "RUNNING",
        "PARTIAL",
        "READY_FOR_REVIEW",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }


def test_terminal_states() -> None:
    assert is_terminal(SessionState.COMPLETED)
    assert is_terminal(SessionState.FAILED)
    assert is_terminal(SessionState.CANCELLED)
    assert not is_terminal(SessionState.PARTIAL)
    assert not is_terminal(SessionState.RUNNING)


# --------------------------------------------------------------------------
# 不允许跳过 confirmation
# --------------------------------------------------------------------------


def test_ready_for_review_requires_confirmation_evidence() -> None:
    assert requires_confirmation_evidence(SessionState.ACTIVE, SessionState.READY_FOR_REVIEW)
    assert requires_confirmation_evidence(SessionState.READY_FOR_REVIEW, SessionState.COMPLETED)


def test_completed_requires_confirmation_evidence() -> None:
    """无 confirmation 证据时，COMPLETED 必须被拒绝。"""
    with pytest.raises(InvalidTransition) as ei:
        can_transition(
            SessionState.READY_FOR_REVIEW,
            SessionState.COMPLETED,
            confirmation_count=0,
        )
    assert "confirmation" in str(ei.value).lower()


def test_completed_allowed_with_confirmation_evidence() -> None:
    can_transition(
        SessionState.READY_FOR_REVIEW,
        SessionState.COMPLETED,
        confirmation_count=1,
    )


def test_active_cannot_jump_to_completed() -> None:
    with pytest.raises(InvalidTransition):
        can_transition(SessionState.ACTIVE, SessionState.COMPLETED, confirmation_count=5)


# --------------------------------------------------------------------------
# PARTIAL 绝不能当 COMPLETED
# --------------------------------------------------------------------------


def test_partial_cannot_go_to_completed() -> None:
    with pytest.raises(InvalidTransition) as ei:
        can_transition(SessionState.PARTIAL, SessionState.COMPLETED, confirmation_count=99)
    assert "PARTIAL" in str(ei.value)


def test_partial_is_not_terminal_and_not_success() -> None:
    from research_vnext.core.domain.state_machine import is_success

    assert not is_success(SessionState.PARTIAL)
    assert is_success(SessionState.COMPLETED)


def test_partial_requires_reason() -> None:
    """PARTIAL 必须说明哪些 stage 成功/失败（总手册 §13.4）。"""
    with pytest.raises(InvalidTransition):
        can_transition(SessionState.RUNNING, SessionState.PARTIAL, partial_reason=None)
    can_transition(SessionState.RUNNING, SessionState.PARTIAL, partial_reason="stage:search failed")


# --------------------------------------------------------------------------
# 合法 / 非法转换矩阵
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,dst",
    [
        ("DRAFT", "ACTIVE"),
        ("DRAFT", "CANCELLED"),
        ("ACTIVE", "WAITING_CONFIRMATION"),
        ("ACTIVE", "RUNNING"),
        ("ACTIVE", "READY_FOR_REVIEW"),
        ("ACTIVE", "CANCELLED"),
        ("WAITING_CONFIRMATION", "ACTIVE"),
        ("WAITING_CONFIRMATION", "CANCELLED"),
        ("RUNNING", "ACTIVE"),
        ("RUNNING", "PARTIAL"),
        ("RUNNING", "FAILED"),
        ("RUNNING", "CANCELLED"),
        ("PARTIAL", "ACTIVE"),
        ("PARTIAL", "FAILED"),
        ("PARTIAL", "CANCELLED"),
        ("READY_FOR_REVIEW", "ACTIVE"),
        ("READY_FOR_REVIEW", "CANCELLED"),
    ],
)
def test_allowed_transitions(src: str, dst: str) -> None:
    kwargs = {}
    if dst == "READY_FOR_REVIEW":
        kwargs["confirmation_count"] = 1
    if dst == "PARTIAL":
        kwargs["partial_reason"] = "x"
    can_transition(SessionState(src), SessionState(dst), **kwargs)


@pytest.mark.parametrize(
    "src,dst",
    [
        ("COMPLETED", "ACTIVE"),
        ("COMPLETED", "RUNNING"),
        ("FAILED", "ACTIVE"),
        ("CANCELLED", "ACTIVE"),
        ("DRAFT", "COMPLETED"),
        ("DRAFT", "RUNNING"),
        ("PARTIAL", "COMPLETED"),
        ("PARTIAL", "READY_FOR_REVIEW"),
        ("RUNNING", "COMPLETED"),
        ("RUNNING", "READY_FOR_REVIEW"),
        ("ACTIVE", "COMPLETED"),
    ],
)
def test_forbidden_transitions(src: str, dst: str) -> None:
    with pytest.raises(InvalidTransition):
        can_transition(SessionState(src), SessionState(dst), confirmation_count=99, partial_reason="x")


def test_self_transition_rejected() -> None:
    with pytest.raises(InvalidTransition):
        can_transition(SessionState.ACTIVE, SessionState.ACTIVE)
