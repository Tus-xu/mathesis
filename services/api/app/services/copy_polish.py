"""统一 AI 文案层（M2 迭代17 §1-D，D2 档 AI 管家化）

学生可见的引导/总结/建议文案统一经本模块输出：
数据驱动模板兜底（离线零成本）→ 可选 LLM 润色（GROWTH_LLM_POLISH 总开关）。

机制：
- 开关关闭（默认）：原样返回模板，行为零变化；
- 开关开启：sha1(scene + user + data_fingerprint) 查 Redis
  （键 copy:{scene}:{user}:{hash}，TTL 24h）→ hit 直接返回（接口耗时不增）；
  → miss 调 get_model_router().chat（system 约束：≤40字、只基于给定数据、
    不编造数字、学生第二人称口吻），asyncio.wait_for 10s；
- 异常/超时/空结果一律回退模板并记日志；Redis 故障不阻断主流程。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import structlog

from app.config import settings
from app.gateway.redis import get_redis
from app.providers.router import get_model_router

logger = structlog.get_logger(__name__)

# ==================== 8 类场景常量 ====================

SCENE_PANEL_ENCOURAGEMENT = "panel_encouragement"  # growth/panel 鼓励语
SCENE_TODAY3_REASON = "today3_reason"  # growth/today-3 的 why/benefit
SCENE_HIGHLIGHT_DESC = "highlight_desc"  # report/highlights 的 desc
SCENE_GROUP_REASON = "group_reason"  # practice/group-recommend 的 reason
SCENE_MIX_EXPLANATION = "mix_explanation"  # practice/difficulty-mix 的 explanation
SCENE_HONESTY_MESSAGE = "honesty_message"  # report/honesty 的 message
SCENE_WEAKPOINT_REASON = "weakpoint_reason"  # report/weak-points 的 ai_reason
SCENE_OVERVIEW_GREETING = "overview_greeting"  # growth/overview 问候语

SCENES = frozenset(
    {
        SCENE_PANEL_ENCOURAGEMENT,
        SCENE_TODAY3_REASON,
        SCENE_HIGHLIGHT_DESC,
        SCENE_GROUP_REASON,
        SCENE_MIX_EXPLANATION,
        SCENE_HONESTY_MESSAGE,
        SCENE_WEAKPOINT_REASON,
        SCENE_OVERVIEW_GREETING,
    }
)

# Redis 缓存 TTL：24h（同一数据指纹 24h 内不重复调 LLM）
CACHE_TTL_S = 24 * 3600
# LLM 调用超时（秒）
LLM_TIMEOUT_S = 10
# B1：prompt 回声检测标记（推理模型把用户 prompt 原文当输出吐回来的特征词）
_ECHO_MARKERS = ("用户要求", "润色", "一句自然", "改写成", "不超过40字", "学习管家")

_SYSTEM_PROMPT = (
    "你是学生的学习管家。把给定的学生学情文案改写成一句自然、亲切的中文，"
    "对学生用第二人称（你）的口吻，不超过 40 字。"
    "只基于给定数据表达：不得编造、修改或增删任何数字与事实。"
    "不要输出解释，只输出改写后的文案本身。"
)


def _cache_key(scene: str, user_id: str, data_fingerprint: str) -> str:
    """copy:{scene}:{user}:{sha1(scene+user+fingerprint)}"""
    h = hashlib.sha1(f"{scene}|{user_id}|{data_fingerprint}".encode()).hexdigest()
    return f"copy:{scene}:{user_id}:{h}"


async def polish(
    scene: str,
    template: str,
    data_fingerprint: str,
    user_id: uuid.UUID | str | None = None,
) -> str:
    """文案润色统一入口。

    :param scene: 场景常量（SCENE_* 之一）
    :param template: 数据驱动模板文案（兜底值，任何失败都返回它）
    :param data_fingerprint: 真实数据指纹——文案所依据数据的最小摘要，
        数据变则指纹变（缓存失效），数据同则 24h 内命中缓存
    :param user_id: 学生 ID（缓存键维度，隔离不同学生的文案）
    """
    if not settings.growth_llm_polish:
        return template
    if not template:
        return template

    uid = str(user_id) if user_id is not None else "anon"
    key = _cache_key(scene, uid, data_fingerprint)

    # 1) 缓存命中直接返回（Redis 故障降级为直连 LLM，不阻断）
    try:
        cached = await get_redis().get(key)
        if cached:
            return cached
    except Exception as e:
        logger.info("copy_polish_cache_read_fail", scene=scene, error=str(e)[:150])

    # 2) 调 LLM 润色（10s 超时；异常/超时/空回退模板）
    try:
        router = get_model_router()
        result = await asyncio.wait_for(
            router.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": template},
                ],
                temperature=0.7,
                max_tokens=80,
                request_id=f"copy-{uuid.uuid4().hex[:12]}",
                scene=scene,
            ),
            timeout=LLM_TIMEOUT_S,
        )
        text = (result.get("content") or "").strip().strip('"“”')
        text = text.splitlines()[0].strip() if text else ""
        # 2026-09-08 B1（V2 文档 bug 清单）：推理型模型会回声 prompt（"用户要求将…润色…"）。
        # 回声与低质输出一律回退模板——绝不让 prompt 原文进学生界面。
        if not text or any(m in text for m in _ECHO_MARKERS):
            logger.info("copy_polish_echo_fallback", scene=scene)
            return template
    except Exception as e:
        logger.info("copy_polish_fallback", scene=scene, error=str(e)[:150])
        return template

    # 3) 写缓存（失败仅记日志，不影响返回）
    try:
        await get_redis().set(key, text, ex=CACHE_TTL_S)
    except Exception as e:
        logger.info("copy_polish_cache_write_fail", scene=scene, error=str(e)[:150])
    return text
