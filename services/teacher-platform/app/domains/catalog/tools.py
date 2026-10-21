# coding: utf-8
"""catalog 域管家工具（quiz/folder/today；§3.8 与 mock /butler/tools 同构）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.domains.catalog.models import Folder, FolderQuestionRef, KpNode, QuizQuestion
from app.domains.catalog.service import QuizQueryFilters, build_quiz_query
from app.domains.tools import ToolSpec



async def _quiz_search(ctx, params: dict) -> dict:
    f = QuizQueryFilters(
        kp=[s.strip() for s in str(params.get('kp', '')).split(',') if s.strip()],
        q=str(params.get('q', '')), difficulty=str(params.get('difficulty', '')),
        q_type=str(params.get('q_type', '')), source=str(params.get('source', '')),
        limit=int(params.get('limit', 20)),
    )
    stmt, count = build_quiz_query(f)
    rows = (await ctx.session.execute(stmt)).scalars().all()
    total = (await ctx.session.execute(count)).scalar()
    return {'total': int(total or 0), 'items': [
        {'id': str(r.id), 'kp_code': r.kp_code, 'q_type': r.q_type, 'difficulty': r.difficulty,
         'stem_latex': r.stem_latex, 'source': r.source} for r in rows
    ]}


async def _quiz_create(ctx, params: dict) -> dict:
    # 红线（S10 复核修正 2026-09-07）：AI/管家入库一律草稿态——teacher_confirmed=False、
    # source='AI配题'；教师确认在题库编辑完成后发生，不得由入库动作代确认。
    kp_code = str(params.get('kp_code') or '')
    if not kp_code and params.get('kp_name'):
        node = (await ctx.session.execute(
            select(KpNode).where(KpNode.name == str(params['kp_name'])),
        )).scalar_one_or_none()
        kp_code = node.code if node else ''
    if not kp_code:
        return {'ok': False, 'error': '知识点未解析（kp_code/kp_name 均为空）'}
    row = QuizQuestion(
        kp_code=kp_code,
        q_type=str(params.get('q_type', 'solve')), difficulty=str(params.get('difficulty', 'medium')),
        stem_latex=str(params.get('stem_latex') or ''), answer=str(params.get('answer') or ''),
        analysis=params.get('analysis'), source='AI配题', teacher_confirmed=False,
        created_by=ctx.user_id,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    return {'ok': True, 'question_id': str(row.id), 'teacher_confirmed': False,
            'note': '已入草稿（AI配题），请在题库中校对题干与答案后确认'}


async def _kp_suggest(ctx, params: dict) -> dict:
    """确定性关键词规则（M1 骨架）；M3 换 B2 kp-suggest 提示词（诚实标注）。"""
    stem = str(params.get('stem', ''))
    return {'suggestion': None, 'note': 'M1 骨架：语义建议待 M3 接入；题干关键词规则未命中' if not stem else None}


async def _folder_create(ctx, params: dict) -> dict:
    row = Folder(name=str(params['name']), desc=params.get('desc'), owner_id=ctx.user_id)
    ctx.session.add(row)
    await ctx.session.flush()
    return {'folder_id': str(row.id)}


async def _folder_add(ctx, params: dict) -> dict:
    folder_id = UUID(str(params['folder_id']))
    n = 0
    for qid in params.get('question_ids', []):
        qid_uuid = UUID(str(qid))
        exists = (await ctx.session.execute(
            select(func.count()).select_from(FolderQuestionRef).where(
                FolderQuestionRef.folder_id == folder_id, FolderQuestionRef.question_id == qid_uuid),
        )).scalar()
        if not exists:
            ctx.session.add(FolderQuestionRef(folder_id=folder_id, question_id=qid_uuid))
            n += 1
            q = await ctx.session.get(QuizQuestion, qid_uuid)
            if q is not None:
                q.usage_count += 1
    return {'added': n}


async def _today_summary(ctx, params: dict) -> dict:
    pending_quiz = (await ctx.session.execute(
        select(func.count()).select_from(QuizQuestion).where(QuizQuestion.teacher_confirmed.is_(False)),
    )).scalar()
    return {'pending_review': int(pending_quiz or 0)}


TOOLS: list[tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            name='quiz.search', label='题库检索', kind='read', confirm_required=False,
            description='按知识点/关键词/难度/题型/来源/年份检索题库，返回分页结果',
            params_schema={'type': 'object', 'properties': {
                'kp': {'type': 'string', 'description': '知识点 code，逗号分隔'},
                'q': {'type': 'string'}, 'difficulty': {'type': 'string'},
                'q_type': {'type': 'string'}, 'source': {'type': 'string'},
                'limit': {'type': 'integer', 'maximum': 100, 'minimum': 1},
            }},
        ),
        _quiz_search,
    ),
    (
        ToolSpec(
            name='quiz.create', label='题目入库', kind='write', confirm_required=True,
            description='识别/编辑后的题目结构化写入题库（教师确认后执行，source=自编）',
            params_schema={'type': 'object', 'required': ['kp_code', 'q_type', 'stem_latex', 'answer'],
                           'properties': {'kp_code': {'type': 'string'}, 'kp_name': {'type': 'string'},
                                          'q_type': {'enum': ['choice', 'fill', 'solve']},
                                          'difficulty': {'enum': ['easy', 'medium', 'hard']},
                                          'stem_latex': {'type': 'string'}, 'answer': {'type': 'string'},
                                          'analysis': {'type': 'string'}}},
        ),
        _quiz_create,
    ),
    (
        ToolSpec(
            name='quiz.kp_suggest', label='知识点归属建议', kind='read', confirm_required=False,
            description='按题干关键词在知识点分类树内建议归属（M3 接 B2 提示词后升级为语义建议）',
            params_schema={'type': 'object', 'properties': {'stem': {'type': 'string'}}},
        ),
        _kp_suggest,
    ),
    (
        ToolSpec(
            name='folder.create', label='新建专题夹', kind='write', confirm_required=True,
            description='创建引用式专题夹（不挪题）',
            params_schema={'type': 'object', 'required': ['name'],
                           'properties': {'name': {'type': 'string'}, 'desc': {'type': 'string'}}},
        ),
        _folder_create,
    ),
    (
        ToolSpec(
            name='folder.add_questions', label='题目收入专题夹', kind='write', confirm_required=True,
            description='把题目以引用方式收入专题夹（usage_count 行为飞轮 +1）',
            params_schema={'type': 'object', 'required': ['folder_id', 'question_ids'],
                           'properties': {'folder_id': {'type': 'string'}, 'question_ids': {'type': 'array', 'items': {'type': 'string'}}}},
        ),
        _folder_add,
    ),
    (
        ToolSpec(
            name='today.summary', label='今日工作台摘要', kind='read', confirm_required=False,
            description='聚合今日待批改数/任务进度等真实工作台数据',
            params_schema={'type': 'object', 'properties': {}},
        ),
        _today_summary,
    ),
]
