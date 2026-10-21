"""RV04 · title-author-year 指纹（DOI 之外的第三层去重键）。"""

from __future__ import annotations

import hashlib
import re
import unicodedata


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s


def title_author_year_fingerprint(
    *,
    title: str,
    authors: list[dict] | None,
    published_date: str,
) -> str:
    t = _norm_text(title)[:200]
    names = []
    for a in authors or []:
        n = _norm_text(a.get("name") or " ".join(x for x in [a.get("given"), a.get("family")] if x))
        if n:
            names.append(n)
    names = sorted(names)[:8]
    year = (published_date or "")[:4]
    raw = "|".join([t, ",".join(names), year])
    return hashlib.sha256(raw.encode()).hexdigest()
