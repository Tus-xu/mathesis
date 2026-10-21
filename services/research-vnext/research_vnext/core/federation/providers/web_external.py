"""RV06 · WebExternalProvider（contract 保留，本轮默认 disabled）。

不做真实抓取：外部网页抓取需要出网授权、robots 判定与内容留存合规口径，
这些都不在本轮范围内。被调用时明确抛 `ProviderDisabled`，
避免"静默空结果"被误读成"网上确实没有"。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import FederatedQuery, ProviderHit
from ..scope import ResolvedScope
from ..types import EvidenceType
from .base import FederatedProvider, ProviderDisabled


class WebExternalAdapter(FederatedProvider):
    name = "web_external"
    version = "0.1.0"
    evidence_type = EvidenceType.WEB_EXTERNAL

    def search(self, query: FederatedQuery, scope: ResolvedScope) -> Sequence[ProviderHit]:
        raise ProviderDisabled("web_external provider 为 contract-only（本轮默认 disabled）")


__all__ = ["WebExternalAdapter"]
