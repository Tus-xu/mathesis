# coding: utf-8
"""contents 域 models（02-ARCHITECTURE §4.2：模板与内容）。

slides/boards 存 JSONB；PATCH 走 JSON Patch 语义（B4 规范 §3.2）。
slots 内部结构：教学意图五类模板（layout_type/pedagogical_role/required_slots/
optional_slots/max_text/formula_capacity/visual_capacity/locked_elements）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# PG 生产用 JSONB；SQLite 测试环境退化为通用 JSON（B4 规范 §8 协议 fake 口径，列类型导入面不变）
JsonB = JSONB().with_variant(JSON(), "sqlite")
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> UUID:
    return uuid4()


class DeckTemplate(Base):
    __tablename__ = 'deck_templates'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    style: Mapped[str] = mapped_column(String(32))    # academic/chalkboard/geometric/classic/minimal
    slide_count: Mapped[int] = mapped_column(Integer, default=0)
    thumb: Mapped[str | None] = mapped_column(String(256), nullable=True)  # MinIO key：服务端真实渲染图卡（M2-B）
    slots: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, default=list)
    source: Mapped[str] = mapped_column(String(32), default='builtin')


class LessonTemplate(Base):
    __tablename__ = 'lesson_templates'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    boards: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, default=list)
    source: Mapped[str] = mapped_column(String(32), default='builtin')  # builtin/teacher_upload
    quality_report: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)


class Deck(Base):
    __tablename__ = 'decks'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(256))
    template_id: Mapped[UUID | None] = mapped_column(ForeignKey('deck_templates.id'), nullable=True)
    source: Mapped[str] = mapped_column(String(32))   # topic/photo/lesson-push/review-notes
    class_id: Mapped[UUID | None] = mapped_column(nullable=True)
    slides: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, default=list)
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())


class Plan(Base):
    __tablename__ = 'plans'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    topic: Mapped[str] = mapped_column(String(256))
    class_id: Mapped[UUID | None] = mapped_column(nullable=True)
    lesson_type: Mapped[str] = mapped_column(String(32))
    boards: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, default=list)   # 十板块三栏
    outline: Mapped[dict[str, Any] | None] = mapped_column(JsonB, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # confirmed 后对 AI 只读（R5）
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())


# export_jobs 唯一定义在 app/db/models/platform.py（B3 迁移底座；真实库无 FK，列宽 ≥ 域内旧定义）。
# 域内别名复用（rescue 整合裁决 2026-09-07）。
from app.db.models.platform import ExportJobs as ExportJob  # noqa: E402


class SlidePatchLog(Base):
    """元素级 diff 日志（撤销审计；B4 规范 §3.2 写日志保留元素级 diff）。"""

    __tablename__ = 'slide_patch_logs'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    deck_id: Mapped[UUID] = mapped_column(ForeignKey('decks.id', ondelete='CASCADE'))
    slide_id: Mapped[str] = mapped_column(String(64))
    patch: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, default=list)  # JSON Patch ops
    modifier: Mapped[str] = mapped_column(String(16), default='teacher')      # teacher（AI 无写路径，R5）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
