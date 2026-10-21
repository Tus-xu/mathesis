"""RV16 · ResearchProject 域（ORM + API schema）。

`ResearchProject` 是持久科研工作区容器：一个项目下挂文献库 / PaperSpec / Claim /
Evidence / Finding / 导出 / 会话。归属 `tenant_id + owner_id`（KERNEL §7 跨租户隔离）。

`revision` 是 append-only 闸门：任何更新带 `expected_revision`，冲突 409（KERNEL §3）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class ResearchProjectRow(Base):
    __tablename__ = "research_project"
    __table_args__ = (
        # 幂等唯一键：**部分索引**（仅非空 client_request_id 生效）——
        # 与迁移 0016 一致；回填的既有项目空串键不参与约束。
        Index(
            "uq_research_project_idem",
            "tenant_id",
            "owner_id",
            "client_request_id",
            unique=True,
            postgresql_where=text("client_request_id <> ''"),
        ),
        Index("ix_rp_tenant_owner", "tenant_id", "owner_id"),
        Index("ix_rp_owner_archived", "owner_id", "archived"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    client_request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------
# API schemas（与 ORM 分离，见 ARCHITECTURE §4.2）
# --------------------------------------------------------------------------


class ResearchProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class ResearchProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    archived: bool | None = None
    expected_revision: int = 0
    client_request_id: str = Field(min_length=8, max_length=64)


class ResearchProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    revision: int
    archived: bool
    created_at: datetime
    updated_at: datetime
    counts: dict[str, int] | None = None


class LiteratureCardOut(BaseModel):
    id: str
    title: str
    tldr: str | None = None
    source: str
    doi: str | None = None
    year: int | None = None
    ingested: bool = False
    locator: str | None = None


class CapabilityOut(BaseModel):
    key: str
    title: str
    description: str
    entry: str


class WelcomeSummaryOut(BaseModel):
    greeting_name: str
    recent_projects: list[ResearchProjectOut]
    recent_literature: list[LiteratureCardOut]
    capabilities: list[CapabilityOut]
