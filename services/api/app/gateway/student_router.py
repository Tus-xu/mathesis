"""学生端路由（ADR-M2B-002 / SSOT §5.11-5.12）

端点：
- POST /api/student/error-records — 手动收录错题
- GET /api/student/error-records — 三视图查询
- PATCH /api/student/error-records/{id} — 改错因/备注（AI 初判必须学生可改）
- DELETE /api/student/error-records/{id} — 软删
- GET /api/student/error-records/review-plan — 间隔复习计划（今日到期+未来15天）
- POST /api/student/error-records/{id}/review — 复习完成推进（1/3/7/15，可回 forgotten）
- GET /api/student/warnings — 温和预警（F6 规则引擎）
- POST /api/student/practice/submit — 作答提交判分
- POST /api/student/practice/start — 开练（F5）
- GET /api/student/practice/daily — 今日一题
- GET /api/student/streak — 打卡
- GET /api/student/mastery/summary — 掌握度总览（F6）
- GET /api/student/mastery/trend — 掌握度曲线（日快照聚合）
- GET /api/student/knowledge-graph — 知识图谱（F7）
- GET /api/student/knowledge-graph/nodes/{kp_code} — 节点学习卡
- GET /api/student/daily-plan — 首页三卡（F8）
- GET /api/student/assignments — 任务列表（F10）
- GET /api/student/assignments/{id} — 任务详情
"""

from __future__ import annotations

import asyncio
import math
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import get_current_user
from app.models.class_member import ClassMember
from app.models.coursework import (
    Assignment,
    AssignmentTarget,
    DailyQuestion,
    ErrorRecord,
    MasteryRecord,
    Quiz,
    QuizItem,
    Streak,
    Submission,
    SubmissionItem,
)
from app.models.database import background_session_factory, get_db
from app.models.event import Event
from app.models.file import File, FileAsset
from app.models.knowledge_point import KnowledgePoint
from app.models.mastery_snapshot import MasterySnapshot
from app.models.tutor_session import TutorSession
from app.models.user import User
from app.models.user_profile import UserProfile
from app.providers.router import get_model_router
from app.providers.sandbox import check_equivalence
from app.services import fsrs
from app.services.error_record_assets import normalize_error_assets
from app.services.geogebra_figure import build_ggb_payload, generate_ggb, resolve_image_data_uri
from app.skills.question_supply import daily_ai_used, quiz_item_from_bank, supply_questions
from app.skills.smart_quiz.main import KP_MAP, generate_quiz_item

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/student", tags=["student"])

# 选择/判断/填空每题分值（quiz_items 无分值字段，M2 约定 10 分制）
_ITEM_SCORE = 10.0
# 错因五枚举（ADR-M2B-002）
_VALID_ERROR_TYPES = {"concept", "formula", "calculation", "logic", "reading"}
# 收录渠道三枚举（SSOT §5.11）
_VALID_SOURCE_CHANNELS = {"manual_photo", "auto_judge", "chat_command"}
# 作答题型三枚举（ADR-031）
_VALID_Q_TYPES = {"choice", "blank", "solution"}
# 交互类型契约（阶段 1：interaction_type 新增，q_type 兼容保留；前端优先读 interaction_type）
# 映射：choice → choice、blank → blank、solution → text（前端可作答类型）
_Q_TYPE_TO_INTERACTION = {"choice": "choice", "blank": "blank", "solution": "text"}
# 间隔复习推进间隔（天）：1/3/7/15（SSOT §6.3）
_REVIEW_INTERVALS = (1, 3, 7, 15)


# ==================== 错题去重（F-去重：同用户同题只留一条活动记录） ====================


async def _upsert_error_record(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    question_text: str,
    answer_text: str | None = None,
    source_channel: str = "auto_judge",
    error_type: str | None = None,
    kp_code: str | None = None,
    file_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    image: list | None = None,
    origin: str | None = None,
    note: str | None = None,
) -> tuple[ErrorRecord, bool]:
    """错题收录 upsert：同用户同题干（去空白后一致）只保留一条活动记录。

    命中既有记录：wrong_count +1、补全缺失字段（答案/错因/知识点/配图/复习排期），
    不新建行；未命中：新建。数据库唯一索引（m2_016）兜底并发竞态。
    origin（om5）来源细分：self_test/chat_quiz/socratic/mock_exam/variant/manual；
    命中既有行时仅在其 origin 为空时补写（首来源优先，不覆盖已有归因）。
    返回 (记录, 是否新建)。
    """
    norm = (question_text or "").strip() or "（题干未提供）"
    rs = await db.execute(
        select(ErrorRecord)
        .where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.deleted_at.is_(None),
            func.md5(func.btrim(ErrorRecord.question_text)) == func.md5(norm),
        )
        .order_by(ErrorRecord.created_at.asc())
    )
    existing = rs.scalars().first()
    if existing is not None:
        existing.wrong_count = int(existing.wrong_count or 1) + 1
        if not existing.answer_text and answer_text:
            existing.answer_text = answer_text
        if existing.error_type is None and error_type:
            existing.error_type = error_type
        if existing.kp_code is None and kp_code:
            existing.kp_code = kp_code
        if not existing.image and image:
            existing.image = normalize_error_assets(image)
        if existing.origin is None and origin:
            existing.origin = origin
        if note and str(note).strip():
            existing.note = str(note).strip()
        if existing.next_review_at is None:
            existing.next_review_at = datetime.now(UTC) + timedelta(days=_REVIEW_INTERVALS[0])
        await db.flush()
        return existing, False

    record = ErrorRecord(
        user_id=user_id,
        question_text=norm,
        answer_text=answer_text,
        source_channel=source_channel,
        error_type=error_type,
        kp_code=kp_code,  # "custom" 原样保留：列表页据此显示"综合练习"（_auto_record_error 调用前已自行剥除）
        file_id=file_id,
        conversation_id=conversation_id,
        message_id=message_id,
        ai_judged=False,
        next_review_at=datetime.now(UTC) + timedelta(days=_REVIEW_INTERVALS[0]),
        image=normalize_error_assets(image),
        origin=origin,
        note=note,
    )
    db.add(record)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # 并发竞态兜底（唯一索引已建时）：保存点回滚插入，按既有行累加
        rs2 = await db.execute(
            select(ErrorRecord)
            .where(
                ErrorRecord.user_id == user_id,
                ErrorRecord.deleted_at.is_(None),
                func.md5(func.btrim(ErrorRecord.question_text)) == func.md5(norm),
            )
            .order_by(ErrorRecord.created_at.asc())
        )
        race_winner = rs2.scalars().first()
        if race_winner is not None:
            race_winner.wrong_count = int(race_winner.wrong_count or 1) + 1
            await db.flush()
            return race_winner, False
        raise
    return record, True


# ==================== Schemas ====================


class ErrorRecordCreate(BaseModel):
    question_text: str
    answer_text: str | None = None
    file_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    error_type: str | None = None  # concept/formula/calculation/logic/reading
    kp_code: str | None = None
    source_channel: str  # manual_photo/auto_judge/chat_command
    note: str | None = None  # 学生自述错因/备注


class PracticeSubmitRequest(BaseModel):
    quiz_id: str | None = None
    assignment_id: str | None = None
    items: list[dict]  # [{item_no, q_type, answer_text?, file_id?}]
    client_submit_id: str


class PracticeStartRequest(BaseModel):
    mode: str  # special/retry/daily
    kp_code: str | None = None
    count: int | None = None  # 专题训练题量（special 可选，5~30，缺省 5）


class ErrorRecordPatch(BaseModel):
    error_type: str | None = None  # 五枚举之一；学生手动改错因（红线：AI 初判必须可改）
    note: str | None = None  # 学生备注


class ErrorReviewRequest(BaseModel):
    result: str | None = None  # remembered/forgotten；缺省按 remembered（原契约兼容）


# ==================== 错题本（F4） ====================


@router.post("/error-records")
async def create_error_record(
    req: ErrorRecordCreate,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """手动收录错题（SSOT §5.11）"""
    user_id = user["sub"]

    # source_channel 三枚举校验（SSOT §5.11）
    if req.source_channel not in _VALID_SOURCE_CHANNELS:
        return {"code": 40001, "message": f"无效收录渠道: {req.source_channel}"}

    # error_type 校验
    valid_types = {"concept", "formula", "calculation", "logic", "reading", None}
    if req.error_type not in valid_types:
        return {"code": 40001, "message": f"无效错因类型: {req.error_type}"}

    # ai_judged 语义 = "错因由 AI 判定"（SSOT §4.9）：学生手动带 error_type 收录时为 False；
    # error_type 为空 → AI 初判异步回填，回填完成时由 _async_error_analysis 置 true

    # 去重收录：同用户同题干只保留一条活动记录，重复收录累加 wrong_count
    record, created = await _upsert_error_record(
        db,
        uuid.UUID(user_id),
        question_text=req.question_text,
        answer_text=req.answer_text,
        source_channel=req.source_channel,
        error_type=req.error_type,
        kp_code=req.kp_code,
        file_id=uuid.UUID(req.file_id) if req.file_id else None,
        conversation_id=uuid.UUID(req.conversation_id) if req.conversation_id else None,
        message_id=uuid.UUID(req.message_id) if req.message_id else None,
        origin="manual",
        note=req.note,
    )
    await db.commit()

    # AI 初判异步回填（仅新收录且 error_type 为空时；重复收录不重复触发分析）
    if created and req.error_type is None:
        background.add_task(_async_error_analysis, str(record.id))

    return {
        "code": 0,
        "data": {
            "record_id": str(record.id),
            "error_type": record.error_type,
            "ai_judged": record.ai_judged,
            "created": created,
            "next_review_at": record.next_review_at.isoformat() if record.next_review_at else None,
        },
    }


# ==================== 学习事件总线（迭代15 L0-3） ====================


class LearningEventCreate(BaseModel):
    """学习事件（迭代15）：对话/练题/考试的判分结果统一上报入口。

    设计动机（迭代15 根因 C）：对话内判分原是纯前端行为，后端对"学生答了什么、
    对错如何"一无所知，导致错题本/学情/复习排期/薄弱点推荐全线断粮。
    本端点一个入口三个消费者：错题收录 + BKT 掌握度 + 复习排期。
    """

    kind: str = "quiz_judge"  # quiz_judge（v1 唯一类型；hint/mastered 预留）
    question_text: str
    options: list[str] | None = None
    answer: str | None = None  # 标准答案
    chosen: str | None = None  # 学生选择
    correct: bool
    kp_code: str | None = None
    kp_name: str | None = None
    error_type: str | None = None  # 可选初判错因；为空则 AI 异步回填
    source: str = "chat_quiz"  # chat_quiz/practice/exam
    conversation_id: str | None = None
    message_id: str | None = None
    image: list | None = None  # 题目配图（可选，错题快照）


_VALID_EVENT_SOURCES = {"chat_quiz", "practice", "exam"}


@router.post("/learning-events")
async def create_learning_event(
    req: LearningEventCreate,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学习事件总线：判分事件 → 错题收录 + 掌握度更新 + 复习排期（迭代15 L0-3）"""
    if req.kind != "quiz_judge":
        return {"code": 40001, "message": f"暂不支持的事件类型: {req.kind}"}
    if req.source not in _VALID_EVENT_SOURCES:
        return {"code": 40001, "message": f"无效事件来源: {req.source}"}

    user_id = uuid.UUID(user["sub"])
    record_id: str | None = None
    error_recorded = False
    next_review: datetime | None = None

    # 知识点编码统一解析（错题归档与掌握度共用同一编码，迭代15）
    resolved_kp = await _resolve_kp_code(db, req.kp_code, req.kp_name)

    # 消费者 1：答错 → 错题收录（去重 upsert：同用户同题干全时段唯一，
    # 重复答错累加 wrong_count，不再同日/跨日重复建行）
    if not req.correct:
        _origin = {"chat_quiz": "chat_quiz", "practice": "self_test", "exam": "mock_exam"}.get(
            req.source, "chat_quiz"
        )
        record, created = await _upsert_error_record(
            db,
            user_id,
            question_text=req.question_text,
            answer_text=req.answer or "",
            source_channel="auto_judge",
            error_type=req.error_type,
            kp_code=resolved_kp or req.kp_code,
            conversation_id=uuid.UUID(req.conversation_id) if req.conversation_id else None,
            message_id=uuid.UUID(req.message_id) if req.message_id else None,
            image=req.image or [],
            origin=_origin,
        )
        record_id = str(record.id)
        next_review = record.next_review_at
        error_recorded = created
        if created and req.error_type is None:
            background.add_task(_async_error_analysis, record_id)

    # 消费者 2：BKT 掌握度后验更新（先解析 AI 侧编码到学情侧编码，迭代15）
    mastery_updated = False
    if resolved_kp:
        mastery_updated = await _update_mastery(db, user_id, resolved_kp, correct=req.correct)

    # 消费者 3：学习活跃度打卡（任何判分都算当日学习行为）
    await _upsert_streak(db, user_id)

    await db.commit()

    # 消费者 4（迭代15 B8 · L1-5 连续受挫干预）：连错计数（Redis，fail-open）——
    # 连错 ≥2 时 smart_quiz 出题自动降一档并侧重基础巩固（services/quiz_streak.py）
    from app.services.quiz_streak import bump_quiz_wrong_streak

    await bump_quiz_wrong_streak(user_id, correct=req.correct)

    # 消费者 5（迭代17 AI 管家）：判分事件 → 管家中枢（best-effort，失败不阻塞判分主链路）
    try:
        from app.butler.event_bus import get_event_bus
        from app.butler.orchestrator import get_orchestrator

        bus = get_event_bus()
        ev = await bus.emit(
            db,
            user_id=user_id,
            event_type="quiz_judge",
            source_type=req.source,
            source_id=record_id,
            payload={"correct": req.correct, "kp_code": resolved_kp, "error_type": req.error_type},
        )
        if ev is not None:
            await get_orchestrator().dispatch(db, ev)
        await db.commit()
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.info("butler_event_emit_fail", error=str(e)[:150])

    return {
        "code": 0,
        "data": {
            "error_recorded": error_recorded,
            "record_id": record_id,
            "mastery_updated": mastery_updated,
            "next_review_at": next_review.isoformat() if next_review else None,
        },
    }


@router.post("/error-records/{record_id}/review")
async def complete_error_review(
    record_id: uuid.UUID,
    req: ErrorReviewRequest | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """间隔复习完成推进（SSOT §6.3：next_review_at 按 1/3/7/15 天推进，复习完成后服务端更新）

    result=forgotten：重置回第 1 天间隔、复习进度清零重走；
    remembered（缺省，原契约兼容）：正常推进，走完 15 天档（review_count≥4）置 null 毕业。
    """
    user_id = uuid.UUID(user["sub"])
    record = await db.get(ErrorRecord, record_id)
    # 越权不泄露存在性（SSOT §5.0 纪律）
    if record is None or record.deleted_at or record.user_id != user_id:
        return {"code": 40400, "message": "错题记录不存在"}

    result = (req.result if req else None) or "remembered"
    if result not in ("remembered", "forgotten"):
        return {"code": 40001, "message": f"非法复习结果: {result}，仅支持 remembered/forgotten"}

    graduated = False
    if result == "forgotten":
        # 没记住：重置回第 1 天间隔重新走（review_count 清零）
        record.review_count = 0
        record.next_review_at = datetime.now(UTC) + timedelta(days=_REVIEW_INTERVALS[0])
        # M2 迭代16 第二批：forgotten 计一次答错（estimate_stability 答错惩罚使 S 自然下降）
        record.wrong_count = (record.wrong_count or 0) + 1
    else:
        record.review_count = (record.review_count or 0) + 1
        if record.review_count >= len(_REVIEW_INTERVALS):
            # 走完 1/3/7/15 全部档位 → 毕业，不再排期
            record.next_review_at = None
            graduated = True
        else:
            # 收录时已排首档（1 天）；第 n 次复习完成后推进到第 n+1 档
            record.next_review_at = datetime.now(UTC) + timedelta(
                days=_REVIEW_INTERVALS[record.review_count]
            )

    # M2 迭代16 第二批：FSRS write-path —— 复习完成时回填稳定度缓存列（不动 1/3/7/15 排期）
    record.fsrs_stability = fsrs.estimate_stability(
        record.review_count or 0, record.wrong_count or 1
    )
    record.fsrs_retrievability = 1.0  # 刚复习完，可提取性视为满值
    record.fsrs_computed_at = datetime.now(UTC)
    # 事件埋点（随同一事务 commit）：review_done 复习结果 + loop_step_done 闭环 record 步
    db.add(
        Event(
            user_id=user_id,
            event="review_done",
            props={
                "record_id": str(record.id),
                "result": result,
                "kp_code": record.kp_code,
            },
        )
    )
    db.add(
        Event(
            user_id=user_id,
            event="loop_step_done",
            props={
                "step": "record",
                "record_id": str(record.id),
                "kp_code": record.kp_code,
            },
        )
    )
    await db.commit()

    # 迭代21 数据回流（补齐 butler/event_bus.py:9 docstring 承诺的挂点）：复习完成
    # → learning_events.error_reviewed（FSRS 记忆曲线/到期队列的驱动数据源）。
    # 沿用 create_learning_event 消费者 5 的 best-effort 纪律：主事务已 commit，
    # 事件落库失败只回滚事件本身，不影响复习推进结果。
    try:
        from app.butler.event_bus import get_event_bus

        ev = await get_event_bus().emit(
            db,
            user_id=user_id,
            event_type="error_reviewed",
            source_type="review",
            source_id=str(record.id),
            payload={
                "record_id": str(record.id),
                "result": result,
                "kp_code": record.kp_code,
                "review_count": int(record.review_count or 0),
                "stability": float(record.fsrs_stability or 0.0),
                "graduated": graduated,
            },
        )
        if ev is not None:
            await db.commit()
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.info("butler_event_emit_fail", event_type="error_reviewed", error=str(e)[:150])

    return {
        "code": 0,
        "data": {
            "record_id": str(record.id),
            "review_count": record.review_count,
            "next_review_at": record.next_review_at.isoformat() if record.next_review_at else None,
            "graduated": graduated,
            # M2 迭代16 第二批：FSRS 增量字段（纯增量，不破坏既有契约）
            "fsrs_stability": float(record.fsrs_stability),
            "memory_level": fsrs.fsrs_level(record.fsrs_stability),
        },
    }


@router.get("/error-records")
async def list_error_records(
    view: str = Query(default="time", pattern="^(kp|time|error_type)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """三视图查询错题（SSOT §5.11）"""
    user_id = uuid.UUID(user["sub"])

    query = select(ErrorRecord).where(
        ErrorRecord.user_id == user_id,
        ErrorRecord.deleted_at.is_(None),
    )

    # 排序
    if view == "time":
        query = query.order_by(ErrorRecord.created_at.desc())
    elif view == "kp":
        query = query.order_by(ErrorRecord.kp_code, ErrorRecord.created_at.desc())
    elif view == "error_type":
        query = query.order_by(ErrorRecord.error_type, ErrorRecord.created_at.desc())

    # 分页
    total_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_q)).scalar() or 0

    query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    records = result.scalars().all()

    # 批量取知识点名称（孤儿码如 e2e_xxx/wp_xxx 不在 KP 表 → kp_name=None 由前端兜底；
    # "custom" 为对话出题无归属兜底码，映射显示名"综合练习"）
    kp_name_map = await _kp_name_map(db, [r.kp_code for r in records])

    items = [
        {
            "record_id": str(r.id),
            "question_text": _safe_latex_truncate(r.question_text, 200),
            "answer_text": r.answer_text[:100] if r.answer_text else None,
            "error_type": r.error_type,
            "kp_code": r.kp_code,
            "kp_name": kp_name_map.get(r.kp_code),
            "source_channel": r.source_channel,
            "next_review_at": r.next_review_at.isoformat() if r.next_review_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]

    return {"code": 0, "data": {"total": total, "items": items}}


@router.get("/error-records/review-plan")
async def error_review_plan(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """间隔复习计划（SSOT §6.3）：今日到期 + 未来 15 天按日聚合"""
    user_id = uuid.UUID(user["sub"])
    now = datetime.now(UTC)

    # 在册 = 未软删且未毕业（next_review_at 非空）
    active_cond = (
        ErrorRecord.user_id == user_id,
        ErrorRecord.deleted_at.is_(None),
        ErrorRecord.next_review_at.is_not(None),
    )

    # 今日到期（含已过期未复习的）
    due_q = (
        select(ErrorRecord)
        .where(*active_cond, ErrorRecord.next_review_at <= now)
        .order_by(ErrorRecord.next_review_at)
    )
    due_records = (await db.execute(due_q)).scalars().all()

    kp_name_map = await _kp_name_map(db, [r.kp_code for r in due_records[:10]])
    due_items = [
        {
            "record_id": str(r.id),
            "question_text": r.question_text,  # om5：复习卡题干全量（截断导致"题目没写全"投诉）
            "kp_code": r.kp_code,
            "kp_name": kp_name_map.get(r.kp_code),
            "review_count": r.review_count or 0,
            # om5：带原题附件（拍照/题库图），复习"如图"题时引导链路能拿到原图
            "file_id": str(r.file_id) if r.file_id else None,
        }
        for r in due_records[:10]
    ]

    # 未来 15 天按日聚合
    horizon = now + timedelta(days=15)
    upcoming_rows = await db.execute(
        select(func.date(ErrorRecord.next_review_at), func.count())
        .where(
            *active_cond, ErrorRecord.next_review_at > now, ErrorRecord.next_review_at <= horizon
        )
        .group_by(func.date(ErrorRecord.next_review_at))
        .order_by(func.date(ErrorRecord.next_review_at))
    )
    upcoming = [{"date": str(d), "count": c} for d, c in upcoming_rows.all()]

    total_q = select(func.count()).select_from(ErrorRecord).where(*active_cond)
    total_active = (await db.execute(total_q)).scalar() or 0

    return {
        "code": 0,
        "data": {
            "due_today": len(due_records),
            "due_items": due_items,
            "upcoming": upcoming,
            "total_active": total_active,
        },
    }


@router.patch("/error-records/{record_id}")
async def update_error_record(
    record_id: uuid.UUID,
    req: ErrorRecordPatch,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题修改：学生手动改错因（红线：AI 初判必须可改）+ 备注"""
    user_id = uuid.UUID(user["sub"])
    record = await db.get(ErrorRecord, record_id)
    # 越权不泄露存在性（SSOT §5.0 纪律）
    if record is None or record.deleted_at or record.user_id != user_id:
        return {"code": 40400, "message": "错题记录不存在"}

    if req.error_type is None and req.note is None:
        return {"code": 40001, "message": "无可更新字段（error_type/note 至少一项）"}
    if req.error_type is not None and req.error_type not in _VALID_ERROR_TYPES:
        return {"code": 40001, "message": f"无效错因类型: {req.error_type}"}

    if req.error_type is not None:
        old_error_type = record.error_type
        record.error_type = req.error_type
        # 学生手动改错因：ai_judged 置 False 并留痕（SSOT §4.9 红线）
        record.ai_judged = False
        record.corrected_by_user = True
        db.add(
            Event(
                user_id=user_id,
                event="error_type_corrected",
                props={
                    "record_id": str(record.id),
                    "old_error_type": old_error_type,
                    "new_error_type": req.error_type,
                },
            )
        )
    if req.note is not None:
        record.note = req.note
    await db.commit()

    return {
        "code": 0,
        "data": {
            "record_id": str(record.id),
            "error_type": record.error_type,
            "ai_judged": record.ai_judged,
            "corrected_by_user": record.corrected_by_user,
            "note": record.note,
        },
    }


@router.delete("/error-records/{record_id}")
async def delete_error_record(
    record_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题删除（软删）"""
    user_id = uuid.UUID(user["sub"])
    record = await db.get(ErrorRecord, record_id)
    # 越权不泄露存在性（SSOT §5.0 纪律）
    if record is None or record.deleted_at or record.user_id != user_id:
        return {"code": 40400, "message": "错题记录不存在"}

    record.deleted_at = datetime.now(UTC)
    await db.commit()

    return {"code": 0, "data": {"record_id": str(record.id), "deleted": True}}


@router.post("/error-records/{record_id}/figure")
async def create_error_record_figure(
    record_id: uuid.UUID,
    force: bool = False,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题本动态图形：由错题题干（OCR/拍照入本文本）生成 GeoGebra 交互构造并持久化。

    - 已有 ggb 且未 force → 直接返回既有（幂等）；
    - force=true 或缺失 → AI 重新生成，写入 record.image（保留原有 data-URI 配图）。
    - best-effort：生成失败返回 50400，前端降级静态图，不影响错题主体。
    """
    user_id = uuid.UUID(user["sub"])
    record = await db.get(ErrorRecord, record_id)
    if record is None or record.deleted_at or record.user_id != user_id:
        return {"code": 40400, "message": "错题记录不存在"}

    image = normalize_error_assets(record.image)
    existing_ggb = next((e for e in image if isinstance(e, dict) and e.get("type") == "ggb"), None)
    if existing_ggb and not force:
        return {"code": 0, "data": {"ggb": existing_ggb, "generated": False}}

    # 若有原图照片，读图供视觉通道（MathMover 内核：AI 看图理解几何约束后精确重建）
    image_data_uri = (
        await resolve_image_data_uri(db, record.file_id, user_id) if record.file_id else None
    )
    ggb = await generate_ggb(
        record.question_text,
        figure_hint=record.note,
        interactive=True,
        image_data_uri=image_data_uri,
        user_id=str(user_id),
        db=db,
    )
    if not ggb:
        return {"code": 50400, "message": "动态图形生成失败，请稍后重试"}

    payload = build_ggb_payload(ggb["commands"], ggb["view"], caption=record.note or "")
    image = [e for e in image if not (isinstance(e, dict) and e.get("type") == "ggb")]
    image.append(payload)
    record.image = normalize_error_assets(image)
    await db.commit()

    return {"code": 0, "data": {"ggb": payload, "generated": True}}


# ==================== 作答提交判分（F3） ====================


@router.post("/practice/submit")
async def practice_submit(
    req: PracticeSubmitRequest,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """作答提交判分（SSOT §5.12 / ADR-031）"""
    user_id = uuid.UUID(user["sub"])

    # 入参三重校验（API §9.3：空 items / 非法 q_type / 无任何归属 → 40001）
    if not req.items:
        return {"code": 40001, "message": "items 不能为空"}
    for it in req.items:
        if it.get("q_type") not in _VALID_Q_TYPES:
            return {"code": 40001, "message": f"非法题型: {it.get('q_type')}"}
    # quiz_id 安全解析：对话内 AI 出题的临时题组 id（如 local_*）非 UUID，
    # 无 DB 归属行，按"无 quiz 归属"场景处理（掌握度回填靠 item.kp_code，标准答案靠 item.expected_answer）
    quiz_uuid: uuid.UUID | None = None
    if req.quiz_id:
        try:
            quiz_uuid = uuid.UUID(req.quiz_id)
        except ValueError:
            quiz_uuid = None

    # assignment_id 安全解析：非法 UUID → 40001（不 500）
    assignment_uuid: uuid.UUID | None = None
    if req.assignment_id:
        try:
            assignment_uuid = uuid.UUID(req.assignment_id)
        except ValueError:
            return {"code": 40001, "message": f"非法 assignment_id: {req.assignment_id}"}

    assignment: Assignment | None = None
    if assignment_uuid is not None:
        assignment = await _visible_assignment(db, user_id, assignment_uuid)
        if assignment is None:
            return {"code": 40400, "message": "任务不存在"}
        if assignment.deadline and assignment.deadline < datetime.now(UTC):
            return {"code": 40901, "message": "任务已截止"}
        # tel D2/A2：type 门扩展——pre_class/homework 与 quiz 同样强制绑定
        # assignment.quiz_id（quiz 已物化，assessment.py:354），否则提交会跳过
        # quiz 归属校验、判分回退客户端 expected_answer（越权判分面）。
        if assignment.type in ("quiz", "pre_class", "homework"):
            if assignment.quiz_id is None:
                return {"code": 40901, "message": "任务未绑定题组"}
            if quiz_uuid is not None and quiz_uuid != assignment.quiz_id:
                return {"code": 40001, "message": "quiz_id 与任务不匹配"}
            quiz_uuid = assignment.quiz_id

    # 归属校验：quiz_id/assignment_id 均缺且 items 无 kp_code 时无法掌握度回填（ADR-037 无 quiz 归属场景靠 item.kp_code）
    if (
        quiz_uuid is None
        and assignment_uuid is None
        and not any(it.get("kp_code") for it in req.items)
    ):
        return {"code": 40001, "message": "quiz_id 与 assignment_id 均缺时，items 须携带 kp_code"}

    # 归属校验（迭代06 审计修复）：quiz_id 存在时须属于当前用户，否则 40400（越权写数据防护）
    quiz = None
    if quiz_uuid is not None:
        quiz = await db.get(Quiz, quiz_uuid)
        if (
            quiz is None
            or quiz.deleted_at
            or (assignment is None and str(quiz.user_id) != str(user_id))
        ):
            return {"code": 40400, "message": "题组不存在"}

    # 模拟试卷分值规格解析（P0-1 突破：150 分制判分不再被 10 分制架空）：
    # Quiz.source = "exam:{type}" 时按卷型逐题型取 score_each（choice/blank=5、solution=19 等），
    # 试卷提交总分与试卷标注满分对齐；非试卷题组保持 10 分制不变。
    exam_score_map: dict[str, float] | None = None
    if quiz is not None and quiz.source and quiz.source.startswith("exam:"):
        try:
            from app.skills.mock_exam import EXAM_SPECS

            exam_type = quiz.source.split(":", 1)[1]
            spec = EXAM_SPECS.get(exam_type)
            if spec:
                exam_score_map = {qt: float(sc) for qt, _n, sc in spec["groups"]}
        except Exception as e:
            logger.warning("exam_score_map_failed", error=str(e)[:100])

    # 幂等检查（重放返回已存判分结果——ADR-036 双口径补强：
    # 不重复判分/收录/打卡，但客户端重试必须能拿到首次的真实结果，而非空载荷）
    existing = await db.execute(
        select(Submission).where(
            Submission.user_id == user_id,
            Submission.client_submit_id == req.client_submit_id,
            Submission.deleted_at.is_(None),
        )
    )
    existing_sub = existing.scalar_one_or_none()
    if existing_sub is not None:
        items_result = await db.execute(
            select(SubmissionItem)
            .where(
                SubmissionItem.submission_id == existing_sub.id,
                SubmissionItem.deleted_at.is_(None),
            )
            .order_by(SubmissionItem.item_no)
        )
        replay_results = []
        for si in items_result.scalars().all():
            entry: dict = {"item_no": si.item_no, "verdict": si.verdict, "score": si.score}
            if si.error_type:
                entry["error_type"] = si.error_type
            replay_results.append(entry)
        return {
            "code": 0,
            "data": {
                "submission_id": str(existing_sub.id),
                "results": replay_results,
                "mastery_updated": False,
                "replayed": True,
            },
        }

    # 创建 submission
    submission = Submission(
        user_id=user_id,
        quiz_id=quiz_uuid,
        assignment_id=assignment_uuid,
        client_submit_id=req.client_submit_id,
        status="graded",
    )
    db.add(submission)
    await db.flush()
    # asyncpg + SQLAlchemy：commit 后实例属性会 expire，之后再访问会尝试同步 IO
    # （MissingGreenlet → 500）。提交事实的主键在入事务时就固定，先取值再 commit。
    submission_id = submission.id

    # 预取题组题目（item_no → QuizItem），判分与掌握度都依赖标准答案/知识点
    quiz_items: dict[int, QuizItem] = {}
    if quiz_uuid is not None:
        rows = await db.execute(
            select(QuizItem).where(
                QuizItem.quiz_id == quiz_uuid,
                QuizItem.deleted_at.is_(None),
            )
        )
        quiz_items = {i.item_no: i for i in rows.scalars().all()}
        requested_numbers = [int(item.get("item_no", 0)) for item in req.items]
        if len(requested_numbers) != len(set(requested_numbers)):
            return {"code": 40001, "message": "题号不能重复"}
        if any(number not in quiz_items for number in requested_numbers):
            return {"code": 40001, "message": "提交包含不属于该题组的题目"}
        for item in req.items:
            expected_type = quiz_items[int(item.get("item_no", 0))].q_type
            if item.get("q_type") != expected_type:
                return {"code": 40001, "message": "题型与题组不匹配"}

    for item in req.items:
        raw_file_id = item.get("file_id")
        if not raw_file_id:
            continue
        try:
            file_uuid = uuid.UUID(str(raw_file_id))
        except ValueError:
            return {"code": 40001, "message": "非法 file_id"}
        owned_file = await db.get(File, file_uuid)
        if owned_file is None or owned_file.deleted_at is not None or owned_file.user_id != user_id:
            return {"code": 40400, "message": "文件不存在"}

    # 多图附件校验（图片作业上传）：每项可携带多个 file_id，逐一核验归属
    for item in req.items:
        for raw_att in item.get("attachments") or []:
            att_file_id = raw_att if isinstance(raw_att, str) else (raw_att or {}).get("file_id")
            if not att_file_id:
                continue
            try:
                att_uuid = uuid.UUID(str(att_file_id))
            except (ValueError, AttributeError, TypeError):
                return {"code": 40001, "message": "非法附件 file_id"}
            owned_att = await db.get(File, att_uuid)
            if (
                owned_att is None
                or owned_att.deleted_at is not None
                or owned_att.user_id != user_id
            ):
                return {"code": 40400, "message": "附件文件不存在"}

    results = []
    total_score = 0.0
    has_pending = False
    mastery_updated_count = 0  # 真实发生掌握度更新的题数（mastery_updated 语义对齐，C-P2-5）
    # 迭代21 数据回流：逐题判分事件先收集、主 commit 后统一 emit
    # （bus.emit 内部失败会 rollback 整个 session，循环中途 emit 会连已判题一起回滚，
    #  故与 create_learning_event 消费者 5 同纪律：主事务落定后再落事件）
    judged_items: list[dict] = []

    for item in req.items:
        item_no = item.get("item_no", 0)
        q_type = item.get("q_type", "choice")
        answer_text = item.get("answer_text")
        file_id = item.get("file_id")
        quiz_item = quiz_items.get(item_no)

        # 解答题拍照作答（迭代10 v1.4）：answer_text 空 + file_id 存在时，
        # 先回填 OCR 产物再判分；OCR 无产物（解析中/失败）走 pending_review 占位，
        # 不再直接判 wrong 0 分污染错题本与掌握度
        if q_type == "solution" and not (answer_text or "").strip() and file_id:
            answer_text = await _load_file_ocr_text(db, file_id, user_id)
            if answer_text:
                item["answer_text"] = answer_text

        # 多图附件归一化（图片作业上传）：校验通过后读取文件元数据与 OCR 产物
        att_objs: list[dict] = []
        for raw_att in item.get("attachments") or []:
            att_file_id = raw_att if isinstance(raw_att, str) else (raw_att or {}).get("file_id")
            if not att_file_id:
                continue
            att_uuid = uuid.UUID(str(att_file_id))
            owned_att = await db.get(File, att_uuid)
            ocr_text = None
            if owned_att is not None and owned_att.file_type == "image":
                ocr_text = await _load_file_ocr_text(db, str(att_uuid), user_id)
            att_objs.append(
                {
                    "file_id": str(att_uuid),
                    "filename": owned_att.filename if owned_att else "attachment",
                    "mime": owned_att.mime if owned_att else "image/jpeg",
                    "status": "ready" if ocr_text else "photo",
                    "ocr_text": ocr_text,
                }
            )

        # 纯图片作答回填：任何题型只要附了图且没填文字，就用首张图 OCR 文本兜底（不判 0 分）
        if not (answer_text or "").strip() and att_objs:
            backfill = next((a.get("ocr_text") for a in att_objs if a.get("ocr_text")), None)
            if backfill:
                answer_text = backfill
                item["answer_text"] = backfill

        # 判分逻辑（choice 比对标答 / blank SymPy 等价 / solution AI 初批）
        # 无 DB 归属题（对话内 AI 出题）时，标准答案/题干取客户端随题卡携带的字段
        # 满分口径：作业题以 quiz_item.max_score 为准（组卷发布 选择/填空 5 分、解答 12 分）；
        # 模拟试卷按卷型分值规格；两者都没有才回退 10 分制。
        # 旧实现只认 exam_score_map，作业选择题按 10 分判 → 学生看到 10 分但 max_score=5，
        # 教师终审被 final_score_exceeds_question_maximum 拒绝。
        item_max = (
            float(quiz_item.max_score)
            if quiz_item is not None and quiz_item.max_score is not None
            else (exam_score_map.get(q_type) if exam_score_map else None)
        )
        if assignment_uuid is not None and q_type == "solution":
            # 教师发布作业的主观题直接进入人工复核，提交事务不依赖任何外部 AI。
            verdict, score, extra = (
                "pending_review",
                None,
                {
                    "degraded": "teacher_review",
                    "ai_pregraded": False,
                },
            )
        elif q_type == "solution" and file_id and not (answer_text or "").strip():
            verdict, score, extra = "pending_review", None, {"degraded": "ocr_pending"}
        elif (file_id or att_objs) and not (answer_text or "").strip():
            # 纯图片作答（不限于解答题）：有图无文字 → 教师人工复核，不判 0 分
            verdict, score, extra = "pending_review", None, {"degraded": "photo_attached"}
        else:
            verdict, score, extra = await _grade_item(
                db,
                quiz_item,
                q_type,
                answer_text,
                user_id=user_id,
                expected_answer=item.get("expected_answer") if quiz_item is None else None,
                question_text=item.get("question_text") if quiz_item is None else None,
                max_score=item_max,
            )

        sub_item = SubmissionItem(
            submission_id=submission.id,
            item_no=item_no,
            q_type=q_type,
            answer_text=answer_text,
            file_id=uuid.UUID(file_id) if file_id else None,
            attachments=att_objs,
            verdict=verdict,
            score=score,
            ai_pregraded=bool(extra.get("ai_pregraded")),
            error_type=extra.get("error_type"),
        )
        db.add(sub_item)

        if score is not None:
            total_score += score
        if verdict == "pending_review":
            has_pending = True
        else:
            # 掌握度后验更新（按题目关联知识点；pending_review 不计入对错）
            kp_code = quiz_item.kp_code if quiz_item else item.get("kp_code")
            if kp_code:
                await _update_mastery(
                    db,
                    user_id,
                    kp_code,
                    correct=(verdict == "correct"),
                    hint_count=int(item.get("hint_count") or 0),
                )
                mastery_updated_count += 1
            # 迭代21 数据回流：收集判分事实（pending_review 不算"已判"，与掌握度口径一致）
            judged_items.append(
                {
                    "item_no": int(item_no),
                    "correct": verdict == "correct",
                    "kp_code": kp_code,
                    "score": float(score) if score is not None else None,
                }
            )

        entry: dict = {"item_no": item_no, "verdict": verdict, "score": score}
        if extra.get("error_type"):
            entry["error_type"] = extra["error_type"]
        if extra.get("comment"):
            entry["comment"] = extra["comment"]
        if extra.get("degraded"):
            entry["degraded"] = extra["degraded"]
        results.append(entry)

        # 错题自动收录
        if verdict == "wrong":
            if assignment_uuid is not None:
                _origin = "assignment"
            elif quiz is not None and (quiz.source or "").startswith("exam:"):
                _origin = "mock_exam"
            elif quiz is not None and (quiz.source or "") == "retry":
                _origin = "retry"
            elif quiz is None:
                _origin = "chat_quiz"  # 无 DB 归属：对话内出题/变式卡
            else:
                _origin = "self_test"
            await _auto_record_error(
                db, user_id, item, sub_item, quiz_item, background, origin=_origin
            )

    submission.total_score = total_score
    submission.status = "pending_review" if has_pending else "graded"

    # 打卡：当日有提交即计连续天数
    await _upsert_streak(db, user_id)
    # weak_points 联动更新（SSOT §5.12：提交成功即触发，补 M1 只读缺口）
    await _update_weak_points(db, user_id)
    await db.commit()

    # 迭代21 数据回流（E 项最大缺口：本路由原先完全不写 learning_events）：判分成功
    # → 逐题 quiz_judge 事件（幂等键 submission_id:item_no，learning_events 幂等列天然
    # 防重放）。best-effort：事件落库失败只回滚事件，不影响判分主结果。
    if judged_items:
        try:
            from app.butler.event_bus import get_event_bus

            bus = get_event_bus()
            for judged in judged_items:
                await bus.emit(
                    db,
                    user_id=user_id,
                    event_type="quiz_judge",
                    source_type="practice",
                    source_id=str(submission_id),
                    payload=judged,
                    idempotency_key=f"{submission_id}:{judged['item_no']}:quiz_judge",
                )
            await db.commit()
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            logger.info(
                "butler_event_emit_fail",
                event_type="quiz_judge",
                submission_id=str(submission_id),
                error=str(e)[:150],
            )

    return {
        "code": 0,
        "data": {
            "submission_id": str(submission_id),
            "results": results,
            "mastery_updated": mastery_updated_count > 0,
        },
    }


# ==================== 刷题（F5） ====================


@router.get("/practice/kp-catalog")
async def practice_kp_catalog(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """特定知识点任选目录：题库有题的教材章节（om5 练题中心"任选知识点"）"""
    rows = await db.execute(
        text(
            "SELECT k.code, k.name, count(b.id) AS bank_count "
            "FROM knowledge_points k "
            "JOIN question_bank b ON b.deleted_at IS NULL AND b.scope = 'student' "
            "  AND (b.source_batch IS NULL OR b.source_batch <> 'cmm_math_v1') "
            "  AND k.code = ANY (b.kp_codes) "
            "WHERE k.code LIKE 'MATH-PEP-%' AND k.parent_id IS NOT NULL "
            "GROUP BY k.code, k.name HAVING count(b.id) > 0 ORDER BY k.code"
        )
    )
    return {"code": 0, "data": {"items": [dict(r._mapping) for r in rows.all()]}}


@router.post("/practice/start")
async def practice_start(
    req: PracticeStartRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """开练（F5 三模式；题库优先：真题检索 → AI 生成只补缺口）

    special 支持可选 count（5~30，缺省 5）大题量专题训练；响应 items 带 source（真题来源）
    与 ai_generated 标记，data 带 bank_count/ai_count 构成标注；
    日限只计 AI 生成题（daily_cap.scope="ai_only"），题库真题不占额度。
    """
    user_id = uuid.UUID(user["sub"])

    # mode 三枚举校验（API §9.7，非法值拒绝而非静默按 special）
    if req.mode not in ("special", "retry", "daily"):
        return {"code": 40001, "message": f"非法模式: {req.mode}，仅支持 special/retry/daily"}

    limit = settings.student_daily_practice_limit
    # 日限口径：只计 AI 生成题（题库真题不占额度），作答/组卷两路合并（见 daily_ai_used）
    used = await daily_ai_used(db, user_id)
    # 当日口径 = 服务器本地日期（daily_questions.date 匹配用）
    today = datetime.now().astimezone().date()

    # 按模式出题（题库优先：先检索题库，缺口才走 LLM 生成+四闸）
    try:
        if req.mode == "daily":
            # 每日一题（学情化，迭代17）：每生每天一题，知识点按该生薄弱点 Top 加权轮换
            # 日限口径对齐（P1-3 突破）：只限 AI 生成题——当日题库命中则 0 消耗不拦截；
            # 仅当题库无当日 kp 匹配、需 AI 生成且额度已满才 42901（与 special 同构）
            daily = await db.execute(
                select(DailyQuestion).where(
                    DailyQuestion.date == today, DailyQuestion.user_id == user_id
                )
            )
            daily_q = daily.scalar_one_or_none()
            if daily_q:
                quiz_id = daily_q.quiz_id
            else:
                # 题库优先探测：命中该生薄弱 kp 的中等选择题 → 纯题库题，不耗 AI 额度
                # （迭代09 治理：仅采样真实数学知识点，排除 pb 测试占位点）
                kp_rows = await db.execute(select(KnowledgePoint.code, KnowledgePoint.grade))
                kp_pool = [
                    str(c)
                    for c, g in kp_rows.all()
                    if str(c).startswith(_REAL_KP_PREFIXES) and (not g or g.startswith("高"))
                ]
                if not kp_pool:
                    kp_pool = list(KP_MAP.keys())
                # 学情驱动选题：薄弱点 Top 优先（无学情回退日期轮换）
                daily_kp = await _pick_weak_kp(db, user_id, kp_pool)
                bank_probe = await supply_questions(
                    db, kp_codes=[daily_kp], q_type="choice", difficulty="medium", count=1
                )
                if not bank_probe and used >= limit:
                    return {
                        "code": 42901,
                        "message": f"今日 AI 出题已达上限（{limit} 题），题库暂无匹配真题",
                    }
                try:
                    quiz_id = await _generate_daily_quiz(db, user_id, daily_kp)
                except QuizGenerationError:
                    # D7：闸拦下全部 AI 候选≠学生该吃 50301——回退题库真题
                    await db.rollback()
                    quiz_id = await _daily_bank_fallback(db, user_id, daily_kp)
                except IntegrityError:
                    # 并发竞态：另一请求已落该生当日题组（uq(user_id,date) 冲突）→
                    # 回滚本事务后重查已存在行，幂等返回（不 500）
                    await db.rollback()
                    daily = await db.execute(
                        select(DailyQuestion).where(
                            DailyQuestion.date == today, DailyQuestion.user_id == user_id
                        )
                    )
                    daily_q = daily.scalar_one_or_none()
                    if daily_q is None:
                        raise QuizGenerationError("每日一题生成冲突且未查到已存在行") from None
                    quiz_id = daily_q.quiz_id
        elif req.mode == "retry":
            # 重练：从错题关联 kp 出题（逻辑不变：日限前置拦截，生成内题库优先）
            if used >= limit:
                return {"code": 42901, "message": f"今日刷题已达上限（{limit} 题）"}
            quiz_id = await _generate_retry_quiz(db, user_id)
        else:
            # 专练：须传 kp_code 且知识点须存在于知识库（迭代05 A-P0-2，ADR-038）
            if not req.kp_code:
                return {"code": 40001, "message": "专练模式须指定知识点 kp_code"}
            count = req.count if req.count is not None else 5
            if not (5 <= count <= 30):
                return {"code": 40001, "message": f"非法题量: {count}，专题训练支持 5~30 题"}
            kp_exists = await db.execute(
                select(KnowledgePoint.id).where(KnowledgePoint.code == req.kp_code).limit(1)
            )
            if kp_exists.scalar_one_or_none() is None:
                return {"code": 40400, "message": f"知识点不存在: {req.kp_code}"}
            # 题库优先探测（只读，不落库）：命中即为本次题库题；缺口才需 AI 额度
            bank_probe = await supply_questions(db, kp_codes=[req.kp_code], count=count)
            if len(bank_probe) < count and used >= limit:
                # 日限只限 AI 生成题：纯题库命中不受限；需要 AI 补缺口且额度已满才 42901
                return {
                    "code": 42901,
                    "message": f"今日 AI 出题已达上限（{limit} 题），题库命中 {len(bank_probe)} 题不足以成组",
                }
            quiz_id = await _generate_special_quiz(
                db, user_id, req.kp_code, count=count, bank_rows=bank_probe
            )
    except QuizGenerationError as e:
        # LLM 出题失败：回滚空题组，返回明确错误而非空 items
        await db.rollback()
        logger.warning("quiz_generation_failed", mode=req.mode, error=str(e))
        return {"code": 50301, "message": f"出题失败：{e}"}

    # 获取题目
    items_result = await db.execute(
        select(QuizItem).where(QuizItem.quiz_id == quiz_id, QuizItem.deleted_at.is_(None))
    )
    items = items_result.scalars().all()

    items_data = [
        {
            "item_no": i.item_no,
            # 阶段 1 契约：interaction_type（前端可作答类型）新增，q_type 兼容保留
            "interaction_type": _Q_TYPE_TO_INTERACTION.get(i.q_type, "text"),
            "q_type": i.q_type,
            "question_text": i.question_text,
            "options": i.options,
            "kp_code": i.kp_code,
            "difficulty": i.difficulty,
            "ai_generated": i.ai_generated,
            "source": i.source,  # 题库真题来源（AI 题为 null）
            "image": i.image or [],  # 配图（data URI / URL 列表，P2-5）
        }
        for i in items
    ]
    bank_count = sum(1 for i in items if not i.ai_generated)

    return {
        "code": 0,
        "data": {
            "quiz_id": str(quiz_id),
            "mode": req.mode,
            "items": items_data,
            # 构成标注：题库真题 / AI 生成各多少题
            "bank_count": bank_count,
            "ai_count": len(items) - bank_count,
            # 口径如实标注：used 只计 AI 生成题（题库真题不占额度）
            "daily_cap": {"limit": limit, "used": used, "scope": "ai_only"},
        },
    }


@router.get("/practice/daily")
async def practice_daily(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """今日一题 + 打卡状态（与 daily-plan 共用装配，见 _daily_question_payload）"""
    user_id = uuid.UUID(user["sub"])
    return {"code": 0, "data": await _daily_question_payload(db, user_id)}


@router.get("/streak")
async def get_streak(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """打卡状态"""
    user_id = uuid.UUID(user["sub"])
    streak = await db.get(Streak, user_id)

    return {
        "code": 0,
        "data": {
            "current_streak": streak.current_streak if streak else 0,
            "longest_streak": streak.longest_streak if streak else 0,
            "last_active_date": (
                streak.last_active_date.isoformat() if streak and streak.last_active_date else None
            ),
        },
    }


# ==================== 练题中心推荐（闭环迭代13） ====================


@router.get("/lab/recommend")
async def lab_recommend(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """练题中心首页推荐聚合（闭环迭代13，vision「专项训练×模拟考试一体两面」落地）：
    一次请求返回薄弱点训练推荐 + 模拟卷推荐 + 待复习错题 + 连击 + 今日一题，
    供练题中心真实功能页渲染，避免前端 5 次并发拉取。
    """
    user_id = uuid.UUID(user["sub"])
    now = datetime.now(UTC)

    # 1) 薄弱点训练推荐（掌握度升序前 5，专项训练入口）
    weak_rows = await db.execute(
        select(MasteryRecord.mastery, KnowledgePoint)
        .join(KnowledgePoint, MasteryRecord.kp_id == KnowledgePoint.id)
        .where(MasteryRecord.user_id == user_id)
        .order_by(MasteryRecord.mastery.asc())
        .limit(5)
    )
    weak_points = [
        {"kp_code": kp.code, "kp_name": kp.name, "mastery": float(m)} for m, kp in weak_rows.all()
    ]

    # 2) 模拟卷推荐（最近 5 次 + 成绩聚合：最高/最近/次数）
    last_score_sq = (
        select(Submission.total_score)
        .where(
            Submission.quiz_id == Quiz.id,
            Submission.user_id == user_id,
            Submission.deleted_at.is_(None),
        )
        .order_by(Submission.created_at.desc())
        .limit(1)
        .correlate(Quiz)
        .scalar_subquery()
    )
    exam_rows = await db.execute(
        select(
            Quiz.id,
            Quiz.title,
            Quiz.source,
            Quiz.created_at,
            func.max(Submission.total_score).label("best_score"),
            func.count(Submission.id).label("attempts"),
            last_score_sq.label("last_score"),
        )
        .outerjoin(
            Submission,
            and_(
                Submission.quiz_id == Quiz.id,
                Submission.user_id == user_id,
                Submission.deleted_at.is_(None),
            ),
        )
        .where(
            Quiz.user_id == user_id,
            Quiz.deleted_at.is_(None),
            Quiz.source.like("exam:%"),
        )
        .group_by(Quiz.id)
        .order_by(Quiz.created_at.desc())
        .limit(5)
    )
    exams = [
        {
            "exam_id": str(r.id),
            "title": r.title,
            "source": r.source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "best_score": float(r.best_score) if r.best_score is not None else None,
            "last_score": float(r.last_score) if r.last_score is not None else None,
            "attempts": int(r.attempts or 0),
        }
        for r in exam_rows.all()
    ]

    # 3) 待复习错题（到期前 3 条，错题讲解闭环入口）
    due_rows = await db.execute(
        select(ErrorRecord)
        .where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.next_review_at.is_not(None),
            ErrorRecord.next_review_at <= now,
            ErrorRecord.deleted_at.is_(None),
        )
        .order_by(ErrorRecord.next_review_at)
        .limit(3)
    )
    due_reviews = [
        {
            "id": str(r.id),
            "question_text": _latex_safe_preview(r.question_text, 96),
            "kp_code": r.kp_code,
            "error_type": r.error_type,
            "review_count": r.review_count,
            "next_review_at": r.next_review_at.isoformat() if r.next_review_at else None,
        }
        for r in due_rows.scalars().all()
    ]

    # 4) 连击
    streak = await db.get(Streak, user_id)

    # 5) 今日一题（复用 daily-plan 装配）
    daily_payload = await _daily_question_payload(db, user_id)

    return {
        "code": 0,
        "data": {
            "weak_points": weak_points,
            "exams": exams,
            "due_reviews": due_reviews,
            "streak": {
                "current_streak": streak.current_streak if streak else 0,
                "longest_streak": streak.longest_streak if streak else 0,
            },
            "daily_question": daily_payload,
        },
    }


# ==================== 学情（F6） ====================


@router.get("/mastery/summary")
async def mastery_summary(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """掌握度总览（F6）"""
    user_id = uuid.UUID(user["sub"])

    # 查掌握度记录
    records = await db.execute(
        select(MasteryRecord, KnowledgePoint)
        .join(KnowledgePoint, MasteryRecord.kp_id == KnowledgePoint.id)
        .where(MasteryRecord.user_id == user_id)
    )
    rows = records.all()

    radar = []
    top_weak = []
    for mr, kp in rows:
        item = {"kp_code": kp.code, "kp_name": kp.name, "mastery": float(mr.mastery)}
        radar.append(item)
        top_weak.append(item)

    # radar 按掌握度降序截断 ≤12（迭代05 定稿口径，C-P2-2）
    radar.sort(key=lambda x: x["mastery"], reverse=True)
    radar = radar[:12]

    # Top3 薄弱
    top_weak.sort(key=lambda x: x["mastery"])
    top_weak = top_weak[:3]

    # 提示依赖度 = 累计提示数 / max(1, 累计做题数)（mastery_records 实数）
    totals = await db.execute(
        select(
            func.coalesce(func.sum(MasteryRecord.hint_count), 0),
            func.coalesce(func.sum(MasteryRecord.practice_count), 0),
        ).where(MasteryRecord.user_id == user_id)
    )
    hint_total, practice_total = totals.one()

    # F1 引导式解题提示/答案请求聚合（迭代05 B-P1-16：补上 socratic 数据源断链）
    # 口径（F1 §3.1.6）：（练习提示数 + 引导提示数 + 答案请求数）/ max(1, 练习做题数 + 引导会话数)
    socratic_hints = 0
    socratic_sessions = 0
    try:
        ts_rows = await db.execute(
            select(TutorSession.hint_counts, TutorSession.answer_requests)
            .where(TutorSession.user_id == user_id)
            .limit(200)
        )
        for hc, ar in ts_rows.all():
            socratic_sessions += 1
            socratic_hints += sum(int(v or 0) for v in (hc or {}).values()) + int(ar or 0)
    except Exception as e:
        logger.warning("mastery_socratic_stats_failed", error=str(e)[:100])

    hint_dependency = float(hint_total + socratic_hints) / max(
        1, int(practice_total) + socratic_sessions
    )

    # P1-4 突破：7 日环比（今日均值 vs 7 天前均值，来自 mastery_snapshots 日快照；
    # 无 7 天前数据 → None，前端显示"暂无可比"而非误导性 0）
    delta_vs_7d: float | None = None
    try:
        today = date.today()
        since7 = today - timedelta(days=7)
        avg_now = (
            await db.execute(
                select(func.avg(MasterySnapshot.mastery)).where(
                    MasterySnapshot.user_id == user_id,
                    MasterySnapshot.date == today,
                )
            )
        ).scalar()
        avg_7d = (
            await db.execute(
                select(func.avg(MasterySnapshot.mastery)).where(
                    MasterySnapshot.user_id == user_id,
                    MasterySnapshot.date == since7,
                )
            )
        ).scalar()
        if avg_now is not None and avg_7d is not None:
            delta_vs_7d = round(float(avg_now) - float(avg_7d), 4)
    except Exception as e:
        logger.warning("mastery_delta_failed", error=str(e)[:100])

    return {
        "code": 0,
        "data": {
            "radar": radar,
            "top_weak": top_weak,
            "hint_dependency": round(hint_dependency, 4),
            "delta_vs_7d": delta_vs_7d,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }


@router.get("/mastery/today-actions")
async def mastery_today_actions(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """今日 3 件事行动清单（迭代15 B6 / 方案 L2-3 学情行动化）

    学情页首屏从"百分比汇报"改为"可执行行动"：到期错题变式复习 → 薄弱点专练 → 挑战 1 题，
    每张卡带理由与预计时长；雷达图/百分比由前端降级到折叠区。
    防伪勤奋叫停：最薄弱 kp 掌握度仍 ≥0.9 时不下发专练卡，改发 move_on 提示劝换新知识点。
    """
    user_id = uuid.UUID(user["sub"])
    now = datetime.now(UTC)
    actions: list[dict] = []
    notice: dict | None = None

    # 1) 到期错题变式复习（间隔复习排期的行动化出口；复习用变式不用原题，由对话侧承接）
    active_cond = (
        ErrorRecord.user_id == user_id,
        ErrorRecord.deleted_at.is_(None),
        ErrorRecord.next_review_at.is_not(None),
    )
    due_count = (
        await db.execute(
            select(func.count())
            .select_from(ErrorRecord)
            .where(*active_cond, ErrorRecord.next_review_at <= now)
        )
    ).scalar() or 0
    if due_count:
        due_rows = await db.execute(
            select(ErrorRecord)
            .where(*active_cond, ErrorRecord.next_review_at <= now)
            .order_by(ErrorRecord.next_review_at)
            .limit(3)
        )
        due_records = due_rows.scalars().all()
        kp_names = await _kp_name_map(db, [r.kp_code for r in due_records])
        actions.append(
            {
                "type": "review",
                "title": f"复习 {due_count} 道到期错题",
                "reason": "间隔复习到点了——换个条件再试试，做对它才算真正翻篇",
                "duration_min": 10,
                "items": [
                    {
                        "record_id": str(r.id),
                        "question_text": _safe_latex_truncate(r.question_text, 120),
                        "kp_code": r.kp_code,
                        "kp_name": kp_names.get(r.kp_code),
                    }
                    for r in due_records
                ],
            }
        )

    # 2) 薄弱点变式专练 / 防伪勤奋叫停
    weak_rows = await db.execute(
        select(MasteryRecord.mastery, KnowledgePoint)
        .join(KnowledgePoint, MasteryRecord.kp_id == KnowledgePoint.id)
        .where(MasteryRecord.user_id == user_id)
        .order_by(MasteryRecord.mastery.asc())
        .limit(1)
    )
    weakest = weak_rows.first()
    if weakest is not None:
        w_mastery, w_kp = float(weakest[0]), weakest[1]
        if w_mastery < 0.7:
            actions.append(
                {
                    "type": "weak",
                    "title": f"专练「{w_kp.name}」",
                    "reason": f"当前掌握度 {round(w_mastery * 100)}%，是你最薄弱的环节",
                    "duration_min": 15,
                    "kp_code": w_kp.code,
                    "kp_name": w_kp.name,
                    "mastery": w_mastery,
                }
            )
        elif w_mastery >= 0.9:
            # 防伪勤奋：最薄弱项都已 ≥90%，继续刷同 kp 是伪勤奋，主动劝换
            notice = {
                "type": "move_on",
                "text": (
                    f"连最薄弱的「{w_kp.name}」掌握度都已达 {round(w_mastery * 100)}%，"
                    "继续在已掌握的内容上刷题收益很低——建议换一个新知识点开荒"
                ),
            }

    # 3) 挑战 1 题：今日一题未完成优先，否则兜底薄弱点提高题
    daily = await _daily_question_payload(db, user_id)
    if daily.get("quiz_id") and not daily.get("completed"):
        actions.append(
            {
                "type": "daily",
                "title": "挑战今日一题",
                "reason": "每天一道保持手感，守住你的连击记录",
                "duration_min": 5,
                "quiz_id": daily["quiz_id"],
                "item": daily.get("item"),
            }
        )
    elif weakest is not None and float(weakest[0]) < 0.9:
        actions.append(
            {
                "type": "challenge",
                "title": f"挑战 1 道「{weakest[1].name}」提高题",
                "reason": "在薄弱点上做一点拉伸，比重复会做的题更有效",
                "duration_min": 5,
                "kp_code": weakest[1].code,
                "kp_name": weakest[1].name,
            }
        )

    return {
        "code": 0,
        "data": {
            "actions": actions[:3],
            "notice": notice,
            "due_today": int(due_count),
            "generated_at": now.isoformat(),
        },
    }


@router.get("/mastery/trend")
async def mastery_trend(
    days: int = Query(default=30, ge=7, le=90),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """掌握度曲线（数据源 mastery_snapshots 日快照，按日平均）"""
    user_id = uuid.UUID(user["sub"])
    since = date.today() - timedelta(days=days - 1)
    rows = await db.execute(
        select(MasterySnapshot.date, func.avg(MasterySnapshot.mastery))
        .where(MasterySnapshot.user_id == user_id, MasterySnapshot.date >= since)
        .group_by(MasterySnapshot.date)
        .order_by(MasterySnapshot.date)
    )
    points = [{"date": d.isoformat(), "avg_mastery": float(a)} for d, a in rows.all()]

    if not points:
        # 无快照（未产生过 BKT 更新）→ ADR-039 兜底单点：当前均值如实标注，无记录时 0.5
        avg = await db.execute(
            select(func.avg(MasteryRecord.mastery)).where(MasteryRecord.user_id == user_id)
        )
        points = [{"date": date.today().isoformat(), "avg_mastery": float(avg.scalar() or 0.5)}]

    return {
        "code": 0,
        "data": {
            "points": points,
        },
    }


@router.get("/warnings")
async def get_warnings(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """F6 预警规则引擎（温和话术不制造焦虑；无任何数据 → 空列表）

    规则（第 4 条"作业未交"因 M2 assignments 恒空不实现）：
    ① 某知识点最近 3 次判分全错 → kp_streak_fail
    ② 本周提交数较上周降 ≥50% 且上周 ≥4 次 → activity_drop
    ③ 提示依赖度周环比升 ≥30%（tutor_sessions 实数）→ hint_dependency_up
    ⑤ 最近两次有总分的提交降 ≥20 分 → score_drop
    """
    user_id = uuid.UUID(user["sub"])
    now = datetime.now(UTC)
    warnings: list[dict] = []

    # 周口径：自然周（周一 00:00 起，UTC）
    week_start = datetime.combine(
        date.today() - timedelta(days=date.today().weekday()), time.min, tzinfo=UTC
    )
    last_week_start = week_start - timedelta(days=7)

    # ① 知识点连错：submission_items join submissions/quiz_items，按 kp 分组取最近 3 次判分
    verdict_rows = await db.execute(
        select(QuizItem.kp_code, SubmissionItem.verdict)
        .join(Submission, SubmissionItem.submission_id == Submission.id)
        .join(
            QuizItem,
            (QuizItem.quiz_id == Submission.quiz_id) & (QuizItem.item_no == SubmissionItem.item_no),
        )
        .where(
            Submission.user_id == user_id,
            Submission.deleted_at.is_(None),
            SubmissionItem.deleted_at.is_(None),
            QuizItem.deleted_at.is_(None),
            QuizItem.kp_code.is_not(None),
            SubmissionItem.verdict.in_(("correct", "wrong")),
        )
        .order_by(SubmissionItem.created_at.desc())
    )
    recent_by_kp: dict[str, list[str]] = {}
    for kp_code, verdict in verdict_rows.all():
        lst = recent_by_kp.setdefault(kp_code, [])
        if len(lst) < 3:
            lst.append(verdict)
    streak_fail_kps = [
        c for c, vs in recent_by_kp.items() if len(vs) == 3 and all(v == "wrong" for v in vs)
    ]
    if streak_fail_kps:
        kp_name_map = await _kp_name_map(db, streak_fail_kps)
        for c in streak_fail_kps:
            kp_name = kp_name_map.get(c)
            warnings.append(
                {
                    "rule": "kp_streak_fail",
                    "type": "kp",
                    "level": "gentle",
                    "kp_code": c,
                    "kp_name": kp_name,
                    "message": f"最近「{kp_name or c}」连续几次没达标，要不要来个10分钟专练？",
                    "created_at": now.isoformat(),
                }
            )

    # ② 活跃度下降：本周提交数较上周降 ≥50% 且上周 ≥4 次
    this_week_subs = (
        await db.execute(
            select(func.count())
            .select_from(Submission)
            .where(
                Submission.user_id == user_id,
                Submission.deleted_at.is_(None),
                Submission.created_at >= week_start,
            )
        )
    ).scalar() or 0
    last_week_subs = (
        await db.execute(
            select(func.count())
            .select_from(Submission)
            .where(
                Submission.user_id == user_id,
                Submission.deleted_at.is_(None),
                Submission.created_at >= last_week_start,
                Submission.created_at < week_start,
            )
        )
    ).scalar() or 0
    if last_week_subs >= 4 and this_week_subs <= last_week_subs / 2:
        warnings.append(
            {
                "rule": "activity_drop",
                "type": "activity",
                "level": "gentle",
                "message": "这周练习比上周少了一些，每天10分钟就能找回节奏，从今日一题开始吧？",
                "created_at": now.isoformat(),
            }
        )

    # ③ 提示依赖度周环比升 ≥30%（tutor_sessions 实数：提示阶梯计数 + 答案请求 / 会话数）
    ts_rows = await db.execute(
        select(TutorSession.hint_counts, TutorSession.answer_requests, TutorSession.created_at)
        .where(
            TutorSession.user_id == user_id,
            TutorSession.deleted_at.is_(None),
            TutorSession.created_at >= last_week_start,
        )
        .limit(400)
    )
    dep = {"this": [0, 0], "last": [0, 0]}  # 每周 [提示数, 会话数]
    for hc, ar, created in ts_rows.all():
        bucket = "this" if created >= week_start else "last"
        dep[bucket][0] += sum(int(v or 0) for v in (hc or {}).values()) + int(ar or 0)
        dep[bucket][1] += 1
    dep_this = dep["this"][0] / max(1, dep["this"][1])
    dep_last = dep["last"][0] / max(1, dep["last"][1])
    if dep_last > 0 and (dep_this - dep_last) / dep_last >= 0.3:
        warnings.append(
            {
                "rule": "hint_dependency_up",
                "type": "habit",
                "level": "gentle",
                "message": "最近求助次数变多了，先试着自己多想3分钟再点提示，印象会更深哦。",
                "created_at": now.isoformat(),
            }
        )

    # ⑤ 最近两次有总分的提交降 ≥20 分
    score_rows = await db.execute(
        select(Submission.total_score)
        .where(
            Submission.user_id == user_id,
            Submission.deleted_at.is_(None),
            Submission.total_score.is_not(None),
        )
        .order_by(Submission.created_at.desc())
        .limit(2)
    )
    scores = [float(s) for (s,) in score_rows.all()]
    if len(scores) == 2 and scores[1] - scores[0] >= 20:
        warnings.append(
            {
                "rule": "score_drop",
                "type": "score",
                "level": "gentle",
                "message": "最近两次得分有些波动，可能只是状态起伏，来一组错题重练稳稳手感？",
                "created_at": now.isoformat(),
            }
        )

    return {"code": 0, "data": {"warnings": warnings, "computed_at": now.isoformat()}}


# ==================== 知识图谱（F7） ====================

# 真实数学知识点前缀白名单（与 mock_exam.is_real_kp_code 同规则：
# 测试曾以 "pb" 前缀写入占位知识点，需在展示/采样处排除。
# MX/BK 不带连字符：模块类知识点为 "MX{hex}-M{n}-NNN" / "BK{hex}-NNN" 形态；
# TST 为测试命名空间，各测试文件 fixture 自清洁，需放行保证组卷测试可造数据）
_REAL_KP_PREFIXES = ("MATH-", "MX", "BK", "TST")


def _is_real_kp(kp) -> bool:
    """知识点是否为真实数学知识点（白名单前缀 + 高中 grade）"""
    if not getattr(kp, "code", None):
        return False
    if not kp.code.startswith(_REAL_KP_PREFIXES):
        return False
    return not kp.grade or kp.grade.startswith("高")


@router.get("/knowledge-graph")
async def knowledge_graph(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """静态三级树 + 着色（F7）"""
    user_id = uuid.UUID(user["sub"])

    # 查所有知识点（迭代09 治理：过滤测试占位点——pb 开头的空点/校验点等，
    # 仅保留真实数学知识点 MATH- / MX- / BK- 前缀，避免图谱出现"空点/题库点"脏节点）
    kps = await db.execute(select(KnowledgePoint))
    all_kps = [k for k in kps.scalars().all() if _is_real_kp(k)]

    # 查掌握度
    mastery_map: dict[uuid.UUID, float] = {}
    records = await db.execute(select(MasteryRecord).where(MasteryRecord.user_id == user_id))
    for mr in records.scalars().all():
        mastery_map[mr.kp_id] = float(mr.mastery)

    # id → code 映射（parent_id → parent_code）
    code_by_id = {kp.id: kp.code for kp in all_kps}

    # 各知识点错题数（实数）
    err_rows = await db.execute(
        select(ErrorRecord.kp_code, func.count())
        .where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.kp_code.is_not(None),
            ErrorRecord.deleted_at.is_(None),
        )
        .group_by(ErrorRecord.kp_code)
    )
    error_count_map = dict(err_rows.all())

    nodes = []
    for kp in all_kps:
        mastery = mastery_map.get(kp.id)
        color = _mastery_color(mastery)
        nodes.append(
            {
                "code": kp.code,
                "name": kp.name,
                "parent_code": code_by_id.get(kp.parent_id) if kp.parent_id else None,
                "grade": getattr(kp, "grade", None),
                "mastery": mastery,
                "color": color,
                "error_count": error_count_map.get(kp.code, 0),
            }
        )

    return {"code": 0, "data": {"nodes": nodes}}


@router.get("/knowledge-graph/nodes/{kp_code}")
async def knowledge_node_card(
    kp_code: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """节点学习卡"""
    user_id = uuid.UUID(user["sub"])

    kp = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
    kp_obj = kp.scalar_one_or_none()
    if not kp_obj:
        return {"code": 40400, "message": "知识点不存在"}

    # 最近错题
    errors = await db.execute(
        select(ErrorRecord)
        .where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.kp_code == kp_code,
            ErrorRecord.deleted_at.is_(None),
        )
        .order_by(ErrorRecord.created_at.desc())
        .limit(3)
    )
    recent_errors = [
        {
            "record_id": str(e.id),
            "question_text": _safe_latex_truncate(e.question_text, 100),
            "error_type": e.error_type,
        }
        for e in errors.scalars().all()
    ]

    mastery = None
    mr = await db.execute(
        select(MasteryRecord).where(
            MasteryRecord.user_id == user_id, MasteryRecord.kp_id == kp_obj.id
        )
    )
    mr_obj = mr.scalar_one_or_none()
    if mr_obj:
        mastery = float(mr_obj.mastery)

    # 学习建议按着色分档（SRS 阈值：<0.4 红 / 0.4~0.7 黄 / ≥0.7 绿 / 无数据灰）
    color = _mastery_color(mastery)
    if mastery is None:
        suggested_actions = [{"action": "quiz", "reason": "学一学试试"}]
    elif mastery < 0.4:
        suggested_actions = [
            {"action": "guide", "reason": "基础薄弱，建议引导复习"},
            {"action": "quiz", "reason": "针对练习巩固"},
        ]
    elif mastery < 0.7:
        suggested_actions = [{"action": "quiz", "reason": "即将掌握，加强练习"}]
    else:
        suggested_actions = [{"action": "quiz", "reason": "保持手感"}]

    # 前置联动：本节点为红且父节点也未掌握（红/黄）→ 提示先补前置
    prerequisite_hint = None
    if color == "red" and kp_obj.parent_id:
        parent = await db.get(KnowledgePoint, kp_obj.parent_id)
        if parent is not None:
            p_mr = await db.execute(
                select(MasteryRecord).where(
                    MasteryRecord.user_id == user_id, MasteryRecord.kp_id == parent.id
                )
            )
            p_mr_obj = p_mr.scalar_one_or_none()
            p_mastery = float(p_mr_obj.mastery) if p_mr_obj else None
            if _mastery_color(p_mastery) in ("red", "yellow"):
                prerequisite_hint = {
                    "kp_code": parent.code,
                    "kp_name": parent.name,
                    "message": f"前置「{parent.name}」也未掌握，建议先补前置",
                }

    return {
        "code": 0,
        "data": {
            "kp_code": kp_code,
            "name": kp_obj.name,
            "mastery": mastery,
            "recent_errors": recent_errors,
            "suggested_actions": suggested_actions,
            "prerequisite_hint": prerequisite_hint,
            # P1-4 突破：学情→行动闭环——图谱节点可直接发起专练/重练（前端一键跳转）
            "actions": [
                {
                    "type": "practice",
                    "label": "去专练这个知识点",
                    "href": f"/student/practice?mode=special&kp={kp_code}",
                },
                {
                    "type": "guide",
                    "label": "让 AI 带我做一道",
                    "href": f"/student/chat?kp={kp_code}",
                },
            ],
        },
    }


# ==================== 学习路径（F8） ====================

# LaTeX 命令匹配：\vec \begin \frac 等（带花括号体）
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\s*(\{[^}]*\})*")


def _clean_latex_text(text: str, max_len: int = 24) -> str:
    """清洗带 LaTeX 的题目文本为纯文本摘要，避免截断公式产生乱码。

    - 去掉行内/块级公式（$...$、$$...$$、\\(...\\)）与 LaTeX 命令
    - 压缩空白、清理连续标点（公式剥离后可能残留" ， ，求"）
    - 安全截断（在汉字/常规字符边界截，绝不落在公式中间）
    """
    if not text:
        return ""
    t = text
    # 块级/行内公式整体剥离（贪心非空，容忍跨行）
    t = re.sub(r"\$\$.*?\$\$|\$.*?\$|\\\(.*?\\\)", " ", t, flags=re.S)
    # 剥离 LaTeX 命令（含其花括号参数，如 \frac{1}{2}、\vec{a}）
    t = _LATEX_CMD_RE.sub(" ", t)
    # 剥残留花括号/控制符
    t = t.replace("{", " ").replace("}", " ").replace("\\", " ")
    # 压缩空白
    t = re.sub(r"\s+", " ", t)
    # 清理公式剥离后残留的"空格+标点"（如"， ，"、"； ；"），只留一个分隔标点
    t = re.sub(r"([，。；;：、])\s*([，。；;：、])", r"\1", t)
    t = re.sub(r"\s+([，。；;：、)）])", r"\1", t)
    t = re.sub(r"([（(])\s+", r"\1", t)
    # 去首尾标点与空白
    t = t.strip(" ：:，。；;、()（）")
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    return t[:max_len].rstrip(" ，。；;、") + "…"


def _latex_safe_preview(text: str, limit: int = 96) -> str:
    """保留 LaTeX 的安全截断（供 MarkdownView 渲染的摘要）：绝不切断 $...$ 数学段——
    切断会导致 KaTeX 定界符不闭合、整段以源码形式乱码（练题中心待复习侧栏实测乱码根因）。

    规则：limit 处若落在数学段内，先在宽限窗口内向后找闭合 $；找不到则退回最后一个
    完整公式/文本边界；极端情况（整段就是一个超长公式）硬切兜底。
    """
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    in_math = False
    last_good = 0  # 最后一个「不在数学段内」的安全切割点
    end = min(len(t), limit)
    i = 0
    while i < end:
        ch = t[i]
        if ch == "$" and (i == 0 or t[i - 1] != "\\"):
            in_math = not in_math
            if not in_math:
                last_good = i + 1  # 公式刚闭合，切在公式后安全
        elif not in_math and not ch.isspace():
            last_good = i + 1
        i += 1
    if in_math:
        # 宽限窗口内找闭合 $（题目里 \$ 转义极罕见，简单 find 即可）
        close = t.find("$", end)
        if 0 < close <= limit + 120:
            return t[: close + 1] + "…"
    if last_good >= 12:
        return t[:last_good].rstrip() + "…"
    return t[:limit] + "…"


def _safe_latex_truncate(text: str, max_len: int = 120) -> str:
    """LaTeX 安全截断：截断位置绝不在 $...$ 公式中间，避免公式残片乱码。

    - 若 max_len 处落在公式内，回退到最近一个完整公式的边界
    - 纯文本不受影响；截断后补省略号
    """
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = max_len
    while cut > 0:
        prefix = text[:cut]
        # $ 出现奇数次 = 在公式内（行内公式以 $ 成对）
        if prefix.count("$") % 2 == 0:
            # 且不在 \begin{...} 环境内部
            last_begin = prefix.rfind(r"\begin{")
            if last_begin == -1 or prefix.find(r"\end{", last_begin) != -1:
                break
        cut -= 1
    if cut <= 0:
        cut = max_len
    return text[:cut].rstrip(" ，。；;、") + "…"


@router.get("/daily-plan")
async def daily_plan(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """首页三卡（规则引擎，不用大模型）

    today_tasks ≤5，装配顺序：今日到期复习 → 红黄薄弱点专练 → 每日一题 → 未完成任务；
    week_goal 取掌握度最低且非灰色（有 BKT 记录）的知识点，无薄弱点为 null。
    """
    user_id = uuid.UUID(user["sub"])
    now = datetime.now(UTC)

    tasks: list[dict] = []

    # ①今日到期复习（最近到期的优先，每条错题一个任务）
    due_reviews = await db.execute(
        select(ErrorRecord)
        .where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.next_review_at.is_not(None),
            ErrorRecord.next_review_at <= now,
            ErrorRecord.deleted_at.is_(None),
        )
        .order_by(ErrorRecord.next_review_at)
        .limit(3)
    )
    for r in due_reviews.scalars().all():
        tasks.append(
            {
                "type": "review",
                "ref_id": str(r.id),
                "title": f"复习错题：{_clean_latex_text(r.question_text)}",
                "reason": f"间隔复习第 {(r.review_count or 0) + 1} 次",
            }
        )

    # ②红黄薄弱点专练（mastery 升序取 ≤2；父子同弱时父节点前置）
    weak_rows = await db.execute(
        select(MasteryRecord.mastery, KnowledgePoint)
        .join(KnowledgePoint, MasteryRecord.kp_id == KnowledgePoint.id)
        .where(MasteryRecord.user_id == user_id, MasteryRecord.mastery < 0.7)
        .order_by(MasteryRecord.mastery.asc())
        .limit(20)
    )
    weak = [(float(m), kp) for m, kp in weak_rows.all()]
    by_id = {kp.id: (m, kp) for m, kp in weak}
    ordered_weak: list[tuple[float, KnowledgePoint]] = []
    _emitted: set[str] = set()

    def _emit(entry: tuple[float, KnowledgePoint]) -> None:
        _, kp = entry
        if kp.code in _emitted:
            return
        # 父子同弱 → 父节点前置（先补前置再练本节点）
        if kp.parent_id in by_id:
            _emit(by_id[kp.parent_id])
        _emitted.add(kp.code)
        ordered_weak.append(entry)

    for entry in weak:
        _emit(entry)

    for m, kp in ordered_weak[:2]:
        tasks.append(
            {
                "type": "practice",
                "ref_id": kp.code,
                "title": f"专练：{kp.name}",
                "reason": f"当前掌握度 {round(m * 100)}%，优先巩固薄弱点",
            }
        )

    # ③每日一题（与 practice/daily 同一装配；未完成才列入任务）
    daily_payload = await _daily_question_payload(db, user_id)
    if not daily_payload["completed"]:
        tasks.append(
            {
                "type": "daily_question",
                "ref_id": daily_payload["quiz_id"],
                "title": "今日一题",
                "reason": "每日打卡",
            }
        )

    # ④未完成任务（M2 assignments 恒空，真实查询占位，教师端 M3 接线后自动生效）
    remain = 5 - len(tasks)
    if remain > 0:
        asg_rows = await db.execute(
            select(Assignment)
            .where(
                Assignment.status == "published",
                Assignment.deleted_at.is_(None),
                (Assignment.deadline.is_(None)) | (Assignment.deadline > now),
            )
            .order_by(Assignment.deadline)
            .limit(remain)
        )
        for a in asg_rows.scalars().all():
            tasks.append(
                {
                    "type": "assignment",
                    "ref_id": str(a.id),
                    "title": a.title,
                    "reason": "任务待完成",
                }
            )

    # 周目标：掌握度最低且非灰色的知识点；无薄弱点 → null
    week_goal = None
    if ordered_weak:
        m, kp = ordered_weak[0]
        week_goal = {
            "text": f"本周攻克「{kp.name}」（当前掌握度 {round(m * 100)}%）",
            "progress": m,
        }

    return {
        "code": 0,
        "data": {
            "today_tasks": tasks[:5],
            "daily_question": daily_payload,
            "week_goal": week_goal,
        },
    }


# ==================== 任务（F10） ====================


@router.get("/assignments")
async def list_assignments(
    status: str = Query(default="todo"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """任务列表（F10 学生侧，M2 迭代16 第二批真实查询）

    来源：本人所在班级（class_members）→ assignment_targets（target_type=class
    定向本班 / target_type=student 定向本人）→ assignments（published 且未软删）。
    status 查询参数：todo（未完成，缺省）/ done（已完成）/ all。
    progress：quiz 类按 本人已作答数 vs quiz_items 总数；watch 类按 lesson 完成事件（无数据 0/1）。
    """
    if status not in ("todo", "done", "all"):
        return {"code": 40001, "message": f"非法状态: {status}，仅支持 todo/done/all"}
    user_id = uuid.UUID(user["sub"])
    now = datetime.now(UTC)

    # 本人所在班级（tel D3：与详情闸 _visible_assignment 口径一致——
    # 只有 confirmed 成员才消费班级定向任务；pending 学生显示「等待老师确认」引导态）
    cls_rs = await db.execute(
        select(ClassMember.class_id).where(
            ClassMember.user_id == user_id,
            ClassMember.confirmed.is_(True),
            ClassMember.deleted_at.is_(None),
        )
    )
    class_ids = [c for (c,) in cls_rs.all()]

    # 定向条件：定向本人 或 定向本人所在班级
    ors = [
        and_(
            AssignmentTarget.target_type == "student",
            AssignmentTarget.target_id == user_id,
        )
    ]
    if class_ids:
        ors.append(
            and_(
                AssignmentTarget.target_type == "class",
                AssignmentTarget.target_id.in_(class_ids),
            )
        )
    target_rs = await db.execute(
        select(AssignmentTarget.assignment_id).where(
            AssignmentTarget.deleted_at.is_(None),
            or_(*ors),
        )
    )
    assignment_ids = list({a for (a,) in target_rs.all()})
    if not assignment_ids:
        return {"code": 0, "data": {"total": 0, "items": []}}

    asg_rs = await db.execute(
        select(Assignment)
        .where(
            Assignment.id.in_(assignment_ids),
            Assignment.deleted_at.is_(None),
            Assignment.status == "published",
        )
        .order_by(Assignment.created_at.desc())
    )
    assignments = asg_rs.scalars().all()

    # tel A2：teacher_name（creator join users.nickname）与 kp_labels（quiz_items.kp_code
    # → knowledge_points.name，≤2）批量预取——纯加法字段，列表页展示真实教师与知识点。
    creator_ids = {a.creator_id for a in assignments}
    nickname_map: dict[uuid.UUID, str] = {}
    if creator_ids:
        u_rows = await db.execute(select(User.id, User.nickname).where(User.id.in_(creator_ids)))
        nickname_map = {uid: (nick or "") for uid, nick in u_rows.all()}

    quiz_ids = {a.quiz_id for a in assignments if a.quiz_id}
    kp_labels_by_quiz: dict[uuid.UUID, list[str]] = {}
    if quiz_ids:
        kp_rows = await db.execute(
            select(QuizItem.quiz_id, QuizItem.kp_code, QuizItem.item_no)
            .where(
                QuizItem.quiz_id.in_(quiz_ids),
                QuizItem.deleted_at.is_(None),
                QuizItem.kp_code.isnot(None),
            )
            .order_by(QuizItem.item_no)
        )
        ordered_codes: dict[uuid.UUID, list[str]] = {}
        for qid, code, _item_no in kp_rows.all():
            codes = ordered_codes.setdefault(qid, [])
            if code not in codes:
                codes.append(code)
        all_codes = {code for codes in ordered_codes.values() for code in codes}
        name_map: dict[str, str] = {}
        if all_codes:
            kp_name_rows = await db.execute(
                select(KnowledgePoint.code, KnowledgePoint.name).where(
                    KnowledgePoint.code.in_(all_codes)
                )
            )
            name_map = dict(kp_name_rows.all())
        kp_labels_by_quiz = {
            qid: [name_map[c] for c in codes if c in name_map][:2]
            for qid, codes in ordered_codes.items()
        }

    rows = []
    for a in assignments:
        if a.type in ("quiz", "pre_class", "homework") and a.quiz_id:
            # quiz/pre_class/homework 类（tel D2：两类型 quiz 均已物化）：
            # 本人已作答题数（submissions.assignment_id 归属）vs 题组总题数
            total = (
                await db.execute(
                    select(func.count(QuizItem.id)).where(
                        QuizItem.quiz_id == a.quiz_id,
                        QuizItem.deleted_at.is_(None),
                    )
                )
            ).scalar() or 0
            done = (
                await db.execute(
                    select(func.count(func.distinct(SubmissionItem.item_no)))
                    .join(Submission, SubmissionItem.submission_id == Submission.id)
                    .where(
                        Submission.user_id == user_id,
                        Submission.assignment_id == a.id,
                        Submission.deleted_at.is_(None),
                        SubmissionItem.deleted_at.is_(None),
                    )
                )
            ).scalar() or 0
            done = min(int(done), int(total)) if total else 0
            progress = {"done": done, "total": int(total)}
            completed = total > 0 and done >= total
        else:
            # watch 类：lesson_done 事件（props.lesson_id 匹配）；无数据则 0/1
            done = 0
            if a.lesson_id:
                ev_cnt = (
                    await db.execute(
                        select(func.count(Event.id)).where(
                            Event.user_id == user_id,
                            Event.event == "lesson_done",
                            Event.props["lesson_id"].as_string() == a.lesson_id,
                        )
                    )
                ).scalar() or 0
                done = 1 if ev_cnt > 0 else 0
            progress = {"done": done, "total": 1}
            completed = done >= 1

        overdue = bool(a.deadline and a.deadline < now and not completed)
        rows.append(
            {
                "assignment_id": str(a.id),
                "title": a.title,
                "type": a.type,
                "deadline": a.deadline.isoformat() if a.deadline else None,
                "status": a.status,
                "progress": progress,
                "overdue": overdue,
                # tel A2 纯加法：真实教师名与知识点标签（≤2，无则空数组）
                "teacher_name": nickname_map.get(a.creator_id, ""),
                "kp_labels": kp_labels_by_quiz.get(a.quiz_id, []) if a.quiz_id else [],
                "_completed": completed,
            }
        )

    # status 过滤（todo 缺省：只看未完成）
    if status == "todo":
        rows = [r for r in rows if not r["_completed"]]
    elif status == "done":
        rows = [r for r in rows if r["_completed"]]

    total = len(rows)
    page_rows = rows[(page - 1) * size : page * size]
    items = [{k: v for k, v in r.items() if k != "_completed"} for r in page_rows]
    return {"code": 0, "data": {"total": total, "items": items}}


@router.get("/assignments/{assignment_id}")
async def get_assignment(
    assignment_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """任务详情"""
    user_id = uuid.UUID(user["sub"])
    assignment = await _visible_assignment(db, user_id, assignment_id)
    if assignment is None:
        return {"code": 40400, "message": "任务不存在"}

    items = []
    if assignment.quiz_id:
        quiz_rows = await db.execute(
            select(QuizItem)
            .where(
                QuizItem.quiz_id == assignment.quiz_id,
                QuizItem.deleted_at.is_(None),
            )
            .order_by(QuizItem.item_no)
        )
        items = [
            {
                "item_no": item.item_no,
                "q_type": item.q_type,
                "interaction_type": _Q_TYPE_TO_INTERACTION.get(item.q_type, "text"),
                "question_text": item.question_text,
                "options": item.options,
                "kp_code": item.kp_code,
                "difficulty": item.difficulty,
                "image": item.image or [],
            }
            for item in quiz_rows.scalars().all()
        ]

    return {
        "code": 0,
        "data": {
            "assignment_id": str(assignment.id),
            "title": assignment.title,
            "type": assignment.type,
            "deadline": assignment.deadline.isoformat() if assignment.deadline else None,
            "status": assignment.status,
            "quiz_id": str(assignment.quiz_id) if assignment.quiz_id else None,
            "items": items,
        },
    }


@router.get("/assignments/{assignment_id}/result")
async def get_assignment_result(
    assignment_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回本人最近一次提交及教师确认后的终分/反馈。"""
    user_id = uuid.UUID(user["sub"])
    assignment = await _visible_assignment(db, user_id, assignment_id)
    if assignment is None:
        return {"code": 40400, "message": "任务不存在"}
    submission = (
        await db.execute(
            select(Submission)
            .where(
                Submission.assignment_id == assignment_id,
                Submission.user_id == user_id,
                Submission.deleted_at.is_(None),
            )
            .order_by(Submission.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if submission is None:
        return {"code": 40400, "message": "尚未提交"}
    item_rows = await db.execute(
        select(SubmissionItem)
        .where(
            SubmissionItem.submission_id == submission.id,
            SubmissionItem.deleted_at.is_(None),
        )
        .order_by(SubmissionItem.item_no)
    )
    items = []
    for item in item_rows.scalars().all():
        # tel F3：系统自动判分（选择题/判断题提交即判；填空四层判分判出 correct/wrong）
        # 的分数即时终局——不再等教师 confirm 才下发（刷新后「✓ 判对 +10」不再消失）。
        # 仍按 confirm 下发的：主观题（pending_review）、低置信 needs_review、
        # 教师未确认的 AI 建议分。教师确认值（teacher_final_score→score）优先。
        auto_final = (
            item.confirmed_at is None
            and item.verdict in ("correct", "wrong")
            and not item.needs_review
            and item.score is not None
        )
        released = item.score is not None and (item.confirmed_at is not None or auto_final)
        items.append(
            {
                "submission_item_id": str(item.id),
                "item_no": item.item_no,
                "answer_text": item.answer_text,
                "file_id": str(item.file_id) if item.file_id else None,
                "attachments": item.attachments or [],
                "status": item.suggestion_status,
                "score": float(item.score) if released else None,
                "teacher_feedback": item.teacher_feedback if item.confirmed_at else None,
                "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
            }
        )
    released_scores = [item["score"] for item in items if item["score"] is not None]
    return {
        "code": 0,
        "data": {
            "assignment_id": str(assignment_id),
            "submission_id": str(submission.id),
            "status": submission.status,
            "total_score": sum(released_scores) if released_scores else None,
            "items": items,
        },
    }


async def _visible_assignment(
    db: AsyncSession, user_id: uuid.UUID, assignment_id: uuid.UUID
) -> Assignment | None:
    """学生作业统一范围闸：已确认班级成员/本人定向且作业已发布。"""
    assignment = await db.get(Assignment, assignment_id)
    if assignment is None or assignment.deleted_at is not None or assignment.status != "published":
        return None
    member_class_ids = select(ClassMember.class_id).where(
        ClassMember.user_id == user_id,
        ClassMember.confirmed.is_(True),
        ClassMember.deleted_at.is_(None),
    )
    target = (
        await db.execute(
            select(AssignmentTarget.id)
            .where(
                AssignmentTarget.assignment_id == assignment_id,
                AssignmentTarget.deleted_at.is_(None),
                or_(
                    and_(
                        AssignmentTarget.target_type == "student",
                        AssignmentTarget.target_id == user_id,
                    ),
                    and_(
                        AssignmentTarget.target_type == "class",
                        AssignmentTarget.target_id.in_(member_class_ids),
                    ),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return assignment if target is not None else None


# ==================== 内部工具 ====================


def _mastery_color(mastery: float | None) -> str:
    """着色规则：无数据=gray；<0.4=red；0.4~0.7=yellow；≥0.7=green"""
    if mastery is None:
        return "gray"
    if mastery < 0.4:
        return "red"
    if mastery < 0.7:
        return "yellow"
    return "green"


async def _kp_name_map(db: AsyncSession, codes: list[str | None]) -> dict[str | None, str | None]:
    """kp_code → 显示名批量映射

    KP 表命中取 name；"custom"（对话出题无归属兜底码）映射"综合练习"；
    孤儿码（不在 KP 表）→ None，由前端兜底显示。
    """
    name_map: dict[str | None, str | None] = {None: None}
    unique_codes = {c for c in codes if c}
    if not unique_codes:
        return name_map
    rows = await db.execute(
        select(KnowledgePoint.code, KnowledgePoint.name).where(
            KnowledgePoint.code.in_(unique_codes)
        )
    )
    found = dict(rows.all())
    for c in unique_codes:
        name_map[c] = "综合练习" if c == "custom" else found.get(c)
    return name_map


async def _daily_question_payload(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """今日一题装配（practice/daily 与 daily-plan 共用）：该生当日题 + 题目摘要 + 完成状态 + 打卡"""
    today = date.today()

    # 查每日一题（迭代17 学情化：每生每天一题）
    daily = await db.execute(
        select(DailyQuestion).where(DailyQuestion.date == today, DailyQuestion.user_id == user_id)
    )
    daily_q = daily.scalar_one_or_none()

    # 当日是否已提交该题组 + 首题摘要
    completed = False
    item_summary = None
    if daily_q:
        done = await db.execute(
            select(func.count()).where(
                Submission.user_id == user_id,
                Submission.quiz_id == daily_q.quiz_id,
                Submission.deleted_at.is_(None),
            )
        )
        completed = (done.scalar() or 0) > 0
        first_item = await db.execute(
            select(QuizItem)
            .where(QuizItem.quiz_id == daily_q.quiz_id, QuizItem.deleted_at.is_(None))
            .order_by(QuizItem.item_no)
            .limit(1)
        )
        item_obj = first_item.scalar_one_or_none()
        if item_obj:
            item_summary = {
                "item_no": item_obj.item_no,
                # 阶段 1 契约：interaction_type 与 practice/start 保持一致
                "interaction_type": _Q_TYPE_TO_INTERACTION.get(item_obj.q_type, "text"),
                "q_type": item_obj.q_type,
                "question_text": _safe_latex_truncate(item_obj.question_text, 100),
            }

    # 查打卡
    streak = await db.get(Streak, user_id)

    return {
        "date": today.isoformat(),
        "quiz_id": str(daily_q.quiz_id) if daily_q else None,
        "item": item_summary,
        "completed": completed,
        "streak": {
            "current": streak.current_streak if streak else 0,
            "longest": streak.longest_streak if streak else 0,
        },
    }


async def _grade_item(
    db: AsyncSession,
    quiz_item: QuizItem | None,
    q_type: str,
    answer_text: str | None,
    user_id: str = "",
    expected_answer: str | None = None,
    question_text: str | None = None,
    max_score: float | None = None,
) -> tuple[str, float | None, dict]:
    """真实判分（ADR-031）

    - choice/judge：与 quiz_items.answer 规范化比对
    - blank：check_equivalence 四层兜底（精确→数值容差→simplify→pending_review）
    - solution：AI 初批留痕，verdict=pending_review 待教师确认；LLM 不可用降级

    quiz_item 为 None（对话内 AI 出题的临时题组）时，标准答案/题干回退到
    客户端随题卡携带的 expected_answer / question_text。

    max_score：该题满分（模拟试卷按卷型分值规格传入；None 回退 10 分制）。
    返回 (verdict, score, extra)；extra 可含 error_type/comment/method/degraded/ai_pregraded。
    """
    item_score = max_score if max_score is not None and max_score > 0 else _ITEM_SCORE
    expected = (quiz_item.answer or "").strip() if quiz_item and quiz_item.answer else ""
    if not expected and expected_answer:
        expected = str(expected_answer).strip()

    if q_type in ("choice", "judge"):
        if not expected or answer_text is None:
            # 无标准答案可比对 → 待人工
            return "pending_review", None, {}
        if _match_choice(answer_text, expected):
            return "correct", item_score, {}
        return "wrong", 0.0, {}

    if q_type == "blank":
        if not answer_text or not answer_text.strip():
            return "wrong", 0.0, {}
        if not expected:
            return "pending_review", None, {}
        result = await check_equivalence(answer_text.strip(), expected)
        verdict = result.get("verdict", "pending_review")
        method = result.get("method")
        if verdict == "correct":
            return "correct", item_score, {"method": method}
        if verdict == "wrong":
            return "wrong", 0.0, {"method": method}
        return "pending_review", None, {"method": method}  # 沙箱判不出 → 待人工

    if q_type == "solution":
        return await _ai_pregrade_solution(
            quiz_item,
            answer_text,
            user_id=str(user_id),
            db=db,
            question_text=question_text,
            expected_answer=expected,
            max_score=item_score,
        )

    return "pending_review", None, {}


def _match_choice(student: str, expected: str) -> bool:
    """选择/判断答案比对：提取选项字母归一排序后整体相等。

    om5 晨间修复：多选题（answer="ACD"）支持——学生作答按字母集合排序比对；
    单选题保留"选项首字母一致"的宽容（学生答"A."/"选项A" 均可）。
    旧逻辑对多选有放水漏洞（学生"AB" vs 期望"ACD"首字母同为 A 即判对），一并修复。

    迭代20 门禁修复：判断题中文标答（answer="正确"/"错误"）此前必然判错——
    "正确"里没有 A-F 字母，两条归一串都为空走 `not s or not e → False`。
    补中文判断语义归一（正确/对/是/T 与 错误/错/否/F 各归一桶）后字符串等值比对。
    """
    s = "".join(sorted(re.findall(r"[A-F]", (student or "").upper())))
    e = "".join(sorted(re.findall(r"[A-F]", (expected or "").upper())))
    if s and e:
        if s == e:
            return True
        return len(e) == 1 and s[0] == e[0]
    # 无 A-F 字母（判断题中文答案族）→ 归一化等值比对
    ns, ne = _normalize_judge_answer(student), _normalize_judge_answer(expected)
    return bool(ns) and ns == ne


_JUDGE_TRUE = ("正确", "对", "是的", "是", "t", "true", "√", "y", "yes")
_JUDGE_FALSE = ("错误", "错", "不对", "否", "不正确", "f", "false", "×", "n", "no")


def _normalize_judge_answer(text: str | None) -> str:
    """判断题答案归一：真值族→「对」，假值族→「错」，其余原样去空白。"""
    t = (text or "").strip().lower()
    if not t:
        return ""
    if t in _JUDGE_TRUE:
        return "对"
    if t in _JUDGE_FALSE:
        return "错"
    return t


async def _load_file_ocr_text(db: AsyncSession, file_id: str, user_id: uuid.UUID) -> str | None:
    """读文件 markdown/text 解析产物（解答题拍照作答回填用，迭代10 v1.4）

    越权/不存在/未解析完成/无产物一律返回 None（调用方走 pending_review 占位，
    不判 0 分、不收录错题）。读取模式与 agent_router 附件产物一致（ADR-007）。
    """
    try:
        fid = uuid.UUID(str(file_id))
    except (ValueError, AttributeError, TypeError):
        return None
    f = await db.get(File, fid)
    if f is None or f.deleted_at or str(f.user_id) != str(user_id):
        return None
    if f.status != "parsed":
        return None
    assets = await db.execute(
        select(FileAsset)
        .where(
            FileAsset.file_id == fid,
            FileAsset.asset_type.in_(["markdown", "text"]),
            FileAsset.deleted_at.is_(None),
        )
        .order_by(FileAsset.page_no)
    )
    content = "\n".join((a.content or "") for a in assets.scalars().all()).strip()
    return content or None


async def _ai_pregrade_solution(
    quiz_item: QuizItem | None,
    answer_text: str | None,
    user_id: str = "",
    db: AsyncSession | None = None,
    question_text: str | None = None,
    expected_answer: str = "",
    max_score: float | None = None,
    allow_xingchen: bool = True,
) -> tuple[str, float | None, dict]:
    """解答题 AI 初批（留痕待教师确认）——tel D9 行为保持抽取后的兼容门面。

    实现已整体抽取到 app/services/solution_pregrade.pregrade_solution（共享模块，
    教师批改台 suggest_grade 显式触发同一管线）；本包装保留原名字与签名，
    使既有调用方（本文件 _grade_item、butler/workflow_tools 本地降级）零改动。
    """
    from app.services.solution_pregrade import pregrade_solution

    return await pregrade_solution(
        quiz_item,
        answer_text,
        user_id=user_id,
        db=db,
        question_text=question_text,
        expected_answer=expected_answer,
        max_score=max_score,
        allow_xingchen=allow_xingchen,
    )


async def _update_mastery(
    db: AsyncSession,
    user_id: uuid.UUID,
    kp_code: str,
    *,
    correct: bool,
    hint_count: int = 0,
) -> bool:
    """BKT-lite 后验更新掌握度：答对向 1 收敛、答错向 0 收敛，clamp [0,1]

    P1-4 突破（前置依赖惩罚）：子节点练习时若父节点未掌握（<0.4 红区），
    正确增益 ×0.5 衰减——前置没打牢，学后续知识事倍功半，学情如实反映。

    迭代15：返回值改为 bool——知识点未入库返回 False（调用方可如实上报 mastery_updated）。
    """
    kp_row = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
    kp_obj = kp_row.scalar_one_or_none()
    if kp_obj is None:
        return False  # 知识点未入库，跳过（不写孤儿记录）

    mr = await db.get(MasteryRecord, (user_id, kp_obj.id))
    if mr is None:
        mr = MasteryRecord(user_id=user_id, kp_id=kp_obj.id, mastery=0.5)
        db.add(mr)
        await db.flush()

    # 前置依赖惩罚：父节点存在且未掌握（无记录视为未学=未掌握）→ 正确增益 ×0.5
    gain = 0.2
    if correct and kp_obj.parent_id:
        p_mr = await db.get(MasteryRecord, (user_id, kp_obj.parent_id))
        p_mastery = float(p_mr.mastery) if p_mr is not None else 0.0
        if p_mastery < 0.4:
            gain = 0.2 * 0.5  # 前置未掌握，子节点学习收益减半

    p = float(mr.mastery)
    p = p + (1.0 - p) * gain if correct else p * 0.8
    mr.mastery = min(1.0, max(0.0, p))
    mr.practice_count = (mr.practice_count or 0) + 1
    mr.correct_count = (mr.correct_count or 0) + (1 if correct else 0)
    mr.hint_count = (mr.hint_count or 0) + max(0, hint_count)
    mr.last_practiced_at = datetime.now(UTC)

    # 同步当日掌握度快照（mastery_snapshots，F6 trend 数据源；同日同 kp 覆盖为最新后验值）
    today = date.today()
    snap = await db.execute(
        select(MasterySnapshot).where(
            MasterySnapshot.user_id == user_id,
            MasterySnapshot.kp_code == kp_code,
            MasterySnapshot.date == today,
        )
    )
    snap_obj = snap.scalar_one_or_none()
    if snap_obj is None:
        db.add(
            MasterySnapshot(user_id=user_id, kp_code=kp_code, date=today, mastery=float(mr.mastery))
        )
    else:
        snap_obj.mastery = float(mr.mastery)
    return True


async def _resolve_kp_code(
    db: AsyncSession, kp_code: str | None, kp_name: str | None
) -> str | None:
    """知识点编码解析（迭代15）：AI 侧 kp_code（如 "derivative"）与学情侧编码
    （如 MATH-G2-DERIV-103）不一致——直接落 mastery 会静默跳过。
    解析顺序：精确 code → 精确 name → name 包含（取编码最小者=章级父节点兜底）。
    """
    if kp_code:
        row = await db.execute(select(KnowledgePoint.id).where(KnowledgePoint.code == kp_code))
        if row.scalar_one_or_none() is not None:
            return kp_code
    if kp_name:
        name = kp_name.strip()
        row = await db.execute(select(KnowledgePoint).where(KnowledgePoint.name == name))
        obj = row.scalar_one_or_none()
        if obj is not None:
            return obj.code
        row = await db.execute(
            select(KnowledgePoint)
            .where(KnowledgePoint.name.contains(name))
            .order_by(KnowledgePoint.code)
        )
        obj = row.scalars().first()
        if obj is not None:
            return obj.code
    return None


async def _upsert_streak(db: AsyncSession, user_id: uuid.UUID) -> None:
    """打卡 upsert：提交当日连续天数 +1（当日重复不累计，断签重置）"""
    today = date.today()
    streak = await db.get(Streak, user_id)
    if streak is None:
        db.add(Streak(user_id=user_id, current_streak=1, longest_streak=1, last_active_date=today))
        return
    if streak.last_active_date == today:
        return  # 当日已计
    if streak.last_active_date == today - timedelta(days=1):
        streak.current_streak = (streak.current_streak or 0) + 1
    else:
        streak.current_streak = 1
    streak.longest_streak = max(streak.longest_streak or 0, streak.current_streak)
    streak.last_active_date = today


async def _auto_record_error(
    db: AsyncSession,
    user_id: uuid.UUID,
    item: dict,
    sub_item: SubmissionItem,
    quiz_item: QuizItem | None = None,
    background: BackgroundTasks | None = None,
    origin: str | None = None,
) -> None:
    """错题自动收录（题干/知识点优先取 quiz_items 实数据；错因 AI 初判异步回填，SSOT §4.9）"""
    # 题干兜底链：quiz_items → 客户端题卡 → 标准答案占位（question_text 永不落空串）
    question_text = (quiz_item.question_text if quiz_item else None) or (
        item.get("question_text") or ""
    ).strip()
    if not question_text:
        expected = (quiz_item.answer if quiz_item else None) or item.get("expected_answer") or ""
        question_text = f"（题干未提供）标准答案：{expected}" if expected else "（题干未提供）"
    # kp 兜底链：quiz_items → 客户端题卡；"custom"（对话出题无归属兜底码）不落库
    kp_code = (quiz_item.kp_code if quiz_item else None) or item.get("kp_code")
    if kp_code == "custom":
        kp_code = None

    record, created = await _upsert_error_record(
        db,
        user_id,
        question_text=question_text,
        answer_text=item.get("answer_text"),
        source_channel="auto_judge",
        error_type=None,  # AI 初判异步回填
        kp_code=kp_code,
        image=(quiz_item.image if quiz_item and quiz_item.image else (item.get("image") or [])),
        origin=origin,
    )
    await db.flush()  # 取到 record.id 供异步回填定位
    if created:
        if background is not None:
            background.add_task(_async_error_analysis, str(record.id))
        else:
            asyncio.create_task(_async_error_analysis(str(record.id)))

        # P1-5 突破（记忆激活）：错题 → 长期记忆联动——同步写一条 weak_kp 情景记忆，
        # 让"刷题错过的知识点"进入 P6 记忆槽位（对话与练习数据打通，记忆不再只来自聊天）。
        # 异步 fire-and-forget（独立会话 + 去重），任何失败只记日志，绝不阻塞判分主链路。
        if kp_code:
            if background is not None:
                background.add_task(_record_error_memory, str(user_id), kp_code, question_text)
            else:
                asyncio.create_task(_record_error_memory(str(user_id), kp_code, question_text))


# 错因初判 prompt（SSOT §4.9 wf_error_analysis 本地降级实现；五枚举严格子集 ADR-026）
_ERROR_ANALYSIS_PROMPT = """\
你是数学错因分析专家。请判断学生做错这道题的首要错因。

【题干】
{question}

【学生作答/错误表现】
{answer}

【错因定义（五选一）】
- concept：概念不清（定义/定理/性质理解错误）
- formula：公式记错（公式本身记错或用错）
- calculation：计算失误（思路正确但算错）
- logic：思路卡壳（方法/方向错误）
- reading：审题偏差（条件看漏/理解错题意）

只输出一个英文单词（concept/formula/calculation/logic/reading），拿不准输出 unknown。
"""


async def _judge_error_type(
    question_text: str,
    answer_text: str | None,
    user_id: str = "",
    db: AsyncSession | None = None,
) -> str | None:
    """错因分类（wf_error_analysis 降级实现；返回五枚举或 None）

    迭代05 1.9 接线：wf_error_analysis 优先（SSOT §4.9）→ 本地分类降级。
    db 传入时按三层解析有效配置（管理后台配置即时生效），缺省走 env。
    """
    from app.providers.xingchen import resolve_effective_xingchen_config

    xcfg = await resolve_effective_xingchen_config(db, user_id or None)
    if xcfg.enabled:
        try:
            from app.providers.xingchen import run_workflow

            wf_result = await run_workflow(
                "wf_error_analysis",
                uid=user_id or "system",
                parameters={
                    "AGENT_USER_INPUT": "分析这道题的错因",
                    "question_text": question_text[:1000],
                    "answer_text": (answer_text or "")[:500],
                    "student_answer": "",
                    "context_kp": "",
                },
                config=xcfg,
            )
            wf_error_type = wf_result.get("error_type")
            if wf_error_type in _VALID_ERROR_TYPES:
                return wf_error_type
            return None  # 工作流拿不准 → 不回填，学生可手动选择
        except Exception as e:
            logger.info("wf_error_analysis_fallback_local", error=str(e)[:150])

    # 本地降级分类
    try:
        router = get_model_router()
        result = await router.chat(
            messages=[
                {"role": "system", "content": "你是错因分类器，只输出一个单词。"},
                {
                    "role": "user",
                    "content": _ERROR_ANALYSIS_PROMPT.format(
                        question=question_text[:1000],
                        answer=(answer_text or "（未提供作答）")[:500],
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=32,
            request_id=f"errana-{uuid.uuid4().hex[:12]}",
            scene="error_analysis",
        )
        raw = (result.get("content") or "").strip().lower()
        for t in _VALID_ERROR_TYPES:
            if t in raw:
                return t
        return None  # 拿不准 → 不回填，学生可手动选择（错因必须可改是红线）
    except Exception as e:
        logger.warning("error_analysis_failed", error=str(e)[:150])
        return None


async def _async_error_analysis(record_id: str) -> None:
    """错因 AI 初判异步回填（SSOT §4.9）

    纪律：无论回填成功与否均置 ai_judged=true（避免反复触发）；
    拿不准时 error_type 保持 null 由学生手动选择。
    迭代05 1.9 接线 wf_error_analysis 后：优先工作流 → 降级本函数本地分类。
    """
    try:
        async with background_session_factory() as db:
            record = await db.get(ErrorRecord, uuid.UUID(record_id))
            if record is None or record.deleted_at or record.error_type:
                return
            error_type = await _judge_error_type(
                record.question_text, record.answer_text, user_id=str(record.user_id), db=db
            )
            # om5 修复轮 D3：LLM 调用窗口内学生可能已手改错因（阶段0 实测 TOCTOU 覆盖事故）。
            # 复查后再写：corrected_by_user 或 error_type 已有值 → 一律放弃回填。
            await db.refresh(record)
            if record.corrected_by_user or record.error_type:
                logger.info("error_analysis_skip_user_corrected", record_id=record_id)
                return
            # om5 修复轮 J4：作答缺失/无意义（如占位"X"）时禁止编造错因过程——
            # 诚实标注待补充，不回填五枚举。
            ans = (record.answer_text or "").strip()
            if len(ans) <= 2:
                if not record.note:
                    record.note = "作答缺失或过短，AI 无法归因；请补充作答或手动选择错因"
                    record.ai_judged = True
                    await db.commit()
                logger.info("error_analysis_skip_missing_answer", record_id=record_id)
                return
            if error_type:
                record.error_type = error_type
            record.ai_judged = True
            await db.commit()
            logger.info("error_analysis_backfilled", record_id=record_id, error_type=error_type)
    except Exception as e:
        logger.warning("error_analysis_task_failed", record_id=record_id, error=str(e)[:150])


async def _record_error_memory(user_id: str, kp_code: str, question_text: str) -> None:
    """错题 → 长期记忆（weak_kp）写入（P1-5 突破）

    独立会话异步执行：同一 (user_id, content) 活跃行已存在则跳过（去重）；
    内容取"常错知识点 + 题干摘要"，带 kp 来源标记。任何异常吞掉只记日志。
    """
    try:
        from app.models.episodic_memory import EpisodicMemory

        # 用独立会话查询知识点显示名（本函数无 db 句柄，独立开 session）
        async with background_session_factory() as db:
            name_row = await db.execute(
                select(KnowledgePoint.name).where(KnowledgePoint.code == kp_code)
            )
            kp_display = name_row.scalar_one_or_none() or kp_code
            content = f"常错知识点：{kp_display}"
            stem = (question_text or "").strip()[:40]
            if stem:
                content += f"（{stem}…）"
            content = content[:200]
            dup = await db.execute(
                select(EpisodicMemory.id)
                .where(
                    EpisodicMemory.user_id == uuid.UUID(user_id),
                    EpisodicMemory.kind == "weak_kp",
                    EpisodicMemory.content == content,
                    EpisodicMemory.deleted_at.is_(None),
                )
                .limit(1)
            )
            if dup.scalar_one_or_none() is not None:
                return  # 已存在同内容记忆（去重）
            db.add(
                EpisodicMemory(
                    user_id=uuid.UUID(user_id),
                    kind="weak_kp",
                    content=content,
                    source="quiz",
                    importance=4,  # 错题记忆重要度高于普通对话事实
                )
            )
            await db.commit()
            logger.info("error_memory_recorded", user_id=user_id, kp=kp_code)
    except Exception as e:
        logger.warning("error_memory_record_failed", user_id=user_id, error=str(e)[:150])


async def _update_weak_points(db: AsyncSession, user_id: uuid.UUID) -> None:
    """薄弱知识点联动更新（SSOT §5.12：提交成功即触发 user_profiles.weak_points）

    口径：mastery 最低的 3 个知识点（与 mastery/summary top_weak 同源）。
    """
    rows = await db.execute(
        select(MasteryRecord.mastery, KnowledgePoint.code)
        .join(KnowledgePoint, MasteryRecord.kp_id == KnowledgePoint.id)
        .where(MasteryRecord.user_id == user_id)
        .order_by(MasteryRecord.mastery.asc())
        .limit(3)
    )
    weak = [code for _, code in rows.all()]
    profile = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
    profile.weak_points = weak


class QuizGenerationError(Exception):
    """题组生成失败（LLM 不可用或输出非法）——调用方应返回明确错误而非空题组"""


def _normalize_options(options) -> dict | None:
    """options 归一化为 dict（smart_quiz JSON 产出 list，quiz_items.options 为 JSONB dict）"""
    if isinstance(options, dict):
        return options
    if isinstance(options, list) and options:
        return {chr(ord("A") + i): str(opt) for i, opt in enumerate(options)}
    return None


def _normalize_stem(text: str) -> str:
    """规范化题干（去全部空白 + 小写）——重复题判定口径，与前端/测试一致"""
    return "".join(str(text).split()).lower()


async def _generate_one_quiz_item(
    llm,
    quiz: Quiz,
    kp_code: str,
    kp_name: str,
    difficulty: str,
    q_type: str,
    db: AsyncSession | None = None,
    xcfg=None,
) -> dict:
    """单题生成（迭代05 1.9 接线 + 阶段3 质量闸闭环）：

    wf_smart_quiz 优先 → 本地 generate_quiz_item 降级；生成后必过质量四闸
    （run_quiz_gates，A-P0-1 闭环），未过闸携失败原因重试 2 次（B-P1-8，
    SSOT §4.7"机检不过重试 2 次后 50301"），仍失败抛 QuizGenerationError。
    降级切换不让前端感知（星辰指南 §4）。
    db 传入时按三层解析有效配置（管理后台配置即时生效），缺省走 env。
    """
    from app.providers.xingchen import resolve_effective_xingchen_config
    from app.skills.smart_quiz.main import RETRY_FEEDBACK, run_quiz_gates

    # xcfg 预解析传入时跳过 db 解析（并发组卷路径共用同一 AsyncSession，
    # 配置已在调用方串行解析好，避免并发查询同一 session）
    if xcfg is not None:
        pass
    elif db is not None:
        xcfg = await resolve_effective_xingchen_config(
            db, str(quiz.user_id) if quiz.user_id else None
        )
    else:
        xcfg = await resolve_effective_xingchen_config(None, None)
    last_failures: list[str] = []
    retry_feedback = ""
    for attempt in range(3):  # 首次 + 重试 2 次
        quiz_data: dict | None = None

        # 首次尝试：星辰 wf_smart_quiz 优先（开启时）
        if xcfg.enabled and attempt == 0:
            try:
                from app.providers.xingchen import run_workflow

                result = await run_workflow(
                    "wf_smart_quiz",
                    uid=str(quiz.user_id),
                    parameters={
                        "AGENT_USER_INPUT": f"出一道{kp_name}·{difficulty}·{q_type}题",
                        "kp_name": kp_name,
                        "kp_code": kp_code,
                        "difficulty": difficulty,
                        "q_type": q_type,
                        "grade_hint": "G2",
                    },
                    config=xcfg,
                )
                candidate = {
                    "q_type": q_type,
                    "question_text": result.get("question_text") or "",
                    "options": result.get("options"),
                    "answer": result.get("answer") or "",
                    "answer_analysis": result.get("explanation") or "",
                    "difficulty": result.get("difficulty") or difficulty,
                }
                if candidate["question_text"] and candidate["answer"]:
                    quiz_data = candidate
                    logger.info("quiz_via_workflow", flow="wf_smart_quiz", kp=kp_code)
                else:
                    logger.info("wf_smart_quiz_invalid_output_fallback")
            except Exception as e:
                logger.info("wf_smart_quiz_fallback_local", error=str(e)[:150])

        # 本地生成（首次或工作流不可用/输出非法；重试携失败原因）
        if quiz_data is None:
            quiz_data, _raw = await generate_quiz_item(
                llm,
                kp_code=kp_code,
                kp_name=kp_name,
                difficulty=difficulty,
                q_type=q_type,
                request_id=f"quizgen-{uuid.uuid4().hex[:12]}",
                retry_feedback=retry_feedback,
                temperature=0.8 if attempt == 0 else 0.5,
            )

        if not quiz_data or not quiz_data.get("question_text") or not quiz_data.get("answer"):
            last_failures = ["LLM 输出非法（缺题干或答案）"]
        else:
            # 质量四闸（与 chat 出题路径同一实现，SSOT §4.7 本地机检终闸）
            # + 闸 5 答案键独立黑盒复核（N2：判分键错位事故防复发）
            passed, failures, _notes = await run_quiz_gates(quiz_data)
            if passed:
                from app.skills.smart_quiz.main import verify_answer_key

                ok, reason, _n = await verify_answer_key(
                    quiz_data, llm, f"quizgen-{uuid.uuid4().hex[:12]}"
                )
                if not ok:
                    passed, failures = False, [reason]
            if passed:
                return quiz_data
            last_failures = failures
            logger.info(
                "quiz_gate_failed",
                attempt=attempt + 1,
                kp=kp_code,
                failures=";".join(failures)[:150],
            )

        # 携失败原因重生成（重试降温度）
        retry_feedback = RETRY_FEEDBACK.format(failures="；".join(last_failures)[:300])

    raise QuizGenerationError(f"质量闸重试 2 次仍未通过：{';'.join(last_failures)[:100]}")


async def _fill_quiz_items(
    db: AsyncSession,
    quiz: Quiz,
    specs: list[tuple[str, str, str]],
    *,
    start_no: int = 1,
    exclude_stems: set[str] | None = None,
) -> None:
    """按 (kp_code, difficulty, q_type) 规格为题组真实生成 QuizItem（复用 smart_quiz）。

    任一题生成失败抛 QuizGenerationError，调用方回滚，不出空题组。
    start_no：题号起始（题库题已占前序题号时从其后编号）。

    去重护栏（阶段 1.1）：exclude_stems 为已选题规范化题干集合（如题库题题干）；
    AI 题按规范化题干与已选题（题库 + 并发 AI 题）排重，重复则换题重试
    （每槽位最多 3 次尝试），仍重复/失败弃题；不足 min_ok 抛 QuizGenerationError。
    """
    llm = get_model_router()
    # 先串行解析 kp 显示名与星辰配置（同一 AsyncSession 不能并发执行查询；
    # xcfg 串行解析一次后传入并发生成，避免并发任务争抢 session）
    from app.providers.xingchen import resolve_effective_xingchen_config

    xcfg = await resolve_effective_xingchen_config(db, str(quiz.user_id) if quiz.user_id else None)
    kp_names: dict[str, str] = {}
    for kp_code, _d, _q in specs:
        if kp_code not in kp_names:
            kp_row = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
            kp_obj = kp_row.scalar_one_or_none()
            kp_names[kp_code] = kp_obj.name if kp_obj else KP_MAP.get(kp_code, kp_code)

    # 大题量专练（count 5~30）串行逐题生成不可接受：并发 Semaphore(3) 限流，
    # LLM 调用全部完成后再顺序落库（AsyncSession 不跨并发共享，故 db=None）。
    # 韧性策略（与组卷同纪律）：单题失败不拖垮整套——填空题先降级为选择题重试一次，
    # 仍失败弃题；成功题数 <70% 计划数才抛 QuizGenerationError（绝不出空题组）。
    sem = asyncio.Semaphore(3)

    # 已选/已生成题目的规范化题干集合（asyncio 单线程：检查-添加之间无 await，原子安全）
    seen_stems: set[str] = set(exclude_stems or ())
    MAX_AI_TRIES = 3  # 每个槽位最多 3 次尝试（原始 + 2 次去重换题）

    async def _try_generate_item(
        llm, quiz, kp_code: str, kp_name: str, difficulty: str, q_type: str, xcfg
    ) -> dict | None:
        """单次生成尝试：失败/降级/缺字段返回 None；未知异常上抛"""
        try:
            quiz_data = await _generate_one_quiz_item(
                llm,
                quiz,
                kp_code,
                kp_name,
                difficulty,
                q_type,
                db=None,
                xcfg=xcfg,
            )
        except QuizGenerationError:
            if q_type != "blank":
                logger.warning("quiz_item_dropped", kp=kp_code, q_type=q_type)
                return None
            # 填空题机检门槛高（SymPy 可解析），失败降级为选择题重试一次
            logger.info("quiz_blank_fallback_choice", kp=kp_code)
            try:
                quiz_data = await _generate_one_quiz_item(
                    llm,
                    quiz,
                    kp_code,
                    kp_name,
                    difficulty,
                    "choice",
                    db=None,
                    xcfg=xcfg,
                )
            except QuizGenerationError:
                logger.warning("quiz_item_dropped", kp=kp_code, q_type="blank→choice")
                return None
        except Exception as e:
            raise QuizGenerationError(f"LLM 出题失败: {str(e)[:100]}") from None
        if not quiz_data or not quiz_data.get("question_text") or not quiz_data.get("answer"):
            logger.warning("quiz_item_dropped", kp=kp_code, reason="缺题干或答案")
            return None
        return quiz_data

    async def _gen(spec: tuple[str, str, str]) -> dict | None:
        kp_code, difficulty, q_type = spec
        async with sem:
            for _attempt in range(MAX_AI_TRIES):
                quiz_data = await _try_generate_item(
                    llm, quiz, kp_code, kp_names[kp_code], difficulty, q_type, xcfg
                )
                if quiz_data is None:
                    return None
                norm = _normalize_stem(str(quiz_data.get("question_text") or ""))
                if norm and norm not in seen_stems:
                    seen_stems.add(norm)
                    return quiz_data
                # 与已选题（题库 / 并发 AI 题）题干重复 → 换题重试，绝不落重复题
                logger.info("quiz_item_duplicate_retry", kp=kp_code, attempt=_attempt + 1)
            return None

    settled = await asyncio.gather(*(_gen(spec) for spec in specs))
    # om5 修复轮 J3：小题组允许缺一（N-1 也成组）——3 题组原 ceil(0.7*3)=3 即要求全成，
    # 错题重练 2/3 即整组 50301 拒绝。放宽为 70% 与 N-1 取小，且至少 1 题。
    min_ok = max(1, min(math.ceil(len(specs) * 0.7), len(specs) - 1)) if len(specs) > 1 else 1
    pairs = [(spec, data) for spec, data in zip(specs, settled, strict=True) if data]
    if len(pairs) < min_ok:
        raise QuizGenerationError(
            f"可用题数不足（{len(pairs)}/{len(specs)}），为保证不把错题给学生，本次不成组"
        )
    if len(pairs) < len(specs):
        logger.info("quiz_items_partial", planned=len(specs), actual=len(pairs))

    for item_no, ((kp_code, difficulty, q_type), quiz_data) in enumerate(pairs, start=start_no):
        db.add(
            QuizItem(
                quiz_id=quiz.id,
                item_no=item_no,
                q_type=str(quiz_data.get("q_type") or q_type),
                question_text=str(quiz_data["question_text"]),
                options=_normalize_options(quiz_data.get("options")),
                answer=str(quiz_data["answer"]),
                answer_analysis=quiz_data.get("answer_analysis"),
                kp_code=kp_code,
                difficulty=str(quiz_data.get("difficulty") or difficulty),
                ai_generated=True,
                sympy_check_code=quiz_data.get("sympy_check_code"),
            )
        )
    await db.flush()

    # P1-3 防幻觉评分（迭代18）：练题补缺 AI 题逐题评分落库（与对话出题同一把尺）
    try:
        from app.services.hallucination_score import persist_scores, score_items

        score_rows = await score_items(
            [data for _spec, data in pairs],
            kp_names=[kp_names.get(kp_code, kp_code) for (kp_code, _d, _q), _ in pairs],
            expected_difficulties=[difficulty for (_k, difficulty, _q), _ in pairs],
            kp_codes=[kp_code for (kp_code, _d, _q), _ in pairs],
        )
        await persist_scores(
            score_rows,
            scene="smart_quiz_practice",
            request_id=f"practice:{quiz.id}",
        )
    except Exception as _se:
        logger.warning("practice_score_failed", error=str(_se)[:150])


async def _pick_weak_kp(db: AsyncSession, user_id: uuid.UUID, kp_pool: list[str]) -> str:
    """学情化每日一题选题（迭代17）：该生薄弱知识点 Top 优先（mastery 升序、练习量少者优先），
    无学情数据时回退日期轮换（保证确定性，全站兜底）。"""
    pool_set = set(kp_pool)
    rs = await db.execute(
        select(KnowledgePoint.code, MasteryRecord.mastery, MasteryRecord.practice_count)
        .join(KnowledgePoint, MasteryRecord.kp_id == KnowledgePoint.id)
        .where(MasteryRecord.user_id == user_id)
        .order_by(MasteryRecord.mastery.asc(), MasteryRecord.practice_count.asc())
    )
    weak_codes = [str(c) for c, _m, _p in rs.all() if str(c) in pool_set]
    if weak_codes:
        return weak_codes[0]
    return kp_pool[date.today().toordinal() % len(kp_pool)]


async def _generate_daily_quiz(
    db: AsyncSession, user_id: uuid.UUID, kp_code: str | None = None
) -> uuid.UUID:
    """生成每日一题（迭代17 学情化：知识点按该生薄弱点轮换，题库优先 → 缺口真实生成 1 道选择题）

    题库优先：题库命中当日 kp 的中等选择题则直接用真题（0 LLM 调用）。
    """
    if kp_code is None:
        kp_rows = await db.execute(select(KnowledgePoint.code).order_by(KnowledgePoint.code))
        kp_pool = [str(c) for c in kp_rows.scalars().all()]
        if not kp_pool:
            kp_pool = list(KP_MAP.keys())
        kp_code = await _pick_weak_kp(db, user_id, kp_pool)
    quiz = Quiz(
        user_id=user_id,
        source="daily",
        title=f"每日一题 {date.today()}",
        kp_codes=[kp_code],
    )
    db.add(quiz)
    await db.flush()

    bank_rows = await supply_questions(
        db, kp_codes=[kp_code], q_type="choice", difficulty="medium", count=1
    )
    if bank_rows:
        db.add(quiz_item_from_bank(bank_rows[0], quiz_id=quiz.id, item_no=1, kp_code=kp_code))
        await db.flush()
    else:
        await _fill_quiz_items(db, quiz, [(kp_code, "medium", "choice")])

    daily = DailyQuestion(user_id=user_id, date=date.today(), quiz_id=quiz.id)
    db.add(daily)
    await db.flush()
    return quiz.id


async def _daily_bank_fallback(db: AsyncSession, user_id: uuid.UUID, daily_kp: str) -> uuid.UUID:
    """日练兜底（D7）：AI 生成整组被质量闸拦下时（如解析含过程性语言 3 连拒），
    改供题库真题保证"每日一题"不空手。不放宽任何质量闸，只换题源。"""
    candidates = await supply_questions(db, kp_codes=[daily_kp], q_type="choice", count=1)
    if not candidates:
        candidates = await supply_questions(
            db, kp_codes=list(KP_MAP.keys()), q_type="choice", difficulty="medium", count=1
        )
    if not candidates:
        raise QuizGenerationError("题库暂无可用真题，每日一题生成失败")
    quiz = Quiz(
        user_id=user_id,
        source="daily",
        title=f"每日一题 {date.today()}",
        kp_codes=[daily_kp],
    )
    db.add(quiz)
    await db.flush()
    db.add(quiz_item_from_bank(candidates[0], quiz_id=quiz.id, item_no=1, kp_code=daily_kp))
    db.add(DailyQuestion(user_id=user_id, date=date.today(), quiz_id=quiz.id))
    await db.flush()
    return quiz.id


async def _generate_retry_quiz(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """重练：取最近错题关联知识点出题（去重，最多 3 个 kp；逐槽位题库优先，缺口 LLM）"""
    rows = await db.execute(
        select(ErrorRecord.kp_code)
        .where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.kp_code.is_not(None),
            ErrorRecord.deleted_at.is_(None),
        )
        .order_by(ErrorRecord.created_at.desc())
        .limit(20)
    )
    kp_codes = list(dict.fromkeys(r[0] for r in rows.all()))[:3]
    if not kp_codes:
        raise QuizGenerationError("暂无错题记录，无法生成重练题组")

    quiz = Quiz(user_id=user_id, source="retry", title="错题重练", kp_codes=kp_codes)
    db.add(quiz)
    await db.flush()

    # 题库优先：逐槽位抽题（卷内去重），缺口槽位收集后走 LLM 生成
    picked_hashes: set[str] = set()
    ai_specs: list[tuple[str, str, str]] = []
    item_no = 0
    for c in kp_codes:
        bank_rows = await supply_questions(
            db,
            kp_codes=[c],
            q_type="choice",
            difficulty="medium",
            count=1,
            exclude_hashes=picked_hashes,
        )
        if bank_rows:
            picked_hashes.add(bank_rows[0].hash)
            item_no += 1
            db.add(quiz_item_from_bank(bank_rows[0], quiz_id=quiz.id, item_no=item_no, kp_code=c))
        else:
            ai_specs.append((c, "medium", "choice"))
    await db.flush()
    if ai_specs:
        await _fill_quiz_items(db, quiz, ai_specs, start_no=item_no + 1)
    return quiz.id


# 专练槽位难度/题型梯度：easy→medium→hard 循环、choice/choice/blank 循环
# （前 3 槽与存量契约一致：easy choice / medium choice / hard blank）
_SPECIAL_PATTERN = [("easy", "choice"), ("medium", "choice"), ("hard", "blank")]


def _special_specs(kp_code: str, count: int) -> list[tuple[str, str, str]]:
    """专练 AI 缺口槽位：按存量梯度循环展开 count 个 (kp_code, difficulty, q_type) 规格"""
    return [(kp_code, *_SPECIAL_PATTERN[i % 3]) for i in range(count)]


async def _generate_special_quiz(
    db: AsyncSession,
    user_id: uuid.UUID,
    kp_code: str,
    *,
    count: int = 3,
    bank_rows: list | None = None,
) -> uuid.UUID:
    """专练：题库优先（真题检索，题型/难度不限随机取），缺口按难度梯度 LLM 生成+四闸

    count 缺省 3 保持存量内部契约；路由层 special 默认 5、上限 30。
    bank_rows：路由层已探测的题库命中行（避免二次随机抽样不一致）；None 时本函数自探。
    Quiz.source 如实标注构成：纯题库 bank / 混合 mixed / 纯 AI ai_generated。

    去重护栏（阶段 1.1）：题库行按 hash 排重；AI 补题按规范化题干与已选题排重（重复重试换题）；
    最终题数不足 count 时抛 QuizGenerationError（明确失败，绝不返回重复题凑数）。
    """
    quiz = Quiz(
        user_id=user_id, source="ai_generated", title=f"专练：{kp_code}", kp_codes=[kp_code]
    )
    db.add(quiz)
    await db.flush()

    rows = (
        bank_rows
        if bank_rows is not None
        else await supply_questions(db, kp_codes=[kp_code], count=count)
    )
    # 题库行 hash 排重（防御：hash 唯一约束下同 hash 不会出现，防同题干不同 hash 的脏数据）
    seen_hashes: set[str] = set()
    unique_rows: list = []
    for row in rows:
        h = getattr(row, "hash", None)
        if h is not None and h in seen_hashes:
            continue
        if h is not None:
            seen_hashes.add(h)
        unique_rows.append(row)

    item_no = 0
    for row in unique_rows:
        item_no += 1
        db.add(quiz_item_from_bank(row, quiz_id=quiz.id, item_no=item_no, kp_code=kp_code))
    await db.flush()

    deficit = count - len(unique_rows)
    seen_stems = {_normalize_stem(row.stem) for row in unique_rows}
    if deficit > 0:
        await _fill_quiz_items(
            db,
            quiz,
            _special_specs(kp_code, deficit),
            start_no=item_no + 1,
            exclude_stems=seen_stems,
        )
    quiz.source = "bank" if deficit == 0 else ("mixed" if unique_rows else "ai_generated")
    await db.flush()

    # 最终数量校验：去重后仍不足 count → 明确失败，不返回重复题凑数
    total = (
        await db.execute(
            select(func.count(QuizItem.id)).where(
                QuizItem.quiz_id == quiz.id, QuizItem.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    # om5 晨间修复（J3 同类）：practice special 允许 N-1 成组——9/10 被硬拒是用户实测
    # "练题中心没题/只有 1 题"的直接根因（日志 quiz_generation_failed 9/10）。
    min_ok = max(1, min(count, math.ceil(count * 0.7), count - 1)) if count > 1 else 1
    if total < min_ok:
        raise QuizGenerationError(
            f"可用题数不足（{total}/{count}），为保证不把错题给学生，本次不成组"
        )
    return quiz.id
