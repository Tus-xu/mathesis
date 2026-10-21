"""用户档案模型（无软删）"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserProfile(Base, TimestampMixin):
    """用户档案 — 无软删（手册 §6.3）"""

    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")
    weak_points: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
