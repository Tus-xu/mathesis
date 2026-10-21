"""学生状态记忆（M2 迭代17）

- 长期画像：student_profiles 表（AI 计算的学生标签/薄弱点排名/学习风格/阶段），
  由管家异步更新，跨会话稳定复用；
- 短期会话态：Redis（今日推送计数、最近一次管家动作时间戳，用于反骚扰去重）。

画像卡文本复用 learning_profile service（P0 注入 system prompt 的同一数据源），
保证"管家看到的"与"对话内核看到的"一致。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.redis import get_redis
from app.models.student_profile import StudentProfile
from app.services.learning_profile import get_learning_profile_service

logger = structlog.get_logger(__name__)

# 短期态 Redis 键前缀
_KEY_PUSH_COUNT = "butler:push:{user_id}:{date}"  # 当日主动推送计数
_KEY_LAST_EVENT = "butler:last_event:{user_id}:{event_type}"  # 同类事件最近触发时间戳


async def get_profile(db: AsyncSession, user_id: uuid.UUID) -> StudentProfile | None:
    """读取学生画像（无则返回 None，由调用方决定是否创建）。"""
    rs = await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
    return rs.scalar_one_or_none()


async def upsert_profile(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    tags: list | None = None,
    weak_point_rank: list | None = None,
    learning_style: str | None = None,
    current_stage: str | None = None,
    profile_card: str | None = None,
) -> StudentProfile:
    """创建或更新学生画像（字段级合并，不覆盖未提供的字段）。"""
    profile = await get_profile(db, user_id)
    if profile is None:
        profile = StudentProfile(user_id=user_id)
        db.add(profile)
    if tags is not None:
        profile.tags = tags
    if weak_point_rank is not None:
        profile.weak_point_rank = weak_point_rank
    if learning_style is not None:
        profile.learning_style = learning_style
    if current_stage is not None:
        profile.current_stage = current_stage
    if profile_card is not None:
        profile.profile_card = profile_card
    profile.computed_at = datetime.now(UTC)
    return profile


async def build_profile_card(db: AsyncSession, user_id: uuid.UUID) -> str:
    """构建学情画像卡文本（复用 learning_profile service，P0 注入同源）。"""
    try:
        service = get_learning_profile_service()
        return await service.build_profile_card_text(db, str(user_id))
    except Exception as e:  # noqa: BLE001
        logger.info("butler_profile_card_fallback", error=str(e)[:150])
        return ""


async def refresh_profile_from_mastery(
    db: AsyncSession, user_id: uuid.UUID
) -> StudentProfile:
    """由掌握度数据重算画像（薄弱点排名 + 画像卡），用于事件后刷新。"""
    from app.butler.tools import query_weak_points

    weak = await query_weak_points(db, user_id, limit=5)
    card = await build_profile_card(db, user_id)
    profile = await upsert_profile(
        db,
        user_id,
        weak_point_rank=[
            {"kp_code": w["kp_code"], "kp_name": w["kp_name"], "mastery": w["mastery"]}
            for w in weak
        ],
        profile_card=card or None,
    )
    return profile


# ==================== 短期会话态（反骚扰） ====================


async def incr_push_count(user_id: uuid.UUID, limit: int) -> bool:
    """当日主动推送计数 +1，返回是否仍在限额内（超限返回 False）。"""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    key = _KEY_PUSH_COUNT.format(user_id=user_id, date=today)
    try:
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 24 * 3600)
        return count <= limit
    except Exception as e:  # noqa: BLE001
        logger.info("butler_push_count_fail", error=str(e)[:150])
        return True  # Redis 故障 fail-open，不因计数失败阻断


async def event_seen_recently(user_id: uuid.UUID, event_type: str, hours: int) -> bool:
    """同类事件在 N 小时内是否已处理过（去重）。"""
    key = _KEY_LAST_EVENT.format(user_id=user_id, event_type=event_type)
    try:
        redis = get_redis()
        ts = await redis.get(key)
        if ts:
            last = float(ts)
            return (datetime.now(UTC).timestamp() - last) < hours * 3600
        return False
    except Exception as e:  # noqa: BLE001
        logger.info("butler_dedup_fail", error=str(e)[:150])
        return False


async def mark_event_seen(user_id: uuid.UUID, event_type: str) -> None:
    """记录同类事件最近触发时间戳。"""
    key = _KEY_LAST_EVENT.format(user_id=user_id, event_type=event_type)
    try:
        await get_redis().set(key, str(datetime.now(UTC).timestamp()), ex=24 * 3600)
    except Exception as e:  # noqa: BLE001
        logger.info("butler_mark_event_fail", error=str(e)[:150])
