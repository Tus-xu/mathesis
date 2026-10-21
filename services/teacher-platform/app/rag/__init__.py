"""app.rag —— B2 检索层对外面（B1 图节点只经这些入口取知识与向量）。

- Embedder：全平台唯一 embed 入口（G2 定案，LlmGateway 无 embed）
- Retriever.retrieve / CragPipeline.answer / search_web（架构 §8 冻结签名）
- KnowledgeWorkspace.register（ingest 管线专用，不加端点）
- RetrievalBackend / PgVectorCragBackend（KNR 演进边界）
"""

from app.rag.collections import (
    ALL_COLLECTIONS,
    COLLECTIONS,
    NINE_PAGE_TYPES,
    validate_collection,
)
from app.rag.embedder import DEFAULT_MODEL, EMBEDDING_DIMS, Embedder, EmbedderError
from app.rag.retriever import RetrievedChunk, Retriever, TenantScope
from app.rag.crag import CragAnswer, CragAssessment, CragPipeline
from app.rag.backend import (
    BackendHealth,
    IndexStatus,
    KnowledgeDocumentRef,
    PgVectorCragBackend,
    RetrievalBackend,
)
from app.rag.workspace import KnowledgeWorkspace, ManifestMissingError
from app.rag.websearch import WebResult, search_web

__all__ = [
    "ALL_COLLECTIONS", "COLLECTIONS", "NINE_PAGE_TYPES", "validate_collection",
    "DEFAULT_MODEL", "EMBEDDING_DIMS", "Embedder", "EmbedderError",
    "RetrievedChunk", "Retriever", "TenantScope",
    "CragAnswer", "CragAssessment", "CragPipeline",
    "BackendHealth", "IndexStatus", "KnowledgeDocumentRef",
    "PgVectorCragBackend", "RetrievalBackend",
    "KnowledgeWorkspace", "ManifestMissingError",
    "WebResult", "search_web",
]
