# coding: utf-8
"""classroom 域管家工具（§3.8）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domains import classroom
from app.domains.tools import ToolSpec



async def _push_question(ctx, params: dict) -> dict:
    activity = await classroom.service.push_activity(
        ctx.session, UUID(str(params['session_id'])), kind='question',
        question_id=UUID(str(params['question_id'])) if params.get('question_id') else None,
        config={'stem_text': str(params['stem_text'])},
    )
    return {'activity_id': str(activity.id), 'status': activity.status}


async def _snapshot(ctx, params: dict) -> dict:
    return await classroom.service.snapshot(ctx.session, UUID(str(params['session_id'])))


TOOLS: list[tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            name='classroom.push_question', label='课堂推题', kind='write', confirm_required=True,
            description='向进行中的课堂推送一道题互动（教师确认后执行；状态机 collecting）',
            params_schema={'type': 'object', 'required': ['session_id', 'stem_text'],
                           'properties': {'session_id': {'type': 'string'}, 'stem_text': {'type': 'string'},
                                          'question_id': {'type': 'string'}}},
        ),
        _push_question,
    ),
    (
        ToolSpec(
            name='classroom.snapshot', label='课堂实况', kind='read', confirm_required=False,
            description='读取课堂权威快照（在线人数/活动状态/作答统计）',
            params_schema={'type': 'object', 'required': ['session_id'], 'properties': {'session_id': {'type': 'string'}}},
        ),
        _snapshot,
    ),
]
