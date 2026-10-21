"""RV06 · provider 共用的确定性 lexical 打分。

刻意**不**依赖 embedding：RV04 的 `literature_record` 与 `research_asset` 都没有
向量列，而 Teacher 侧的向量是 768/1024 两套（跨库取向量会把维度问题引进来）。
本模块只做词法信号，向量分项由各 provider 如实填 0.0 —— 而不是编一个数。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    """中文 bigram + 拉丁/数字 token（检索用，非语言学分词）。"""
    lowered = (text or "").lower()
    tokens: set[str] = set()
    for run in _CJK.findall(lowered):
        for i in range(len(run) - 1):
            tokens.add(run[i : i + 2])
    tokens.update(_LATIN.findall(lowered))
    return tokens


def lexical_score(query: str, *fields: str) -> float:
    """词法分 [0,1]：任一字面整串命中 → 0.9 起，否则按 token 覆盖率。

    多字段（标题/摘要/章节）取最大值，避免长文本把命中稀释掉。
    """
    q = (query or "").strip().lower()
    if not q:
        return 0.0
    q_tokens = tokenize(q)
    if not q_tokens:
        return 0.0
    compact_q = q.replace(" ", "")
    best = 0.0
    for raw in fields:
        text = (raw or "").lower()
        if not text:
            continue
        compact = text.replace(" ", "")
        if compact_q and compact_q in compact:
            best = max(best, 0.95)
            continue
        overlap = len(q_tokens & tokenize(text)) / len(q_tokens)
        best = max(best, 0.9 * overlap)
    return best


def parse_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def recency_component(value: object, *, newest: datetime | None, oldest: datetime | None) -> float:
    """相对新鲜度 [0.5, 1.0]。

    用**结果集内部**的最新/最旧做归一化，而不是 `datetime.now()`：
    打分必须可复现，否则同一查询在不同时刻会给出不同排序。
    """
    ts = parse_ts(value)
    if ts is None or newest is None or oldest is None:
        return 0.5
    span = (newest - oldest).total_seconds()
    if span <= 0:
        return 1.0
    age_ratio = (newest - ts).total_seconds() / span
    return 1.0 - 0.5 * max(0.0, min(1.0, age_ratio))


#: 章节标题整串命中 = 最强定位信号，给主导权重。
TITLE_MATCH_WEIGHT = 1.0

#: 教材章节标题普遍带编号前缀（`1. 集合的概念` / `2.1 等式性质`）。
_LEADING_NUMBER = re.compile(r"^[\s\d.、]+")


def normalize_title(title: str) -> str:
    """剥离编号前缀并压平空白，用于标题与查询的**精确**比对。"""
    return _LEADING_NUMBER.sub("", (title or "").strip().lower().replace(" ", ""))


def title_match_bonus(query: str, title: str, *, weight: float = TITLE_MATCH_WEIGHT) -> float:
    """章节标题命中加分，区分「精确同名」与「只是包含」。

    为什么必须有这一项（首轮评测的实测教训）：只按 chunk 文本做 bigram overlap 时，
    "标题精确匹配、但正文把关键词拆散在多个 chunk 里"的章节，会与"正文里恰好出现
    同一个词"的噪音章节同分，排序退化为按 id —— 固定评测集上 Recall@10 只有 0.25。

    为什么还要分级：教材里同名/近名章节很常见（`2.1 等差数列` vs `等差数列的应用`），
    两者都"包含"查询词，但只有前者是用户要找的那一节。固定评测集上
    Recall@10 从 0.75 提到 0.875 靠的就是这条区分。
    """
    q = (query or "").strip().lower().replace(" ", "")
    if len(q) < 2:
        return 0.0
    raw = (title or "").strip().lower().replace(" ", "")
    if not raw:
        return 0.0
    stripped = normalize_title(title)
    if stripped and stripped == q:  # 编号剥离后完全同名
        return weight
    if q in raw:  # 标题包含查询（如 `等差数列的应用`）
        return weight * 0.6
    if len(stripped) >= 3 and stripped in q:  # 查询更长（如 `两角和与差的余弦`）
        return weight * 0.5
    return 0.0


__all__ = [
    "tokenize",
    "lexical_score",
    "parse_ts",
    "recency_component",
    "normalize_title",
    "title_match_bonus",
    "TITLE_MATCH_WEIGHT",
]
