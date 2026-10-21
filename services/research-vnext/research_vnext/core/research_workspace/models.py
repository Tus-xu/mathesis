"""RV17 · 科研工作区 ORM 行 + API schema。

六张新表（见迁移 `0017_rv17_research_workspace`）：

- `ResearchPaperRow`        —— 项目级文献（归属 + 元数据 + 阅读定位 + 解析文本缓存）
- `ResearchNoteRow`         —— 带 locator 的笔记
- `ResearchProjectFileRow`  —— 草稿文件树（path/kind/content/revision）
- `ResearchCompileRunRow`   —— 项目级真实编译运行 + 结构化日志
- `ResearchAgentTaskRow`    —— 统一 Research Agent 任务
- `ResearchProjectRecentRow`—— 重新进入项目的状态恢复

铁律：所有行都带 `tenant_id`；查询一律带租户过滤（KERNEL §7 跨租户隔离）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

# --------------------------------------------------------------------------
# 封闭词表
# --------------------------------------------------------------------------

READ_UNREAD = "unread"
READ_READING = "reading"
READ_READ = "read"
READ_STATES = frozenset({READ_UNREAD, READ_READING, READ_READ})

PARSE_NONE = "none"
PARSE_PENDING = "pending"
PARSE_PARSED = "parsed"
PARSE_FAILED = "failed"
PARSE_STATUSES = frozenset({PARSE_NONE, PARSE_PENDING, PARSE_PARSED, PARSE_FAILED})

FILE_KINDS = frozenset({"tex", "bib", "sty", "cls", "image", "pdf", "data"})
#: 「草稿」= 可编辑的文本文件；「数据」= 二进制/数据文件（用于概览统计）。
DRAFT_FILE_KINDS = frozenset({"tex", "bib", "sty", "cls"})
DATA_FILE_KINDS = frozenset({"image", "pdf", "data"})

COMPILE_RUNNING = "running"
COMPILE_SUCCESS = "success"
COMPILE_FAILED = "failed"
COMPILE_TIMEOUT = "timeout"
COMPILE_ERROR = "error"

TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_STATUSES = frozenset({TASK_QUEUED, TASK_RUNNING, TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchPaperRow(Base):
    """项目↔文献归属 + 元数据 + 阅读定位 + 已解析页文本缓存。

    `parse_status=failed` 时 `pages` 为空，但文献**仍可用**：PDF 字节在
    `research_asset`（`asset_id`），阅读器直接读 PDF，不依赖解析结果
    （宪法 §10：元数据解析失败不得让文献不可用）。
    """

    __tablename__ = "research_paper"
    __table_args__ = (
        Index("ix_rp17_project", "project_id", "created_at"),
        Index("ix_rp17_asset", "asset_id"),
        Index(
            "uq_research_paper_project_hash",
            "project_id",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash <> '' AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(Text, nullable=False, default="")
    doi: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    read_state: Mapped[str] = mapped_column(String(16), nullable=False, default=READ_UNREAD)
    last_page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, default=PARSE_NONE)
    #: [{page,width,height,blocks:[{text,bbox,section}]}, ...]
    pages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: [{"title","level","page"}, ...] —— 从 PDF 抽取的章节（可选）
    page_sections: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: 已解析文本拼接（检索用；**不是** embedding）。
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 检索词元（CJK 2-gram + 拉丁词，空格分隔；见 `search_tokenize.py`）。
    #: GIN 索引 `ix_rp17_tokens_tsv` 建在 `to_tsvector('simple', search_tokens)`（迁移 0018）。
    search_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parse_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    client_request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchNoteRow(Base):
    """笔记（带 locator，点击可回到 paper/page/selection）。"""

    __tablename__ = "research_note"
    __table_args__ = (Index("ix_rn17_project", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    paper_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(Text, nullable=False, default="")
    selected_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 检索词元（CJK 2-gram + 拉丁词，空格分隔；见 `search_tokenize.py`）。
    search_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    locator: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    xref_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    xref_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchProjectFileRow(Base):
    """草稿文件树中的单个文件（`revision` 做乐观并发）。"""

    __tablename__ = "research_project_file"
    __table_args__ = (
        UniqueConstraint("project_id", "path", name="uq_rpf17_project_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="tex")
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ResearchCompileRunRow(Base):
    """项目级真实编译运行。

    `pdf_artifact_id` 仅在 `status=success` 时非空；编译失败**不得清空**上次成功产物
    —— `latest` 端点单独返回 `last_successful_pdf_artifact_id`（宪法 §23）。
    """

    __tablename__ = "research_compile_run"
    __table_args__ = (
        Index("ix_rcr17_project", "project_id", "created_at"),
        Index("ix_rcr17_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    engine: Mapped[str] = mapped_column(String(16), nullable=False, default="xelatex")
    entry: Mapped[str] = mapped_column(String(128), nullable=False, default="main.tex")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=COMPILE_RUNNING)
    pdf_artifact_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    pdf_storage_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    pdf_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    log_lines: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    diagnostics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchAgentTaskRow(Base):
    """统一 Research Agent 任务（preset → 真实能力）。"""

    __tablename__ = "research_agent_task"
    __table_args__ = (Index("ix_rat17_project", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    preset: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TASK_QUEUED)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchTerminologyRow(Base):
    """项目级**术语记忆**（宪法 §42 Terminology Memory）。

    §42：「至少在同一 Project 内保持：同一个专业术语，翻译一致。」
    这张表就是「一致」的**数据层保证**：`(tenant_id, project_id, source_term)` 唯一
    —— 同一项目同一个术语**只能存在一个译名**，不存在应用层各写各的可能。

    `source_term` 存的是**规范化**后的形态（小写 + 空白折叠，见 `terminology._norm`），
    查询侧用同一函数，避免大小写/空格导致"看起来有记忆其实查不到"。
    """

    __tablename__ = "research_terminology"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "project_id", "source_term", name="uq_rt19_tenant_proj_term"
        ),
        Index("ix_rt19_project", "tenant_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: 规范化源术语（小写 + 空白折叠）
    source_term: Mapped[str] = mapped_column(Text, nullable=False)
    #: 已确立译名
    target_term: Mapped[str] = mapped_column(Text, nullable=False)
    #: 被复用次数（排序 / 淘汰依据）
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ResearchProjectRecentRow(Base):
    """最近打开状态（重新进入项目恢复 READ/WRITE、页码、文件、行）。"""

    __tablename__ = "research_project_recent"
    __table_args__ = (
        UniqueConstraint("project_id", "actor_id", name="uq_rpr17_project_actor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    workspace: Mapped[str] = mapped_column(String(8), nullable=False, default="read")
    paper_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


__all__ = [
    "COMPILE_ERROR",
    "COMPILE_FAILED",
    "COMPILE_RUNNING",
    "COMPILE_SUCCESS",
    "COMPILE_TIMEOUT",
    "DATA_FILE_KINDS",
    "DRAFT_FILE_KINDS",
    "FILE_KINDS",
    "PARSE_FAILED",
    "PARSE_NONE",
    "PARSE_PARSED",
    "PARSE_PENDING",
    "PARSE_STATUSES",
    "READ_READ",
    "READ_READING",
    "READ_STATES",
    "READ_UNREAD",
    "TASK_CANCELLED",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_QUEUED",
    "TASK_RUNNING",
    "TASK_STATUSES",
    "ResearchAgentTaskRow",
    "ResearchCompileRunRow",
    "ResearchNoteRow",
    "ResearchPaperRow",
    "ResearchProjectFileRow",
    "ResearchProjectRecentRow",
    "ResearchTerminologyRow",
    "utcnow",
]
