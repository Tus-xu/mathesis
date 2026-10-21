# coding: utf-8
"""classroom 域业务服务（M2-A · Gate 1 主体，L9-basic 服务端权威判据）。

铁律（架构 §12）：一切状态转移走三层状态机并写 durable 事件（classroom_events，
UNIQUE(session_id, seq)——靠约束不靠检查）；作答幂等靠
UNIQUE(activity_id, participant_id, attempt_no)，重复提交返回首次结果；
非法转移返回业务错误码（ClassroomBusinessError），禁止 500。

B3 依赖口径（B4 规范 §8 协议 fake）：DB 会话由调用方注入（生产 asyncpg / 测试
aiosqlite，JSONB 已带 sqlite variant）；RealtimeHub 传输层见 realtime.py（协议 +
InProc fake，B3 落地后替换实现不换调用方）。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.classroom import state_machine as sm
from app.domains.classroom.models import (
    ClassroomActivity,
    ClassroomAiGeneration,
    ClassroomEvent,
    ClassroomParticipant,
    ClassroomResponse,
    ClassroomSession,
    ClassroomSummary,
)
from app.domains.contents.models import Deck

ERR = {
    'session_not_found': 40404,
    'activity_not_found': 40405,
    'session_closed': 42203,
    'not_collecting': 42204,
    'activity_locked': 42205,
    'invalid_transition': sm.ERR_INVALID_TRANSITION,
}


class ClassroomBusinessError(Exception):
    """课堂业务错误（路由层包响应壳 {code,message}，非 500）。"""

    def __init__(self, message: str, code: int):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class SubmittedResponse:
    response_id: UUID
    answer: dict
    attempt_no: int
    created: bool


async def _append_event(session: AsyncSession, session_id: UUID, event_type: str, payload: dict) -> int:
    """durable 事件写入：seq = max+1，UNIQUE(session_id, seq) 冲突重试（约束兜底）。"""
    for _ in range(5):
        cur = (await session.execute(
            select(func.max(ClassroomEvent.seq)).where(ClassroomEvent.session_id == session_id),
        )).scalar() or 0
        row = ClassroomEvent(session_id=session_id, seq=cur + 1, event_type=event_type, payload=payload)
        session.add(row)
        try:
            await session.flush()
            return row.seq
        except Exception:
            await session.rollback()
    raise ClassroomBusinessError('课堂事件写入冲突', 42301)


def _require_state(entity_status: str, frm: str) -> None:
    if entity_status != frm:
        raise ClassroomBusinessError(f'状态 {entity_status!r} 不满足前置 {frm!r}', ERR['invalid_transition'])


# ── Session 层 ──────────────────────────────────────────────────────────────

async def create_session(session: AsyncSession, *, teacher_id: UUID, class_id: UUID) -> ClassroomSession:
    """开课：join_code 6 位数字，唯一约束冲突重试。"""
    for _ in range(6):
        row = ClassroomSession(
            teacher_id=teacher_id, class_id=class_id,
            join_code=f'{random.randint(0, 999999):06d}', status='created',
        )
        session.add(row)
        try:
            await session.flush()
            await _append_event(session, row.id, 'session.created', {'teacher_id': str(teacher_id)})
            return row
        except IntegrityError:
            await session.rollback()
    raise ClassroomBusinessError('join_code 生成冲突，请重试', 42302)


async def open_session(session: AsyncSession, session_id: UUID) -> ClassroomSession:
    row = await session.get(ClassroomSession, session_id)
    if row is None:
        raise ClassroomBusinessError('课堂不存在', ERR['session_not_found'])
    sm.transition('session', row.status, 'open')
    row.status, row.started_at = 'open', _now()
    await _append_event(session, session_id, sm.event_type('session', 'created', 'open'), {})
    return row


async def join(session: AsyncSession, *, join_code: str, student_name: str, token_ttl_seconds: int = 4 * 3600) -> dict:
    """学生 H5 加入（无 JWT）：换课堂作用域短时 token（§10）。"""
    from app.domains.classroom.classroom_token import mint_classroom_token

    row = (await session.execute(
        select(ClassroomSession).where(ClassroomSession.join_code == join_code),
    )).scalar_one_or_none()
    if row is None:
        raise ClassroomBusinessError('课堂码无效', ERR['session_not_found'])
    if row.status != 'open':
        raise ClassroomBusinessError('课堂未开放或已结束', ERR['session_closed'])

    anon_id = f'st-{uuid4().hex[:10]}'
    participant = ClassroomParticipant(session_id=row.id, student_name=student_name, anon_id=anon_id)
    session.add(participant)
    await _append_event(session, row.id, 'participant.joined', {'anon_id': anon_id})
    token = mint_classroom_token(
        secret=_secret(), session_id=str(row.id), participant_id=str(participant.id),
        anon_id=anon_id, student_name=student_name, ttl_seconds=token_ttl_seconds,
    )
    return {'session_id': str(row.id), 'participant_id': str(participant.id), 'token': token}


def _secret() -> str:
    """JWT secret：生产经 B3 config/.env 注入（架构 §15 JWT_SECRET）；缺省仅限单测。"""
    import os

    return os.environ.get('JWT_SECRET', 'dev-only-secret-not-for-production')


async def end_session(session: AsyncSession, session_id: UUID) -> ClassroomSummary:
    """结课：ended→archived；未完成活动标记 completed；生成 classroom_summary。"""
    row = await session.get(ClassroomSession, session_id)
    if row is None:
        raise ClassroomBusinessError('课堂不存在', ERR['session_not_found'])
    sm.transition('session', row.status, 'ended')
    row.status, row.ended_at = 'ended', _now()
    await _append_event(session, session_id, sm.event_type('session', 'open', 'ended'), {})

    acts = (await session.execute(
        select(ClassroomActivity).where(ClassroomActivity.session_id == session_id),
    )).scalars().all()
    stats_acts = []
    for a in acts:
        if a.status == 'revealed':
            # 仅 revealed→completed 是合法收尾；collecting/locked 等中间态保留原状（summary 照常聚合）
            sm.transition('activity', a.status, 'completed')
            frm = 'revealed'
            a.status = 'completed'
            await _append_event(session, session_id, sm.event_type('activity', frm, 'completed'), {'activity_id': str(a.id)})
        stats_acts.append(a)
    sm.transition('session', 'ended', 'archived')
    row.status = 'archived'
    await _append_event(session, session_id, sm.event_type('session', 'ended', 'archived'), {})

    stats = await _aggregate(session, stats_acts)
    summary = ClassroomSummary(session_id=session_id, stats=stats, insight=_insight(stats))
    session.add(summary)
    return summary


async def _aggregate(session: AsyncSession, acts: list[ClassroomActivity]) -> dict:
    total_responses = correct = gradable = 0
    per_activity: list[dict] = []
    for a in acts:
        answers = (await session.execute(
            select(ClassroomResponse).where(ClassroomResponse.activity_id == a.id),
        )).scalars().all()
        total_responses += len(answers)
        # 服务端判卷（S16 审计修复：correct 字段无人写入导致 accuracy 恒 0）——
        # question 类活动对照 config.answer 判定（选择/填空语义：剥离空白全等比较）
        expected = str((a.config or {}).get('answer') or '').strip().upper()
        ok = 0
        for r in answers:
            given = str((r.answer or {}).get('choice') or '').strip().upper()
            if given and expected and given == expected:
                ok += 1
        if a.kind == 'question' and expected:
            gradable += len(answers)
            correct += ok
        per_activity.append({
            'activity_id': str(a.id), 'kind': a.kind, 'responses': len(answers),
            'correct': ok if a.kind == 'question' else None,
            'answer': expected or None,
        })
    participants = (await session.execute(
        select(func.count()).select_from(ClassroomParticipant)
        .where(ClassroomParticipant.session_id == (acts[0].session_id if acts else None)),
    )).scalar() or 0
    return {
        'activities': per_activity,
        'total_responses': total_responses,
        'accuracy': round(correct / gradable, 4) if gradable else None,
        'participants': participants,
    }


def _insight(stats: dict) -> str | None:
    weak = [a for a in stats['activities'] if a.get('correct') is not None and stats['total_responses']
            and a['responses'] and a['correct'] / a['responses'] < 0.6]
    if not weak:
        return None
    return f'有 {len(weak)} 道题正确率低于 60%，建议生成讲评与变式训练'


# ── Activity 层 ─────────────────────────────────────────────────────────────

async def push_activity(
    session: AsyncSession, session_id: UUID, *,
    kind: str, question_id: UUID | None = None, config: dict | None = None, ord_: int = 0,
) -> ClassroomActivity:
    """推互动：draft→ready→pushed→collecting（链式合法转移，逐段写事件）。"""
    parent = await session.get(ClassroomSession, session_id)
    if parent is None:
        raise ClassroomBusinessError('课堂不存在', ERR['session_not_found'])
    if parent.status != 'open':
        raise ClassroomBusinessError('课堂未开放', ERR['session_closed'])
    row = ClassroomActivity(session_id=session_id, kind=kind, question_id=question_id,
                            config=config or {}, ord=ord_, status='draft')
    session.add(row)
    await session.flush()
    for frm in ('draft', 'ready', 'pushed'):
        to = {'draft': 'ready', 'ready': 'pushed', 'pushed': 'collecting'}[frm]
        sm.transition('activity', frm, to)
        row.status = to
        await _append_event(session, session_id, sm.event_type('activity', frm, to), {'activity_id': str(row.id)})
    row.pushed_at = _now()
    return row


async def set_activity_status(session: AsyncSession, activity_id: UUID, to: str) -> ClassroomActivity:
    """lock/reveal/complete：collecting→locked→revealed→completed。"""
    row = await session.get(ClassroomActivity, activity_id)
    if row is None:
        raise ClassroomBusinessError('活动不存在', ERR['activity_not_found'])
    frm = row.status
    sm.transition('activity', frm, to)
    row.status = to
    if to == 'locked':
        row.locked_at = _now()
    await _append_event(session, row.session_id, sm.event_type('activity', frm, to), {'activity_id': str(activity_id)})
    return row


# ── 作答（幂等核心，G8）────────────────────────────────────────────────────

async def submit_response(
    session: AsyncSession, activity_id: UUID, participant_id: UUID, *,
    answer: dict, image_key: str | None = None, attempt_no: int = 1,
) -> SubmittedResponse:
    """学生作答：UNIQUE(activity_id, participant_id, attempt_no) 幂等——重复提交返回首次结果
    （即便活动已 lock/reveal：幂等语义优先于状态检查；并发竞态由唯一约束兜底，G8）。"""
    act = await session.get(ClassroomActivity, activity_id)
    if act is None:
        raise ClassroomBusinessError('活动不存在', ERR['activity_not_found'])
    existing = (await session.execute(
        select(ClassroomResponse).where(
            ClassroomResponse.activity_id == activity_id,
            ClassroomResponse.participant_id == participant_id,
            ClassroomResponse.attempt_no == attempt_no,
        ),
    )).scalar_one_or_none()
    if existing is not None:
        return SubmittedResponse(existing.id, existing.answer, existing.attempt_no, False)
    if act.status != 'collecting':
        raise ClassroomBusinessError(f'活动状态 {act.status!r} 不收作答', ERR['not_collecting'])
    row = ClassroomResponse(activity_id=activity_id, participant_id=participant_id,
                            answer=answer, image_key=image_key, attempt_no=attempt_no)
    session.add(row)
    try:
        await session.flush()
        await _append_event(session, act.session_id, 'response.submitted',
                            {'activity_id': str(activity_id), 'attempt_no': attempt_no, 'first': True})
        return SubmittedResponse(row.id, answer, attempt_no, True)
    except IntegrityError:
        await session.rollback()
        first = (await session.execute(
            select(ClassroomResponse).where(
                ClassroomResponse.activity_id == activity_id,
                ClassroomResponse.participant_id == participant_id,
                ClassroomResponse.attempt_no == attempt_no,
            ),
        )).scalar_one()
        return SubmittedResponse(first.id, first.answer, first.attempt_no, False)


# ── 统计 / 快照 / 重放（RealtimeHub 业务语义）───────────────────────────────

async def activity_stats(session: AsyncSession, activity_id: UUID, *, revealed: bool) -> dict:
    act = await session.get(ClassroomActivity, activity_id)
    if act is None:
        raise ClassroomBusinessError('活动不存在', ERR['activity_not_found'])
    answers = (await session.execute(
        select(ClassroomResponse).where(ClassroomResponse.activity_id == activity_id),
    )).scalars().all()
    dist: dict[str, int] = {}
    for r in answers:
        key = str((r.answer or {}).get('choice', ''))
        dist[key] = dist.get(key, 0) + 1
    # 服务端判卷正确率（与 _aggregate 同口径：对照 config.answer）
    act_config = act.config or {}
    expected = str(act_config.get('answer') or '').strip().upper()
    correct_rate = None
    if expected and answers:
        ok = sum(1 for r in answers
                 if str((r.answer or {}).get('choice') or '').strip().upper() == expected)
        correct_rate = round(ok / len(answers), 4)
    return {
        'activity_id': str(activity_id), 'status': act.status,
        'responded': len(answers),
        'distribution': dist if (revealed or act.status in ('revealed', 'completed')) else {'_hidden': len(answers)},
        'correct_rate': correct_rate if (revealed or act.status in ('revealed', 'completed')) else None,
    }


async def snapshot(session: AsyncSession, session_id: UUID, *, student: bool = False) -> dict:
    """权威快照（重连恢复的第一帧；学生视角投影不含他人身份/明细）。"""
    s = await session.get(ClassroomSession, session_id)
    if s is None:
        raise ClassroomBusinessError('课堂不存在', ERR['session_not_found'])
    acts = (await session.execute(
        select(ClassroomActivity).where(ClassroomActivity.session_id == session_id).order_by(ClassroomActivity.ord),
    )).scalars().all()
    last_seq = (await session.execute(
        select(func.max(ClassroomEvent.seq)).where(ClassroomEvent.session_id == session_id),
    )).scalar() or 0
    participants_n = (await session.execute(
        select(func.count()).select_from(ClassroomParticipant)
        .where(ClassroomParticipant.session_id == session_id),
    )).scalar() or 0
    payload: dict = {
        'session': {'id': str(s.id), 'status': s.status, 'join_code': s.join_code if not student else None},
        'participants': participants_n,
        'last_seq': last_seq,
        'activities': [],
    }
    for a in acts:
        item = {'id': str(a.id), 'kind': a.kind, 'ord': a.ord, 'status': a.status,
                'config': a.config if not student else _student_config(a.config)}
        if not student:
            st = await activity_stats(session, a.id, revealed=True)
            item['responded'] = st['responded']
            item['distribution'] = st['distribution']
        payload['activities'].append(item)
    return payload


def _student_config(config: dict) -> dict:
    """学生投影：剥离答案类字段（answer/analysis/correct）。"""
    return {k: v for k, v in (config or {}).items() if k not in ('answer', 'analysis', 'correct')}


async def replay_events(session: AsyncSession, session_id: UUID, *, after_seq: int) -> list[ClassroomEvent]:
    """durable 重放（PG 权威回放源，架构 §7.1）：after(Last-Event-ID) 补拉。"""
    rows = (await session.execute(
        select(ClassroomEvent)
        .where(ClassroomEvent.session_id == session_id, ClassroomEvent.seq > after_seq)
        .order_by(ClassroomEvent.seq),
    )).scalars().all()
    return list(rows)


# ── 存入课件 / 公平点名 / AI 变式状态面 ─────────────────────────────────────

async def save_to_deck(session: AsyncSession, session_id: UUID, *, title: str, template_id: UUID | None, created_by: UUID) -> Deck:
    """「存入课件」：真实 decks 行（slides 结构化），替换 mock 直接成功（G1 缺口修复）。

    端点归属登记 IFC-004（§12.3 未列该端点；建议 POST /classroom/sessions/{id}/save-to-deck，
    由 B5 对账后定稿）。source 暂用 'lesson-push'（V3Deck.source 四值枚举内）。
    """
    s = await session.get(ClassroomSession, session_id)
    if s is None:
        raise ClassroomBusinessError('课堂不存在', ERR['session_not_found'])
    acts = (await session.execute(
        select(ClassroomActivity).where(ClassroomActivity.session_id == session_id).order_by(ClassroomActivity.ord),
    )).scalars().all()
    slides = [{'id': f'cls-{i}', 'layout': 'review' if a.kind == 'question' else 'blank',
               'elements': [{
                   'id': f'cls-{i}-e0', 'type': 'text', 'left': 72, 'top': 52,
                   'width': 1136, 'height': 64, 'z': 1, 'teacher_confirmed': False,
                   'html': (a.config or {}).get('stem_text') or f'课堂互动 · {a.kind}',
                   'font_size': 32,
               }]} for i, a in enumerate(acts)]
    deck = Deck(title=title, template_id=template_id, source='lesson-push',
                class_id=s.class_id, slides=slides, created_by=created_by)
    session.add(deck)
    await _append_event(session, session_id, 'deck.saved', {'deck_id': str(deck.id), 'slides': len(slides)})
    return deck


async def call_random(session: AsyncSession, session_id: UUID) -> dict:
    """公平随机点名：本轮优先未点过的（事件历史为公平账本），全员点过则重开一轮。"""
    participants = (await session.execute(
        select(ClassroomParticipant).where(ClassroomParticipant.session_id == session_id),
    )).scalars().all()
    if not participants:
        raise ClassroomBusinessError('课堂暂无学生', ERR['session_not_found'])
    called = {ev.payload.get('anon_id') for ev in await replay_events(session, session_id, after_seq=0)
              if ev.event_type == 'teacher.call_random'}
    remaining = [p for p in participants if p.anon_id not in called]
    pool = remaining or participants
    picked = random.choice(pool)
    await _append_event(session, session_id, 'teacher.call_random',
                        {'anon_id': picked.anon_id, 'participant_id': str(picked.id), 'round_reset': not remaining})
    return {'participant_id': str(picked.id), 'anon_id': picked.anon_id, 'name': picked.student_name,
            'student_name': picked.student_name, 'round_reset': not remaining}


async def request_ai_generation(session: AsyncSession, activity_id: UUID, *, kind: str = 'variation') -> ClassroomAiGeneration:
    """触发 AI 变式：AiGeneration 行落库（HITL 子流程起点；真 AI 编排归 B1，M3 接）。"""
    act = await session.get(ClassroomActivity, activity_id)
    if act is None:
        raise ClassroomBusinessError('活动不存在', ERR['activity_not_found'])
    row = ClassroomAiGeneration(activity_id=activity_id, kind=kind, status='not_requested')
    session.add(row)
    await session.flush()
    await _advance_ai(session, row, 'generating')
    return row


async def advance_ai_generation(session: AsyncSession, gen_id: UUID, to: str, *, content: dict | None = None, verifier_result: dict | None = None) -> ClassroomAiGeneration:
    row = await session.get(ClassroomAiGeneration, gen_id)
    if row is None:
        raise ClassroomBusinessError('AI 生成记录不存在', 40406)
    await _advance_ai(session, row, to, content=content, verifier_result=verifier_result)
    return row


async def _advance_ai(session: AsyncSession, row: ClassroomAiGeneration, to: str, **kw) -> None:
    frm = row.status
    sm.transition('ai_generation', frm, to)
    row.status = to
    if (content := kw.get('content')) is not None:
        row.content = content
    if (vr := kw.get('verifier_result')) is not None:
        row.verifier_result = vr
    act = await session.get(ClassroomActivity, row.activity_id)
    await _append_event(session, act.session_id, sm.event_type('ai_generation', frm, to).replace('ai_generation', 'ai'),
                        {'ai_generation_id': str(row.id), 'status': to})


def _now():
    return datetime.now(timezone.utc)
