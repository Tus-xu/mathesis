"""RetrievalBackend —— 检索引擎演进边界（V2.1 KNR，架构 §4.10）。

P0 唯一实现 PgVectorCragBackend（包装既有 Retriever + CragPipeline，不重写）。
PageIndex/GraphRAG/LightRAG 永远在本协议之后替换（P2+），不进 P0/P1 代码——
学 DeepTutor 的 architecture boundary，不学它的 feature count。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from app.rag.retriever import RetrievedChunk, Retriever, TenantScope
from app.rag.workspace import KnowledgeWorkspace


@dataclass
class KnowledgeDocumentRef:
    """ingest 输入：已注册文档 + 其分块（内容与元数据）。"""

    doc_id: str
    doc_ref: str
    collection: str
    chunks: list[tuple[str, dict]] = field(default_factory=list)  # (content, metadata)


@dataclass
class IndexStatus:
    ok: bool
    doc_ref: str
    indexed_chunks: int = 0
    index_version: int = 0
    error: Optional[str] = None


@dataclass
class BackendHealth:
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RetrievalBackend(Protocol):
    """冻结签名（02-ARCHITECTURE §8）。chunks 为 additive keyword 参数。"""

    async def ingest(self, doc: KnowledgeDocumentRef, *, chunks: Optional[list] = None) -> IndexStatus: ...

    async def retrieve(
        self,
        query: str,
        *,
        scope: Optional[TenantScope] = None,
        collections: Optional[Sequence[str]] = None,
        top_k: int = 6,
    ) -> list[RetrievedChunk]: ...

    async def health(self) -> BackendHealth: ...


class PgVectorCragBackend:
    """P0 唯一实现：pgvector 召回 + BGE 精排 + CRAG 评估（包装，不重写）。"""

    def __init__(self, retriever: Retriever, workspace: KnowledgeWorkspace) -> None:
        self._retriever = retriever
        self._workspace = workspace

    async def ingest(
        self, doc: KnowledgeDocumentRef, *, chunks: Optional[list] = None
    ) -> IndexStatus:
        """分块 embedding 入库：要求文档已 register（无 manifest 不注册），幂等重跑。

        chunks 元素 = (content, metadata)；重复 (doc_ref, chunk_index) 由唯一约束
        冲突后跳过（幂等重跑语义）。
        """
        items = chunks if chunks is not None else doc.chunks
        if not items:
            return IndexStatus(ok=False, doc_ref=doc.doc_ref, error="无分块内容（ingest 拒绝空索引）")
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            from app.db.models.knowledge import KnowledgeChunk
            from app.rag.embedder import EmbedderError

            embedder: Embedder = self._retriever._embedder  # noqa: SLF001 — 同域包装层
            await self._workspace.mark_indexing(doc.doc_id)
            vectors = await embedder.embed([c for c, _m in items])
            async with self._retriever._session_factory() as session:  # noqa: SLF001
                inserted = 0
                for i, ((content, meta), vec) in enumerate(zip(items, vectors)):
                    stmt = pg_insert(KnowledgeChunk).values(
                        collection=doc.collection,
                        doc_ref=doc.doc_ref,
                        chunk_index=meta.get("chunk_index", i),
                        content=content,
                        embedding=vec,
                        metadata_=meta,
                        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    )
                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["doc_ref", "chunk_index"]
                    )
                    result = await session.execute(stmt)
                    inserted += result.rowcount or 0
                await session.commit()
            version = await self._workspace.mark_ready(doc.doc_id)
            return IndexStatus(ok=True, doc_ref=doc.doc_ref, indexed_chunks=inserted,
                               index_version=version)
        except Exception as exc:
            try:
                await self._workspace.mark_error(doc.doc_id, str(exc))
            except Exception:
                pass
            return IndexStatus(ok=False, doc_ref=doc.doc_ref, error=f"{type(exc).__name__}: {exc}")

    async def retrieve(
        self,
        query: str,
        *,
        scope: Optional[TenantScope] = None,
        collections: Optional[Sequence[str]] = None,
        top_k: int = 6,
    ) -> list[RetrievedChunk]:
        cols = list(collections) if collections else None
        if cols is None:
            raise ValueError("retrieve 必须显式给出 collections（防全库无界召回）")
        return await self._retriever.retrieve(
            query, collections=cols, top_k=top_k, rerank=True, tenant=scope
        )

    async def health(self) -> BackendHealth:
        details: dict[str, Any] = {"backend": "PgVectorCragBackend"}
        try:
            if self._retriever._session_factory is None:  # noqa: SLF001
                details["db"] = "session_factory 未注入"
                return BackendHealth(ok=False, details=details)
            from sqlalchemy import text as sql_text

            async with self._retriever._session_factory() as session:
                await session.execute(sql_text("SELECT 1"))
            details["db"] = "ok"
        except Exception as exc:
            details["db"] = f"{type(exc).__name__}: {exc}"
            return BackendHealth(ok=False, details=details)
        try:
            from app.rag.embedder import EmbedderError

            _ = self._retriever._embedder._key()  # noqa: SLF001 — 健康检查含 key 存在性
            details["embedder"] = "key 已配置"
        except EmbedderError as exc:
            details["embedder"] = str(exc)
        return BackendHealth(ok=details.get("db") == "ok", details=details)
