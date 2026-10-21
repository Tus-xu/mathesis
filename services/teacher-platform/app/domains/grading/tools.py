# coding: utf-8
"""grading 域管家工具（§3.8）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domains.grading.models import GradingAssignment
from app.domains.tools import ToolSpec



async def _grading_overview(ctx, params: dict) -> dict:
    total = (await ctx.session.execute(select(func.count()).select_from(GradingAssignment))).scalar()
    graded = (await ctx.session.execute(
        select(func.coalesce(func.sum(GradingAssignment.graded), 0)),
    )).scalar()
    return {'assignments': int(total or 0), 'graded': int(graded or 0)}


TOOLS: list[tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            name='grading.overview', label='批改概览', kind='read', confirm_required=False,
            description='查询批改任务提交/批改进度（真实聚合，无数据返回空态）',
            params_schema={'type': 'object', 'properties': {}},
        ),
        _grading_overview,
    ),
]
