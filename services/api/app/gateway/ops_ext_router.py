"""运维监控路由扩展（ADR-027 / ADR-M2B-003 #3）

端点：
- GET /api/ops/xingchen/usage — 星辰额度监控（X-Tool-Key 鉴权）
"""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_call import AICall
from app.models.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops"])


async def verify_tool_key(x_tool_key: str = Header(default="")):
    """X-Tool-Key 鉴权依赖"""
    if not settings.tool_api_key:
        return {"code": 50301, "message": "工具密钥未配置"}
    if x_tool_key != settings.tool_api_key:
        return {"code": 40301, "message": "无效的工具密钥"}
    return None


@router.get("/xingchen/usage")
async def xingchen_usage(
    x_tool_key: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
):
    """星辰额度与调用量监控（ADR-027）

    X-Tool-Key 鉴权（运维内部口径）。
    响应：{date, total_calls, by_workflow: {flow_id: count}}
    """
    # 鉴权
    if not settings.tool_api_key or x_tool_key != settings.tool_api_key:
        return {"code": 40301, "message": "无效的工具密钥"}

    # 当日口径 = 服务器本地日期的零点（带本地时区；timestamptz 比较由驱动转 UTC，时区边缘不偏移）
    now_local = datetime.now().astimezone()
    today = now_local.date()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    # 聚合 ai_calls（provider=xingchen）
    result = await db.execute(
        select(
            AICall.scene,
            func.count(AICall.id),
        ).where(
            AICall.provider == "xingchen",
            AICall.created_at >= today_start,
        ).group_by(AICall.scene)
    )
    rows = result.all()

    by_workflow = {}
    total = 0
    for scene, count in rows:
        by_workflow[scene] = count
        total += count

    # 阈值告警检查
    if total > settings.xingchen_daily_alert_threshold:
        logger.warning(
            "xingchen_usage_alert",
            total=total,
            threshold=settings.xingchen_daily_alert_threshold,
        )

    return {
        "code": 0,
        "data": {
            "date": today.isoformat(),
            "total_calls": total,
            "by_workflow": by_workflow,
        },
    }
