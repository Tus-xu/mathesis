"""RV14 · LaTeX 模板（钉死版本 1）。

任务书 2：模板固定、哈希可查（template_sha256）；模板自身不得携带危险
控制序列；中文字体 = Fandol 四族（镜像内 texlive-lang-chinese）。
模板变更必须显式 bump LATEX_TEMPLATE_VERSION 并更新 ARTIFACT-HASHES。
"""

from __future__ import annotations

import hashlib

LATEX_TEMPLATE_VERSION = 2

_LATEX_TEMPLATE = r"""% rv14 template v2 (pinned; hash recorded in ARTIFACT-HASHES.md)
% v2 变更：section 顶部编号样式（8 标题 / 8.1 子标题）、首行缩进两字符、
% 紧凑列表间距 —— 对齐学术期刊单栏排版（用户提供的样例页样式）。
\documentclass[11pt,fontset=fandol]{ctexart}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{enumitem}
\usepackage[a4paper,margin=2.5cm]{geometry}
\usepackage{hyperref}
\hypersetup{hidelinks}
% 章节标题左移编号（Sec 8 样式：编号与标题同行，无冒号）
\ctexset{
  section = {format = {\Large\bfseries}, name = {,}, number = \arabic{section}},
  subsection = {format = {\large\bfseries}, name = {,}, number = \arabic{section}.\arabic{subsection}},
}
% 正文首行缩进两字符（中文排版惯例；段落间不额外加空）
\setlength{\parindent}{2em}
\setlength{\parskip}{0.2em}
% 列表紧凑（enumitem）
\setlist{itemsep=0.15em, topsep=0.3em, parsep=0em}
@@LINEAGE@@
\begin{document}
\begin{center}{\LARGE @@TITLE@@}\end{center}
\vspace{0.5em}
@@BODY@@
\end{document}
"""


def template_sha256() -> str:
    """模板正文的 SHA-256（lineage/title/body 占位符原样参与哈希）。"""
    return hashlib.sha256(_LATEX_TEMPLATE.encode("utf-8")).hexdigest()


def render_document(
    body: str,
    *,
    title: str,
    meta: dict | None = None,
    citations: dict | None = None,
) -> str:
    """把 serializer 的 LaTeX body 包进钉死模板，得到完整可编译文档。

    title 来自 PaperSpec 之外的用户可见标题，同样必须转义。
    meta/citations 目前只进 lineage 注释（引用条目在 body 的
    thebibliography 中，由 serializer 生成）。
    """
    from .serializer import _escape_latex

    meta = meta or {}
    lineage = (
        f"% rv14 lineage: paperId={meta.get('paperId', '')} "
        f"revision={meta.get('revision', 0)} contentHash={meta.get('contentHash', '')}"
    )
    doc = _LATEX_TEMPLATE
    doc = doc.replace("@@LINEAGE@@", lineage)
    doc = doc.replace("@@TITLE@@", _escape_latex(str(title or "")))
    doc = doc.replace("@@BODY@@", body)
    return doc
