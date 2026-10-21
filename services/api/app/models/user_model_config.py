"""用户模型配置（自定义模型API）"""

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserModelConfig(Base, TimestampMixin):
    """用户自定义模型配置 — 一用户一行，字段级回退 .env"""

    __tablename__ = "user_model_configs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True
    )

    # 主通道（星火）
    primary_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    primary_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    primary_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 备用通道（DeepSeek/mimo）
    secondary_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    secondary_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    secondary_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    secondary_thinking: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
