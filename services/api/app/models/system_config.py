"""系统级配置（管理后台全局配置 KV 表）

key 主键、value JSONB 存配置内容；敏感字段（api_key 等）Fernet 加密后存入，
由读写双方自行加解密（对齐 user_integration_configs 范式）。

当前使用的 key：
- model.global    全局默认模型通道 {"primary": {...}, "secondary": {...}}
- xingchen.global 星辰全局凭证 {"base_url", "api_key", "api_secret"}
- cloud_kb        云知识库 {"enabled", "provider", "credentials", ...}
- workflows       工作流覆盖 {"<flow>": {"flow_id", "timeout"}}
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemConfig(Base):
    """系统级配置 KV — 管理后台维护的全局配置（key 主键，全表仅数行）"""

    __tablename__ = "system_configs"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


async def get_system_config(db: AsyncSession, key: str, default=None):
    """读取系统配置值，不存在返回 default"""
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        return default
    return row.value


async def upsert_system_config(
    db: AsyncSession, key: str, value: dict, description: str | None = None
) -> SystemConfig:
    """upsert 系统配置（value 整体替换；description 缺省保持不变）"""
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemConfig(key=key, value=value, description=description)
        db.add(row)
    else:
        row.value = value  # 重新赋值以触发 JSONB 变更检测
        if description is not None:
            row.description = description
    await db.flush()
    return row
