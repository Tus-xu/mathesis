# coding: utf-8
"""classroom 域路由（teacher-v3.1，架构 §12：服务端权威三层状态机 + 双端 SSE）。

契约事实源：frontend/src/api/teacherV3.ts `v3Api.classroom` + types/teacherV3.ts
V3ClassroomSession/Snapshot/JoinResult + classroomReducer.ts（前端投影权威）。

S16 审计修复（2026-09-07，Chief Agent）——本文件实现「事件投影层」：
1. SSE 帧格式对齐前端 reducer 权威契约：durable/live 一律 `event: event` +
   {seq,event_id,event_type,payload,created_at} 包装器（sse_channel frame="wrapped"）；
   事件名下划线化（participant.joined → participant_joined）。
2. 双观众通道分流（mock 逐订阅者投影的服务端等价物）：教师通道 session:{id}:t /
   学生通道 session:{id}:s——学生公布前拿不到分布/答案/点名姓名（G7 §12.5）。
3. snapshot 完整投影：questions（题面）+ activities.stats.my_submitted + class_name。
4. 推题服务端补全题面 config（stem_latex/options/answer）——学生端题面渲染的数据源。
5. 作答归一化：裸字符串/数字 → {choice: ...}（修复 H5 裸串 500 变砖）。
6. response_submitted 广播带活动全量投影（reducer 直接 upsert）。

鉴权三分（架构 §10）：教师端点 JWT；join 无 JWT 换课堂 token；学生端点课堂 token。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user, get_session
from ...core.errors import ApiError, ErrorCode
from ...core.hub import get_hub
from ...core.response import ok
from ...core.security import UserToken
from ...db.models import Classes
from ...domains.catalog.models import QuizQuestion
from . import service as cs
from .classroom_token import verify_classroom_token
from .models import (
    ClassroomActivity,
    ClassroomAiGeneration,
    ClassroomParticipant,
    ClassroomResponse,
    ClassroomSession,
)
from .service import ClassroomBusinessError

router = APIRouter()

_ERR_HTTP = {40400: ErrorCode.NOT_FOUND, 40401: ErrorCode.NOT_FOUND_RESOURCE, 40402: ErrorCode.NOT_FOUND_ITEM,
             40900: ErrorCode.CONFLICT, 42200: ErrorCode.VALIDATION, 42300: ErrorCode.CONFLICT}

_REVEALED_STATES = ("revealed", "completed")


def _biz_error(e: ClassroomBusinessError) -> ApiError:
    """B4 业务错误码 → 契约错误信封（非法状态转移返回业务错误码而非 500，G7）。"""
    code = _ERR_HTTP.get(int(e.code), ErrorCode.VALIDATION)
    return ApiError(code, str(e))


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise ApiError(ErrorCode.VALIDATION, f"非法 UUID: {value}")


def _assert_session_teacher(s: ClassroomSession, user) -> None:
    """会话属主校验（审计 #9）：课堂只属开课教师，他人管理端点 → 403。"""
    if str(s.teacher_id) != str(user.id):
        raise ApiError(ErrorCode.FORBIDDEN, "该课堂由另一位教师开设")


def _et(raw: str) -> str:
    """事件名下划线化（前端 reducer 契约：participant.joined → participant_joined）。"""
    return str(raw or "").replace(".", "_")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _require_session(session: AsyncSession, session_id: str) -> ClassroomSession:
    s = await session.get(ClassroomSession, _uuid(session_id))
    if s is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "课堂不存在")
    return s


async def _current_seq(session: AsyncSession, session_id: uuid.UUID) -> int:
    from ...db.models import ClassroomEvents

    return (await session.execute(
        select(ClassroomEvents.seq).where(ClassroomEvents.session_id == session_id)
        .order_by(ClassroomEvents.seq.desc()).limit(1)
    )).scalar() or 0


def _participant_contract(p: ClassroomParticipant) -> dict:
    return {
        "participant_id": str(p.id),
        "student_name": p.student_name,
        "anon_id": p.anon_id,
        "joined_at": p.joined_at.isoformat() if p.joined_at else None,
    }


def _ai_generation_contract(gen: ClassroomAiGeneration | None) -> dict | None:
    if gen is None:
        return None
    return {"status": gen.status, "content": gen.content, "verifier_result": gen.verifier_result}


async def _activity_contract(
    session: AsyncSession,
    a: ClassroomActivity,
    *,
    student: bool = False,
    participant_id: uuid.UUID | None = None,
) -> dict:
    """活动投影。分布/正确率：教师恒可见（L9 判定「大屏实时分布」）；
    学生仅 revealed/completed 后可见（G7 §12.5，mock projectEventForStudent 同口径）。"""
    revealed_for_audience = (not student) or a.status in _REVEALED_STATES
    stats = await cs.activity_stats(session, a.id, revealed=revealed_for_audience)
    # 学生投影：公布前剥答案（G7）；revealed 后下发 answer（H5 揭晓态「正确答案」数据源）
    if student:
        cfg = a.config or {}
        config = cfg if a.status in _REVEALED_STATES else cs._student_config(cfg)
    else:
        config = a.config
    item: dict = {
        "activity_id": str(a.id),
        "kind": a.kind,
        "question_id": str(a.question_id) if a.question_id else (a.config or {}).get("question_id"),
        "config": config,
        "ord": a.ord,
        "status": a.status,
        "stats": {
            "answered": stats["responded"],
            "distribution": stats["distribution"],
            "correct_rate": stats.get("correct_rate"),
        },
        "pushed_at": a.pushed_at.isoformat() if a.pushed_at else None,
        "locked_at": a.locked_at.isoformat() if a.locked_at else None,
    }
    if student and participant_id is not None:
        mine = (await session.execute(
            select(ClassroomResponse.id).where(
                ClassroomResponse.activity_id == a.id,
                ClassroomResponse.participant_id == participant_id,
            ).limit(1)
        )).first()
        item["stats"]["my_submitted"] = mine is not None
    gen = (await session.execute(
        select(ClassroomAiGeneration).where(ClassroomAiGeneration.activity_id == a.id)
        .order_by(ClassroomAiGeneration.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if gen is not None:
        item["ai_generation"] = _ai_generation_contract(gen)
    return item


async def _class_name(session: AsyncSession, class_id: uuid.UUID | None) -> str:
    if class_id is None:
        return ""
    row = (await session.execute(select(Classes.name).where(Classes.id == class_id))).scalar_one_or_none()
    return row or ""


async def _session_contract(
    session: AsyncSession,
    s: ClassroomSession,
    *,
    student: bool = False,
    participant_id: uuid.UUID | None = None,
) -> dict:
    participants = (await session.execute(
        select(ClassroomParticipant).where(ClassroomParticipant.session_id == s.id)
        .order_by(ClassroomParticipant.joined_at)
    )).scalars().all()
    activities = (await session.execute(
        select(ClassroomActivity).where(ClassroomActivity.session_id == s.id).order_by(ClassroomActivity.ord)
    )).scalars().all()
    return {
        "session_id": str(s.id),
        "class_id": str(s.class_id),
        "class_name": await _class_name(session, s.class_id),
        "join_code": s.join_code if not student else None,
        "status": s.status,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "participants": [_participant_contract(p) for p in participants],
        "activities": [
            await _activity_contract(session, a, student=student, participant_id=participant_id)
            for a in activities
        ],
        "online_count": len(participants),
    }


async def _question_briefs(session: AsyncSession, activities: list[ClassroomActivity]) -> list[dict]:
    """题面投影（snapshot.questions / activity_pushed.question）。答案不下发——公布语义只开分布。"""
    qids: list[uuid.UUID] = []
    for a in activities:
        qid = (a.config or {}).get("question_id")
        if qid:
            try:
                qids.append(uuid.UUID(str(qid)))
            except (ValueError, AttributeError):
                continue
    if not qids:
        return []
    rows = (await session.execute(select(QuizQuestion).where(QuizQuestion.id.in_(qids)))).scalars().all()
    return [
        {
            "question_id": str(q.id),
            "stem_latex": q.stem_latex,
            "options": list(q.options) if q.options else None,
            "q_type": q.q_type,
            "difficulty": q.difficulty,
        }
        for q in rows
    ]


async def _snapshot(
    session: AsyncSession,
    s: ClassroomSession,
    *,
    student: bool = False,
    participant_id: uuid.UUID | None = None,
) -> dict:
    from .models import ClassroomSummary

    acts = (await session.execute(
        select(ClassroomActivity).where(ClassroomActivity.session_id == s.id).order_by(ClassroomActivity.ord)
    )).scalars().all()
    summary_row = (await session.execute(
        select(ClassroomSummary).where(ClassroomSummary.session_id == s.id)
    )).scalar_one_or_none()
    return {
        "session": await _session_contract(session, s, student=student, participant_id=participant_id),
        "questions": await _question_briefs(session, acts),
        "seq": await _current_seq(session, s.id),
        "summary": ({"stats": summary_row.stats, "insight": summary_row.insight} if summary_row else None),
    }


async def _broadcast(
    session: AsyncSession,
    session_id: uuid.UUID,
    raw_event_type: str,
    teacher_payload: dict,
    student_payload: dict | None = None,
    *,
    teacher_only: bool = False,
) -> None:
    """双观众 live 广播（wrapped 帧契约）。seq 取当前权威 max（durable 已落 PG 兜底重放）。"""
    try:
        seq = await _current_seq(session, session_id)
        def wrap(payload: dict) -> dict:
            return {"seq": seq, "event_id": uuid.uuid4().hex, "event_type": _et(raw_event_type),
                    "payload": payload, "created_at": _now_iso()}
        hub = get_hub()
        await hub.broadcast_live(f"{session_id}:t", "event", wrap(teacher_payload))
        if not teacher_only:
            await hub.broadcast_live(f"{session_id}:s", "event", wrap(student_payload if student_payload is not None else teacher_payload))
    except Exception:  # live 丢失可容忍（架构 §7.2），不阻塞业务响应
        pass


async def _enrich_replay(
    session: AsyncSession,
    sid: uuid.UUID,
    rows: list,
    *,
    student: bool = False,
    participant_id: uuid.UUID | None = None,
) -> list[dict]:
    """durable 重放投影：PG 原始行 → 前端 reducer 契约形状（payload 里补全 activity/participant
    当前投影）。注意 snapshot 先行重置到 max seq，重放事件仅覆盖断连窗口，投影取当前态即幂等一致。"""
    acts = (await session.execute(
        select(ClassroomActivity).where(ClassroomActivity.session_id == sid)
    )).scalars().all()
    act_map = {str(a.id): a for a in acts}
    briefs = {b["question_id"]: b for b in await _question_briefs(session, acts)}

    async def act_contract(aid: str | None) -> dict | None:
        a = act_map.get(str(aid))
        if a is None:
            return None
        return await _activity_contract(session, a, student=student, participant_id=participant_id)

    out: list[dict] = []
    for ev in rows:
        et = _et(ev.event_type)
        if student and et == "teacher_call_random":
            continue  # 点名姓名不下发学生通道（G7）
        payload = dict(ev.payload or {})
        if et == "participant_joined":
            anon = payload.get("anon_id")
            p = (await session.execute(
                select(ClassroomParticipant).where(
                    ClassroomParticipant.session_id == sid,
                    ClassroomParticipant.anon_id == anon,
                )
            )).scalar_one_or_none()
            if p is not None:
                payload["participant"] = _participant_contract(p)
        elif et in ("activity_pushed", "activity_locked", "activity_revealed", "activity_completed"):
            aid = payload.get("activity_id")
            payload["activity"] = await act_contract(aid)
            a = act_map.get(str(aid))
            if a is not None:
                qid = (a.config or {}).get("question_id")
                payload["question"] = briefs.get(str(qid))
        elif et == "response_submitted":
            aid = payload.get("activity_id")
            payload["activity"] = await act_contract(aid)
        elif et == "summary_ready":
            from .models import ClassroomSummary

            srow = (await session.execute(
                select(ClassroomSummary).where(ClassroomSummary.session_id == sid)
            )).scalar_one_or_none()
            payload["summary"] = {"stats": srow.stats, "insight": srow.insight} if srow else None
        out.append({
            "seq": ev.seq,
            "event_id": str(ev.id),
            "event_type": et,
            "payload": payload,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        })
    return out


# ---------- 教师端点（教师 JWT） ----------

@router.post("/classroom/sessions")
async def create_session(
    request: Request,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """开课：created → open（join_code=6 位）。"""
    class_id = body.get("class_id")
    if not class_id:
        raise ApiError(ErrorCode.VALIDATION, "class_id 必填")
    try:
        teacher_id = uuid.UUID(user.id)
    except (ValueError, AttributeError, TypeError):
        raise ApiError(ErrorCode.VALIDATION, "token sub 非法")
    try:
        created = await cs.create_session(session, teacher_id=teacher_id, class_id=_uuid(class_id))
        opened = await cs.open_session(session, created.id)
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    await session.refresh(opened)
    data = await _session_contract(session, opened)
    data["topic"] = body.get("topic")
    data["class_name"] = body.get("class_name") or data.get("class_name")
    await _broadcast(session, opened.id, "session.opened", {"session": {"status": "open", **{k: data[k] for k in ("session_id", "class_name") if data.get(k)}}})
    return ok(data, request_id=request.state.request_id)


@router.post("/classroom/sessions/{session_id}/end")
async def end_session(
    request: Request,
    session_id: str,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """结课 → 归档 → 生成 classroom_summaries。"""
    s = await _require_session(session, session_id)
    _assert_session_teacher(s, user)
    try:
        summary = await cs.end_session(session, s.id)
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    data = await _session_contract(session, s)
    summary_payload = {"stats": summary.stats, "insight": summary.insight}
    data["summary"] = summary_payload
    await _broadcast(session, s.id, "session.ended", {"ended_at": s.ended_at.isoformat() if s.ended_at else None})
    await _broadcast(session, s.id, "summary.ready", {"summary": summary_payload})
    return ok(data, request_id=request.state.request_id)


async def _enrich_question_config(session: AsyncSession, kind: str, config: dict, question_id=None) -> dict:
    """推题服务端补全题面（S16 审计：此前 config 只带 duration，学生端无题面可渲染）。
    服务端单点写题面（含答案）——学生投影经公布门剥答案，单一事实源。"""
    if kind != "question":
        return config
    qid = question_id or config.get("question_id")
    if not qid:
        return config
    try:
        qrow = (await session.execute(
            select(QuizQuestion).where(QuizQuestion.id == _uuid(str(qid)))
        )).scalar_one_or_none()
    except ApiError:
        return config
    if qrow is None:
        return config
    enriched = dict(config or {})
    enriched.setdefault("question_id", str(qrow.id))
    enriched.setdefault("stem_latex", qrow.stem_latex)
    enriched.setdefault("options", list(qrow.options) if qrow.options else None)
    enriched.setdefault("answer", qrow.answer)
    return enriched


@router.post("/classroom/sessions/{session_id}/activities")
async def push_activity(
    request: Request,
    session_id: str,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    s = await _require_session(session, session_id)
    _assert_session_teacher(s, user)
    kind = body.get("kind")
    if kind not in ("question", "poll", "game", "photo_submit"):
        raise ApiError(ErrorCode.VALIDATION, "kind 非法")
    config = await _enrich_question_config(session, kind, body.get("config") or {}, body.get("question_id"))
    try:
        act = await cs.push_activity(
            session, s.id, kind=kind,
            question_id=_uuid(body["question_id"]) if body.get("question_id") else None,
            config=config,
        )
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    data = await _activity_contract(session, act)
    briefs = {b["question_id"]: b for b in await _question_briefs(session, [act])}
    question = briefs.get(str((act.config or {}).get("question_id") or ""))
    await _broadcast(session, s.id, "activity.pushed",
                     {"activity": data, "question": question},
                     {"activity": await _activity_contract(session, act, student=True), "question": question})
    return ok(data, request_id=request.state.request_id)


async def _transition(request: Request, session: AsyncSession, session_id: str, activity_id: str, to: str, user: UserToken) -> dict:
    s = await _require_session(session, session_id)
    _assert_session_teacher(s, user)
    try:
        act = await cs.set_activity_status(session, _uuid(activity_id), to)
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    data = await _activity_contract(session, act)
    await _broadcast(session, s.id, f"activity.{to}",
                     {"activity": data},
                     {"activity": await _activity_contract(session, act, student=True)})
    return ok(data, request_id=request.state.request_id)


@router.post("/classroom/sessions/{session_id}/activities/{activity_id}/lock")
async def lock_activity(request: Request, session_id: str, activity_id: str,
                        user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await _transition(request, session, session_id, activity_id, "locked", user)


@router.post("/classroom/sessions/{session_id}/activities/{activity_id}/reveal")
async def reveal_activity(request: Request, session_id: str, activity_id: str,
                          user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await _transition(request, session, session_id, activity_id, "revealed", user)


@router.post("/classroom/sessions/{session_id}/activities/{activity_id}/variation")
async def variation(
    request: Request,
    session_id: str,
    activity_id: str,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """AI 变式（AiGeneration 状态机）。当前触发到 generating 为止；LLM 内容生成走 B1 课堂 AI
    工具链（M3），未接通前诚实停留在 generating 并在 content.note 说明——不伪造变式题。"""
    s = await _require_session(session, session_id)
    _assert_session_teacher(s, user)
    try:
        gen = await cs.request_ai_generation(session, _uuid(activity_id), kind="variation")
        await cs.advance_ai_generation(session, gen.id, "generating")
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    payload = {"activity_id": str(activity_id), "ai_generation": _ai_generation_contract(gen)}
    await _broadcast(session, s.id, "ai_generation.updated", payload)
    return ok({
        "ai_generation": _ai_generation_contract(gen),
        "note": "AI 变式生成通道（B1 classroom_ai 工具链）未接通：状态停在 generating，不生成占位题目",
    }, request_id=request.state.request_id)


@router.post("/classroom/sessions/{session_id}/save-to-deck")
async def save_to_deck(
    request: Request,
    session_id: str,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """「存入课件」：课堂真实 deck 关联（L9 判定⑧——mock 时代为直接成功，此处为真实副作用）。"""
    s = await _require_session(session, session_id)
    _assert_session_teacher(s, user)
    try:
        deck = await cs.save_to_deck(
            session, s.id,
            title=str(body.get("title") or "课堂课件"),
            template_id=_uuid(body["template_id"]) if body.get("template_id") else None,
            created_by=uuid.UUID(user.id),
        )
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    return ok({"deck_id": str(deck.id), "title": deck.title}, request_id=request.state.request_id)


@router.post("/classroom/sessions/{session_id}/call-random")
async def call_random(
    request: Request,
    session_id: str,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    s = await _require_session(session, session_id)
    _assert_session_teacher(s, user)
    try:
        result = await cs.call_random(session, s.id)
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    # 点名结果只走教师通道（被点学生姓名不得广播给全班通道——G7 审计修复）
    await _broadcast(session, s.id, "teacher.call_random", {"participant": result}, teacher_only=True)
    return ok({"participant": result}, request_id=request.state.request_id)


@router.get("/classroom/sessions/{session_id}/snapshot")
async def snapshot(
    request: Request,
    session_id: str,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """权威快照（教师端重连恢复锚点）。学生视角走 student-stream（join + token）。"""
    s = await _require_session(session, session_id)
    _assert_session_teacher(s, user)
    return ok(await _snapshot(session, s), request_id=request.state.request_id)


# ---------- 学生端点（无 JWT / 课堂 token） ----------

@router.post("/classroom/join")
async def join(request: Request, body: dict, session: AsyncSession = Depends(get_session)) -> dict:
    """学生 H5 加入（无账号）：join_code + 姓名 → 课堂作用域短时 token。"""
    join_code = (body.get("join_code") or "").strip()
    student_name = (body.get("student_name") or "").strip()
    if not join_code or not student_name:
        raise ApiError(ErrorCode.VALIDATION, "join_code 与 student_name 必填")
    try:
        result = await cs.join(session, join_code=join_code, student_name=student_name)
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    p = (await session.execute(
        select(ClassroomParticipant).where(ClassroomParticipant.id == _uuid(result["participant_id"]))
    )).scalar_one_or_none()
    await _broadcast(session, _uuid(result["session_id"]), "participant.joined",
                     {"participant": _participant_contract(p) if p else None})
    return ok({
        "session_id": result["session_id"],
        "participant_id": result["participant_id"],
        "student_name": student_name,
        "token": result["token"],
    }, request_id=request.state.request_id)


def _normalize_answer(value) -> dict:
    """作答归一化（S16 审计：H5 裸字符串 "A" → service 聚合 .get('choice') 500 变砖）。
    标量 → {"choice": str}；dict 透传；其余 → 空对象交业务校验。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, int, float, bool)):
        return {"choice": str(value)}
    return {}


@router.post("/classroom/sessions/{session_id}/responses")
async def submit_response(
    request: Request,
    session_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """学生作答提交（课堂 token；幂等 UNIQUE(activity_id, participant_id, attempt_no)）。"""
    claims = _require_classroom_token(request, session_id)
    activity_id = body.get("activity_id")
    if not activity_id:
        raise ApiError(ErrorCode.VALIDATION, "activity_id 必填")
    s = await _require_session(session, session_id)
    image_key = body.get("image_key")
    if image_key and len(str(image_key)) > 256:
        raise ApiError(ErrorCode.VALIDATION, "photo_submit 请先经 presign 直传对象存储，body 只带对象 key（≤256 字符）")
    try:
        res = await cs.submit_response(
            session,
            _uuid(activity_id),
            _uuid(claims["participant_id"]),
            answer=_normalize_answer(body.get("answer")),
            image_key=image_key,
            attempt_no=int(body.get("attempt_no") or 1),
        )
        await session.commit()
    except ClassroomBusinessError as e:
        raise _biz_error(e)
    act = await session.get(ClassroomActivity, _uuid(activity_id))
    pid = _uuid(claims["participant_id"])
    data_teacher = await _activity_contract(session, act)
    data_student = await _activity_contract(session, act, student=True, participant_id=pid)
    await _broadcast(session, s.id, "response.submitted",
                     {"activity_id": activity_id, "participant_id": str(pid), "activity": data_teacher},
                     {"activity_id": activity_id, "participant_id": str(pid), "activity": data_student})
    return ok({"ok": True, "duplicate": not bool(getattr(res, "created", True)), "activity": data_student},
              request_id=request.state.request_id)


# ---------- SSE 双端流（wrapped 帧契约，双观众通道分流） ----------

@router.get("/classroom/sessions/{session_id}/stream")
async def teacher_stream_endpoint(
    request: Request,
    session_id: str,
    user: UserToken = Depends(get_current_user),
) -> dict:
    """教师 SSE：snapshot → durable 重放（Last-Event-ID）→ live 续接。教师 JWT 鉴权（红线：PII/答案不得无凭据可拉）。"""
    from ...core.sse import sse_channel
    from ...db.session import session_factory

    sid = _uuid(session_id)

    async def snapshot_fn() -> dict:
        async with session_factory()() as ss:
            s2 = await ss.get(ClassroomSession, sid)
            if s2 is None:
                return {"session": {"session_id": session_id, "status": "ended"}, "questions": [], "seq": 0, "summary": None}
            return await _snapshot(ss, s2)

    async def replay_fn(after_seq: int) -> list[dict]:
        async with session_factory()() as ss:
            rows = await cs.replay_events(ss, sid, after_seq=after_seq)
            return await _enrich_replay(ss, sid, rows, student=False)

    return await sse_channel(request, snapshot_fn=snapshot_fn, replay_fn=replay_fn,
                             live_channel=f"session:{sid}:t", frame="wrapped")


@router.get("/classroom/sessions/{session_id}/student-stream")
async def student_stream_endpoint(request: Request, session_id: str):
    """学生 SSE（学生视角投影；课堂 token 鉴权；点名姓名/未公布分布不出学生通道）。"""
    from ...core.sse import sse_channel
    from ...db.session import session_factory

    claims = _require_classroom_token(request, session_id)
    sid = _uuid(session_id)
    pid = _uuid(claims["participant_id"])

    async def snapshot_fn() -> dict:
        async with session_factory()() as ss:
            s2 = await ss.get(ClassroomSession, sid)
            if s2 is None:
                return {"session": {"session_id": session_id, "status": "ended"}, "questions": [], "seq": 0, "summary": None}
            return await _snapshot(ss, s2, student=True, participant_id=pid)

    async def replay_fn(after_seq: int) -> list[dict]:
        async with session_factory()() as ss:
            rows = await cs.replay_events(ss, sid, after_seq=after_seq)
            return await _enrich_replay(ss, sid, rows, student=True, participant_id=pid)

    return await sse_channel(request, snapshot_fn=snapshot_fn, replay_fn=replay_fn,
                             live_channel=f"session:{sid}:s", frame="wrapped")


# ---------- 鉴权辅助 ----------

def _require_classroom_token(request: Request, session_id: str) -> dict:
    """课堂作用域 token 校验（学生端点）。"""
    import os

    auth = request.headers.get("Authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        raise ApiError(ErrorCode.UNAUTHORIZED, "缺少课堂 token")
    try:
        claims = verify_classroom_token(token, secret=os.environ.get("JWT_SECRET", "dev-only-secret-not-for-production"), session_id=session_id)
    except Exception as e:
        raise ApiError(ErrorCode.UNAUTHORIZED, f"课堂 token 无效: {e}")
    if claims.get("session_id") != session_id:
        raise ApiError(ErrorCode.FORBIDDEN, "课堂 token 与会话不匹配")
    return claims
