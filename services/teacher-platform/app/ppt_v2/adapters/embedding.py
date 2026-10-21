"""EmbeddingAdapter —— V2 教材索引 embedding 关卡（R11）。

包装 app.rag.embedder.Embedder（全平台唯一 embedding 入口，G2 定案；dirty 现场不改写）。
在既有契约之上叠加 V2 铁律：
- 实测维度必须 == V2_INDEX_DIMS(768)，否则 fail closed（绝不产出/写入索引）；
- 拒绝退化的全零向量（R04 契约观察：现有 Embedder 对全零向量原样放行）；
- 返回 V2 DTO（model/dim/text_hash），不泄露供应商响应结构。
"""
from __future__ import annotations

import os

from app.ppt_v2.adapters.dto import EmbeddingVector
from app.ppt_v2.adapters.errors import AdapterFatalError, AdapterRetryableError
from app.ppt_v2.adapters.redact import sha16
from app.rag.embedder import DEFAULT_MODEL, EMBEDDING_DIMS, Embedder, EmbedderError

# 与 V2 教材索引 / pgvector Vector(768) 同源（R04 契约测试已钉死）
V2_INDEX_DIMS = EMBEDDING_DIMS


class EmbeddingAdapter:
    """V2 教材 embedding 适配：维度铁律 + 全零拒绝 + DTO 元数据。

    维度校验由本 adapter 权威执行（Embedder 以 dims=-1 构造，关闭其内部维度检查，
    避免上游/环境的 EMBEDDING_DIMS 干扰 V2 铁律）；Embedder 网络/上游错误按分类学
    映射为可重试/不可恢复。
    """

    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        model: str | None = None,
        expected_dims: int = V2_INDEX_DIMS,
    ) -> None:
        self._embedder = embedder or Embedder(dims=-1)
        self._model = model or os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL
        self._expected_dims = expected_dims

    @property
    def model(self) -> str:
        return self._model

    @property
    def expected_dims(self) -> int:
        return self._expected_dims

    async def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        """文本列表 → V2 EmbeddingVector 列表（顺序与输入一致；空列表直接返回）。

        dimension 不符 → AdapterFatalError（fail closed，不写索引）；
        全零向量 → AdapterFatalError（退化输入，拒绝写入）。
        """
        if not texts:
            return []
        try:
            raw = await self._embedder.embed(texts)
        except EmbedderError as exc:
            if exc.status_code is not None and exc.status_code >= 500:
                raise AdapterRetryableError(str(exc), status_code=exc.status_code) from exc
            raise AdapterFatalError(str(exc), status_code=exc.status_code) from exc
        out: list[EmbeddingVector] = []
        for text, vec in zip(texts, raw, strict=True):
            if len(vec) != self._expected_dims:
                raise AdapterFatalError(
                    f"embedding 维度不符 V2 铁律: 实测 {len(vec)} 期望 {self._expected_dims} —— fail closed，不写索引"
                )
            if not any(vec):
                raise AdapterFatalError("embedding 返回全零向量（退化）——拒绝写入 V2 索引")
            out.append(
                EmbeddingVector(
                    text_hash=sha16(text),
                    vector=vec,
                    model=self._model,
                    dim=len(vec),
                )
            )
        return out
