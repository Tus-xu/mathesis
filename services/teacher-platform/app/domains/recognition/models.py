# coding: utf-8
"""recognition 域 models（02-ARCHITECTURE §4.4：拍照识别）。

红线：原图永久保留（R8）——original_key 永不删除；识别结果永远可与原图对照。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# PG 生产用 JSONB；SQLite 测试环境退化为通用 JSON（B4 规范 §8 协议 fake 口径，列类型导入面不变）
JsonB = JSONB().with_variant(JSON(), "sqlite")
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> UUID:
    return uuid4()


class Photo(Base):
    __tablename__ = 'photos'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    owner_id: Mapped[UUID] = mapped_column()
    original_key: Mapped[str] = mapped_column(String(256))    # teacher-photos/originals/...
    enhanced_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ocr_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default='uploaded')  # uploaded/enhancing/recognized/failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecognitionJob(Base):
    __tablename__ = 'recognition_jobs'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    photo_ids: Mapped[list[UUID]] = mapped_column(JsonB, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)   # V3PhotoIngestConfig
    status: Mapped[str] = mapped_column(String(16), default='queued')     # queued/running/done/failed
    blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonB, nullable=True)
    deck_id: Mapped[UUID | None] = mapped_column(ForeignKey('decks.id'), nullable=True)  # 草稿 deck（R2）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
