"""用户级集成配置（对象存储 / 星辰工作流）

范式对齐 UserModelConfig：字段级回退 .env，敏感字段 Fernet 加密后存 JSONB。
一用户一类型一行，主键 (user_id, kind)。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 合法 kind 值
INTEGRATION_KINDS = ("storage", "xingchen")


class UserIntegrationConfig(Base):
    """用户级集成配置 — config JSONB 仅存用户覆盖项，缺字段回退 settings"""

    __tablename__ = "user_integration_configs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)  # storage | xingchen
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


async def get_user_integration_row(
    user_id: str, kind: str, db: AsyncSession
) -> UserIntegrationConfig | None:
    """读取用户集成配置行，无配置返回 None"""
    result = await db.execute(
        select(UserIntegrationConfig).where(
            UserIntegrationConfig.user_id == user_id,
            UserIntegrationConfig.kind == kind,
        )
    )
    return result.scalar_one_or_none()


async def get_user_integration_overrides(user_id: str, kind: str, db: AsyncSession) -> dict | None:
    """读取用户覆盖字典（敏感字段仍为密文，由调用方解密），无配置返回 None"""
    row = await get_user_integration_row(user_id, kind, db)
    if row is None:
        return None
    return dict(row.config or {})
