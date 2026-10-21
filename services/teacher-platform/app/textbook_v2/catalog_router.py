"""教材目录只读端点（T2，PLAN §4.2/§11-B3）。

为备课中心前端（B5 PrepFromTextbook 章节数据源）提供教材目录事实：

- ``GET /textbooks``：已授权教材列表（仅 ACTIVE/STAGED，复用 ``rights.visible_scope()``
  ——QUARANTINED 永不出现）；``{items:[{source_id,batch_id,book_name,volume,
  rights_status,section_count}]}``；
- ``GET /textbooks/{source_id}/sections``：指定教材的章节目录，
  ``{sections:[{id,section_path,title,level,order}]}``。

过滤口径 **ADAPT 自** ``teacher_prep/conversation/options.py``（LP-01，不新造目录逻辑）：
level>1、有 section_path / 真实编号标题、剔除「未分节」「1. 范围」等 PDF 切片碎片；
排序 = 教材编号自然序（``_sort_key`` 同源）。本文件只读：不写库、不动 rights 状态机、
不编造目录——查不到就返回空集合（诚实空态）。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.deps import get_current_user
from ..db.session import get_session
from .models import TextbookSection, TextbookSource
from .rights import visible_scope

router = APIRouter()

# LP-01 同源：真实章节编号形如「3.1.1」「1.2」；不带编号且无 section_path 的 level=1 行
# 是 PDF 切片碎片段（「未分节」「1. 范围」），不得进目录（options.py:27 已核同款）。
_SECTION_NO_RE = re.compile(r"^(\d+)(?:\.\d+)*[.、]?\s")


def _sort_key(text: str) -> tuple[int, ...]:
    """按教材编号自然序排序键（「3.1.1」<「3.2.1」<「10.1」）；无编号 → 末尾稳定序。

    ADAPT 自 options.py::_sort_key（同源复制，跨域 import 会引入 prep 依赖，不取）。
    """
    m = re.match(r"^\s*(\d+(?:\.\d+)*)", text or "")
    if not m:
        return (9999,)
    return tuple(int(x) for x in m.group(1).split("."))


def _display_name(source: TextbookSource) -> str:
    """教材展示名：publisher/edition/grade/volume 去重拼接（options.label_of 同口径）。

    TextbookSource 无 book_name 列（书名事实在批次 manifest），DB 投影用既有的
    label 口径，避免前端再拼一遍。
    """
    seen: set[str] = set()
    parts: list[str] = []
    for raw in (source.publisher, source.edition, source.grade, source.volume):
        part = str(raw or "").strip()
        if part and part not in seen:
            seen.add(part)
            parts.append(part)
    return " · ".join(parts)


@router.get("/textbooks")
async def list_textbooks(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    """已授权教材目录（T2）：仅 ACTIVE/STAGED；QUARANTINED 永不可见（rights.visible_scope）。"""
    stmt = (
        select(
            TextbookSource,
            func.count(TextbookSection.id).label("section_count"),
        )
        .where(visible_scope())
        .outerjoin(TextbookSection, TextbookSection.textbook_id == TextbookSource.id)
        .group_by(TextbookSource.id)
        .order_by(TextbookSource.publisher, TextbookSource.grade, TextbookSource.volume)
        .limit(200)
    )
    rows = (await session.execute(stmt)).all()
    items = [
        {
            "source_id": str(src.id),
            "batch_id": src.batch_id,
            "book_name": _display_name(src),
            "volume": src.volume,
            "rights_status": src.rights_status,
            "section_count": int(section_count or 0),
        }
        for src, section_count in rows
    ]
    return {"items": items}


@router.get("/textbooks/{source_id}/sections")
async def list_textbook_sections(
    source_id: str,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    """指定教材的章节目录（T2）：LP-01 噪声过滤 + 编号自然序；未授权/未知 → 404。"""
    try:
        source_uid = source_id if isinstance(source_id, uuid.UUID) else uuid.UUID(str(source_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="教材不存在") from None

    source = (
        await session.execute(
            select(TextbookSource).where(
                TextbookSource.id == source_uid,
                visible_scope(),
            )
        )
    ).scalars().first()
    if source is None:
        raise HTTPException(status_code=404, detail="教材不存在或未授权")

    rows = (
        (
            await session.execute(
                select(TextbookSection)
                .where(TextbookSection.textbook_id == source_uid)
                .order_by(TextbookSection.level, TextbookSection.order)
                .limit(400)
            )
        )
        .scalars()
        .all()
    )
    sections: list[dict[str, Any]] = []
    for r in rows:
        path = (r.section_path or "").strip()
        title = (r.title or "").strip()
        if not title:
            continue
        numbered = bool(_SECTION_NO_RE.match(title)) or bool(path)
        # T2 口径：真实章节 = 有编号/path；无编号且无 path 的 level<=1 行是切片碎片（LP-01）
        if not numbered and (r.level or 1) <= 1:
            continue
        sections.append(
            {
                "id": str(r.id),
                "section_path": path or None,
                "title": title,
                "level": int(r.level or 1),
                "order": int(r.order or 0),
            }
        )
    sections.sort(key=lambda s: _sort_key(s["section_path"] or s["title"]))
    return {"sections": sections}
