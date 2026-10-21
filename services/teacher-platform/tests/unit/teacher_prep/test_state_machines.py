"""R14 状态机纯函数测试：合法/非法转换、确认角色闸门、幂等键规范。"""
from __future__ import annotations

import pytest

from app.teacher_prep import state_machines as sm
from app.teacher_prep.state_machines import (
    PrepConfirmForbidden,
    PrepStateTransitionInvalid,
)


def test_session_legal_transitions() -> None:
    # draft → active → cancelled → archived
    sm.assert_transition("session", "draft", "active")
    sm.assert_transition("session", "active", "cancelled")
    sm.assert_transition("session", "cancelled", "archived")
    # draft → archived 直通（允许一次性归档）
    sm.assert_transition("session", "draft", "archived")
    # active → archived
    sm.assert_transition("session", "active", "archived")


@pytest.mark.parametrize(
    "current,target",
    [
        ("active", "draft"),     # 已激活不可回退 draft
        ("archived", "active"),  # 终态不可复活
        ("archived", "cancelled"),
        ("cancelled", "active"),
        ("draft", "proposal"),   # 未知状态
    ],
)
def test_session_illegal_transitions(current: str, target: str) -> None:
    with pytest.raises(PrepStateTransitionInvalid):
        sm.assert_transition("session", current, target)


def test_blueprint_legal_transitions() -> None:
    sm.assert_transition("blueprint", "proposal", "confirmed")
    sm.assert_transition("blueprint", "proposal", "rejected")
    sm.assert_transition("blueprint", "confirmed", "proposal")  # 修订再提案
    sm.assert_transition("blueprint", "rejected", "proposal")   # 驳回后可重提


@pytest.mark.parametrize(
    "current,target",
    [
        ("confirmed", "rejected"),  # 已确认不可直接驳回
        ("rejected", "confirmed"),  # 驳回后必须重新提案才能确认
        ("archived", "proposal"),
    ],
)
def test_blueprint_illegal_transitions(current: str, target: str) -> None:
    with pytest.raises(PrepStateTransitionInvalid):
        sm.assert_transition("blueprint", current, target)


@pytest.mark.parametrize(
    "role",
    ["teacher", "owner", "assistant"],
)
def test_confirm_actor_allows_teaching_roles(role: str) -> None:
    sm.assert_confirm_actor(role)  # 不抛


@pytest.mark.parametrize("role", ["student", "system", "ai", ""])
def test_confirm_actor_rejects_non_teaching(role: str) -> None:
    with pytest.raises(PrepConfirmForbidden):
        sm.assert_confirm_actor(role)


def test_idempotency_key_validation() -> None:
    sm.validate_idempotency_key("a" * 8)
    sm.validate_idempotency_key("r14-key-0001")
    with pytest.raises(PrepStateTransitionInvalid):
        sm.validate_idempotency_key("short")  # 少于 8
    with pytest.raises(PrepStateTransitionInvalid):
        sm.validate_idempotency_key("has space key !!")  # 非法字符
