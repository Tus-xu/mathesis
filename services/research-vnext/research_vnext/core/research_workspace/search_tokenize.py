"""RV17 · 检索分词（CJK 2-gram + 拉丁词）——索引与查询共用的**唯一**实现。

背景（P0-6 缺陷修复）
--------------------
PostgreSQL 默认解析器按空白/标点切词；中文没有词边界，一整段 CJK 会被
`to_tsvector('simple', ...)` 当成**单个巨型 token**，与查询侧同法切出的
token 永远不可能相等 —— 表现为「拉丁文可检索、CJK 0 命中」。`pg_trgm` 对
2 字中文短查询的相似度阈值不稳，不足以单独兜底。

方案：**自建 bigram 词元**。

- 索引侧：把 CJK 连续串切成**重叠 2-gram**（拉丁词原样保留、小写），空格
  连接后写入 `research_paper.search_tokens` / `research_note.search_tokens`，
  再以 `to_tsvector('simple', search_tokens)` 建 GIN 索引（见迁移 `0018`）。
- 查询侧：用**同一** `tokens()` 切分，词元之间用 `&`（AND）组合成 tsquery。

于是查询 `误差估计` → `误差 & 差估 & 估计`，可命中语料「有限元误差估计」；
查询 `FE` → `fe` 仍按拉丁词命中（不破坏英文检索）。

> 迁移 `0018` 复用本模块对存量行做回填（`index_text`），保证索引/查询同源。
"""

from __future__ import annotations

import re

#: 拉丁字母 / 数字词（英文词、跨语言标识符、年份、公式里的字母数字片段）。
_LATIN_RE = re.compile(r"[A-Za-z0-9]+")
#: CJK 连续串（基本区 + 扩展 A + 兼容区）。
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

#: 拉丁词元最短长度：过滤 `a`/`o`/`h` 之类的噪声 token；
#: 2 字母缩写（`FE`、`PDE`）保留。
_MIN_LATIN = 2


def cjk_bigrams(run: str) -> list[str]:
    """把一段**纯 CJK** 串切成重叠 2-gram；单字串退化为自身。"""
    if not run:
        return []
    if len(run) < 2:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def tokens(text: object) -> list[str]:
    """文本 → 词元列表（拉丁词小写 + CJK 2-gram），保持出现顺序。"""
    if text is None:
        return []
    s = str(text)
    if not s:
        return []
    out: list[str] = []
    for m in _LATIN_RE.finditer(s):
        w = m.group(0).lower()
        if len(w) >= _MIN_LATIN:
            out.append(w)
    for m in _CJK_RUN_RE.finditer(s):
        out.extend(cjk_bigrams(m.group(0)))
    return out


def index_text(*parts: object) -> str:
    """把多段文本转成「空格分隔、去重保序」的词元串，写入 `search_tokens`。

    去重只为减小索引体积；顺序不影响检索（tsvector 词元无序）。
    """
    flat: list[str] = []
    for p in parts:
        flat.extend(tokens(p))
    return " ".join(dict.fromkeys(flat))


def query_tsquery(query: str) -> str:
    """查询 → tsquery 文本（词元间用 `&` 组合）。

    无词元时返回空串，调用方应跳过该分支（`to_tsquery('simple','')` 会报错）。
    词元以单引号包裹并转义单引号，避免 tsquery 语法注入（用户输入不可信）。
    """
    toks = list(dict.fromkeys(tokens(query or "")))
    if not toks:
        return ""
    return " & ".join("'" + t.replace("'", "''") + "'" for t in toks)


__all__ = ["cjk_bigrams", "index_text", "query_tsquery", "tokens"]
