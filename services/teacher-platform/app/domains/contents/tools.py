# coding: utf-8
"""contents 域管家工具（deck/plan；§3.8）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domains.contents import service as cs
from app.domains.contents.models import Deck
from app.domains.tools import ToolSpec



async def _deck_create(ctx, params: dict) -> dict:
    deck = Deck(title=str(params['title']),
                template_id=UUID(str(params['template_id'])) if params.get('template_id') else None,
                source='topic', created_by=ctx.user_id, slides=[])
    ctx.session.add(deck)
    await ctx.session.flush()
    return {'deck_id': str(deck.id)}


async def _navigate(ctx, params: dict) -> dict:
    return {'action': 'navigate', 'route': str(params['route']), 'query': params.get('query', {})}


async def _deck_export(ctx, params: dict) -> dict:
    job = await cs.register_export(ctx.session, UUID(str(params['deck_id'])), fmt=str(params['format']))
    return {'export_job_id': str(job.id), 'status': job.status}


async def _plan_confirm(ctx, params: dict) -> dict:
    plan = await cs.confirm_plan(ctx.session, UUID(str(params['plan_id'])))
    return {'plan_id': str(plan.id), 'confirmed': True}


TOOLS: list[tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            name='deck.create', label='新建课件', kind='write', confirm_required=True,
            description='创建空白课件草稿（教师确认后执行）',
            params_schema={'type': 'object', 'required': ['title'],
                           'properties': {'title': {'type': 'string'}, 'template_id': {'type': 'string'}}},
        ),
        _deck_create,
    ),
    (
        ToolSpec(
            name='navigate.slides', label='页面跳转', kind='frontend', confirm_required=False,
            description='跳转到课件工坊/备课中心等页面并预填参数（前端 Action Registry 执行）',
            params_schema={'type': 'object', 'required': ['route'],
                           'properties': {'route': {'type': 'string'}, 'query': {'type': 'object'}}},
        ),
        _navigate,
    ),
    (
        ToolSpec(
            name='deck.export', label='导出课件', kind='write', confirm_required=True,
            description='登记 PPTX/PDF 导出任务（渲染由 worker 执行，任务中心可查进度）',
            params_schema={'type': 'object', 'required': ['deck_id', 'format'],
                           'properties': {'deck_id': {'type': 'string'}, 'format': {'enum': ['pptx', 'pdf']}}},
        ),
        _deck_export,
    ),
    (
        ToolSpec(
            name='plan.confirm', label='教案定稿', kind='write', confirm_required=True,
            description='确认教案定稿（定稿后对 AI 只读，改稿须草稿副本）',
            params_schema={'type': 'object', 'required': ['plan_id'], 'properties': {'plan_id': {'type': 'string'}}},
        ),
        _plan_confirm,
    ),
]
