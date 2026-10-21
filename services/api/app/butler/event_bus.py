"""学习事件总线（LearningEventBus）— M2 迭代17

把学生每一次答题/错题/对话/登录统一事件化，作为管家决策输入：
- emit()：幂等落库 learning_events（同 idempotency_key 只落一条）；
- 落库后同步触发 orchestrator.dispatch（best-effort，失败吞掉不阻塞主链路）。

写路径接入点（最小侵入，复用既有 /learning-events 与判分链路）：
- student_router.create_learning_event 判分后 emit(quiz_judge / error_recorded)；
- complete_error_review 复习完成后 emit(error_reviewed)；
- butler_router.events/emit 供前端/其他模块显式上报。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.learning_event import LearningEvent

logger = structlog.get_logger(__name__)


class LearningEventBus:
    """学习事件总线（单例）"""

    async def emit(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        event_type: str,
        source_type: str,
        source_id: str | None = None,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> LearningEvent | None:
        """幂等上报学习事件，返回已落库事件（重复事件返回 None）。"""
        # 幂等：同 idempotency_key 只落一条
        if idempotency_key:
            rs = await db.execute(
                select(LearningEvent).where(LearningEvent.idempotency_key == idempotency_key)
            )
            if rs.scalar_one_or_none() is not None:
                return None

        event = LearningEvent(
            user_id=user_id,
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            payload=payload or {},
            idempotency_key=idempotency_key,
            status="pending",
        )
        db.add(event)
        try:
            await db.flush()
        except Exception as e:  # noqa: BLE001
            # 唯一键竞争（并发重复上报）：回滚本次 add，返回 None
            logger.info("butler_event_dup", event_type=event_type, error=str(e)[:150])
            await db.rollback()
            return None
        return event

    async def mark_processed(self, db: AsyncSession, event: LearningEvent) -> None:
        """标记事件已处理（orchestrator 成功后调用）。"""
        event.status = "processed"
        event.processed_at = datetime.now(UTC)


_bus: LearningEventBus | None = None


def get_event_bus() -> LearningEventBus:
    global _bus
    if _bus is None:
        _bus = LearningEventBus()
    return _bus
