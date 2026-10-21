"""RV05 · typed ParserProvider 协议与结构化解析结果。

上传内容始终 untrusted：解析出的「指令」只作证据内容，不进 system/tool policy。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

AnchorKind = Literal["paragraph", "section", "title", "table", "figure", "formula", "page"]

ANCHOR_KINDS: frozenset[str] = frozenset(
    {"paragraph", "section", "title", "table", "figure", "formula", "page"}
)


class ParserProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail


@dataclass(frozen=True)
class ParserIdentity:
    """锁定解析器身份：版本 / digest / license / config。"""

    name: str
    version: str
    digest: str
    license: str
    config: dict[str, Any] = field(default_factory=dict)

    def config_hash(self) -> str:
        raw = json.dumps(self.config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["config_hash"] = self.config_hash()
        return d


@dataclass
class ParseElement:
    kind: str  # AnchorKind
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) PDF user space
    text: str = ""
    section_path: list[str] = field(default_factory=list)
    level: int = 0
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def region_hash(self, page_width: float, page_height: float) -> str:
        payload = f"{self.page}|{self.bbox[0]:.2f},{self.bbox[1]:.2f},{self.bbox[2]:.2f},{self.bbox[3]:.2f}|{page_width:.2f}|{page_height:.2f}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def bbox_in_bounds(self, page_width: float, page_height: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if x0 < 0 or y0 < 0 or x1 > page_width + 0.5 or y1 > page_height + 0.5:
            return False
        if x1 < x0 or y1 < y0:
            return False
        return True


@dataclass
class PageMeta:
    page: int
    width: float
    height: float
    rotation: int = 0
    text_items: int = 0
    is_scanned: bool = False


@dataclass
class ParseDocument:
    pages: list[PageMeta] = field(default_factory=list)
    elements: list[ParseElement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    succeeded_pages: list[int] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)
    missing_kinds: list[str] = field(default_factory=list)

    @property
    def is_partial(self) -> bool:
        return bool(self.failed_pages)

    def section_tree(self) -> list[dict[str, Any]]:
        roots: list[dict[str, Any]] = []
        stack: list[dict[str, Any]] = []
        for el in self.elements:
            if el.kind not in ("title", "section"):
                continue
            node = {
                "title": el.text.strip(),
                "page": el.page,
                "level": el.level,
                "bbox": list(el.bbox),
                "children": [],
            }
            while stack and stack[-1]["level"] >= el.level:
                stack.pop()
            if stack:
                stack[-1]["children"].append(node)
            else:
                roots.append(node)
            stack.append(node)
        return roots


@runtime_checkable
class ParserProvider(Protocol):
    @property
    def identity(self) -> ParserIdentity: ...

    def parse(self, pdf_path: Path, *, config: dict[str, Any] | None = None) -> ParseDocument: ...


def content_fingerprint(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_digest(path: Path) -> str:
    return content_fingerprint(path)
