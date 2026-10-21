"""RV07 · CSL-JSON 作为**唯一权威**的引用元数据。

设计取舍（必须写下来，否则下一个人会把 BibTeX 当权威）：

- canonical 形态是 **CSL-JSON**：它是唯一同时能表达"结构化姓名 + 分层日期 +
  容器题名 + 标识符"的开放格式，且与 Zotero / Pandoc / citeproc 生态互通；
- BibTeX / RIS / DOI 都是**派生视图**，导出必须可回读，回读必须可对账
  （见 `roundtrip.py`）；
- **绝不为缺失字段编造值**：没有 DOI 就不写 DOI 字段，没有年份就 `issued=None`。
  这条在 `to_*` 里是"字段为空即不输出"，在 `from_*` 里是"字段缺失即留空"。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .errors import CitationContractError

#: 允许的 CSL 类型（CSL 1.0.2 的常用子集；未列入的一律拒绝，避免"看起来像 CSL"）。
CSL_TYPES: frozenset[str] = frozenset(
    {
        "article-journal",
        "article-magazine",
        "article-newspaper",
        "book",
        "chapter",
        "dataset",
        "document",
        "manuscript",
        "monograph",
        "paper-conference",
        "posted-content",
        "report",
        "review",
        "software",
        "thesis",
        "webpage",
    }
)

#: 参与往返对账的字段（顺序无关，取集合语义）。
COMPARABLE_FIELDS: tuple[str, ...] = (
    "id",
    "csl_type",
    "title",
    "authors",
    "issued",
    "container_title",
    "doi",
    "url",
    "publisher",
    "page",
    "volume",
    "issue",
    "issn",
    "language",
    "abstract",
)

#: CSL-JSON 的键名（导出用）。
_CSL_KEYS = {
    "title": "title",
    "container_title": "container-title",
    "doi": "DOI",
    "url": "URL",
    "issn": "ISSN",
    "page": "page",
    "volume": "volume",
    "issue": "issue",
    "publisher": "publisher",
    "language": "language",
    "abstract": "abstract",
}

#: 已知的 CSL-JSON 顶层键（其余键进 `extra`，保证往返无损）。
KNOWN_CSL_KEYS: frozenset[str] = frozenset(
    {"type", "id", "title", "author", "issued", *(_CSL_KEYS.values())}
)

_DOI_PREFIXES: tuple[str, ...] = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def normalize_doi(raw: str) -> str:
    """DOI 规范化：去解析前缀、去空白、转小写。

    确定性是硬要求 —— 同一个 DOI 的不同写法必须收敛到同一字符串，
    否则去重与回源都会出现"看起来不同的两条"。
    """
    text = (raw or "").strip()
    lowered = text.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip().lower()


@dataclass(frozen=True)
class CslDate:
    year: int
    month: int | None = None
    day: int | None = None

    def __post_init__(self) -> None:
        if not (1 <= int(self.year) <= 9999):
            raise CitationContractError(f"年份非法: {self.year!r}")
        if self.month is not None and not (1 <= int(self.month) <= 12):
            raise CitationContractError(f"月份非法: {self.month!r}")
        if self.day is not None and not (1 <= int(self.day) <= 31):
            raise CitationContractError(f"日期非法: {self.day!r}")
        object.__setattr__(self, "year", int(self.year))
        if self.month is not None:
            object.__setattr__(self, "month", int(self.month))
        if self.day is not None:
            object.__setattr__(self, "day", int(self.day))

    def date_parts(self) -> list[int]:
        parts = [self.year]
        if self.month is not None:
            parts.append(self.month)
            if self.day is not None:
                parts.append(self.day)
        return parts

    @classmethod
    def from_date_parts(cls, parts: Any) -> CslDate | None:  # noqa: ANN401
        if not parts:
            return None
        flat = parts[0] if isinstance(parts, (list, tuple)) and parts and isinstance(parts[0], (list, tuple)) else parts
        if not isinstance(flat, (list, tuple)) or not flat:
            return None
        year = int(flat[0])
        month = int(flat[1]) if len(flat) > 1 else None
        day = int(flat[2]) if len(flat) > 2 else None
        return cls(year, month, day)


@dataclass(frozen=True)
class CslName:
    """结构化姓名。`literal` 用于机构作者（无法拆分 family/given 时）。"""

    family: str = ""
    given: str = ""
    literal: str = ""

    def __post_init__(self) -> None:
        if not (self.family or self.given or self.literal):
            raise CitationContractError("CslName 至少要有 family / given / literal 之一")

    def as_csl(self) -> dict[str, str]:
        if self.family or self.given:
            out = {}
            if self.family:
                out["family"] = self.family
            if self.given:
                out["given"] = self.given
            return out
        return {"literal": self.literal}

    @classmethod
    def from_csl(cls, payload: Any) -> CslName:  # noqa: ANN401
        if isinstance(payload, str):
            return cls(literal=payload)
        if not isinstance(payload, dict):
            raise CitationContractError(f"无法解析姓名: {payload!r}")
        return cls(
            family=str(payload.get("family") or ""),
            given=str(payload.get("given") or ""),
            literal=str(payload.get("literal") or ""),
        )


@dataclass(frozen=True)
class CslItem:
    """一条引用的 canonical 元数据。"""

    csl_type: str
    id: str = ""
    title: str = ""
    authors: tuple[CslName, ...] = ()
    issued: CslDate | None = None
    container_title: str = ""
    doi: str = ""
    url: str = ""
    publisher: str = ""
    page: str = ""
    volume: str = ""
    issue: str = ""
    issn: str = ""
    language: str = ""
    abstract: str = ""
    extra: tuple[tuple[str, str], ...] = field(default=())

    def __post_init__(self) -> None:
        if self.csl_type not in CSL_TYPES:
            raise CitationContractError(
                f"未知 CSL 类型: {self.csl_type!r}（允许 {sorted(CSL_TYPES)}）"
            )
        object.__setattr__(self, "authors", tuple(self.authors))
        # 本地引用键：缺失时从 DOI / 标题确定性派生（这是**键**，不是元数据）。
        if not self.id:
            base = normalize_doi(self.doi) or (self.title.strip()[:64] if self.title else "")
            if not base:
                raise CitationContractError("CslItem 无法确定 id（既无 id，也无 DOI/标题）")
            object.__setattr__(self, "id", f"local-{hashlib.sha256(base.encode('utf-8')).hexdigest()[:16]}")
        # extra 排序，保证往返确定性
        object.__setattr__(
            self, "extra", tuple(sorted(((str(k), str(v)) for k, v in self.extra), key=lambda kv: kv[0]))
        )

    def as_csl_json(self) -> dict[str, Any]:
        return to_csl_json(self)


# --------------------------------------------------------------------------
# CSL-JSON 双向
# --------------------------------------------------------------------------


def to_csl_json(item: CslItem) -> dict[str, Any]:
    """导出 CSL-JSON。**空字段一律不输出**（不编造）。"""
    payload: dict[str, Any] = {"type": item.csl_type, "id": item.id}
    if item.title:
        payload["title"] = item.title
    if item.authors:
        payload["author"] = [a.as_csl() for a in item.authors]
    if item.issued is not None:
        payload["issued"] = {"date-parts": [item.issued.date_parts()]}
    for attr, key in _CSL_KEYS.items():
        value = getattr(item, attr)
        if value:
            payload[key] = normalize_doi(value) if attr == "doi" else value
    for key, value in item.extra:
        if key not in payload:
            payload[key] = value
    return payload


def from_csl_json(payload: dict[str, Any]) -> CslItem:
    """解析 CSL-JSON。**缺失字段留空**，不补默认年份/默认期刊。"""
    if not isinstance(payload, dict):
        raise CitationContractError("CSL-JSON 必须是对象")
    csl_type = str(payload.get("type") or "")
    if not csl_type:
        raise CitationContractError("CSL-JSON 缺少 type")
    issued = None
    if isinstance(payload.get("issued"), dict):
        issued = CslDate.from_date_parts(payload["issued"].get("date-parts"))
    authors = tuple(CslName.from_csl(a) for a in (payload.get("author") or []))
    extra = tuple(
        (k, str(v)) for k, v in payload.items() if k not in KNOWN_CSL_KEYS and v not in (None, "", [], {})
    )
    return CslItem(
        csl_type=csl_type,
        id=str(payload.get("id") or ""),
        title=str(payload.get("title") or ""),
        authors=authors,
        issued=issued,
        container_title=str(payload.get("container-title") or ""),
        doi=normalize_doi(str(payload.get("DOI") or "")),
        url=str(payload.get("URL") or ""),
        publisher=str(payload.get("publisher") or ""),
        page=str(payload.get("page") or ""),
        volume=str(payload.get("volume") or ""),
        issue=str(payload.get("issue") or ""),
        issn=str(payload.get("ISSN") or ""),
        language=str(payload.get("language") or ""),
        abstract=str(payload.get("abstract") or ""),
        extra=extra,
    )


__all__ = [
    "CSL_TYPES",
    "COMPARABLE_FIELDS",
    "KNOWN_CSL_KEYS",
    "normalize_doi",
    "CslDate",
    "CslName",
    "CslItem",
    "to_csl_json",
    "from_csl_json",
]
