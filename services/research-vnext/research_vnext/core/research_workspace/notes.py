"""RV17 · 笔记服务（P0-2）。

笔记必须带 locator（paper/page/section/selected_text），点击可回到原文
（宪法 §11/§44）。删除为软删除（`deleted_at`），保留审计。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import AccessContext
from ..domain.errors import NotFound
from .models import ResearchNoteRow, utcnow
from .search_tokenize import index_text

XREF_KINDS = frozenset({"paper", "agent", "manual"})


def _rebuild_note_tokens(row: ResearchNoteRow) -> None:
    """重建笔记检索词元（标题 + 正文 + 章节 + 选中文本），供 CJK/拉丁检索。"""
    row.search_tokens = index_text(row.title, row.body, row.section, row.selected_text)


class InvalidNoteInput(Exception):
    """笔记入参非法（API 层映射为 422）。"""


def note_public_dict(row: ResearchNoteRow) -> dict:
    locator = dict(row.locator or {})
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "title": row.title or "",
        "body": row.body or "",
        "paper_id": str(row.paper_id) if row.paper_id else None,
        "page": row.page,
        "section": row.section or "",
        "selected_text": row.selected_text or "",
        "locator": locator,
        "xref_kind": row.xref_kind,
        "xref_id": row.xref_id or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_notes(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, limit: int = 100
) -> list[ResearchNoteRow]:
    stmt = (
        select(ResearchNoteRow)
        .where(
            ResearchNoteRow.tenant_id == tenant_id,
            ResearchNoteRow.project_id == project_id,
            ResearchNoteRow.deleted_at.is_(None),
        )
        .order_by(ResearchNoteRow.updated_at.desc())
        .limit(max(1, min(int(limit), 500)))
    )
    return list(db.execute(stmt).scalars().all())


def recent_notes(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, limit: int = 6
) -> list[ResearchNoteRow]:
    return list_notes(db, tenant_id=tenant_id, project_id=project_id, limit=limit)


def get_note(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, note_id: uuid.UUID
) -> ResearchNoteRow:
    row = db.get(ResearchNoteRow, note_id)
    if row is None or row.tenant_id != tenant_id or row.project_id != project_id or row.deleted_at is not None:
        raise NotFound(f"笔记不存在: {note_id}")
    return row


def _validate_xref(kind: str) -> str:
    k = (kind or "manual").strip() or "manual"
    if k not in XREF_KINDS:
        raise InvalidNoteInput(f"xref_kind 非法: {k!r}（允许 {sorted(XREF_KINDS)}）")
    return k


def create_note(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID,
    title: str = "",
    body: str = "",
    paper_id: uuid.UUID | None = None,
    page: int | None = None,
    section: str = "",
    selected_text: str = "",
    bbox: list | None = None,
    xref_kind: str = "manual",
    xref_id: str = "",
) -> ResearchNoteRow:
    kind = _validate_xref(xref_kind)
    if not (title or "").strip() and not (body or "").strip():
        raise InvalidNoteInput("title 与 body 不能同时为空")

    locator: dict = {}
    if paper_id is not None:
        locator["paper_id"] = str(paper_id)
    if page is not None:
        locator["page"] = int(page)
    if section:
        locator["section"] = section
    if selected_text:
        locator["selected_text"] = selected_text
    if bbox:
        locator["bbox"] = list(bbox)

    now = utcnow()
    row = ResearchNoteRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        actor_id=ctx.actor_id,
        title=(title or "")[:2000],
        body=body or "",
        paper_id=paper_id,
        page=int(page) if page is not None else None,
        section=section or "",
        selected_text=selected_text or "",
        search_tokens="",
        locator=locator,
        xref_kind=kind,
        xref_id=(xref_id or "")[:64],
        created_at=now,
        updated_at=now,
    )
    _rebuild_note_tokens(row)
    db.add(row)
    db.flush()
    return row


def update_note(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    note_id: uuid.UUID,
    title: str | None = None,
    body: str | None = None,
    page: int | None = None,
    section: str | None = None,
    selected_text: str | None = None,
) -> ResearchNoteRow:
    row = get_note(db, tenant_id=tenant_id, project_id=project_id, note_id=note_id)
    if title is not None:
        row.title = title[:2000]
    if body is not None:
        row.body = body
    if page is not None:
        row.page = int(page)
    if section is not None:
        row.section = section
    if selected_text is not None:
        row.selected_text = selected_text
    # 重建 locator（保持与显式列一致）
    locator = dict(row.locator or {})
    if row.paper_id is not None:
        locator["paper_id"] = str(row.paper_id)
    if row.page is not None:
        locator["page"] = row.page
    if row.section:
        locator["section"] = row.section
    if row.selected_text:
        locator["selected_text"] = row.selected_text
    row.locator = locator
    _rebuild_note_tokens(row)
    row.updated_at = utcnow()
    db.flush()
    return row


def delete_note(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, note_id: uuid.UUID
) -> None:
    row = get_note(db, tenant_id=tenant_id, project_id=project_id, note_id=note_id)
    row.deleted_at = utcnow()
    row.updated_at = utcnow()
    db.flush()


__all__ = [
    "InvalidNoteInput",
    "create_note",
    "delete_note",
    "get_note",
    "list_notes",
    "note_public_dict",
    "recent_notes",
    "update_note",
]
