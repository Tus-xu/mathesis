# coding: utf-8
"""M2-A Classroom Basic 服务层全链路测试（L9-basic 服务端判据对拍）。

用真实 SQLite（aiosqlite）执行：唯一约束幂等、状态机事件、快照/重放、summary 聚合。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domains import classroom as _cfg_marker  # noqa: F401  确保包导入
from app.domains.classroom import service as cs
from app.domains.classroom.classroom_token import ClassroomTokenError, verify_classroom_token
from app.domains.classroom.realtime import InProcRealtimeBus, teacher_stream
from app.domains.classroom.state_machine import InvalidTransitionError
from app.domains.catalog.models import Clazz  # noqa: F401
from app.db.base import Base


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
def ids():
    return {'teacher': uuid.uuid4(), 'class': uuid.uuid4()}


async def _open_classroom(session, ids) -> tuple:
    s = await cs.create_session(session, teacher_id=ids['teacher'], class_id=ids['class'])
    await cs.open_session(session, s.id)
    return s


# ── 开课 / join / token ────────────────────────────────────────────────────

async def test_create_and_open_session(db, ids):
    s = await _open_classroom(db, ids)
    assert s.status == 'open' and len(s.join_code) == 6 and s.join_code.isdigit()
    assert s.started_at is not None


async def test_join_issues_classroom_scoped_token(db, ids):
    s = await _open_classroom(db, ids)
    out = await cs.join(db, join_code=s.join_code, student_name='王雨桐')
    claims = verify_classroom_token(out['token'], secret=cs._secret(), session_id=out['session_id'])
    assert claims['scope'] == 'classroom' and claims['anon_id'].startswith('st-')
    # 跨会话校验：换 session_id 校验必须拒绝（403 级）
    with pytest.raises(ClassroomTokenError) as exc:
        verify_classroom_token(out['token'], secret=cs._secret(), session_id=str(uuid.uuid4()))
    assert exc.value.code == 40301
    # 坏签名拒绝
    with pytest.raises(ClassroomTokenError):
        verify_classroom_token(out['token'][:-2] + 'xx', secret=cs._secret())


# ── 推互动 / 作答幂等（L9 判定③）─────────────────────────────────────────

async def test_push_and_idempotent_response(db, ids):
    s = await _open_classroom(db, ids)
    a = await cs.push_activity(db, s.id, kind='question', config={'stem_text': '椭圆焦点弦', 'answer': 'B'})
    assert a.status == 'collecting'
    p1 = (await cs.join(db, join_code=s.join_code, student_name='陈子豪'))['participant_id']
    p1_uuid = uuid.UUID(p1)
    first = await cs.submit_response(db, a.id, p1_uuid, answer={'choice': 'B', 'correct': True})
    assert first.created is True
    dup = await cs.submit_response(db, a.id, p1_uuid, answer={'choice': 'A', 'correct': False})
    assert dup.created is False and dup.response_id == first.response_id  # 返回首次结果
    assert dup.answer == {'choice': 'B', 'correct': True}


async def test_response_rejected_when_not_collecting(db, ids):
    s = await _open_classroom(db, ids)
    a = await cs.push_activity(db, s.id, kind='poll', config={})
    await cs.set_activity_status(db, a.id, 'locked')
    with pytest.raises(cs.ClassroomBusinessError) as exc:
        await cs.submit_response(db, a.id, uuid.uuid4(), answer={})
    assert exc.value.code == cs.ERR['not_collecting']


# ── lock/reveal/非法转移（L9 判定④）─────────────────────────────────────

async def test_lock_reveal_and_illegal_repush(db, ids):
    s = await _open_classroom(db, ids)
    a = await cs.push_activity(db, s.id, kind='question', config={})
    locked = await cs.set_activity_status(db, a.id, 'locked')
    assert locked.status == 'locked' and locked.locked_at is not None
    revealed = await cs.set_activity_status(db, a.id, 'revealed')
    assert revealed.status == 'revealed'
    # 非法转移：locked 后再 push → 业务错误码而非 500
    with pytest.raises(InvalidTransitionError):
        await cs.set_activity_status(db, a.id, 'collecting')


# ── 快照 / durable 重放（L9 判定②⑦）────────────────────────────────────

async def test_snapshot_and_replay_after_disconnect(db, ids):
    s = await _open_classroom(db, ids)
    a = await cs.push_activity(db, s.id, kind='question', config={'stem_text': '题', 'answer': 'A'})
    p = uuid.UUID((await cs.join(db, join_code=s.join_code, student_name='刘一鸣'))['participant_id'])
    await cs.submit_response(db, a.id, p, answer={'choice': 'A', 'correct': True})

    snap = await cs.snapshot(db, s.id)
    assert snap['session']['status'] == 'open'
    assert snap['participants'] == 1
    assert snap['activities'][0]['responded'] == 1
    last_seq = snap['last_seq']
    assert last_seq > 0

    # 断线重连：after(last_seq) 零丢失（重放序列递增且不重复）
    events_before = await cs.replay_events(db, s.id, after_seq=0)
    seqs = [e.seq for e in events_before]
    assert seqs == sorted(set(seqs))
    missed = await cs.replay_events(db, s.id, after_seq=last_seq)
    assert missed == []

    # 学生投影：不含 join_code / 答案字段
    s_snap = await cs.snapshot(db, s.id, student=True)
    assert s_snap['session']['join_code'] is None
    assert 'answer' not in s_snap['activities'][0]['config']


async def test_teacher_stream_replays_events(db, ids):
    import asyncio

    s = await _open_classroom(db, ids)
    await cs.push_activity(db, s.id, kind='question', config={})
    bus = InProcRealtimeBus()
    agen = teacher_stream(db, str(s.id), bus=bus, last_event_id=0)
    frames = []
    for _ in range(6):  # snapshot(1) + durable 事件 5 条（created/open/ready/pushed/collecting）
        frames.append(await asyncio.wait_for(agen.__anext__(), timeout=3))
    assert frames[0].event == 'snapshot'
    durable = [f for f in frames[1:] if f.seq is not None]
    assert len(durable) == 5
    assert [f.seq for f in durable] == sorted(f.seq for f in durable)
    # live：先让生成器推进到 live 订阅注册，再 publish（惰性生成器时序）
    task = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.05)
    await bus.publish(str(s.id), 'stats.refresh', {'responded': 1})
    live = await asyncio.wait_for(task, timeout=3)
    assert live.event == 'stats.refresh' and live.seq is None
    await agen.aclose()


# ── 存入课件 / 结课 summary（L9 判定⑥⑧）────────────────────────────────

async def test_save_to_deck_and_end_summary(db, ids):
    s = await _open_classroom(db, ids)
    a1 = await cs.push_activity(db, s.id, kind='question', config={'stem_text': '第 1 题：求离心率'})
    p = uuid.UUID((await cs.join(db, join_code=s.join_code, student_name='林小满'))['participant_id'])
    await cs.submit_response(db, a1.id, p, answer={'choice': 'C', 'correct': False})  # collecting 阶段作答
    await cs.set_activity_status(db, a1.id, 'locked')
    await cs.set_activity_status(db, a1.id, 'revealed')
    a2 = await cs.push_activity(db, s.id, kind='poll', config={})

    deck = await cs.save_to_deck(db, s.id, title='高二(3)班 · 课堂回顾', template_id=None, created_by=ids['teacher'])
    assert deck.id is not None and deck.source == 'lesson-push'
    assert len(deck.slides) == 2  # 真实结构化 slides（非 mock 直接成功）
    assert deck.slides[0]['elements'][0]['html'] == '第 1 题：求离心率'

    summary = await cs.end_session(db, s.id)
    assert summary.stats['total_responses'] == 1
    # 判卷新语义（S16 审计修复）：config 无 answer 的活动不可判卷 → accuracy=None（诚实空态），
    # insight 不再假阳性报警。可判卷路径由 test_end_summary_grading 覆盖。
    assert summary.stats['accuracy'] is None
    assert summary.stats['participants'] == 1
    with pytest.raises(cs.ClassroomBusinessError):
        await cs.push_activity(db, s.id, kind='poll', config={})
    assert a2.status in ('collecting', 'completed')


async def test_end_summary_grading(db, ids):
    """结课判卷（S16 修复）：accuracy 对照 config.answer 服务端判定，不再依赖无人写入的 correct 标志。"""
    s = await _open_classroom(db, ids)
    a1 = await cs.push_activity(db, s.id, kind='question', config={'stem_text': '题', 'answer': 'A'})
    p1 = uuid.UUID((await cs.join(db, join_code=s.join_code, student_name='对的同学'))['participant_id'])
    p2 = uuid.UUID((await cs.join(db, join_code=s.join_code, student_name='错的同学'))['participant_id'])
    await cs.submit_response(db, a1.id, p1, answer={'choice': 'A'})
    await cs.submit_response(db, a1.id, p2, answer={'choice': 'C'})
    await cs.set_activity_status(db, a1.id, 'locked')
    await cs.set_activity_status(db, a1.id, 'revealed')
    summary = await cs.end_session(db, s.id)
    assert summary.stats['accuracy'] == 0.5
    per = summary.stats['activities'][0]
    assert per['correct'] == 1 and per['responses'] == 2 and per['answer'] == 'A'


async def test_ai_generation_hitl_flow(db, ids):
    s = await _open_classroom(db, ids)
    a = await cs.push_activity(db, s.id, kind='question', config={})
    gen = await cs.request_ai_generation(db, a.id)
    assert gen.status == 'generating'
    await cs.advance_ai_generation(db, gen.id, 'verifying')
    await cs.advance_ai_generation(db, gen.id, 'awaiting_teacher')
    await cs.advance_ai_generation(db, gen.id, 'approved',
                                   content={'stem': '变式题'}, verifier_result={'passed': True})
    await cs.advance_ai_generation(db, gen.id, 'pushed')
    # pushed 为终态：再转移抛业务错误
    with pytest.raises(InvalidTransitionError):
        await cs.advance_ai_generation(db, gen.id, 'rejected')


# ── 公平点名 ────────────────────────────────────────────────────────────────

async def test_call_random_fair_no_repeat(db, ids):
    s = await _open_classroom(db, ids)
    names = ['王雨桐', '陈子豪', '刘一鸣']
    for n in names:
        await cs.join(db, join_code=s.join_code, student_name=n)
    picked = []
    for _ in range(len(names)):
        out = await cs.call_random(db, s.id)
        picked.append(out['anon_id'])
        assert out['round_reset'] is False
    assert len(set(picked)) == len(names)  # 一轮内不重复
    out = await cs.call_random(db, s.id)
    assert out['round_reset'] is True      # 全员点过 → 重开一轮
