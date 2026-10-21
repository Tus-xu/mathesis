"""连续答错计数（services/quiz_streak.py，迭代15 B8 · L1-5 连续受挫干预）

判分事件总线（POST /student/learning-events）是所有判分的唯一入口，
在此处顺手维护每用户的"连错计数"：答错 INCR、答对清零，TTL 24h。

数据源选型见调研 05b §五：不新建日志表（写入放大）、不查 ErrorRecord
（只记错不记对，"连续"无法成立），Redis 计数器轻量且跨表面口径统一。

纪律：fail-open——Redis 异常绝不阻塞判分/出题主链路，降级为无干预。
"""

import structlog

logger = structlog.get_logger()

_KEY_PREFIX = "quiz_wrong_streak:"
STREAK_TTL_SECONDS = 24 * 3600

# 连续受挫干预阈值（B-C3：连续 2 错降难度）
FRUSTRATION_RELIEF_THRESHOLD = 2


def _key(user_id) -> str:
    return f"{_KEY_PREFIX}{user_id}"


async def bump_quiz_wrong_streak(user_id, *, correct: bool) -> int:
    """判分后更新连错计数，返回当前连错数（答对清零返回 0）。异常降级 0。"""
    try:
        from app.gateway.redis import get_redis

        r = get_redis()
        key = _key(user_id)
        if correct:
            await r.delete(key)
            return 0
        streak = int(await r.incr(key))
        await r.expire(key, STREAK_TTL_SECONDS)
        return streak
    except Exception as e:  # fail-open：计数失败不影响判分主链路
        logger.info("quiz_streak.bump_failed", error=str(e)[:120])
        return 0


async def get_quiz_wrong_streak(user_id) -> int:
    """读取当前连错数（出题前查询，用于降难度干预）。异常降级 0。"""
    try:
        from app.gateway.redis import get_redis

        val = await get_redis().get(_key(user_id))
        return int(val) if val else 0
    except Exception as e:
        logger.info("quiz_streak.read_failed", error=str(e)[:120])
        return 0


def apply_frustration_relief(difficulty: str, wrong_streak: int) -> tuple[str, str]:
    """连续受挫干预（B-C3：连续 2 错降难度）——纯函数，便于单测。

    连错 ≥ 阈值：难度降一档（hard→medium→easy，easy 到底），
    并给出题 prompt 附「基础巩固」指令（步骤更短、计算更简、重建信心）。
    返回 (调整后难度, prompt 附加块)。
    """
    if wrong_streak < FRUSTRATION_RELIEF_THRESHOLD:
        return difficulty, ""
    lowered = {"hard": "medium", "medium": "easy"}.get(difficulty, difficulty)
    note = (
        f"\n【受挫保护】该学生近期已连续答错 {wrong_streak} 题。"
        "本次出题侧重基础巩固：步骤更短、计算更简单、入口更直接，"
        "帮助学生重建信心；考点不变，禁止出偏题怪题。"
    )
    return lowered, note
