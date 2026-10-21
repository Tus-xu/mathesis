"""RV17 · 项目概览 + 状态恢复（P0-5 / §3.8）。

- `overview`：`summary` 是**项目记忆摘要**（宪法 §8）—— 见 `memory.py`：
  模型生成 + 缓存 + 规则降级；`stats {papers, drafts, notes, data}`。
- `recent` / `history`：重新进入项目时恢复 READ/WRITE、最近文献、页码、文件、行（宪法 §84）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.errors import NotFound
from ..project import service as project_svc
from . import memory as memory_svc
from .models import (
    DATA_FILE_KINDS,
    DRAFT_FILE_KINDS,
    ResearchAgentTaskRow,
    ResearchNoteRow,
    ResearchPaperRow,
    ResearchProjectFileRow,
    ResearchProjectRecentRow,
    utcnow,
)


def _stats(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, int]:
    papers = db.execute(
        select(func.count())
        .select_from(ResearchPaperRow)
        .where(
            ResearchPaperRow.tenant_id == tenant_id,
            ResearchPaperRow.project_id == project_id,
            ResearchPaperRow.deleted_at.is_(None),
        )
    ).scalar() or 0
    notes = db.execute(
        select(func.count())
        .select_from(ResearchNoteRow)
        .where(
            ResearchNoteRow.tenant_id == tenant_id,
            ResearchNoteRow.project_id == project_id,
            ResearchNoteRow.deleted_at.is_(None),
        )
    ).scalar() or 0
    kind_rows = db.execute(
        select(ResearchProjectFileRow.kind, func.count())
        .where(
            ResearchProjectFileRow.tenant_id == tenant_id,
            ResearchProjectFileRow.project_id == project_id,
        )
        .group_by(ResearchProjectFileRow.kind)
    ).all()
    drafts = sum(int(n) for k, n in kind_rows if k in DRAFT_FILE_KINDS)
    data = sum(int(n) for k, n in kind_rows if k in DATA_FILE_KINDS)
    return {"papers": int(papers), "drafts": int(drafts), "notes": int(notes), "data": int(data)}


def _latest_touch(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> object:
    stamps: list = []
    for model, col in (
        (ResearchPaperRow, ResearchPaperRow.updated_at),
        (ResearchNoteRow, ResearchNoteRow.updated_at),
        (ResearchProjectFileRow, ResearchProjectFileRow.updated_at),
    ):
        val = db.execute(
            select(func.max(col)).where(
                model.tenant_id == tenant_id, model.project_id == project_id
            )
        ).scalar()
        if val is not None:
            stamps.append(val)
    return max(stamps) if stamps else None


async def overview(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> dict:
    """项目概览（宪法 §3.8 / §8）。

    `summary` 走 **项目记忆摘要**（`memory.summary_for_project`）：
    模型生成 → 缓存（数据指纹失效）→ 规则降级。`source` 如实标注它到底来自哪条路。
    """
    project = project_svc.get_project(
        db, tenant_id=tenant_id, owner_id=_owner_of(db, tenant_id, project_id), project_id=project_id
    )
    stats = _stats(db, tenant_id=tenant_id, project_id=project_id)
    summary, source = await memory_svc.summary_for_project(
        db, tenant_id=tenant_id, project_id=project_id, project=project, stats=stats
    )
    touched = _latest_touch(db, tenant_id=tenant_id, project_id=project_id)
    updated_at = max([t for t in (getattr(project, "updated_at", None), touched) if t is not None], default=None)
    return {
        "project_id": str(project_id),
        "name": project.name,
        "summary": summary,
        "stats": stats,
        "source": source,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def _owner_of(db: Session, tenant_id: uuid.UUID, project_id: uuid.UUID) -> uuid.UUID:
    from ..project.models import ResearchProjectRow

    row = db.get(ResearchProjectRow, project_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound(f"项目不存在: {project_id}")
    return row.owner_id


def get_recent(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, actor_id: uuid.UUID
) -> dict | None:
    row = db.execute(
        select(ResearchProjectRecentRow).where(
            ResearchProjectRecentRow.tenant_id == tenant_id,
            ResearchProjectRecentRow.project_id == project_id,
            ResearchProjectRecentRow.actor_id == actor_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return _recent_dict(row)


def put_recent(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    workspace: str | None = None,
    paper_id: uuid.UUID | None = None,
    page: int | None = None,
    file_path: str | None = None,
    line: int | None = None,
) -> dict:
    row = db.execute(
        select(ResearchProjectRecentRow).where(
            ResearchProjectRecentRow.tenant_id == tenant_id,
            ResearchProjectRecentRow.project_id == project_id,
            ResearchProjectRecentRow.actor_id == actor_id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = ResearchProjectRecentRow(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            workspace="read",
            updated_at=utcnow(),
        )
        db.add(row)
    if workspace in ("read", "write"):
        row.workspace = workspace
    if paper_id is not None:
        row.paper_id = paper_id
    if page is not None:
        row.page = int(page)
    if file_path is not None:
        row.file_path = file_path
    if line is not None:
        row.line = int(line)
    row.updated_at = utcnow()
    db.flush()
    return _recent_dict(row)


def _recent_dict(row: ResearchProjectRecentRow) -> dict:
    return {
        "workspace": row.workspace,
        "paper_id": str(row.paper_id) if row.paper_id else None,
        "page": row.page,
        "file_path": row.file_path or "",
        "line": row.line,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def history(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, actor_id: uuid.UUID
) -> dict:
    recent = get_recent(db, tenant_id=tenant_id, project_id=project_id, actor_id=actor_id) or {
        "workspace": "read",
        "paper_id": None,
        "page": None,
        "file_path": "",
        "line": None,
        "updated_at": None,
    }
    papers = db.execute(
        select(ResearchPaperRow)
        .where(
            ResearchPaperRow.tenant_id == tenant_id,
            ResearchPaperRow.project_id == project_id,
            ResearchPaperRow.deleted_at.is_(None),
        )
        .order_by(ResearchPaperRow.updated_at.desc())
        .limit(10)
    ).scalars().all()
    files = db.execute(
        select(ResearchProjectFileRow)
        .where(
            ResearchProjectFileRow.tenant_id == tenant_id,
            ResearchProjectFileRow.project_id == project_id,
        )
        .order_by(ResearchProjectFileRow.updated_at.desc())
        .limit(10)
    ).scalars().all()
    tasks = db.execute(
        select(ResearchAgentTaskRow)
        .where(
            ResearchAgentTaskRow.tenant_id == tenant_id,
            ResearchAgentTaskRow.project_id == project_id,
        )
        .order_by(ResearchAgentTaskRow.created_at.desc())
        .limit(10)
    ).scalars().all()
    return {
        "recent": recent,
        "recent_papers": [
            {"id": str(p.id), "title": p.title, "page": p.last_page, "updated_at": p.updated_at.isoformat() if p.updated_at else None}
            for p in papers
        ],
        "recent_files": [
            {"path": f.path, "kind": f.kind, "updated_at": f.updated_at.isoformat() if f.updated_at else None}
            for f in files
        ],
        "recent_agent_tasks": [
            {"task_id": str(t.id), "preset": t.preset, "status": t.status, "created_at": t.created_at.isoformat() if t.created_at else None}
            for t in tasks
        ],
    }


__all__ = ["get_recent", "history", "overview", "put_recent"]
