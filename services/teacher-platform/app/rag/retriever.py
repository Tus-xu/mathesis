"""Retriever —— pgvector 余弦召回 + BGE 精排（架构 §8 冻结签名）。

租户铁律（K4，架构红线 15）：一切向量检索必须经 knowledge_documents 租户预过滤——
private → 本人 / school → 同校 / platform → 全员。API 层 assert_resource_owner 挡不住
向量召回层的跨租户泄漏；本模块是泄漏防线本体，tenant=None 时只召回 platform 语料
（种子管线安全默认），绝不无界召回。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from sqlalchemy import bindparam, case, literal_column, or_, select  # noqa: F401
from sqlalchemy import text as text_

from app.db.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.rag.embedder import Embedder, EmbedderError
from app.rag.collections import validate_collection

_RRF_K = 60  # RRF 常数（cormack et al. 惯用值）


@dataclass(frozen=True)
class TenantScope:
    """检索发起者的租户身份；None = 仅 platform 语料。"""

    teacher_id: Optional[str] = None
    school_id: Optional[str] = None


@dataclass
class RetrievedChunk:
    """证据块。doc_ref 定位 + content_hash 指纹（EvidencePack/EvidenceManifest 必需）。"""

    collection: str
    doc_ref: str
    chunk_index: int
    content: str
    score: float
    metadata: dict = field(default_factory=dict)
    content_hash: str = ""
    title: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def locator(self) -> str:
        """教师可见定位（「人教A版选必一 P89」式，来自 metadata.locator）。"""
        return str(self.metadata.get("locator") or self.doc_ref)


def _tenant_predicate(tenant: Optional[TenantScope]):
    """K4 预过滤谓词：platform 恒可见；private/school 需身份匹配。"""
    platform = KnowledgeDocument.visibility == "platform"
    if tenant is None:
        return platform
    clauses = [platform]
    if tenant.teacher_id:
        clauses.append(
            (KnowledgeDocument.visibility == "private")
            & (KnowledgeDocument.owner_teacher_id == tenant.teacher_id)
        )
    if tenant.school_id:
        clauses.append(
            (KnowledgeDocument.visibility == "school")
            & (KnowledgeDocument.school_id == tenant.school_id)
        )
    return or_(*clauses)


class Retriever:
    """向量检索层。session_factory: SQLAlchemy async_sessionmaker（B3 提供）。"""

    def __init__(self, embedder: Embedder, session_factory=None, *, fetch_k: int = 20) -> None:
        self._embedder = embedder
        self._session_factory = session_factory
        self._fetch_k = fetch_k

    async def retrieve(
        self,
        query: str,
        *,
        collections: Sequence[str],
        top_k: int = 6,
        rerank: bool = True,
        tenant: Optional[TenantScope] = None,
        keyword_fusion: bool = False,
    ) -> list[RetrievedChunk]:
        """语义召回 + BGE 精排；keyword_fusion=True 追加中文关键词 ILIKE 召回做 RRF 融合
        （L8 关键词混合的钩子）。M4 实测：中文数学题干含 LaTeX 格式，ILIKE 子串匹配
        失效且泛词（选择题/计算题）污染候选池，hit@5 0.66→0.60 —— 默认关闭，
        中文分词接入后再启用（见 notes/B2-notes-m4-eval.md）。"""
        for c in collections:
            validate_collection(c)
        if self._session_factory is None:
            raise RuntimeError("Retriever 未配置 DB 会话工厂（B3 session_factory 注入）")
        if not query.strip() or not collections:
            return []
        qvec = (await self._embedder.embed([query]))[0]
        dist = KnowledgeChunk.embedding.cosine_distance(qvec)
        stmt = (
            select(KnowledgeChunk, KnowledgeDocument.title, dist.label("dist"))
            .join(KnowledgeDocument, KnowledgeChunk.doc_ref == KnowledgeDocument.doc_ref)
            .where(
                KnowledgeDocument.status == "ready",
                KnowledgeChunk.collection.in_(list(collections)),
                _tenant_predicate(tenant),
            )
            .order_by(dist)
            .limit(max(self._fetch_k, top_k))
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).all()
            candidates = self._rows_to_chunks(rows, lambda row: 1.0 - float(row.dist))
            if keyword_fusion:
                candidates = await self._fuse_keywords(session, query, list(collections),
                                                       tenant, candidates, top_k)

        if not candidates:
            return []
        if rerank and len(candidates) > 1:
            try:
                hits = await self._embedder.rerank(query, [c.content for c in candidates],
                                                   top_n=top_k)
            except EmbedderError:
                # 重排通道不可用（当前 embedding 供应商为讯飞，不提供 reranker）→
                # 降级为语义召回序（不假精排、不抛错，检索仍可用）
                hits = []
            ranked: list[RetrievedChunk] = []
            for h in hits:
                chunk = candidates[h.index]
                chunk.score = h.relevance_score
                ranked.append(chunk)
            if ranked:
                return ranked
        return sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]

    @staticmethod
    def _rows_to_chunks(rows, score_fn) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                collection=row[0].collection,
                doc_ref=row[0].doc_ref,
                chunk_index=row[0].chunk_index,
                content=row[0].content,
                score=score_fn(row),
                metadata=row[0].metadata_ or {},
                content_hash=row[0].content_hash or "",
                title=row[1] or "",
            )
            for row in rows
        ]

    async def _fuse_keywords(self, session, query: str, collections: list[str],
                             tenant: Optional[TenantScope], semantic: list[RetrievedChunk],
                             top_k: int) -> list[RetrievedChunk]:
        """关键词召回与语义召回做 RRF 融合（两路各 fetch_k，融合后取前 fetch_k）。"""
        terms = [t for t in re.split(r"[\s,，、;；]+", query) if len(t) >= 2][:6]
        if not terms:
            return semantic
        score = sum(
            case((KnowledgeChunk.content.ilike(bindparam(f"t{i}")), 1), else_=0)
            for i in range(len(terms))
        )
        params = {f"t{i}": f"%{term}%" for i, term in enumerate(terms)}
        kw_stmt = (
            select(KnowledgeChunk, KnowledgeDocument.title,
                   score.label("kw"))
            .join(KnowledgeDocument, KnowledgeChunk.doc_ref == KnowledgeDocument.doc_ref)
            .where(
                KnowledgeDocument.status == "ready",
                KnowledgeChunk.collection.in_(collections),
                _tenant_predicate(tenant),
                or_(*[KnowledgeChunk.content.ilike(p) for p in params.values()]),
            )
            .order_by(text_("kw DESC"))
            .limit(max(self._fetch_k, top_k))
        )
        kw_rows = (await session.execute(kw_stmt, params)).all()
        kw_chunks = self._rows_to_chunks(kw_rows, lambda row: float(row.kw))

        from collections import defaultdict

        rrf: dict[tuple, float] = defaultdict(float)
        for rank, ch in enumerate(semantic):
            rrf[(ch.doc_ref, ch.chunk_index)] += 1.0 / (_RRF_K + rank + 1)
        for rank, ch in enumerate(kw_chunks):
            rrf[(ch.doc_ref, ch.chunk_index)] += 1.0 / (_RRF_K + rank + 1)
        pool = {**{(c.doc_ref, c.chunk_index): c for c in semantic},
                **{(c.doc_ref, c.chunk_index): c for c in kw_chunks}}
        fused = sorted(pool.values(), key=lambda c: rrf.get((c.doc_ref, c.chunk_index), 0.0),
                       reverse=True)
        return fused[: max(self._fetch_k, top_k)]
