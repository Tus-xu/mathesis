"""RV17 · 项目级文献服务（P0-1）。

- 项目↔文献归属 + 元数据（title/authors/year/venue/doi/read_state/page_count/parse_status）
- 上传（multipart PDF）/ 挂载既有 asset
- 阅读状态与页码更新（PATCH）
- 按页文本 + bbox（供阅读器选文与定位）

**解析失败不得让文献不可用**（宪法 §10）：`parse_status=failed` 时 `pages=[]`，
但 `asset_id` 仍在，阅读器直接读 PDF 字节。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import AccessContext
from ..assets import service as asset_svc
from ..assets.storage import MinioObjectStorage, StorageConfig
from ..domain.errors import NotFound
from .models import (
    PARSE_FAILED,
    PARSE_NONE,
    PARSE_PARSED,
    READ_READING,
    READ_STATES,
    READ_UNREAD,
    ResearchPaperRow,
    utcnow,
)
from .pdfs import extract_pdf
from .search_tokenize import index_text

MAX_TITLE = 2000


class InvalidPaperInput(Exception):
    """文献入参非法（API 层映射为 422）。"""


def paper_public_dict(row: ResearchPaperRow) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "asset_id": str(row.asset_id) if row.asset_id else None,
        "title": row.title or "",
        "authors": list(row.authors or []),
        "year": row.year,
        "venue": row.venue or "",
        "doi": row.doi or "",
        "read_state": row.read_state,
        "last_page": int(row.last_page or 1),
        "page_count": int(row.page_count or 0),
        "parse_status": row.parse_status,
        "parse_error": row.parse_error or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _norm_authors(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for a in raw:
            if isinstance(a, str) and a.strip():
                out.append(a.strip())
            elif isinstance(a, dict):
                name = str(a.get("name") or "").strip()
                if name:
                    out.append(name)
        return out
    return []


def _norm_year(raw) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        y = int(raw)
    except (TypeError, ValueError):
        return None
    return y if 1500 <= y <= 2999 else None


def list_papers(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> list[ResearchPaperRow]:
    rows = db.execute(
        select(ResearchPaperRow)
        .where(
            ResearchPaperRow.tenant_id == tenant_id,
            ResearchPaperRow.project_id == project_id,
            ResearchPaperRow.deleted_at.is_(None),
        )
        .order_by(ResearchPaperRow.created_at.desc())
    ).scalars().all()
    return list(rows)


def get_paper(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, paper_id: uuid.UUID
) -> ResearchPaperRow:
    row = db.get(ResearchPaperRow, paper_id)
    if row is None or row.tenant_id != tenant_id or row.project_id != project_id or row.deleted_at is not None:
        raise NotFound(f"文献不存在: {paper_id}")
    return row


def _load_asset_bytes(db: Session, asset_id: uuid.UUID) -> bytes:
    asset = asset_svc.get_asset(db, asset_id)
    if asset is None:
        raise InvalidPaperInput("asset 不存在")
    st = MinioObjectStorage(StorageConfig.from_env())
    return st.get_bytes(asset.storage_key)


def _rebuild_paper_tokens(row: ResearchPaperRow) -> None:
    """重建检索词元（元数据 + 正文），供 CJK bigram/拉丁全文检索使用。

    **元数据（title/authors/venue/doi）必须计入** —— 否则「标题里有的中文」
    在正文抽取为空或非中文时不可检索（P0-6 中文检索缺陷根因之一）。
    """
    row.search_tokens = index_text(
        row.title, " ".join(row.authors or []), row.venue, row.doi, row.search_text
    )


def _parse_into_paper(db: Session, row: ResearchPaperRow) -> None:
    """对 `row.asset_id` 的 PDF 做页级抽取并把结果写回 row（失败 → failed）。"""
    if row.asset_id is None:
        row.parse_status = PARSE_NONE
        _rebuild_paper_tokens(row)
        return
    try:
        pdf_bytes = _load_asset_bytes(db, row.asset_id)
        data = extract_pdf(pdf_bytes)
    except Exception as e:  # noqa: BLE001 - 解析失败不得让文献不可用
        row.parse_status = PARSE_FAILED
        row.parse_error = f"{type(e).__name__}: {e}"[:1000]
        row.pages = []
        row.page_sections = []
        row.search_text = ""
        row.page_count = row.page_count or 0
        # 正文不可用，但元数据仍应可检索（诚实：不因解析失败而整条消失）。
        _rebuild_paper_tokens(row)
        return

    meta = data.get("metadata") or {}
    if not (row.title or "").strip():
        row.title = (str(meta.get("title") or "").strip() or row.title or "")[:MAX_TITLE]
    if not row.authors and str(meta.get("author") or "").strip():
        row.authors = _norm_authors(meta.get("author"))
    row.pages = data.get("pages") or []
    row.page_sections = data.get("page_sections") or []
    row.search_text = data.get("search_text") or ""
    row.page_count = int(data.get("page_count") or 0)
    row.parse_status = PARSE_PARSED
    row.parse_error = ""
    if row.last_page < 1:
        row.last_page = 1
    _rebuild_paper_tokens(row)


def _find_by_hash(
    db: Session, *, project_id: uuid.UUID, content_hash: str
) -> ResearchPaperRow | None:
    if not content_hash:
        return None
    return db.execute(
        select(ResearchPaperRow).where(
            ResearchPaperRow.project_id == project_id,
            ResearchPaperRow.content_hash == content_hash,
            ResearchPaperRow.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def attach_from_asset(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
    title: str = "",
    authors=None,
    year=None,
    venue: str = "",
    doi: str = "",
) -> tuple[ResearchPaperRow, bool]:
    """挂载既有 asset 为项目文献。同 content_hash 去重 → `(row, deduplicated)`。"""
    asset = asset_svc.get_asset(db, asset_id)
    if asset is None:
        raise InvalidPaperInput(f"asset 不存在: {asset_id}")
    asset_svc.authorize_asset(db, ctx, asset)

    existing = _find_by_hash(db, project_id=project_id, content_hash=asset.content_hash)
    if existing is not None:
        return existing, True

    now = utcnow()
    row = ResearchPaperRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        owner_id=ctx.actor_id,
        asset_id=asset.id,
        title=(title or asset.original_filename or "未命名文献")[:MAX_TITLE],
        authors=_norm_authors(authors),
        year=_norm_year(year),
        venue=venue or "",
        doi=doi or "",
        read_state=READ_UNREAD,
        last_page=1,
        page_count=0,
        parse_status=PARSE_NONE,
        pages=[],
        page_sections=[],
        search_text="",
        search_tokens="",
        parse_error="",
        content_hash=asset.content_hash,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    _parse_into_paper(db, row)
    db.flush()
    return row, False


def attach_from_upload(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID,
    data_path,
    filename: str = "",
    claimed_media_type: str = "",
    title: str = "",
    authors=None,
    year=None,
    venue: str = "",
    doi: str = "",
    client_request_id: str = "",
) -> tuple[ResearchPaperRow, bool]:
    """上传 PDF → 建 asset → 建文献。同内容去重。"""
    asset, asset_deduped = asset_svc.upload_asset(
        db,
        ctx,
        project_id=project_id,
        data_path=data_path,
        filename=filename,
        claimed_media_type=claimed_media_type,
        client_request_id=client_request_id,
    )
    row, paper_deduped = attach_from_asset(
        db,
        ctx,
        project_id=project_id,
        asset_id=asset.id,
        title=title or filename,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
    )
    return row, (asset_deduped and paper_deduped)


def update_paper(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    read_state: str | None = None,
    last_page: int | None = None,
) -> ResearchPaperRow:
    row = get_paper(db, tenant_id=tenant_id, project_id=project_id, paper_id=paper_id)
    if read_state is not None:
        if read_state not in READ_STATES:
            raise InvalidPaperInput(f"read_state 非法: {read_state!r}（允许 {sorted(READ_STATES)}）")
        row.read_state = read_state
    if last_page is not None:
        page = int(last_page)
        if page < 1:
            raise InvalidPaperInput("last_page 必须 ≥ 1")
        if row.page_count and page > row.page_count:
            page = row.page_count
        row.last_page = page
        # 打开到某页即视为"在读"（未显式设 read_state 时）。
        if read_state is None and row.read_state == READ_UNREAD:
            row.read_state = READ_READING
    row.updated_at = utcnow()
    db.flush()
    return row


def delete_paper(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, paper_id: uuid.UUID
) -> None:
    row = get_paper(db, tenant_id=tenant_id, project_id=project_id, paper_id=paper_id)
    row.deleted_at = utcnow()
    row.updated_at = utcnow()
    db.flush()


def paper_text(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, paper_id: uuid.UUID, page: int
) -> dict:
    """按页返回文本块 + bbox。`page` 从 1 起。"""
    row = get_paper(db, tenant_id=tenant_id, project_id=project_id, paper_id=paper_id)
    if page < 1:
        raise InvalidPaperInput("page 必须 ≥ 1")
    pages = list(row.pages or [])
    target = next((p for p in pages if int(p.get("page") or 0) == page), None)
    if target is None:
        return {
            "paper_id": str(row.id),
            "page": page,
            "width": 0.0,
            "height": 0.0,
            "blocks": [],
            "parse_status": row.parse_status,
            "available": row.parse_status == PARSE_PARSED,
        }
    return {
        "paper_id": str(row.id),
        "page": page,
        "width": float(target.get("width") or 0.0),
        "height": float(target.get("height") or 0.0),
        "blocks": list(target.get("blocks") or []),
        "parse_status": row.parse_status,
        "available": True,
    }


__all__ = [
    "InvalidPaperInput",
    "attach_from_asset",
    "attach_from_upload",
    "delete_paper",
    "get_paper",
    "list_papers",
    "paper_public_dict",
    "paper_text",
    "update_paper",
]
