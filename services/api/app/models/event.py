"""事件埋点模型（流水表，BIGSERIAL 主键，仅 id + created_at）"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Event(Base):
    """事件埋点 — BIGSERIAL 主键，仅 id + created_at（手册 §6.5）"""

    __tablename__ = "events"
    __table_args__ = (Index("idx_events_name_time", "event", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    props: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
