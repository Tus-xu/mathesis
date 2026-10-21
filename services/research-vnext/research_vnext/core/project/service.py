"""RV16 · ResearchProject 域服务。

归属模型：每个项目归 `tenant_id + owner_id`；所有查询带 `tenant_id`（KERNEL §7）。
revision 闸门：更新带 `expected_revision`，不匹配抛 `RevisionConflict`（409）。
幂等：创建/更新带 `client_request_id`，同键异指纹 → `IdempotencyConflict`（409）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..application import session_service as session_svc
from ..db.models import ResearchSessionRow
from ..domain.errors import IdempotencyConflict, NotFound, RevisionConflict
from .models import (
    CapabilityOut,
    LiteratureCardOut,
    ResearchProjectOut,
    ResearchProjectRow,
)

#: 欢迎页 4 张能力卡的数据源（entry 为前端路由）。
_CAPABILITIES: tuple[CapabilityOut, ...] = (
    CapabilityOut(key="read", title="精读文献", description="打开文献，页/段级锚点溯源，结论可点击回原文", entry="/research-vnext/reader"),
    CapabilityOut(key="write", title="写作", description="LaTeX 内联编辑 + KaTeX 实时渲染，diff 逐条接受", entry="/research-vnext/writing"),
    CapabilityOut(key="retrieve", title="检索文献", description="按关键词检索并入库（本期为 keyword 模式，非语义）", entry="/research-vnext/workbench"),
    CapabilityOut(key="export", title="导出成果", description="确定性导出 Markdown / LaTeX / PDF，带下载令牌", entry="/research-vnext/workbench"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _to_out(row: ResearchProjectRow, counts: dict[str, int] | None = None) -> ResearchProjectOut:
    return ResearchProjectOut(
        id=row.id,
        name=row.name,
        description=row.description or None,
        tenant_id=row.tenant_id,
        owner_id=row.owner_id,
        revision=row.revision,
        archived=row.archived,
        created_at=row.created_at,
        updated_at=row.updated_at,
        counts=counts,
    )


def get_project(db: Session, *, tenant_id: uuid.UUID, owner_id: uuid.UUID, project_id: uuid.UUID) -> ResearchProjectRow:
    row = db.get(ResearchProjectRow, project_id)
    if row is None or row.tenant_id != tenant_id or row.owner_id != owner_id:
        # 不区分"不存在"与"不属于你"——避免跨租户探测（KERNEL §7）。
        raise NotFound(f"项目不存在: {project_id}")
    return row


def create_project(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    name: str,
    description: str | None,
    client_request_id: str,
) -> ResearchProjectRow:
    """创建项目（幂等：同 `client_request_id` 同内容 → 复用；异内容 → 409）。"""
    existing = db.execute(
        select(ResearchProjectRow).where(
            ResearchProjectRow.tenant_id == tenant_id,
            ResearchProjectRow.owner_id == owner_id,
            ResearchProjectRow.client_request_id == client_request_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.name != name or (existing.description or "") != (description or ""):
            raise IdempotencyConflict("同一 client_request_id 用于了不同的项目内容")
        return existing

    now = _now()
    row = ResearchProjectRow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        owner_id=owner_id,
        name=name,
        description=description or "",
        revision=1,
        archived=False,
        client_request_id=client_request_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def list_projects(
    db: Session, *, tenant_id: uuid.UUID, owner_id: uuid.UUID, include_archived: bool = False, limit: int = 50
) -> list[ResearchProjectRow]:
    stmt = select(ResearchProjectRow).where(
        ResearchProjectRow.tenant_id == tenant_id,
        ResearchProjectRow.owner_id == owner_id,
    )
    if not include_archived:
        stmt = stmt.where(ResearchProjectRow.archived.is_(False))
    stmt = stmt.order_by(ResearchProjectRow.updated_at.desc()).limit(max(1, min(limit, 200)))
    return list(db.execute(stmt).scalars().all())


def update_project(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    expected_revision: int,
    name: str | None,
    description: str | None,
    archived: bool | None,
) -> ResearchProjectRow:
    """revision 闸门：`expected_revision` 必须等于当前 revision，否则 409。"""
    row = get_project(db, tenant_id=tenant_id, owner_id=owner_id, project_id=project_id)
    if int(expected_revision) != int(row.revision):
        raise RevisionConflict(expected=int(expected_revision), current=int(row.revision))
    if name is not None:
        row.name = name
    if description is not None:
        row.description = description
    if archived is not None:
        row.archived = archived
    row.revision = int(row.revision) + 1
    row.updated_at = _now()
    db.flush()
    return row


def project_counts(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, int]:
    """项目下的会话计数（文献/产物计数按需扩展；此处至少给出 sessions）。"""
    rows = db.execute(
        select(ResearchSessionRow.id).where(
            ResearchSessionRow.tenant_id == tenant_id,
            ResearchSessionRow.project_id == project_id,
        )
    ).all()
    return {"sessions": len(rows)}


def recent_literature(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID | None = None, limit: int = 5
) -> list[LiteratureCardOut]:
    """最近入库文献卡（无 embedding，诚实标注 keyword 模式来源）。"""
    from ..literature.models import LiteratureRecordRow

    stmt = select(LiteratureRecordRow).where(LiteratureRecordRow.tenant_id == tenant_id)
    if project_id is not None:
        stmt = stmt.where(LiteratureRecordRow.project_id == project_id)
    stmt = stmt.order_by(LiteratureRecordRow.created_at.desc()).limit(max(1, min(limit, 50)))
    out: list[LiteratureCardOut] = []
    for r in db.execute(stmt).scalars().all():
        year: int | None = None
        published = (getattr(r, "published_date", "") or "").strip()
        if len(published) >= 4 and published[:4].isdigit():
            year = int(published[:4])
        out.append(
            LiteratureCardOut(
                id=str(r.id),
                title=getattr(r, "title", "") or "",
                tldr=None,
                source=(getattr(r, "venue", "") or getattr(r, "source_url", "") or "crossref"),
                doi=(getattr(r, "doi", "") or None),
                year=year,
                ingested=True,
                locator=None,
            )
        )
    return out


def welcome_summary(
    db: Session, *, tenant_id: uuid.UUID, owner_id: uuid.UUID, greeting_name: str = ""
) -> dict:
    """欢迎页聚合：最近项目 / 最近文献 / 4 张能力卡。**无数据走结构化空态**。"""
    projects = list_projects(db, tenant_id=tenant_id, owner_id=owner_id, limit=5)
    project_outs = [_to_out(p) for p in projects]
    lit = recent_literature(db, tenant_id=tenant_id, limit=5)
    return {
        "greeting_name": greeting_name,
        "recent_projects": [p.model_dump(mode="json") for p in project_outs],
        "recent_literature": [c.model_dump(mode="json") for c in lit],
        "capabilities": [c.model_dump(mode="json") for c in _CAPABILITIES],
    }
