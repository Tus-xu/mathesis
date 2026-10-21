# coding: utf-8
"""figures 域管家工具（§3.8）。"""
from __future__ import annotations

from typing import Any

from app.domains.figures.models import Recipe
from app.domains.figures.seeds import preset_rows
from app.domains.tools import ToolSpec

_PRESET_IDS = {r['id'] for r in preset_rows()}



async def _figure_insert(ctx, params: dict) -> dict:
    preset_id = str(params['preset_id'])
    if preset_id not in _PRESET_IDS:
        return {'ok': False, 'error': f'未知图形 preset：{preset_id}'}
    return {'action': 'insert', 'element': {
        'type': 'geometry', 'preset_id': preset_id, 'params': params.get('params', {}),
        'teacher_confirmed': False,
    }}


async def _recipe_save(ctx, params: dict) -> dict:
    preset_id = str(params['preset_id'])
    if preset_id not in _PRESET_IDS:
        return {'ok': False, 'error': f'未知图形 preset：{preset_id}'}
    row = Recipe(name=str(params['name']), preset_id=preset_id, params=params.get('params', {}),
                 school_shared=bool(params.get('school_shared', False)), note=str(params.get('note', '')),
                 owner_id=ctx.user_id)
    ctx.session.add(row)
    await ctx.session.flush()
    return {'recipe_id': str(row.id)}


TOOLS: list[tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            name='figure.insert', label='插入图形', kind='frontend', confirm_required=False,
            description='把参数化图形以 teacher_confirmed=false 落入当前课件页（前端画布执行）',
            params_schema={'type': 'object', 'required': ['preset_id'],
                           'properties': {'preset_id': {'type': 'string'}, 'params': {'type': 'object'},
                                          'slide_id': {'type': 'string'}}},
        ),
        _figure_insert,
    ),
    (
        ToolSpec(
            name='recipe.save', label='保存构造配方', kind='write', confirm_required=True,
            description='保存 preset+params 为可复用配方（school_shared=true 全校可见只读引用）',
            params_schema={'type': 'object', 'required': ['name', 'preset_id'],
                           'properties': {'name': {'type': 'string'}, 'preset_id': {'type': 'string'},
                                          'params': {'type': 'object'}, 'school_shared': {'type': 'boolean'},
                                          'note': {'type': 'string'}}},
        ),
        _recipe_save,
    ),
]
