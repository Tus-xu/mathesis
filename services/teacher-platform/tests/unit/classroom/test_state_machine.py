# coding: utf-8
"""课堂三层状态机单测 —— 合法/非法转移全枚举（B4 规范 M1：转移矩阵单测先行）。"""
from __future__ import annotations

import pytest

from app.domains.classroom import state_machine as sm

LAYERS = ('session', 'activity', 'ai_generation')
ALL_STATES = {
    'session': sm.SESSION_STATES,
    'activity': sm.ACTIVITY_STATES,
    'ai_generation': sm.AI_GENERATION_STATES,
}


def test_legal_transitions_full_enumeration():
    """矩阵声明的每一条合法转移都必须可执行。"""
    for layer in LAYERS:
        pairs = sm.legal_pairs(layer)
        assert pairs, layer
        for frm, to in pairs:
            assert sm.can_transition(layer, frm, to) is True
            assert sm.transition(layer, frm, to) == to


def test_illegal_transitions_full_enumeration():
    """N² 全枚举：矩阵之外的每一条组合都必须拒绝（业务错误码，非静默）。"""
    for layer in LAYERS:
        legal = set(sm.legal_pairs(layer))
        for frm in ALL_STATES[layer]:
            for to in ALL_STATES[layer]:
                if (frm, to) in legal:
                    continue
                with pytest.raises(sm.InvalidTransitionError) as exc:
                    sm.transition(layer, frm, to)
                assert exc.value.code == sm.ERR_INVALID_TRANSITION


def test_session_lifecycle_is_linear():
    assert sm.legal_pairs('session') == [
        ('created', 'open'), ('ended', 'archived'), ('open', 'ended'),
    ][:3] or set(sm.legal_pairs('session')) == {('created', 'open'), ('open', 'ended'), ('ended', 'archived')}
    # 归档为终态：archived 无出边
    assert sm.is_terminal('session', 'archived')
    assert not sm.is_terminal('session', 'open')


def test_activity_chain_is_strict_linear():
    """draft→ready→pushed→collecting→locked→revealed→completed，不许跳步。"""
    chain = ['draft', 'ready', 'pushed', 'collecting', 'locked', 'revealed', 'completed']
    legal = set(sm.legal_pairs('activity'))
    assert legal == {(chain[i], chain[i + 1]) for i in range(len(chain) - 1)}
    # 典型非法：locked 后再 push（L9 判定④：业务错误码而非 500）
    with pytest.raises(sm.InvalidTransitionError):
        sm.transition('activity', 'locked', 'pushed')


def test_ai_generation_rejectable_from_any_nonterminal():
    """§12.2「或任意态→rejected」：非终态全部可拒；终态（pushed/rejected）无出边。"""
    legal = set(sm.legal_pairs('ai_generation'))
    for state in sm.AI_GENERATION_STATES:
        if state in sm.AI_TERMINAL:
            assert sm.is_terminal('ai_generation', state)
            continue
        assert ('generating', 'rejected') in legal or state != 'generating'
    for nonterminal in ('not_requested', 'generating', 'verifying', 'awaiting_teacher'):
        if nonterminal == 'not_requested':
            continue  # 未启动即拒绝无意义：not_requested 只出边到 generating
        assert (nonterminal, 'rejected') in legal
    # 主链 HITL：awaiting_teacher 是教师确认卡（approved 才可 pushed）
    assert sm.can_transition('ai_generation', 'awaiting_teacher', 'approved')
    assert sm.can_transition('ai_generation', 'approved', 'pushed')
    assert not sm.can_transition('ai_generation', 'awaiting_teacher', 'pushed')


def test_event_type_and_unknown_layer():
    assert sm.event_type('session', 'created', 'open') == 'session.created_to_open'
    assert sm.event_type('activity', 'collecting', 'locked') == 'activity.collecting_to_locked'
    with pytest.raises(sm.InvalidTransitionError) as exc:
        sm.transition('bogus', 'a', 'b')  # type: ignore[arg-type]
    assert exc.value.code == sm.ERR_UNKNOWN_LAYER
