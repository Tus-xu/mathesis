"""RV07 · 派生格式：BibTeX / RIS / DOI。

三条纪律：

1. **空字段不输出**（不编造）—— `doi` 为空时 BibTeX 里没有 `doi =`，RIS 里没有 `DO  -`；
2. **输出必须可回读**—— 本模块同时提供 `from_*`，且与 `to_*` 成对维护；
3. **类型映射不可逆时显式留痕**—— BibTeX 用 `csl-type`、RIS 用 `C7` 字段补记
   原始 CSL 类型，避免"chapter 存成 incollection 再读回 book"这种静默降级。

BibTeX 值一律用 `"..."` 定界并转义反斜杠与双引号：这样含花括号的数学标题
也不会破坏括号配平，往返也不丢字符。
"""

from __future__ import annotations

from .csl import CslDate, CslItem, CslName, normalize_doi
from .errors import CitationContractError

__all__ = [
    "BIBTEX_TYPES",
    "BIBTEX_TO_CSL",
    "RIS_TYPES",
    "RIS_TO_CSL",
    "to_bibtex",
    "from_bibtex",
    "to_ris",
    "from_ris",
    "to_doi",
    "from_doi",
]

# --------------------------------------------------------------------------
# 类型映射
# --------------------------------------------------------------------------

BIBTEX_TYPES: dict[str, str] = {
    "article-journal": "article",
    "article-magazine": "article",
    "article-newspaper": "article",
    "book": "book",
    "monograph": "book",
    "chapter": "incollection",
    "paper-conference": "inproceedings",
    "dataset": "misc",
    "report": "techreport",
    "thesis": "phdthesis",
    "webpage": "misc",
    "software": "misc",
    "posted-content": "misc",
    "document": "misc",
    "manuscript": "unpublished",
    "review": "article",
}

RIS_TYPES: dict[str, str] = {
    "article-journal": "JOUR",
    "article-magazine": "MGZN",
    "article-newspaper": "NEWS",
    "book": "BOOK",
    "monograph": "BOOK",
    "chapter": "CHAP",
    "paper-conference": "CPAPER",
    "dataset": "DATA",
    "report": "RPRT",
    "thesis": "THES",
    "webpage": "ELEC",
    "software": "COMP",
    "posted-content": "UNPB",
    "manuscript": "UNPB",
    "document": "GEN",
    "review": "JOUR",
}


def _invert(mapping: dict[str, str]) -> dict[str, str]:
    """反向映射：同一目标类型取**字典序最小**的 CSL 类型（确定性，不靠插入顺序）。"""
    out: dict[str, str] = {}
    for csl_type in sorted(mapping):
        out.setdefault(mapping[csl_type], csl_type)
    return out


BIBTEX_TO_CSL: dict[str, str] = _invert(BIBTEX_TYPES)
RIS_TO_CSL: dict[str, str] = _invert(RIS_TYPES)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _bib_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _bib_unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _bib_name(name: CslName) -> str:
    if name.literal:
        return name.literal
    if name.given:
        return f"{name.family}, {name.given}"
    return name.family


def _parse_bib_name(raw: str) -> CslName:
    text = raw.strip()
    if ", " in text:
        family, given = text.split(", ", 1)
        return CslName(family=family.strip(), given=given.strip())
    return CslName(family=text)


def _split_names(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(" and ") if p.strip()]


def _page_parts(page: str) -> tuple[str, str]:
    if "-" in page:
        start, end = page.split("-", 1)
        return start.strip(), end.strip()
    return page.strip(), ""


# --------------------------------------------------------------------------
# BibTeX
# --------------------------------------------------------------------------


def to_bibtex(item: CslItem) -> str:
    entry = BIBTEX_TYPES[item.csl_type]
    fields: list[tuple[str, str]] = []
    if item.title:
        fields.append(("title", item.title))
    if item.authors:
        fields.append(("author", " and ".join(_bib_name(a) for a in item.authors)))
    if item.issued is not None:
        fields.append(("year", str(item.issued.year)))
        if item.issued.month is not None:
            fields.append(("month", str(item.issued.month)))
            if item.issued.day is not None:
                fields.append(("day", str(item.issued.day)))
    if item.container_title:
        fields.append(("journal", item.container_title))
    if item.publisher:
        fields.append(("publisher", item.publisher))
    if item.volume:
        fields.append(("volume", item.volume))
    if item.issue:
        fields.append(("number", item.issue))
    if item.page:
        fields.append(("pages", item.page))
    if item.issn:
        fields.append(("issn", item.issn))
    if item.doi:
        fields.append(("doi", normalize_doi(item.doi)))
    if item.url:
        fields.append(("url", item.url))
    if item.language:
        fields.append(("language", item.language))
    if item.abstract:
        fields.append(("abstract", item.abstract))
    # 类型映射不可逆时补记原始 CSL 类型（避免静默降级）
    if BIBTEX_TO_CSL.get(entry) != item.csl_type:
        fields.append(("csl-type", item.csl_type))
    for key, value in item.extra:
        fields.append((key, value))

    body = ",\n".join(f'  {k} = "{_bib_escape(v)}"' for k, v in fields)
    return f"@{entry}{{{item.id},\n{body}\n}}\n"


def from_bibtex(text: str) -> CslItem:
    raw = (text or "").strip()
    if not raw.startswith("@"):
        raise CitationContractError("不是合法的 BibTeX 条目（缺少 @）")
    head_end = raw.index("{")
    entry_type = raw[1:head_end].strip().lower()
    if entry_type not in BIBTEX_TO_CSL:
        raise CitationContractError(f"未知 BibTeX 条目类型: {entry_type!r}")

    # 取最外层花括号内容
    depth = 0
    start = head_end
    end = -1
    in_quote = False
    escaped = False
    for i in range(head_end, len(raw)):
        ch = raw[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == "{":
            depth += 1
            if depth == 1:
                start = i
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise CitationContractError("BibTeX 条目括号不配平")
    inner = raw[start + 1 : end]

    # key 到第一个逗号为止
    comma = inner.index(",")
    fields_blob = inner[comma + 1 :]
    fields: dict[str, str] = {}
    order: list[str] = []
    for name, value in _iter_bib_fields(fields_blob):
        if name not in fields:
            order.append(name)
        fields[name] = value

    def take(key: str) -> str:
        return fields.pop(key, "")

    authors = tuple(_parse_bib_name(n) for n in _split_names(take("author")))
    year = take("year")
    month = take("month")
    day = take("day")
    issued = None
    if year.isdigit():
        issued = CslDate(
            int(year),
            int(month) if month.isdigit() else None,
            int(day) if day.isdigit() else None,
        )
    csl_type = take("csl-type") or BIBTEX_TO_CSL[entry_type]
    known = {
        "title", "author", "year", "month", "day", "journal", "publisher",
        "volume", "number", "pages", "issn", "doi", "url", "language", "abstract", "csl-type",
    }
    extra = tuple((k, v) for k, v in fields.items() if k not in known)
    return CslItem(
        csl_type=csl_type,
        id=inner[:comma].strip(),
        title=take("title"),
        authors=authors,
        issued=issued,
        container_title=take("journal"),
        doi=normalize_doi(take("doi")),
        url=take("url"),
        publisher=take("publisher"),
        page=take("pages"),
        volume=take("volume"),
        issue=take("number"),
        issn=take("issn"),
        language=take("language"),
        abstract=take("abstract"),
        extra=extra,
    )


def _iter_bib_fields(blob: str):  # noqa: ANN202
    """逐字段扫描 `name = "value"`（值一律为双引号定界）。"""
    i = 0
    n = len(blob)
    while i < n:
        while i < n and (blob[i].isspace() or blob[i] == ","):
            i += 1
        if i >= n:
            return
        eq = blob.find("=", i)
        if eq < 0:
            return
        name = blob[i:eq].strip().lower()
        j = eq + 1
        while j < n and blob[j].isspace():
            j += 1
        if j >= n or blob[j] != '"':
            raise CitationContractError(f"BibTeX 字段 {name!r} 的值必须用双引号定界")
        j += 1
        buf: list[str] = []
        escaped = False
        while j < n:
            ch = blob[j]
            if escaped:
                buf.append(ch)
                escaped = False
            elif ch == "\\":
                buf.append(ch)
                escaped = True
            elif ch == '"':
                break
            else:
                buf.append(ch)
            j += 1
        if j >= n:
            raise CitationContractError(f"BibTeX 字段 {name!r} 的引号不闭合")
        yield name, _bib_unescape("".join(buf))
        i = j + 1


# --------------------------------------------------------------------------
# RIS
# --------------------------------------------------------------------------


def to_ris(item: CslItem) -> str:
    lines: list[str] = [f"TY  - {RIS_TYPES[item.csl_type]}", f"ID  - {item.id}"]
    if item.title:
        lines.append(f"TI  - {item.title}")
    for a in item.authors:
        if a.literal:
            lines.append(f"AU  - {a.literal}")
        elif a.given:
            lines.append(f"AU  - {a.family}, {a.given}")
        else:
            lines.append(f"AU  - {a.family}")
    if item.issued is not None:
        lines.append(f"PY  - {item.issued.year}")
        parts = item.issued.date_parts()
        if len(parts) > 1:
            mm = f"{parts[1]:02d}"
            dd = f"{parts[2]:02d}" if len(parts) > 2 else ""
            lines.append(f"DA  - {item.issued.year}/{mm}/{dd}")
    if item.container_title:
        lines.append(f"JO  - {item.container_title}")
    if item.volume:
        lines.append(f"VL  - {item.volume}")
    if item.issue:
        lines.append(f"IS  - {item.issue}")
    if item.page:
        sp, ep = _page_parts(item.page)
        if sp:
            lines.append(f"SP  - {sp}")
        if ep:
            lines.append(f"EP  - {ep}")
    if item.publisher:
        lines.append(f"PB  - {item.publisher}")
    if item.issn:
        lines.append(f"SN  - {item.issn}")
    if item.doi:
        lines.append(f"DO  - {normalize_doi(item.doi)}")
    if item.url:
        lines.append(f"UR  - {item.url}")
    if item.language:
        lines.append(f"LA  - {item.language}")
    if item.abstract:
        lines.append(f"AB  - {item.abstract}")
    if RIS_TO_CSL.get(RIS_TYPES[item.csl_type]) != item.csl_type:
        lines.append(f"C7  - CSL:{item.csl_type}")
    for key, value in item.extra:
        lines.append(f"C8  - {key}={value}")
    lines.append("ER  -")
    return "\n".join(lines) + "\n"


def from_ris(text: str) -> CslItem:
    fields: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        if len(line) < 6 or line[2:6] != "  - ":
            continue
        tag = line[:2].upper()
        value = line[6:].strip()
        fields.setdefault(tag, []).append(value)
    if "TY" not in fields:
        raise CitationContractError("RIS 缺少 TY 行")
    ris_type = fields["TY"][0].upper()
    if ris_type not in RIS_TO_CSL:
        raise CitationContractError(f"未知 RIS 类型: {ris_type!r}")

    csl_type = ris_type and RIS_TO_CSL[ris_type]
    for value in fields.get("C7", []):
        if value.startswith("CSL:"):
            csl_type = value[4:].strip()

    issued = None
    if fields.get("DA"):
        chunks = fields["DA"][0].split("/")
        if chunks and chunks[0].isdigit():
            issued = CslDate(
                int(chunks[0]),
                int(chunks[1]) if len(chunks) > 1 and chunks[1].isdigit() else None,
                int(chunks[2]) if len(chunks) > 2 and chunks[2].isdigit() else None,
            )
    elif fields.get("PY") and fields["PY"][0].isdigit():
        issued = CslDate(int(fields["PY"][0]))

    page = ""
    sp = fields.get("SP", [""])[0]
    ep = fields.get("EP", [""])[0]
    if sp and ep:
        page = f"{sp}-{ep}"
    elif sp:
        page = sp

    extra: list[tuple[str, str]] = []
    for value in fields.get("C8", []):
        if "=" in value:
            k, v = value.split("=", 1)
            extra.append((k.strip(), v.strip()))

    return CslItem(
        csl_type=csl_type,
        id=fields.get("ID", [""])[0],
        title=fields.get("TI", [""])[0],
        authors=tuple(_parse_bib_name(a) for a in fields.get("AU", [])),
        issued=issued,
        container_title=(fields.get("JO") or fields.get("JF") or [""])[0],
        doi=normalize_doi(fields.get("DO", [""])[0]),
        url=fields.get("UR", [""])[0],
        publisher=fields.get("PB", [""])[0],
        page=page,
        volume=fields.get("VL", [""])[0],
        issue=fields.get("IS", [""])[0],
        issn=fields.get("SN", [""])[0],
        language=fields.get("LA", [""])[0],
        abstract=fields.get("AB", [""])[0],
        extra=tuple(extra),
    )


# --------------------------------------------------------------------------
# DOI
# --------------------------------------------------------------------------


def to_doi(item: CslItem) -> str:
    """DOI 是最**窄**的派生视图：只承载标识符。

    没有 DOI 时抛错 —— 绝不为缺失 DOI 编造一个（例如用标题哈希冒充 DOI）。
    """
    normalized = normalize_doi(item.doi)
    if not normalized:
        raise CitationContractError("该条目没有 DOI：不得编造 DOI 参与往返")
    return normalized


def from_doi(doi: str) -> CslItem:
    """DOI → 最小 CSL 条目。

    **只**填 DOI 与 id；不推断标题/作者/年份（那会是编造）。
    类型取 `document`（DOI 本身不携带类型信息）。
    """
    normalized = normalize_doi(doi)
    if not normalized:
        raise CitationContractError("DOI 为空")
    return CslItem(csl_type="document", id=normalized, doi=normalized)
