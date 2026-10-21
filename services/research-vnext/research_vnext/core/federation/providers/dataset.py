"""RV06 · DatasetProvider（contract 保留，本轮默认 disabled）。

本轮**不**接入任何真实数据集。这里存在的意义是让"五类规范"里第四类
可被 `get_spec("dataset")` 查询、可在 catalog/describe 中看到，
并在被真正调用时**明确失败**而不是悄悄返回空列表
（空列表会让调用方误以为"查过了，确实没有"）。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import FederatedQuery, ProviderHit
from ..scope import ResolvedScope
from ..types import EvidenceType
from .base import FederatedProvider, ProviderDisabled


class DatasetAdapter(FederatedProvider):
    name = "dataset"
    version = "0.1.0"
    evidence_type = EvidenceType.DATASET

    def search(self, query: FederatedQuery, scope: ResolvedScope) -> Sequence[ProviderHit]:
        raise ProviderDisabled("dataset provider 为 contract-only（本轮默认 disabled）")


__all__ = ["DatasetAdapter"]
