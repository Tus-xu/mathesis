# coding: utf-8
"""课堂三层状态机（G7：server-authoritative，02-ARCHITECTURE §12.2）。

前端（教师 Web/学生 H5）只是 projection；一切状态转移发生在服务端：
  Session:       created → open → ended → archived
  Activity:      draft → ready → pushed → collecting → locked → revealed → completed
  AiGeneration:  not_requested → generating → verifying → awaiting_teacher
                          → approved → pushed   （或任意非终态 → rejected）

非法转移 = 业务错误码（InvalidTransitionError，路由层映射 422xx，禁止 500）；
所有转移写 classroom_events（durable，UNIQUE(session_id, seq)）。
转移矩阵以常量导出（单测全枚举合法/非法，B5 复核同一矩阵）。
"""
from __future__ import annotations

from typing import Literal

StateLayer = Literal['session', 'activity', 'ai_generation']

SESSION_STATES = ('created', 'open', 'ended', 'archived')
ACTIVITY_STATES = ('draft', 'ready', 'pushed', 'collecting', 'locked', 'revealed', 'completed')
AI_GENERATION_STATES = (
    'not_requested', 'generating', 'verifying', 'awaiting_teacher',
    'approved', 'pushed', 'rejected',
)

# 合法转移矩阵（唯一事实源；单测全枚举对拍）
SESSION_TRANSITIONS: dict[str, set[str]] = {
    'created': {'open'},
    'open': {'ended'},
    'ended': {'archived'},
    'archived': set(),
}
ACTIVITY_TRANSITIONS: dict[str, set[str]] = {
    'draft': {'ready'},
    'ready': {'pushed'},
    'pushed': {'collecting'},
    'collecting': {'locked'},
    'locked': {'revealed'},
    'revealed': {'completed'},
    'completed': set(),
}
# rejected 可从任意非终态进入（§12.2「或任意态→rejected」）；终态无出边
AI_TERMINAL = {'pushed', 'rejected'}
AI_GENERATION_TRANSITIONS: dict[str, set[str]] = {
    'not_requested': {'generating'},
    'generating': {'verifying', 'rejected'},
    'verifying': {'awaiting_teacher', 'rejected'},
    'awaiting_teacher': {'approved', 'rejected'},
    'approved': {'pushed'},
    'pushed': set(),
    'rejected': set(),
}

_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    'session': SESSION_TRANSITIONS,
    'activity': ACTIVITY_TRANSITIONS,
    'ai_generation': AI_GENERATION_TRANSITIONS,
}
_STATES: dict[str, tuple[str, ...]] = {
    'session': SESSION_STATES,
    'activity': ACTIVITY_STATES,
    'ai_generation': AI_GENERATION_STATES,
}

# 业务错误码段（02-ARCHITECTURE §10/§12：非法转移=业务错误码，非 500）
ERR_INVALID_TRANSITION = 42201
ERR_UNKNOWN_STATE = 42202
ERR_UNKNOWN_LAYER = 42203


class InvalidTransitionError(Exception):
    """非法状态转移（服务端权威拒绝）。"""

    def __init__(self, layer: str, frm: str, to: str, code: int = ERR_INVALID_TRANSITION):
        self.layer, self.frm, self.to, self.code = layer, frm, to, code
        super().__init__(f'classroom {layer}: {frm!r} → {to!r} 非法转移（code={code}）')


def can_transition(layer: StateLayer, frm: str, to: str) -> bool:
    matrix = _TRANSITIONS.get(layer)
    if matrix is None:
        return False
    return to in matrix.get(frm, set())


def transition(layer: StateLayer, frm: str, to: str) -> str:
    """校验并返回目标态；非法转移抛 InvalidTransitionError（业务错误码）。"""
    matrix = _TRANSITIONS.get(layer)
    if matrix is None:
        raise InvalidTransitionError(layer, frm, to, code=ERR_UNKNOWN_LAYER)
    if frm not in matrix or to not in _STATES[layer]:
        raise InvalidTransitionError(layer, frm, to, code=ERR_UNKNOWN_STATE)
    if to not in matrix[frm]:
        raise InvalidTransitionError(layer, frm, to)
    return to


def legal_pairs(layer: StateLayer) -> list[tuple[str, str]]:
    """导出全量合法转移（供单测全枚举与 B5 对账）。"""
    return [(frm, to) for frm, tos in _TRANSITIONS[layer].items() for to in sorted(tos)]


def event_type(layer: StateLayer, frm: str, to: str) -> str:
    """状态转移对应的 durable 事件名（classroom_events.event_type）。"""
    prefix = {'session': 'session', 'activity': 'activity', 'ai_generation': 'ai'}[layer]
    return f'{prefix}.{frm}_to_{to}'


def is_terminal(layer: StateLayer, state: str) -> bool:
    if layer == 'ai_generation':
        return state in AI_TERMINAL
    matrix = _TRANSITIONS[layer]
    return not matrix.get(state, set())
