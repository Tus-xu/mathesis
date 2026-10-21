"""R08 FakeEmbedding：固定 seed 的确定性 768 维 embedding（真实 provider 属 R11）。

设计（对应 r08-handoff）：
- 同一文本 → 同一向量（gold query 可重复，种子固定）；
- 异文本 → 大概率异向量（sha256 扰动 + 固定 PRNG）；
- 维度固定 768（R04 铁律：teacher 平台 768 / 通用 API 1024，禁止交叉写同一索引）；
- 无状态、不触网、可重复；真实 embedding 供应商契约（xop3qwen8bembedding）验证归 R11。
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Protocol

# 维度铁律（R04）：teacher 平台 embedding=768
EMBEDDING_DIM = 768
# 索引写入时记录的模型标识（R08 fake；R11 真实 provider 换新 index_version）
EMBEDDING_MODEL = "fake-768"


class Embedder(Protocol):
    """嵌入器契约：embed() 返回固定维 float 向量；写索引前断言 dim == EMBEDDING_DIM。"""

    model: str
    dim: int

    def embed(self, text: str) -> list[float]: ...


class FakeEmbedding:
    """确定性 hash→向量嵌入器：sha256(seed:text) 作 PRNG 种子 → L2 归一化 768 维。"""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed
        self.model = EMBEDDING_MODEL
        self.dim = EMBEDDING_DIM

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(f"{self.seed}:{text}".encode()).digest()
        rng = random.Random(digest)
        vec = [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（[-1,1]）；维度不一致直接抛错（fail loud，不静默对齐）。"""
    if len(a) != len(b):
        raise ValueError(f"向量维度不一致: {len(a)} vs {len(b)}")
    if not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
