"""教师生成使用的真实知识证据适配器。

``ContextAssembler`` 只依赖 ``KnowledgeWorkspace.retrieve`` 协议；这里把 teacher
RAG 的 ``RetrievedChunk`` 转成同一套 EvidencePack 片段，不把学生服务的 DB/router
引入教师服务。检索失败返回空证据，由上层质量门禁阻止伪 ready。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .models import EvidenceSource


class TeacherKnowledgeEvidence:
    """Retriever → ContextAssembler KnowledgeWorkspace 的窄适配器。"""

    def __init__(self, retriever: Any, *, collections: Sequence[str]) -> None:
        self._retriever = retriever
        self._collections = tuple(collections)

    async def retrieve(
        self, query: str, *, scope_ids: list[str], feature: str
    ) -> tuple[list[str], list[EvidenceSource]]:
        if not str(query or "").strip() or not self._collections:
            return [], []
        try:
            chunks = await self._retriever.retrieve(
                str(query),
                collections=self._collections,
                top_k=6,
                rerank=True,
                tenant=None,
            )
        except Exception:
            # 上游 embedding/DB 不可用时保留诚实空态；不能用占位文本冒充教材事实。
            return [], []
        facts: list[str] = []
        sources: list[EvidenceSource] = []
        for chunk in chunks or []:
            content = str(getattr(chunk, "content", "") or "").strip()
            if not content:
                continue
            facts.append(content)
            collection = str(getattr(chunk, "collection", "") or "")
            source_type = (
                "quiz" if collection == "quiz_bank"
                else "curriculum" if collection == "curriculum"
                else "textbook"
            )
            doc_ref = str(getattr(chunk, "doc_ref", "") or "unknown")
            chunk_index = getattr(chunk, "chunk_index", 0)
            locator = chunk.locator() if hasattr(chunk, "locator") else doc_ref
            sources.append(EvidenceSource(
                source_id=f"{doc_ref}#{chunk_index}",
                source_type=source_type,
                locator=str(locator),
                retrieval_score=float(getattr(chunk, "score", 0.0) or 0.0),
                content_hash=str(getattr(chunk, "content_hash", "") or "") or None,
                used=True,
            ))
        return facts, sources


def build_default_teacher_knowledge_evidence(session_factory: Any) -> TeacherKnowledgeEvidence:
    """构造生产 RAG provider；密钥/模型仍由 Embedder 环境配置读取。"""

    from app.rag.collections import (
        COLLECTION_CURRICULUM,
        COLLECTION_KP_GRAPH,
        COLLECTION_LESSON_BENCH,
        COLLECTION_PPT_SLOTS,
        COLLECTION_QUIZ_BANK,
    )
    from app.rag.embedder import Embedder
    from app.rag.retriever import Retriever

    retriever = Retriever(
        Embedder(),
        session_factory,
        fetch_k=20,
    )
    return TeacherKnowledgeEvidence(
        retriever,
        collections=(
            COLLECTION_KP_GRAPH,
            COLLECTION_CURRICULUM,
            COLLECTION_QUIZ_BANK,
            COLLECTION_LESSON_BENCH,
            COLLECTION_PPT_SLOTS,
        ),
    )


__all__ = ["TeacherKnowledgeEvidence", "build_default_teacher_knowledge_evidence"]
