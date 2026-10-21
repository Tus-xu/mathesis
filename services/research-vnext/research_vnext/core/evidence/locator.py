"""RV07 · locator 回源。

点击 Evidence 必须能：
- 经**授权**回到 RV05 的 `asset_anchor`（page + bbox / section / table / figure / formula）；
- 或回到一个**可验证**的外部定位（DOI / URL+内容指纹 / 教材 source→section→chunk）。

原始资产更版 / 换 hash / 撤权之后，证据一律标 `STALE` 并 `verifiable=False`
—— 绝不把旧 bbox 套到新文件上（沿用 RV05 `classify_stale` 的唯一判定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..parser.anchors import classify_stale
from .contracts import LocatorBinding
from .errors import EvidenceLocatorError
from .types import LocatorKind, StaleState

__all__ = [
    "ResolvedLocator",
    "resolve_from_anchor",
    "resolve_external",
    "resolve_locator",
    "TEXTBOOK_LOCATOR_KEYS",
]

#: 教材 direct-chunk locator 的**全部**键（多一个都不投影）。
TEXTBOOK_LOCATOR_KEYS: tuple[str, ...] = ("source_id", "section_id", "chunk_id")

#: 外部定位里允许投影出去的键（白名单，防内部字段外泄）。
EXTERNAL_SAFE_KEYS: tuple[str, ...] = (
    "doi",
    "url",
    "fetched_at",
    "content_hash",
    *TEXTBOOK_LOCATOR_KEYS,
)


@dataclass(frozen=True)
class ResolvedLocator:
    """回源结果。`verifiable` 与 `stale` **分离**：

    stale 但仍能定位（旧版本 anchor 还在）与"根本定位不到"是两种不同的事，
    前端展示与降级策略也不同。
    """

    kind: LocatorKind
    anchor_type: str = ""
    page: int | None = None
    bbox: list[float] | None = None
    section_path: list[str] = field(default_factory=list)
    external: dict[str, Any] | None = None
    verifiable: bool = False
    stale: bool = False
    stale_state: StaleState = StaleState.FRESH
    stale_reason: str = ""
    text_hash: str = ""
    region_hash: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "anchor_type": self.anchor_type,
            "page": self.page,
            "bbox": list(self.bbox) if self.bbox else None,
            "section_path": list(self.section_path),
            "external": dict(self.external or {}),
            "verifiable": self.verifiable,
            "stale": self.stale,
            "stale_state": self.stale_state.value,
            "stale_reason": self.stale_reason,
            "text_hash": self.text_hash,
            "region_hash": self.region_hash,
            "confidence": self.confidence,
        }


def resolve_from_anchor(
    locator: LocatorBinding,
    anchor: Any | None,  # noqa: ANN401 — RV05 AssetAnchorRow 或测试替身
    *,
    current_asset_version: int | None,
    current_content_hash: str | None,
) -> ResolvedLocator:
    """把 ANCHOR locator 解析成可展示的回源结果。

    `anchor is None` → `EvidenceLocatorError`：**定位不到不是空结果**，
    调用方必须显式处理，不能渲染成"没有证据"。
    """
    if locator.kind is not LocatorKind.ANCHOR:
        raise EvidenceLocatorError(f"需要 ANCHOR locator，收到 {locator.kind.value}")
    if anchor is None:
        raise EvidenceLocatorError(
            f"anchor 不存在: {locator.payload.get('anchor_id')!r}（来源可能已被删除或未解析）"
        )

    bound_asset = str(locator.payload.get("asset_id"))
    if str(getattr(anchor, "asset_id", "")) != bound_asset:
        raise EvidenceLocatorError(
            "anchor 归属资产与 locator 不一致（疑似跨租户/跨资产引用）",
            bound_asset=bound_asset,
        )
    bound_anchor = str(locator.payload.get("anchor_id"))
    if str(getattr(anchor, "anchor_id", "")) != bound_anchor:
        raise EvidenceLocatorError(f"anchor_id 不一致: 期望 {bound_anchor!r}")

    stale, reason = classify_stale(
        anchor_asset_version=int(getattr(anchor, "asset_version", 0)),
        anchor_content_hash=str(getattr(anchor, "content_hash", "")),
        current_asset_version=current_asset_version,
        current_content_hash=current_content_hash,
    )
    bbox_raw = list(getattr(anchor, "bbox", None) or [])
    bbox = [float(v) for v in bbox_raw] if len(bbox_raw) == 4 else None
    return ResolvedLocator(
        kind=LocatorKind.ANCHOR,
        anchor_type=str(getattr(anchor, "type", "")),
        page=int(getattr(anchor, "page", 0)) or None,
        bbox=bbox,
        section_path=[str(s) for s in (getattr(anchor, "section_path", None) or [])],
        external=None,
        # stale 即不可回源：不静默指向新内容
        verifiable=not stale,
        stale=stale,
        stale_state=StaleState.STALE if stale else StaleState.FRESH,
        stale_reason=reason,
        text_hash=str(getattr(anchor, "text_hash", "")),
        region_hash=str(getattr(anchor, "region_hash", "")),
        confidence=float(getattr(anchor, "confidence", 0.0) or 0.0),
    )


def resolve_external(locator: LocatorBinding) -> ResolvedLocator:
    """解析 EXTERNAL locator。

    可验证性判据（**只有**这三种算可验证）：
    - 教材 direct-chunk 三元组齐全；
    - 有 DOI（稳定标识符）；
    - 有 URL **且**同时有内容指纹与抓取时间（否则 URL 会漂移）。
    """
    if locator.kind is not LocatorKind.EXTERNAL:
        raise EvidenceLocatorError(f"需要 EXTERNAL locator，收到 {locator.kind.value}")
    payload = dict(locator.payload)
    external = {k: payload[k] for k in EXTERNAL_SAFE_KEYS if k in payload}

    if locator.dimension == "TEXTBOOK":
        external = {k: payload[k] for k in TEXTBOOK_LOCATOR_KEYS}
        verifiable = all(external.get(k) for k in TEXTBOOK_LOCATOR_KEYS)
        return ResolvedLocator(
            kind=LocatorKind.EXTERNAL,
            external=external,
            verifiable=verifiable,
            stale=not verifiable,
            stale_state=StaleState.FRESH if verifiable else StaleState.STALE,
            stale_reason="" if verifiable else "textbook_locator_incomplete",
        )

    doi = str(payload.get("doi") or "")
    url = str(payload.get("url") or "")
    content_hash = str(payload.get("content_hash") or "")
    fetched_at = str(payload.get("fetched_at") or "")
    verifiable = bool(doi) or (bool(url) and bool(content_hash) and bool(fetched_at))
    return ResolvedLocator(
        kind=LocatorKind.EXTERNAL,
        external=external,
        verifiable=verifiable,
        stale=not verifiable,
        stale_state=StaleState.FRESH if verifiable else StaleState.STALE,
        stale_reason="" if verifiable else "external_locator_unverifiable",
    )


def resolve_locator(
    locator: LocatorBinding,
    *,
    anchor: Any | None = None,  # noqa: ANN401
    current_asset_version: int | None = None,
    current_content_hash: str | None = None,
) -> ResolvedLocator:
    """按 locator kind 分派。"""
    if locator.kind is LocatorKind.ANCHOR:
        return resolve_from_anchor(
            locator,
            anchor,
            current_asset_version=current_asset_version,
            current_content_hash=current_content_hash,
        )
    return resolve_external(locator)
