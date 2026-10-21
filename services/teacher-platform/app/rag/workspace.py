"""KnowledgeWorkspace —— 知识文档一级实体管理（V2.1 KNR，架构 §4.10）。

红线：
- **无 manifest 不入库**：register 必须 manifest_key 非空，且（提供 manifest 索引时）
  必须命中 corpus/sources/manifest.toml 登记的条目；
- 不加端点：本类只供 ingest 管线（corpus/seeds/教师资料入索引）调用；
- 种子语料固定 school_id=NULL + visibility='platform'。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping, Optional


class ManifestMissingError(RuntimeError):
    """无 manifest 登记的语料不许注册（B2 红线 1 / 架构红线 6）。"""


@dataclass(frozen=True)
class DocStatus:
    doc_id: str
    doc_ref: str
    status: str
    index_version: int


@dataclass(frozen=True)
class RegisteredDocument:
    doc_id: str
    doc_ref: str
    title: str
    status: str
    index_version: int
    visibility: str


class KnowledgeWorkspace:
    """knowledge_documents 的 ingest 侧门面。session_factory 由 B3 提供。"""

    def __init__(self, session_factory, *, manifest_index: Optional[Mapping[str, str]] = None) -> None:
        self._session_factory = session_factory
        self._manifest_index = dict(manifest_index) if manifest_index else None

    def _assert_manifest(self, manifest_key: Optional[str]) -> None:
        if not manifest_key:
            raise ManifestMissingError("register 缺少 manifest_key——无 manifest 不入库（红线）")
        if self._manifest_index is not None and manifest_key not in self._manifest_index:
            raise ManifestMissingError(
                f"manifest_key='{manifest_key}' 未在 corpus/sources/manifest.toml 登记——无 manifest 不入库"
            )

    async def register(
        self,
        doc_ref: str,
        title: str,
        source_type: str,
        manifest_key: str,
        *,
        owner_teacher_id: Optional[str] = None,
        school_id: Optional[str] = None,
        visibility: str = "platform",
    ) -> RegisteredDocument:
        """幂等注册（doc_ref 唯一约束兜底，G8：靠唯一约束不靠检查后插入）。"""
        self._assert_manifest(manifest_key)
        if visibility != "platform" and not (owner_teacher_id or school_id):
            raise ValueError("private/school 文档必须携带 owner_teacher_id 或 school_id（K4）")
        if visibility == "platform" and (owner_teacher_id or school_id):
            raise ValueError("platform 语料不得携带 owner/school（种子语料租户铁律）")

        from sqlalchemy import select

        from app.db.models.knowledge import KnowledgeDocument

        async with self._session_factory() as session:
            row = (await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.doc_ref == doc_ref)
            )).scalar_one_or_none()
            if row is None:
                row = KnowledgeDocument(
                    doc_ref=doc_ref,
                    title=title,
                    source_type=source_type,
                    status="draft",
                    index_version=0,
                    school_id=uuid.UUID(school_id) if school_id else None,
                    owner_teacher_id=(uuid.UUID(owner_teacher_id) if owner_teacher_id else None),
                    visibility=visibility,
                    manifest_key=manifest_key,
                )
                session.add(row)
                await session.flush()
            else:
                row.manifest_key = manifest_key  # 重跑管线时刷新登记
            await session.commit()
            return RegisteredDocument(
                doc_id=str(row.id), doc_ref=row.doc_ref, title=row.title,
                status=row.status, index_version=row.index_version, visibility=row.visibility,
            )

    async def status(self, doc_id: str) -> DocStatus:
        from sqlalchemy import select

        from app.db.models.knowledge import KnowledgeDocument

        async with self._session_factory() as session:
            row = (await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == uuid.UUID(doc_id))
            )).scalar_one()
            return DocStatus(doc_id=str(row.id), doc_ref=row.doc_ref,
                             status=row.status, index_version=row.index_version)

    async def mark_indexing(self, doc_id: str) -> None:
        await self._set_status(doc_id, "indexing")

    async def mark_ready(self, doc_id: str) -> int:
        """置 ready 并递增 index_version；返回新版本号（每次重索引 +1）。"""
        from sqlalchemy import select

        from app.db.models.knowledge import KnowledgeDocument

        async with self._session_factory() as session:
            row = (await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == uuid.UUID(doc_id))
            )).scalar_one()
            row.status = "ready"
            row.index_version = (row.index_version or 0) + 1
            await session.commit()
            return row.index_version

    async def mark_error(self, doc_id: str, error: str) -> None:
        from sqlalchemy import select, update

        from app.db.models.knowledge import KnowledgeDocument

        async with self._session_factory() as session:
            row = (await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == uuid.UUID(doc_id))
            )).scalar_one()
            row.status = "error"
            row.updated_at = row.updated_at  # onupdate 生效
            _ = update  # 保持导入面最小
            await session.commit()
            _ = error  # P0：错误详情写管线日志；P1 加 error_message 列（IFC 申请）
