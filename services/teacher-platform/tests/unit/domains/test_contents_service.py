# coding: utf-8
"""M2-B contents 域服务测试（JSON Patch/diff 日志/确认门/推课件映射/导出登记）。"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.domains.contents import service as cs
from app.domains.contents.models import Deck, Plan, SlidePatchLog


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


pytestmark = pytest.mark.asyncio


def _deck() -> Deck:
    return Deck(title='椭圆的标准方程', source='topic', slides=[
        {'id': 's1', 'layout': 'definition', 'elements': [
            {'id': 'e1', 'type': 'text', 'left': 72, 'top': 52, 'width': 1136, 'height': 64,
             'z': 1, 'html': '旧标题', 'font_size': 32, 'teacher_confirmed': True},
            {'id': 'e2', 'type': 'formula', 'left': 72, 'top': 140, 'width': 600, 'height': 66,
             'z': 2, 'latex': 'x^2=1', 'font_size': 28, 'teacher_confirmed': False},
        ]},
    ])


def _plan() -> Plan:
    return Plan(topic='椭圆的标准方程', lesson_type='新授课', confirmed=False, boards=[
        {'name': '复习引入'}, {'name': '新知探究'}, {'name': '例题精讲'}, {'name': '课堂小结与检测'},
    ])


async def test_patch_slide_json_patch_and_diff_log(db):
    deck = _deck()
    db.add(deck)
    await db.flush()
    out = await cs.patch_slide(db, deck.id, 's1', [
        {'op': 'replace', 'path': '/elements/0/html', 'value': '椭圆的定义'},
        {'op': 'replace', 'path': '/elements/1/latex', 'value': '\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1'},
        {'op': 'add', 'path': '/notes', 'value': '强调 a>b>0'},
    ])
    el0 = out.slides[0]['elements'][0]
    assert el0['html'] == '椭圆的定义'
    assert out.slides[0]['elements'][1]['latex'].startswith('\\frac')
    assert out.slides[0]['notes'] == '强调 a>b>0'
    log = (await db.execute(select(SlidePatchLog).where(SlidePatchLog.deck_id == deck.id))).scalar_one()
    assert log.modifier == 'teacher' and len(log.patch) == 3  # 元素级 diff 审计


async def test_patch_slide_rejects_bad_ops(db):
    deck = _deck()
    db.add(deck)
    await db.flush()
    with pytest.raises(cs.ContentsError) as exc:
        await cs.patch_slide(db, deck.id, 's1', [{'op': 'replace', 'path': '/elements/0/ghost', 'value': 'x'}])
    assert exc.value.code == 42206
    with pytest.raises(cs.ContentsError) as exc2:
        await cs.patch_slide(db, deck.id, 's1', [{'op': 'copy', 'path': '/x', 'value': 1}])
    assert exc2.value.code == 42206


async def test_ai_has_no_direct_write_path(db):
    """R5 红线：AI 无直写旁路——modifier != teacher 一律拒绝。"""
    deck = _deck()
    db.add(deck)
    await db.flush()
    with pytest.raises(cs.ContentsError) as exc:
        await cs.patch_slide(db, deck.id, 's1', [{'op': 'replace', 'path': '/elements/0/html', 'value': 'AI 改'}],
                             modifier='ai')
    assert exc.value.code == 40302


async def test_add_and_delete_slide(db):
    deck = _deck()
    db.add(deck)
    await db.flush()
    new_slide = {'id': 's2', 'layout': 'blank', 'elements': []}
    await cs.add_slide(db, deck.id, new_slide, after_id='s1')
    assert [s['id'] for s in deck.slides] == ['s1', 's2']
    await cs.delete_slide(db, deck.id, 's1')
    assert [s['id'] for s in deck.slides] == ['s2']
    with pytest.raises(cs.ContentsError):
        await cs.delete_slide(db, deck.id, 's1')


async def test_plan_confirm_gate_and_push_to_deck(db):
    plan = _plan()
    db.add(plan)
    await db.flush()
    # 未确认：拒绝推送
    with pytest.raises(cs.ContentsError) as exc:
        await cs.push_to_deck(db, plan.id, template_id=uuid.uuid4(), created_by=uuid.uuid4())
    assert exc.value.code == 42207
    # AI 只读门：confirmed 后 assert_plan_mutable 拒绝（生成域须走草稿副本）
    await cs.confirm_plan(db, plan.id)
    with pytest.raises(cs.ContentsError) as exc2:
        await cs.assert_plan_mutable(plan)
    assert exc2.value.code == 40303
    # 推课件：环节→页型映射（L3 判定⑤：真实 deck 行、元素结构化）
    deck = await cs.push_to_deck(db, plan.id, template_id=uuid.uuid4(), created_by=uuid.uuid4())
    assert deck.source == 'lesson-push'
    layouts = [s['layout'] for s in deck.slides]
    assert layouts == ['review', 'definition', 'example', 'summary']


async def test_register_export(db):
    deck = _deck()
    db.add(deck)
    await db.flush()
    job = await cs.register_export(db, deck.id, fmt='pptx')
    assert job.status == 'queued' and job.deck_id == deck.id
    with pytest.raises(cs.ContentsError):
        await cs.register_export(db, deck.id, fmt='keynote')
    with pytest.raises(cs.ContentsError):
        await cs.register_export(db, uuid.uuid4(), fmt='pptx')  # 课件不存在
