"""RV07 · 引用往返对账。

往返**不是**"能解析回来就算过"：必须能回答"有没有字段在转换里消失、
有没有字段被凭空补出来"。因此每次往返都产出一份 `RoundTripReport`：

- `preserved` —— 原值 == 回读值；
- `lost` —— 格式**声称支持**该字段，回读却变了 → **静默丢失**（禁止）；
- `unsupported_by_format` —— 格式**结构上无法承载**该字段（显式声明，不是丢失）；
- `fabricated` —— 原值为空、回读却有值 → **编造**（禁止）。

`assert_no_silent_loss` 只放行 `lost` 与 `fabricated` 均为空的报告。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .csl import COMPARABLE_FIELDS, CslItem
from .errors import CitationContractError
from .formats import from_bibtex, from_doi, from_ris, to_bibtex, to_doi, to_ris

__all__ = ["RoundTripReport", "roundtrip", "assert_no_silent_loss", "SUPPORTED_FORMATS"]


@dataclass(frozen=True)
class FormatSpec:
    render: Callable[[CslItem], str]
    parse: Callable[[str], CslItem]
    #: 该格式**声称能承载**的字段（不在其中的字段走 unsupported_by_format）
    supported: frozenset[str]
    #: 参与对账的字段
    comparable: frozenset[str]


#: DOI 只承载标识符，**不承载元数据** —— 这是它的 supported 集合只有 doi 的原因。
FORMAT_SPECS: dict[str, FormatSpec] = {
    "bibtex": FormatSpec(
        render=to_bibtex,
        parse=from_bibtex,
        supported=frozenset(COMPARABLE_FIELDS),
        comparable=frozenset(COMPARABLE_FIELDS),
    ),
    "ris": FormatSpec(
        render=to_ris,
        parse=from_ris,
        supported=frozenset(COMPARABLE_FIELDS),
        comparable=frozenset(COMPARABLE_FIELDS),
    ),
    "doi": FormatSpec(
        render=to_doi,
        parse=from_doi,
        supported=frozenset({"doi"}),
        comparable=frozenset(COMPARABLE_FIELDS),
    ),
}

SUPPORTED_FORMATS: tuple[str, ...] = tuple(sorted(FORMAT_SPECS))

_DEFAULTS: dict[str, object] = {
    "id": "",
    "csl_type": "",
    "title": "",
    "authors": (),
    "issued": None,
    "container_title": "",
    "doi": "",
    "url": "",
    "publisher": "",
    "page": "",
    "volume": "",
    "issue": "",
    "issn": "",
    "language": "",
    "abstract": "",
}


@dataclass(frozen=True)
class RoundTripReport:
    fmt: str
    preserved: tuple[str, ...]
    lost: tuple[str, ...]
    unsupported_by_format: tuple[str, ...]
    fabricated: tuple[str, ...]

    @property
    def lossless(self) -> bool:
        return not self.lost and not self.fabricated

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.fmt,
            "lossless": self.lossless,
            "preserved": list(self.preserved),
            "lost": list(self.lost),
            "unsupported_by_format": list(self.unsupported_by_format),
            "fabricated": list(self.fabricated),
        }


def _is_default(value: object) -> bool:
    return value in (None, "", ()) or value == ()


def roundtrip(item: CslItem, fmt: str) -> tuple[CslItem, RoundTripReport]:
    """导出 → 回读 → 对账。未知格式直接拒绝（不静默当 bibtex）。"""
    spec = FORMAT_SPECS.get(fmt)
    if spec is None:
        raise CitationContractError(
            f"未知引用格式: {fmt!r}（支持 {sorted(FORMAT_SPECS)}）"
        )
    rendered = spec.render(item)
    parsed = spec.parse(rendered)

    preserved: list[str] = []
    lost: list[str] = []
    unsupported: list[str] = []
    fabricated: list[str] = []

    for name in sorted(spec.comparable):
        before = getattr(item, name)
        after = getattr(parsed, name)
        if before == after:
            preserved.append(name)
            continue
        if name in spec.supported:
            lost.append(name)
        else:
            unsupported.append(name)

    for name in sorted(spec.comparable):
        before = getattr(item, name)
        after = getattr(parsed, name)
        if _is_default(before) and not _is_default(after):
            fabricated.append(name)

    # extra 键：格式若不声明支持，就是"结构上无法承载"
    for key, _value in item.extra:
        marker = f"extra.{key}"
        if marker not in spec.supported:
            unsupported.append(marker)
        elif marker not in preserved:
            lost.append(marker)

    return parsed, RoundTripReport(
        fmt=fmt,
        preserved=tuple(preserved),
        lost=tuple(sorted(set(lost))),
        unsupported_by_format=tuple(sorted(set(unsupported))),
        fabricated=tuple(sorted(set(fabricated))),
    )


def assert_no_silent_loss(report: RoundTripReport) -> None:
    """静默丢失 / 编造 → 直接失败（这是"引用往返无静默丢失"的机器判据）。"""
    problems: list[str] = []
    if report.lost:
        problems.append(f"静默丢失字段 {list(report.lost)}")
    if report.fabricated:
        problems.append(f"凭空补出字段 {list(report.fabricated)}")
    if problems:
        raise CitationContractError(
            f"{report.fmt} 往返不通过：{'；'.join(problems)}"
            f"（格式结构上无法承载的字段应登记在 unsupported_by_format）"
        )
