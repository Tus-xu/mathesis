"""InboxService：消费侧幂等去重（8200 自持）。

- 首次消息 → 插入行（PROCESSED）→ 后续命中 UNIQUE(dedupe_key) → DUPLICATE；
- payload_hash 不同 → CONFLICT（显式报错，不静默吞）；
- 重复投递 / ACK 前崩溃 / dispatcher 重发 → 单副作用。
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import PptV2Inbox


def _now() -> datetime:
    return datetime.now(UTC)


class InboxService:
    async def dedupe(
        self,
        session: AsyncSession,
        *,
        dedupe_key: str,
        payload: dict,
        message_id: str | None = None,
    ) -> str:
        """返回 FIRST / DUPLICATE / CONFLICT。"""
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        existing = (
            await session.execute(select(PptV2Inbox).where(PptV2Inbox.dedupe_key == dedupe_key))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.payload_hash != payload_hash:
                return "CONFLICT"
            return "DUPLICATE"
        session.add(
            PptV2Inbox(
                owner_service="ppt_studio_v2",
                dedupe_key=dedupe_key,
                message_id=message_id,
                payload_hash=payload_hash,
                status="PROCESSED",
                handled_at=_now(),
            )
        )
        await session.flush()
        return "FIRST"
