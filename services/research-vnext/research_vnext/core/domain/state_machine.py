"""ResearchSession 状态机（RV02）。

任务书给定的 9 个状态**取代**总手册 §7.3 的 PAUSED/REVIEWING/ARCHIVED 表述
（记录于 STATE-MACHINE.md §2 的 supersession）。

两条不可让步的不变量：
1. **不允许跳过 confirmation** —— 进入 `READY_FOR_REVIEW` / `COMPLETED`
   必须已有至少一次 confirmation 证据。
2. **PARTIAL 绝不能当 COMPLETED** —— PARTIAL 只能回到 ACTIVE 或转 FAILED/CANCELLED。
"""

from __future__ import annotations

from enum import Enum

from .errors import (
    ConfirmationRequired,
    InvalidTransition,
    PartialIsNotCompleted,
    PartialReasonRequired,
)


class SessionState(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {SessionState.COMPLETED, SessionState.FAILED, SessionState.CANCELLED}
SUCCESS = {SessionState.COMPLETED}

# 需要"已确认"证据才能进入的目标状态
CONFIRMATION_GATED = {SessionState.READY_FOR_REVIEW, SessionState.COMPLETED}

ALLOWED: dict[SessionState, set[SessionState]] = {
    SessionState.DRAFT: {SessionState.ACTIVE, SessionState.CANCELLED},
    SessionState.ACTIVE: {
        SessionState.WAITING_CONFIRMATION,
        SessionState.RUNNING,
        SessionState.READY_FOR_REVIEW,
        SessionState.CANCELLED,
    },
    SessionState.WAITING_CONFIRMATION: {SessionState.ACTIVE, SessionState.CANCELLED},
    SessionState.RUNNING: {
        SessionState.ACTIVE,
        SessionState.PARTIAL,
        SessionState.FAILED,
        SessionState.CANCELLED,
    },
    SessionState.PARTIAL: {SessionState.ACTIVE, SessionState.FAILED, SessionState.CANCELLED},
    SessionState.READY_FOR_REVIEW: {SessionState.COMPLETED, SessionState.ACTIVE, SessionState.CANCELLED},
    SessionState.COMPLETED: set(),
    SessionState.FAILED: set(),
    SessionState.CANCELLED: set(),
}


def is_terminal(s: SessionState) -> bool:
    return s in TERMINAL


def is_success(s: SessionState) -> bool:
    return s in SUCCESS


def requires_confirmation_evidence(src: SessionState, dst: SessionState) -> bool:
    return dst in CONFIRMATION_GATED


def can_transition(
    src: SessionState,
    dst: SessionState,
    *,
    confirmation_count: int = 0,
    partial_reason: object | None = None,
) -> None:
    """校验一次状态转换；不合法即抛错（fail closed）。"""
    if src == dst:
        raise InvalidTransition(f"不允许自转换: {src.value}")

    # 不变量 2：PARTIAL 永不等于完成
    if src is SessionState.PARTIAL and dst is SessionState.COMPLETED:
        raise PartialIsNotCompleted(
            "PARTIAL 不是完成态：必须先回到 ACTIVE 补齐失败 stage，不得直接 COMPLETED"
        )

    allowed = ALLOWED.get(src, set())
    if dst not in allowed:
        raise InvalidTransition(f"不允许的状态转换: {src.value} -> {dst.value}")

    # 不变量 1：confirmation 闸门
    if dst in CONFIRMATION_GATED and confirmation_count < 1:
        raise ConfirmationRequired(
            f"{src.value} -> {dst.value} 需要至少一次 confirmation 证据，当前 {confirmation_count}"
        )

    # 总手册 §13.4：PARTIAL 必须说明哪些 stage 成功/失败
    if dst is SessionState.PARTIAL and partial_reason is None:
        raise PartialReasonRequired("进入 PARTIAL 必须提供 partial_reason（成功/失败 stage 说明）")
