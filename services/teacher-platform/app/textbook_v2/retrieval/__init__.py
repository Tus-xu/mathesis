"""教材 V2 检索（R08，P0）：FakeEmbedding / 顺序回填 / 索引写入 / Provider / TeachingContext。

- 独立索引 namespace：只写 textbook_v2 的 textbook_chunks.embedding 系列列，
  绝不写 services/api 的 1024 结构（R04 铁律：teacher 768 / api 1024）；
- rights：检索可见 = STAGED+ACTIVE（visible_scope 语义），QUARANTINED 永不进结果；
- 顺序前置（R07 教训）：backfill_chunk_order_from_slice 幂等回填 order；
- index_version 是版本号而非位图：回滚 = 切换读取版本，不删向量。
"""

from app.textbook_v2.retrieval.backfill import (  # noqa: F401
    BackfillResult,
    backfill_chunk_order_from_slice,
)
from app.textbook_v2.retrieval.context import load_teaching_context  # noqa: F401
from app.textbook_v2.retrieval.dto import (  # noqa: F401
    ChunkPreview,
    Fact,
    ProblemCandidate,
    SectionCandidate,
    SectionInfo,
    SourceInfo,
    SourceView,
    TextbookTeachingContext,
    WarningEntry,
)
from app.textbook_v2.retrieval.fake_embedding import (  # noqa: F401
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    FakeEmbedding,
    cosine_similarity,
)
from app.textbook_v2.retrieval.index import (  # noqa: F401
    INDEX_VERSION,
    IndexResult,
    embed_index,
)
from app.textbook_v2.retrieval.provider import (  # noqa: F401
    list_example_candidates,
    locate_sections,
    resolve_source_ref,
)

__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "INDEX_VERSION",
    "FakeEmbedding",
    "cosine_similarity",
    "BackfillResult",
    "backfill_chunk_order_from_slice",
    "IndexResult",
    "embed_index",
    "locate_sections",
    "load_teaching_context",
    "list_example_candidates",
    "resolve_source_ref",
    "SectionCandidate",
    "SectionInfo",
    "SourceInfo",
    "SourceView",
    "Fact",
    "ChunkPreview",
    "WarningEntry",
    "ProblemCandidate",
    "TextbookTeachingContext",
]
