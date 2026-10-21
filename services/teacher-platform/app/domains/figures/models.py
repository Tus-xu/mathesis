# coding: utf-8
"""figures/draw 域 models（02-ARCHITECTURE §4.3）。

figure_presets 种子与前端 mathx/presets.ts 逐一对齐（27 个，见 app/domains/figures/seeds.py）；
IFC-003：补 name/desc/level 列（前端契约字段有落点，G10-2）。
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


class FigurePreset(Base):
    __tablename__ = 'figure_presets'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)   # 'conic/ellipse'
    kind: Mapped[str] = mapped_column(String(16))                   # solid/conic/function/plane/stat
    name: Mapped[str] = mapped_column(String(64))                   # IFC-003
    desc: Mapped[str] = mapped_column(Text, default='')             # IFC-003
    level: Mapped[int] = mapped_column(Integer, default=1)          # IFC-003：1 模板参数化 / 2 构造式
    params_schema: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)  # {params:[], toggles:[]}


class Recipe(Base):
    __tablename__ = 'recipes'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    preset_id: Mapped[str] = mapped_column(ForeignKey('figure_presets.id'))
    params: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    school_shared: Mapped[bool] = mapped_column(Boolean, default=False)  # true=全校可见只读引用
    note: Mapped[str] = mapped_column(Text, default='')
    owner_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DrawLibraryItem(Base):
    __tablename__ = 'draw_library'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(8))       # free/fx
    thumb: Mapped[str | None] = mapped_column(String(256), nullable=True)  # MinIO key（前端 canvas 截图直传）
    records: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonB, nullable=True)  # V3DrawRecipe
    expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
