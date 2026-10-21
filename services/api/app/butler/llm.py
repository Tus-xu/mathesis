"""管家 LLM 生成层（M2 迭代17）

复用 copy_polish 的"缓存 → LLM → 回退"纪律，但支持更长的结构化文案生成
（周报/错因解读/路径规划等 100~150 字场景，而非 ≤40 字润色）。

纪律（与 copy_polish 一致）：
- butler_llm_enabled 总开关关闭 → 直接返回 fallback；
- sha1(scene + user + data_fingerprint) 查 Redis 缓存（TTL 24h）；
- asyncio.wait_for 超时（butler_polish_timeout_s，默认 10s）；
- 异常/超时/空结果一律回退 fallback 并记日志；Redis 故障不阻断。
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


def _cache_key(scene: str, user_id: str, data_fingerprint: str) -> str:
    h = hashlib.sha1(f"{scene}|{user_id}|{data_fingerprint}".encode()).hexdigest()
    return f"butler:{scene}:{user_id}:{h}"


async def generate(
    *,
    scene: str,
    system_prompt: str,
    user_prompt: str,
    fallback: str,
    user_id: uuid.UUID | str | None = None,
    data_fingerprint: str = "",
    max_tokens: int = 400,
    temperature: float = 0.7,
) -> str:
    """管家文案生成统一入口（失败/超时/关闭一律回退 fallback，绝不抛异常）。

    :param scene: 场景标识（butler_* 常量），仅用于缓存键与日志
    :param system_prompt: 管家人格 + 约束（不编造数字、第二人称、限字数）
    :param user_prompt: 规则骨架 + 真实学情数据
    :param fallback: 兜底文案（规则模板，任何失败都返回它）
    :param user_id: 学生 ID（缓存隔离）
    :param data_fingerprint: 数据指纹（数据变则缓存失效）
    """
    if not settings.butler_llm_enabled:
        return fallback
    if not user_prompt.strip():
        return fallback

    uid = str(user_id) if user_id is not None else "anon"
    key = _cache_key(scene, uid, data_fingerprint or user_prompt)

    try:
        cached = await get_redis().get(key)
        if cached:
            return cached
    except Exception as e:  # noqa: BLE001
        logger.info("butler_llm_cache_read_fail", scene=scene, error=str(e)[:150])

    try:
        router = get_model_router()
        result = await asyncio.wait_for(
            router.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                request_id=f"butler-{uuid.uuid4().hex[:12]}",
                scene=scene,
            ),
            timeout=(
                settings.butler_error_detail_timeout_s
                if scene == SCENE_ERROR_DETAIL
                else settings.butler_polish_timeout_s
            ),
        )
        text = (result.get("content") or "").strip().strip('"“”')
        # 2026-09-08 B1（V2 文档）：推理型模型会回声 prompt（"用户要求扮演…"）——
        # 回声一律回退模板，绝不让 prompt 原文进学生界面（错因诊断实测泄露点）
        if text and any(m in text for m in ("用户要求", "扮演", "输出格式", "诚实纪律", "不制造焦虑")):
            logger.info("butler_llm_prompt_echo_fallback", scene=scene)
            return fallback
        if not text:
            logger.info("butler_llm_empty_fallback", scene=scene)
            return fallback
    except Exception as e:  # noqa: BLE001
        logger.info("butler_llm_fallback", scene=scene, error=str(e)[:150])
        return fallback

    try:
        await get_redis().set(key, text, ex=settings.butler_polish_cache_ttl_s)
    except Exception as e:  # noqa: BLE001
        logger.info("butler_llm_cache_write_fail", scene=scene, error=str(e)[:150])
    return text


# ==================== 管家场景常量 ====================

SCENE_DAILY_PLAN = "butler_daily_plan"  # 今日 3 件事标题/介绍/鼓励
SCENE_WEEKLY_REPORT = "butler_weekly_report"  # 小婷的周报
SCENE_ERROR_DIAGNOSIS = "butler_error_diagnosis"  # 错因根因解读
SCENE_PATH_PLAN = "butler_path_plan"  # 学习路径规划文案
SCENE_WEAK_POINT = "butler_weak_point"  # 薄弱点分析建议
SCENE_PROACTIVE = "butler_proactive"  # 主动开场白
SCENE_RECOMMEND = "butler_recommend"  # 资源推荐文案
SCENE_DUE_NOTIFY = "butler_due_notify"  # 该复习 X 了
SCENE_ERROR_DETAIL = "butler_error_detail"  # 错题 AI 生成正解
SCENE_ERROR_TUTOR = "butler_error_tutor"  # 错题 AI 答疑（苏格拉底）
