"""用户模型"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    def __init__(self, **kwargs):
        kwargs.setdefault("status", "active")
        kwargs.setdefault("onboarding_status", "required")
        kwargs.setdefault("security_version", 1)
        super().__init__(**kwargs)

    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="required", server_default="required"
    )
    last_active_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    security_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
