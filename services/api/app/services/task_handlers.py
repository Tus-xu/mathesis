"""任务处理器注册表（task_handlers · 阶段3）

黄金路径 S-B2 后端：practice.generate —— 复用练题中心同一出题内核
（student_router._generate_special_quiz + question_supply 题库优先/日限口径），
单一内核纪律：管家后台任务与练题中心直开走同一实现，禁止第二套生成逻辑。

任务来源：AI 管家对话下单（阶段4 接 Butler v2 工具）与任务中心重试；
本文件在 main.py lifespan 中 import 以完成注册（import 副作用即注册）。
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_point import KnowledgePoint
from app.models.task import Task
from app.services import task_runner
from app.services.task_runner import TaskPermanentError

logger = structlog.get_logger()

# 复用练题中心内核（student_router 不做任何改动）
from app.gateway.student_router import (  # noqa: E402  （置于 runner 注册之后避免循环）
    QuizGenerationError,
    _generate_special_quiz,
)
from app.models.coursework import QuizItem  # noqa: E402
from app.skills.question_supply import daily_ai_used, supply_questions  # noqa: E402
from app.config import settings  # noqa: E402


@task_runner.register_handler("practice.generate")
async def practice_generate(task: Task, db: AsyncSession, progress) -> dict:
    """S-B2 黄金路径：后台生成专练（题库优先 → AI 补缺口 → 五闸，同一内核）。

    payload: {kp_code: str, count: int(5~30)}
    result:  {artifact_type:"practice", quiz_id, question_count, bank_count, jump}
    """
    payload = task.payload or {}
    kp_code = str(payload.get("kp_code") or "").strip()
    count = int(payload.get("count") or 5)
    user_id = task.user_id

    if not kp_code:
        raise TaskPermanentError("缺少知识点参数，请告诉管家要练哪个知识点")
    if not (5 <= count <= 30):
        raise TaskPermanentError(f"非法题量: {count}，支持 5~30 题")

    kp_exists = await db.execute(
        select(KnowledgePoint.id).where(KnowledgePoint.code == kp_code).limit(1)
    )
    if kp_exists.scalar_one_or_none() is None:
        raise TaskPermanentError(f"知识点不存在: {kp_code}")

    await progress("题库检索中", 15)
    bank_rows = await supply_questions(db, kp_codes=[kp_code], count=count)

    # 日限口径与练题中心一致：只计 AI 生成题；题库命中不足且额度已满 → 拒绝
    used = await daily_ai_used(db, user_id)
    limit = settings.student_daily_practice_limit
    if len(bank_rows) < count and used >= limit:
        raise TaskPermanentError(
            f"今日 AI 出题已达上限（{limit} 题），题库命中 {len(bank_rows)} 题不足以成组，明天再来吧"
        )

    await progress(f"题库命中 {len(bank_rows)} 题，AI 补题中", 35)
    try:
        quiz_id: uuid.UUID = await _generate_special_quiz(
            db, user_id, kp_code, count=count, bank_rows=bank_rows
        )
    except QuizGenerationError as e:
        await db.rollback()
        raise TaskPermanentError(f"出题失败：{e}") from None

    await progress("质检通过，保存题组", 90)
    items = await db.execute(
        select(QuizItem).where(QuizItem.quiz_id == quiz_id, QuizItem.deleted_at.is_(None))
    )
    rows = items.scalars().all()
    bank_count = sum(1 for i in rows if not i.ai_generated)

    logger.info(
        "task.practice_generated",
        task_id=str(task.id),
        quiz_id=str(quiz_id),
        count=len(rows),
        bank=bank_count,
    )
    return {
        "artifact_type": "practice",
        "quiz_id": str(quiz_id),
        "mode": "special",
        "kp_code": kp_code,
        "question_count": len(rows),
        "bank_count": bank_count,
        "ai_count": len(rows) - bank_count,
        "jump": "/practice",
    }
