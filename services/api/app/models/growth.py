"""学情增长域模型（M2 迭代16 新增，纯扩展不改动既有表）"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserDailyStat(Base):
    """用户学情日统计表（每日快照：综合分/独立解题率/答题数等）

    写路径：第二批接入离线回填任务；当前由 growth 服务读取时按需计算，不落库。
    """

    __tablename__ = "user_daily_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_user_daily_stats_user_date"),
        Index("idx_user_daily_stats_user_date", "user_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    independent_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    answer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    study_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KpPrerequisite(Base):
    """知识点前置依赖表（ALEKS precedence relation，追根溯源数据源）"""

    __tablename__ = "kp_prerequisites"
    __table_args__ = (UniqueConstraint("kp_code", "prereq_code", name="uq_kp_prereq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kp_code: Mapped[str] = mapped_column(String(32), nullable=False)
    prereq_code: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
