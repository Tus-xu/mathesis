"""RV14 · 引用格式渲染（确定性 GB/T 7714 子集）。

诚实边界：这里是**内置确定性子集渲染器**（期刊文章 + DOI 字段），
不是完整 CSL 引擎。渲染器源码哈希即 cslStyle.sha256（manifest 可查、
变更需 bump CSL_STYLE_VERSION）。待引入完整 CSL 时以 vendor 文件 +
文件哈希替换，manifest 的 kind 字段会显式区分。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CSL_STYLE_VERSION = "gbt7714-subset-v1"
CSL_STYLE_KIND = "builtin-deterministic-gbt7714-subset"


def csl_style_sha256() -> str:
    """渲染器**源码文件**的 SHA-256（代码身份，非语义版本）。"""
    src = Path(__file__).read_bytes()
    return hashlib.sha256(src).hexdigest()


def render_reference(csl: dict) -> str:
    """把一条 CSL JSON 渲染为 GB/T 7714 风格的引用文本（确定性）。

    缺字段如实留空/标注，不编造。支持：author(family/given)、title、
    container-title、issued.date-parts、DOI。
    """
    authors = csl.get("author") or []
    names: list[str] = []
    for a in authors:
        if not isinstance(a, dict):
            continue
        fam = str(a.get("family") or "").strip()
        giv = str(a.get("given") or "").strip()
        names.append(f"{fam} {giv}".strip())
    author_s = "、".join(names) if names else "佚名"

    title = str(csl.get("title") or "").strip()
    container = str(csl.get("container-title") or "").strip()
    year = ""
    issued = csl.get("issued") or {}
    parts = (issued.get("date-parts") or []) if isinstance(issued, dict) else []
    if parts and parts[0] and parts[0][0]:
        year = str(parts[0][0])
    doi = str(csl.get("DOI") or "").strip()

    seg = f"{author_s}. {title}[J]. {container}, {year}.".strip()
    if doi:
        seg = f"{seg} DOI: {doi}."
    return seg
