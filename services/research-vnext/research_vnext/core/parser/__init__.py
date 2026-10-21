"""RV05 · Research 文献/资产 PDF 结构化解析（非 Teacher 教材）。

- PDF.js 前端走受权 Asset API / Range，不直连 object storage。
- Docling 经 typed ParserProvider 在**隔离 worker/子进程**中执行。
- Anchor 绑定 asset version + content hash；文件更换后 stale，不静默指向新内容。
"""

from .anchors import AnchorType, classify_stale, make_anchor_id
from .models import AssetAnchorRow, AssetParseRunRow
from .provider import (
    ParseDocument,
    ParseElement,
    ParserIdentity,
    ParserProvider,
    ParserProviderError,
)

__all__ = [
    "AnchorType",
    "AssetAnchorRow",
    "AssetParseRunRow",
    "ParseDocument",
    "ParseElement",
    "ParserIdentity",
    "ParserProvider",
    "ParserProviderError",
    "classify_stale",
    "make_anchor_id",
]
