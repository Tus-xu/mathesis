"""R08 教材专用索引写入（index_version 版本号语义）。

- 独立索引 namespace：只写 textbook_v2 的 textbook_chunks.embedding 系列列；
  绝不把向量写入 services/api 的 1024 结构（R04 铁律：teacher 768 / api 1024）；
- 写库前先对全部目标 chunk 断言维度 == 768，任一不匹配即 fail closed（抛错不写任何行）；
- index_version 是版本号而非位图：回滚 = 切换读取版本，不删向量、不做破坏性 delete；
- 重跑同 (chunk, index_version) 幂等：已写入且版本一致 → skip（同 content_hash skip 语义）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.importers.slice_importer import CHUNKER_VERSION
from app.textbook_v2.models import TextbookChunk, TextbookSection
from app.textbook_v2.retrieval.fake_embedding import EMBEDDING_DIM, Embedder, FakeEmbedding

# 首个索引版本（fake）；真实 embedding provider（R11）接入时 bump 到 2，可回滚读取 v1
INDEX_VERSION = 1


@dataclass
class IndexResult:
    """一次索引写入的统计（无敏感内容）。"""

    index_version: int
    embedding_model: str
    embedding_dim: int
    written: int = 0
    skipped: int = 0
    issues: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "index_version": self.index_version,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "written": self.written,
            "skipped": self.skipped,
            "issues": self.issues,
        }


async def embed_index(
    session: AsyncSession,
    *,
    source_ids: list[Any] | None = None,
    index_version: int = INDEX_VERSION,
    embedder: Embedder | None = None,
) -> IndexResult:
    """为目标 source 的 chunk 写入 embedding 索引（维度 fail closed，先验后写）。

    - source_ids=None → 全部 section 参与（调用方按需传可见 source 列表）；
    - 已写入且 index_version / embedding_model 相同 → skip（幂等）；
    - 任一向量维度 != 768 → 抛 ValueError，且不执行任何 UPDATE（原子不写）。
    """
    embedder = embedder or FakeEmbedding()
    result = IndexResult(index_version=index_version, embedding_model=embedder.model, embedding_dim=embedder.dim)

    sec_q = select(TextbookSection.id)
    if source_ids:
        sec_q = sec_q.where(TextbookSection.textbook_id.in_(source_ids))
    section_ids = list((await session.execute(sec_q)).scalars().all())
    if not section_ids:
        result.issues.append("无目标 section，跳过索引")
        return result

    chunks = (
        (await session.execute(select(TextbookChunk).where(TextbookChunk.section_id.in_(section_ids)))).scalars().all()
    )

    # 先验：收集需要写入的 (chunk, vec)，全部维度合法后才进入写阶段
    pending: list[tuple[TextbookChunk, list[float]]] = []
    for chunk in chunks:
        if (
            chunk.index_version == index_version
            and chunk.embedding is not None
            and chunk.embedding_model == embedder.model
        ):
            result.skipped += 1
            continue
        vec = embedder.embed(chunk.text)
        if len(vec) != EMBEDDING_DIM:
            raise ValueError(
                f"dimension mismatch: expected {EMBEDDING_DIM}, got {len(vec)} — fail closed，不写库"
            )
        pending.append((chunk, vec))

    for chunk, vec in pending:
        # ORM 属性直写（保持 identity map 同步，后续查询即见新值）
        chunk.embedding = vec
        chunk.embedding_model = embedder.model
        chunk.embedding_dim = embedder.dim
        chunk.chunker_version = CHUNKER_VERSION
        chunk.index_version = index_version
        result.written += 1
    if pending:
        await session.commit()
    return result
