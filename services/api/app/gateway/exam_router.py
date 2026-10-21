"""学生端模拟试卷/专题训练路由（组卷功能）

端点：
- POST /api/student/exam/generate — 组卷（full_mock 全真模拟 / topic 专题训练）
- GET /api/student/exam/history — 本人试卷列表 + 成绩聚合（best/last/attempts）
- GET /api/student/exam/{exam_id} — 试卷详情（提交后逐题带判分结果）

判分复用既有 POST /api/student/practice/submit（quiz_id 归属校验已有，本路由不重复实现）。
组卷逻辑在 app/skills/mock_exam.py，本文件只做参数校验/信封/日限/聚合查询。
"""

from __future__ import annotations

import re
import uuid

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import get_current_user
from app.models.coursework import Quiz, QuizItem, Submission, SubmissionItem
from app.models.database import get_db
from app.models.question_bank import QuestionBank
from app.providers.router import get_model_router
from app.skills.mock_exam import (
    EXAM_SPECS,
    STRUCTURE_NOTE,
    ExamDailyCapError,
    ExamGenerationError,
    ExamModuleNotFoundError,
    assemble_exam,
    assemble_real_paper,
    build_structure,
    exam_type_of,
)
from app.skills.question_supply import daily_ai_used

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/student/exam", tags=["student-exam"])


# ==================== Schemas ====================


class ExamGenerateRequest(BaseModel):
    type: str  # full_mock/topic/real_paper
    kp_module: str | None = None  # 顶级模块 code 如 MATH-G1-FUNC（topic 必填）
    title: str | None = None
    year: int | None = None  # real_paper 必填：真题年份（2010-2024）
    vol: str | None = None  # real_paper 可选：卷别（Ⅰ卷/Ⅱ卷/新课标卷）


# ==================== 辅助 ====================


def _item_payload(item: QuizItem) -> dict:
    """成卷题目对外形状（与 practice/start 的 items 一致，不含答案）"""
    return {
        "item_no": item.item_no,
        "q_type": item.q_type,
        "question_text": item.question_text,
        "options": item.options,
        "kp_code": item.kp_code,
        "difficulty": item.difficulty,
        "ai_generated": item.ai_generated,
        "source": item.source,  # 阶段3 来源透明：题库真题来源（AI 题为 None）
        "image": item.image or [],  # 配图（data URI / URL 列表，P2-5）
    }


# ==================== 组卷 ====================


@router.post("/generate")
async def exam_generate(
    req: ExamGenerateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """组卷（全真模拟/专题训练）：题库真题优先 → AI 生成只补缺口（缺口逐题并发 + 质量四闸）"""
    user_id = uuid.UUID(user["sub"])

    # 卷型二枚举校验（非法值拒绝而非静默按 full_mock）
    if req.type not in EXAM_SPECS:
        return {
            "code": 40001,
            "message": f"非法卷型: {req.type}，仅支持 full_mock/topic/real_paper",
        }
    # 专题训练必须指定顶级模块
    kp_module = (req.kp_module or "").strip()
    if req.type == "topic" and not kp_module:
        return {"code": 40001, "message": "专题训练须指定顶级模块 kp_module"}
    # 阶段5 真题套卷：按年份抽题库真题，全真题不 AI 补题
    if req.type == "real_paper":
        year = req.year or 0
        if not (2010 <= year <= 2024):
            return {"code": 40001, "message": "真题卷须指定年份（2010-2024）"}
        try:
            result = await assemble_real_paper(
                db, year, req.title, user_id, vol=(req.vol or "").strip() or None
            )
        except ExamGenerationError as e:
            await db.rollback()
            logger.warning("real_paper_failed", year=year, error=str(e)[:100])
            return {"code": 50301, "message": f"组卷失败：{e}"}
        return {
            "code": 0,
            "data": await _generate_response(result, db, user_id, structure_note=False),
        }

    try:
        result = await assemble_exam(
            db,
            get_model_router(),
            user_id=user_id,
            exam_type=req.type,
            kp_module=kp_module or None,
            title=req.title,
        )
    except ExamModuleNotFoundError as e:
        return {"code": 40400, "message": str(e)}
    except ExamDailyCapError as e:
        # 日限只限 AI 生成题：题库真题不占额度；判定发生在 LLM 调用前（不烧额度）
        await db.rollback()
        return {"code": 42901, "message": str(e)}
    except ExamGenerationError as e:
        # 可用题不足（题库+AI 合并 <70%）：不落任何数据，明确报错而非空卷子
        await db.rollback()
        logger.warning("exam_generation_failed", type=req.type, error=str(e))
        return {"code": 50301, "message": f"组卷失败：{e}"}

    return {
        "code": 0,
        "data": await _generate_response(
            result, db, user_id, structure_note=(req.type == "full_mock")
        ),
    }


async def _generate_response(result: dict, db, user_id, *, structure_note: bool) -> dict:
    """组卷成功后的统一响应（generate 与 real_paper 共用）"""
    data: dict = {
        "exam_id": str(result["quiz"].id),
        "title": result["title"],
        "type": result["type"],
        "duration_minutes": result["duration_minutes"],
        "total_score": result["total_score"],
        "structure": result["structure"],
        "planned": result["planned"],
        "dropped": result["dropped"],
        # 构成标注：题库真题 / AI 生成各多少题
        "bank_count": result["bank_count"],
        "ai_count": result["ai_count"],
        "items": [_item_payload(i) for i in result["items"]],
        # 日限口径：只计 AI 生成题（题库真题不占额度）；used 含本次成卷的 AI 题（生成即计）
        "daily_cap": {
            "limit": settings.student_daily_practice_limit,
            "used": await daily_ai_used(db, user_id),
        },
    }
    if structure_note:
        # 结构偏差如实标注（多选不支持的原因，见 mock_exam 模块 docstring）
        data["structure_note"] = STRUCTURE_NOTE
    return data


# ==================== 真题套卷（阶段5） ====================


@router.get("/real-papers")
async def real_papers(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """可组卷的真题年份列表（题库 is_real_exam + scope=student，按年份聚合）"""
    user_id = uuid.UUID(user["sub"])
    rows = await db.execute(
        select(QuestionBank.year, QuestionBank.source, QuestionBank.q_type).where(
            QuestionBank.deleted_at.is_(None),
            QuestionBank.scope == "student",
            QuestionBank.is_real_exam.is_(True),
            QuestionBank.year.is_not(None),
        )
    )

    def _vol(src: str) -> str:
        if "甲卷" in src:
            return "甲卷"
        if "乙卷" in src:
            return "乙卷"
        if "Ⅱ" in src or "ⅱ" in src or re.search(r"II", src):
            return "Ⅱ卷"
        if "Ⅰ" in src or "ⅰ" in src or re.search(r"I", src):
            return "Ⅰ卷"
        return "新课标卷"

    agg: dict[tuple, dict] = {}
    for yr, src_name, q_type in rows.all():
        v = _vol(src_name or "")
        key = (yr, v)
        agg.setdefault(
            key, {"year": yr, "vol": v, "count": 0, "choice": 0, "blank": 0, "solution": 0}
        )
        agg[key]["count"] += 1
        if q_type in ("choice", "blank", "solution"):
            agg[key][q_type] += 1
    years = sorted(agg.values(), key=lambda x: (-x["year"], x["vol"]))
    used = (
        await db.execute(
            select(func.count())
            .select_from(Quiz)
            .where(
                Quiz.user_id == user_id,
                Quiz.deleted_at.is_(None),
                Quiz.source == "exam:real_paper",
            )
        )
    ).scalar_one_or_none()
    return {"code": 0, "data": {"years": years, "my_real_attempts": int(used or 0)}}


# ==================== 历史 ====================


@router.get("/history")
async def exam_history(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """本人试卷列表 + 提交成绩聚合（最高分/最近分/次数），按创建时间倒序分页"""
    user_id = uuid.UUID(user["sub"])

    base = select(Quiz).where(
        Quiz.user_id == user_id,
        Quiz.deleted_at.is_(None),
        Quiz.source.like("exam:%"),
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    # 最近一次提交分数（相关子查询，随聚合一行带出；
    # 显式 correlate(Quiz)：外层 JOIN 了 submissions，自动关联会把子查询 FROM 全吃掉）
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
    rows = (
        await db.execute(
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
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    # 本页试卷实际题量（total_score 按卷型分值规格折算，一次 grouped 查询防 N+1）
    page_ids = [r.id for r in rows]
    counts_map: dict[uuid.UUID, dict[str, int]] = {}
    if page_ids:
        count_rows = (
            await db.execute(
                select(QuizItem.quiz_id, QuizItem.q_type, func.count())
                .where(QuizItem.quiz_id.in_(page_ids), QuizItem.deleted_at.is_(None))
                .group_by(QuizItem.quiz_id, QuizItem.q_type)
            )
        ).all()
        for quiz_id, q_type, cnt in count_rows:
            counts_map.setdefault(quiz_id, {})[q_type] = int(cnt)

    items = []
    for r in rows:
        etype = exam_type_of(r.source)
        total_score = None
        if etype:
            _structure, total_score = build_structure(etype, counts_map.get(r.id, {}))
        items.append(
            {
                "exam_id": str(r.id),
                "title": r.title,
                "type": etype,
                "total_score": total_score,
                # 规定用时（卷型规格；实际作答用时未单独落库，M2 最小实现不统计）
                "duration_minutes": EXAM_SPECS[etype]["duration_minutes"] if etype else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "best_score": float(r.best_score) if r.best_score is not None else None,
                "last_score": float(r.last_score) if r.last_score is not None else None,
                "attempts": int(r.attempts),
            }
        )

    return {"code": 0, "data": {"total": total, "items": items}}


# ==================== 详情 ====================


@router.get("/{exam_id}")
async def exam_detail(
    exam_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """试卷详情：未提交不含答案；本人已有提交则逐题带 expected_answer + verdict/score"""
    user_id = uuid.UUID(user["sub"])

    quiz = await db.get(Quiz, exam_id)
    etype = exam_type_of(quiz.source) if quiz is not None else None
    # 越权不泄露存在性：非本人/非试卷/已软删一律 40400
    if quiz is None or quiz.deleted_at or str(quiz.user_id) != str(user_id) or etype is None:
        return {"code": 40400, "message": "试卷不存在"}

    items = (
        (
            await db.execute(
                select(QuizItem)
                .where(QuizItem.quiz_id == quiz.id, QuizItem.deleted_at.is_(None))
                .order_by(QuizItem.item_no)
            )
        )
        .scalars()
        .all()
    )

    # 本人最近一次提交（判分走 practice/submit，此处只读结果）
    latest_sub = (
        await db.execute(
            select(Submission)
            .where(
                Submission.quiz_id == quiz.id,
                Submission.user_id == user_id,
                Submission.deleted_at.is_(None),
            )
            .order_by(Submission.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    sub_items: dict[int, SubmissionItem] = {}
    if latest_sub is not None:
        si_rows = (
            (
                await db.execute(
                    select(SubmissionItem).where(
                        SubmissionItem.submission_id == latest_sub.id,
                        SubmissionItem.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        sub_items = {si.item_no: si for si in si_rows}

    items_data = []
    for i in items:
        entry = _item_payload(i)
        if latest_sub is not None:
            # 已提交才放标准答案（未提交泄答案会破坏模拟考意义）
            entry["expected_answer"] = i.answer
            si = sub_items.get(i.item_no)
            if si is not None:
                entry["verdict"] = si.verdict
                entry["score"] = float(si.score) if si.score is not None else None
        items_data.append(entry)

    counts: dict[str, int] = {}
    for i in items:
        counts[i.q_type] = counts.get(i.q_type, 0) + 1
    structure, total_score = build_structure(etype, counts)

    # 构成标注（与 /generate 对齐）：题库真题 / AI 生成各多少题
    bank_count = sum(1 for i in items if not i.ai_generated)
    ai_count = len(items) - bank_count

    return {
        "code": 0,
        "data": {
            "exam_id": str(quiz.id),
            "title": quiz.title,
            "type": etype,
            "duration_minutes": EXAM_SPECS[etype]["duration_minutes"],
            "total_score": total_score,
            "structure": structure,
            "bank_count": bank_count,
            "ai_count": ai_count,
            "submitted": latest_sub is not None,
            "items": items_data,
        },
    }
