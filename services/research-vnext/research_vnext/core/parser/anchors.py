"""RV05 · Anchor 契约：asset-version-specific，文件更换后 stale。"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any


class AnchorType(str, Enum):
    ASSET = "asset"
    PAGE = "page"
    BBOX = "bbox"
    SECTION = "section"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"


KIND_TO_ANCHOR: dict[str, AnchorType] = {
    "title": AnchorType.SECTION,
    "section": AnchorType.SECTION,
    "paragraph": AnchorType.PARAGRAPH,
    "table": AnchorType.TABLE,
    "figure": AnchorType.FIGURE,
    "formula": AnchorType.FORMULA,
    "page": AnchorType.PAGE,
}


class StaleReason(str, Enum):
    NOT_STALE = ""
    HASH_CHANGED = "asset_hash_changed"
    VERSION_CHANGED = "asset_version_changed"
    ASSET_REVOKED = "asset_revoked"
    PARSE_SUPERSEDED = "parse_superseded"


def make_anchor_id(
    *,
    asset_id: str,
    asset_version: int,
    content_hash: str,
    parse_run_id: str,
    page: int,
    kind: str,
    text_hash: str,
    region_hash: str,
) -> str:
    """确定性 anchor id：同一版本+同一区域 → 同一 id（可回源、可去重）。"""
    raw = "|".join(
        [
            asset_id,
            str(asset_version),
            content_hash,
            parse_run_id,
            str(page),
            kind,
            text_hash,
            region_hash,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def classify_stale(
    *,
    anchor_asset_version: int,
    anchor_content_hash: str,
    current_asset_version: int | None,
    current_content_hash: str | None,
    asset_revoked: bool = False,
) -> tuple[bool, str]:
    """Anchor 是否 stale。

    **绝不静默指向新内容**：hash 或 version 变化必须显式标记 stale，
    前端展示「来源版本已变化」，禁止把旧 bbox 套到新 PDF 上。
    """
    if asset_revoked:
        return True, StaleReason.ASSET_REVOKED.value
    if current_content_hash is None or current_asset_version is None:
        return True, StaleReason.ASSET_REVOKED.value
    if current_content_hash != anchor_content_hash:
        return True, StaleReason.HASH_CHANGED.value
    if current_asset_version != anchor_asset_version:
        return True, StaleReason.VERSION_CHANGED.value
    return False, StaleReason.NOT_STALE.value


def validate_bbox(bbox: Any, page_width: float, page_height: float) -> tuple[float, float, float, float]:
    """bbox 越界 → 丢弃/告警，不产出假精确 anchor。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"bbox 必须是 4 元组，得到 {bbox!r}")
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if x1 < x0 or y1 < y0:
        raise ValueError(f"bbox 反向: {bbox}")
    if x0 < -0.5 or y0 < -0.5 or x1 > page_width + 0.5 or y1 > page_height + 0.5:
        raise ValueError(f"bbox 越界 page=({page_width},{page_height}) bbox={bbox}")
    return (x0, y0, x1, y1)
