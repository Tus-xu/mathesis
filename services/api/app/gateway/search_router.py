"""联网搜索试点路由（SSOT §5.7 / ADR-006）

端点：
- POST /api/search/web — 联网搜索（默认关闭）
"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import get_current_user
from app.models.database import get_db
from app.models.m2_logs import SearchLog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


class WebSearchRequest(BaseModel):
    query: str = Field(..., max_length=200)
    max_results: int = Field(default=5, ge=1, le=10)


@router.post("/web")
async def web_search(
    req: WebSearchRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """联网搜索试点（默认关闭，SSOT §5.7）"""
    user_id = user["sub"]

    # 开关检查
    if not settings.web_search_enabled:
        return {"code": 40301, "message": "联网搜索试点未开启"}

    start_time = time.perf_counter()

    try:
        from app.providers.xingchen import (
            XingchenConcurrencyError,
            resolve_effective_xingchen_config,
            run_workflow,
        )

        # 三层解析有效配置（管理后台配置即时生效；无库内配置时等于 env）
        xcfg = await resolve_effective_xingchen_config(db, user_id)
        result = await run_workflow(
            "wf_web_search",
            uid=user_id,
            parameters={
                "query": req.query,
                "max_results": req.max_results,
            },
            config=xcfg,
        )

        latency_ms = int((time.perf_counter() - start_time) * 1000)
        answer = result.get("answer")
        sources = result.get("sources", [])

        # 落 search_logs
        log = SearchLog(
            user_id=uuid.UUID(user_id),
            query=req.query,
            provider="xingchen_wf",
            result_count=len(sources),
            top_results={"sources": sources[:3]},
            latency_ms=latency_ms,
        )
        db.add(log)
        await db.commit()

        # 搜索无结果
        if not answer:
            return {
                "code": 0,
                "data": {
                    "answer": None,
                    "sources": [],
                    "badge": "web_supplement",
                    "notice": "未找到相关信息",
                },
            }

        return {
            "code": 0,
            "data": {
                "answer": answer,
                "sources": sources,
                "badge": "web_supplement",
            },
        }

    except XingchenConcurrencyError:
        # 20357 → 42902（ADR-004：同账号并发处理中，不走拒答降级，前端 toast 轻提示重试）
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.info("web_search_concurrency_42902")
        db.add(
            SearchLog(
                user_id=uuid.UUID(user_id),
                query=req.query,
                provider="xingchen_wf",
                result_count=0,
                latency_ms=latency_ms,
            )
        )
        await db.commit()
        return {"code": 42902, "message": "AI 服务繁忙（同账号并发处理中），请稍后重试"}

    except Exception as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.warning("web_search_failed", error=str(e))

        # 落日志
        log = SearchLog(
            user_id=uuid.UUID(user_id),
            query=req.query,
            provider="xingchen_wf",
            result_count=0,
            latency_ms=latency_ms,
        )
        db.add(log)
        await db.commit()

        # 降级：回 M1 拒答通用话术
        return {
            "code": 0,
            "data": {
                "answer": None,
                "sources": [],
                "badge": "web_supplement",
                "notice": "联网搜索暂不可用，请稍后重试",
            },
        }
