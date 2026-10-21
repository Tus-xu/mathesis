"""运维域路由（domains/ops/router.py）

埋点上报 + 系统事件。
"""

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.gateway.schemas import ApiResponse
from app.models.database import get_db
from app.models.event import Event

logger = structlog.get_logger()
router = APIRouter()


# ========== Schemas ==========


class EventItem(BaseModel):
    event: str = Field(..., min_length=1, max_length=64)
    props: dict = Field(default_factory=dict)


class EventsBatchRequest(BaseModel):
    events: list[EventItem] = Field(..., min_length=1, max_length=50)


# ========== POST /events — 埋点批量上报 ==========


@router.post("/events")
async def report_events(
    body: EventsBatchRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量上报埋点事件（最多 50 条/次）"""
    user_id = current_user["sub"]

    for item in body.events:
        event = Event(
            user_id=user_id,
            event=item.event,
            props=item.props,
        )
        db.add(event)

    await db.flush()
    logger.info("ops.events_reported", count=len(body.events), user_id=user_id)
    return ApiResponse(code=0, message="ok", data={"received": len(body.events)})
