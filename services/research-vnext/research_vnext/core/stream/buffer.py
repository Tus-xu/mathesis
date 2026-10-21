"""RV16 · StreamBuffer：token 流帧落库 / 重放。

存在的理由（X7 附加约束③ / R3）：REST 派发与 SSE 订阅之间存在**首帧竞态** ——
晚订阅者可能错过首 token。把每帧落库后，晚订阅者按 `turn` 重放（seq 连续），
断线者按 `Last-Event-ID`（= 上一帧 seq）续传，缺口可对账。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import stream_buffer_retention_chunks
from .models import ResearchStreamChunkRow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StreamBuffer:
    """一次 turn 的帧缓冲（落库，进程无关）。"""

    def __init__(self, *, turn_id: uuid.UUID, session_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        self.turn_id = turn_id
        self.session_id = session_id
        self.tenant_id = tenant_id

    def next_seq(self, db: Session) -> int:
        current = db.execute(
            select(func.max(ResearchStreamChunkRow.seq)).where(
                ResearchStreamChunkRow.turn_id == self.turn_id
            )
        ).scalar()
        return int(current or 0) + 1

    def append(self, db: Session, *, frame_type: str, payload: dict) -> int:
        """写入一帧，返回其 `seq`。超过保留上限时裁剪最旧帧（防表膨胀）。"""
        seq = self.next_seq(db)
        db.add(
            ResearchStreamChunkRow(
                turn_id=self.turn_id,
                session_id=self.session_id,
                tenant_id=self.tenant_id,
                seq=seq,
                frame_type=frame_type,
                payload=payload,
                created_at=_now(),
            )
        )
        db.flush()
        self._trim(db)
        return seq

    def _trim(self, db: Session) -> None:
        limit = max(1, stream_buffer_retention_chunks())
        count = db.execute(
            select(func.count()).select_from(ResearchStreamChunkRow).where(
                ResearchStreamChunkRow.turn_id == self.turn_id
            )
        ).scalar() or 0
        if count <= limit:
            return
        keep_from = db.execute(
            select(ResearchStreamChunkRow.seq)
            .where(ResearchStreamChunkRow.turn_id == self.turn_id)
            .order_by(ResearchStreamChunkRow.seq.desc())
            .offset(limit - 1)
            .limit(1)
        ).scalar()
        if keep_from is not None:
            db.execute(
                delete(ResearchStreamChunkRow).where(
                    ResearchStreamChunkRow.turn_id == self.turn_id,
                    ResearchStreamChunkRow.seq < int(keep_from),
                )
            )

    def replay(self, db: Session, *, after_seq: int = 0) -> list[ResearchStreamChunkRow]:
        stmt = (
            select(ResearchStreamChunkRow)
            .where(
                ResearchStreamChunkRow.turn_id == self.turn_id,
                ResearchStreamChunkRow.seq > int(after_seq),
            )
            .order_by(ResearchStreamChunkRow.seq.asc())
        )
        return list(db.execute(stmt).scalars().all())

    def is_terminal(self, db: Session) -> bool:
        """该 turn 是否已有终帧（done/error）——决定是否需要重新驱动 LLM。"""
        last = db.execute(
            select(ResearchStreamChunkRow.frame_type)
            .where(ResearchStreamChunkRow.turn_id == self.turn_id)
            .order_by(ResearchStreamChunkRow.seq.desc())
            .limit(1)
        ).scalar()
        return last in ("done", "error")
