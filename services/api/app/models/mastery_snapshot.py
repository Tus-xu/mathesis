"""掌握度日快照模型（F6 mastery/trend 数据源）

mastery_snapshots：每个 (user_id, kp_code, date) 一行，记录当日 BKT 后验终值；
写路径（student_router._update_mastery）同步 upsert，trend 端点按日聚合平均。
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MasterySnapshot(Base):
    """掌握度日快照（纯流水，无软删；一天一知识点一行）"""

    __tablename__ = "mastery_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "kp_code", "date", name="uq_mastery_snapshots_user_kp_date"),
        Index("idx_mastery_snapshots_user_date", "user_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kp_code: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    mastery: Mapped[float] = mapped_column(Numeric, nullable=False, default=0.5)  # 当日 BKT 后验终值
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
