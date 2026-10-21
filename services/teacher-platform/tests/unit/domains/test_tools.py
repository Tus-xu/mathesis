# coding: utf-8
"""M2-C 管家工具集测试（§3.8：≥12 个、与 mock /butler/tools 同构、invoke 真实执行）。"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.domains.tools import TeacherCtx, invoke_tool, specs

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def ctx(db):
    return TeacherCtx(session=db, user_id=uuid.uuid4(), school_id=uuid.uuid4())


def test_registry_at_least_12_unique_and_well_formed():
    all_specs = specs()
    assert len(all_specs) >= 12
    names = [s['name'] for s in all_specs]
    assert len(names) == len(set(names))
    for s in all_specs:
        assert s['kind'] in ('read', 'write', 'frontend')
        assert s['label'] and s['description'] and isinstance(s['confirm_required'], bool)
        assert isinstance(s['params_schema'], dict)
    # mock 同构语义：写操作必须 confirm_required（R5/R6 确认制）
    for s in all_specs:
        if s['kind'] == 'write':
            assert s['confirm_required'] is True, s['name']
        if s['kind'] == 'frontend':
            assert s['confirm_required'] is False, s['name']


async def test_invoke_quiz_create_and_search(ctx):
    out = await invoke_tool('quiz.create', {
        'kp_code': 'G2-YY-01', 'q_type': 'solve', 'stem_latex': '求椭圆离心率',
        'answer': 'e=1/2', 'difficulty': 'medium',
    }, ctx)
    assert 'question_id' in out
    found = await invoke_tool('quiz.search', {'q': '离心率'}, ctx)
    assert found['total'] == 1 and found['items'][0]['kp_code'] == 'G2-YY-01'


async def test_invoke_folder_flow_usage_count(ctx):
    q = await invoke_tool('quiz.create', {'kp_code': 'K1', 'q_type': 'fill',
                                          'stem_latex': 'x=', 'answer': '1'}, ctx)
    f = await invoke_tool('folder.create', {'name': '周末作业'}, ctx)
    added = await invoke_tool('folder.add_questions', {'folder_id': f['folder_id'],
                                                       'question_ids': [q['question_id']]}, ctx)
    assert added['added'] == 1
    again = await invoke_tool('folder.add_questions', {'folder_id': f['folder_id'],
                                                       'question_ids': [q['question_id']]}, ctx)
    assert again['added'] == 0  # 引用式：重复添加不重复计
    from sqlalchemy import select

    from app.domains.catalog.models import QuizQuestion
    row = await ctx.session.get(QuizQuestion, uuid.UUID(q['question_id']))
    assert row.usage_count == 1  # 行为飞轮 +1


async def test_invoke_frontend_navigate_and_unknown_tool(ctx):
    out = await invoke_tool('navigate.slides', {'route': '/teacher-v3/slides'}, ctx)
    assert out['action'] == 'navigate' and out['route'] == '/teacher-v3/slides'
    with pytest.raises(KeyError):
        await invoke_tool('no.such.tool', {}, ctx)


async def test_invoke_grading_overview_empty_state(ctx):
    out = await invoke_tool('grading.overview', {}, ctx)
    assert out == {'assignments': 0, 'graded': 0}  # 空态结构与有数据一致（红线 6）
