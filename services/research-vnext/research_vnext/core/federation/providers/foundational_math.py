"""RV06 · FoundationalMathProvider：Teacher direct-chunk 的**只读** adapter。

对应 ADR-VN-02 Option C：平台提供只读 corpus adapter，Teacher 现有 768 维度通路径
不动，Research 也不 import Teacher 的 ORM / router / service。

四条硬约束：

1. **只读**：由连接参数强制 `default_transaction_read_only=on`，
   任何写操作由 PostgreSQL 直接拒绝（不是靠"我们只写 SELECT"的自觉）。
   **不是** event listener + `SET SESSION CHARACTERISTICS` —— 那条路径
   依赖"SET 发生在事务开始之前"，实测无效，见 `build_readonly_engine` 的说明。
2. **只 source → section → chunk**：locator 是 `TextbookLocator`，类型层没有
   页码字段。Teacher 的 `textbook_chunks.page_range` 真实存在，适配器**显式丢弃**
   并在 warnings 记 `DROPPED_PAGE_RANGE`。
3. **rights 门**：只返回 `STAGED` / `ACTIVE`；`QUARANTINED` 永不进结果。
4. **标注**：结果带 `TEXTBOOK` + `FOUNDATIONAL_MATH` 标签且 `citation_eligible=False`
   —— 教材不参与"文献已支持"的声明。
5. **有界阻塞**：连接与语句都带驱动级超时（见 `READONLY_CONNECT_TIMEOUT_S` /
   `READONLY_STATEMENT_TIMEOUT_MS`）。跨库读取是本服务唯一的外部 I/O，
   没有超时的话，对端被防火墙丢包时**单次查询会挂到 OS 级 TCP 超时**
   （本机实测约 130s），把编排层的 `deadline_ms` 彻底架空。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Text as SAText
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Engine

from ..contracts import (
    AccessRights,
    FederatedQuery,
    HitScope,
    Provenance,
    ProviderHit,
    ScoreComponents,
    TextbookLocator,
)
from ..scope import ResolvedScope
from ..scoring import lexical_score, recency_component, title_match_bonus
from ..types import EvidenceType, WarnCode
from .base import FederatedProvider, ProviderError

PARSER_VERSION = "teacher-direct-chunk@1.0.0"
SNIPPET_MAX = 400
#: 精排之后保留的候选数（投影成 ProviderHit 的上限），不是 SQL 截断值。
CANDIDATE_LIMIT = 300
VISIBLE_RIGHTS = ("STAGED", "ACTIVE")

#: 建连超时（秒）。跨库读取是本服务唯一的外部 I/O；不设此值时，对端被
#: **防火墙丢包**（而非拒绝）会让 `engine.connect()` 一直等到 OS 级 TCP 超时
#: ——本机实测约 130 秒，而编排层的 `deadline_ms` 只是 provider 之间的
#: 循环检查，**无法抢占**阻塞中的连接。3 秒足以覆盖同机/同机房往返。
READONLY_CONNECT_TIMEOUT_S = 3
#: 语句超时（毫秒）。与建连超时互补：连接建起来了但对端不返回结果时兜底。
READONLY_STATEMENT_TIMEOUT_MS = 5_000

_ENGINE_CACHE: dict[str, Engine] = {}


def foundational_database_url() -> str:
    """只读 DSN。未配置时 provider 抛 `ProviderError`，不静默返回空结果。"""
    return (os.getenv("RESEARCH_FOUNDATIONAL_DATABASE_URL") or "").strip()


def build_readonly_engine(url: str) -> Engine:
    """建立**只读** engine。

    只读由 PostgreSQL 的连接参数强制（`default_transaction_read_only=on`），
    每个事务都是只读事务，写操作直接报 `ReadOnlySqlTransaction`。

    刻意**不用** event listener + `SET SESSION CHARACTERISTICS`：那条路径依赖
    "SET 发生在事务开始之前"，而 SQLAlchemy 的 connect 事件里第一条语句
    往往已经隐式开启事务，结果是设置只作用于当次事务、后续写操作畅通无阻
    —— 一个看起来正确、实测无效的只读保证。（本机实测踩过。）
    """
    if not url:
        raise ProviderError("RESEARCH_FOUNDATIONAL_DATABASE_URL 未配置（fail closed）")
    return create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        connect_args={
            # 只读由 PostgreSQL 连接参数强制（见模块 docstring 第 1 条）。
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={READONLY_STATEMENT_TIMEOUT_MS}"
            ),
            # 建连超时：**缺了它**，对端被丢包时 `connect()` 会一直等到
            # OS 级 TCP 超时（本机实测 ~130s），把编排层的 `deadline_ms` 架空。
            "connect_timeout": READONLY_CONNECT_TIMEOUT_S,
        },
    )


def get_readonly_engine(url: str | None = None) -> Engine:
    target = url or foundational_database_url()
    if not target:
        raise ProviderError("RESEARCH_FOUNDATIONAL_DATABASE_URL 未配置（fail closed）")
    engine = _ENGINE_CACHE.get(target)
    if engine is None:
        engine = build_readonly_engine(target)
        _ENGINE_CACHE[target] = engine
    return engine


_SQL = text(
    """
    SELECT
        c.id                AS chunk_id,
        c.section_id        AS section_id,
        c.text              AS chunk_text,
        c.content_hash      AS content_hash,
        c.index_version     AS index_version,
        (c.page_range IS NOT NULL) AS has_page_range,
        s.textbook_id       AS source_id,
        s.title             AS section_title,
        s.section_path      AS section_path,
        s.created_at        AS section_created_at,
        src.rights_status   AS rights_status,
        src.school_id       AS school_id,
        src.publisher       AS publisher,
        src.grade           AS grade,
        src.volume          AS volume
    FROM textbook_chunks AS c
    JOIN textbook_sections AS s ON s.id = c.section_id
    JOIN textbook_sources AS src ON src.id = s.textbook_id
    WHERE src.rights_status = ANY(:visible_rights)
      AND (
            (CAST(:school_id AS text) IS NULL AND src.school_id IS NULL)
         OR (src.school_id IS NOT NULL
             AND CAST(src.school_id AS text) = CAST(:school_id AS text))
      )
    ORDER BY c.id
    LIMIT :limit
    """
).bindparams(
    # 显式给参数类型：psycopg 对 "list → text[]" 与 "None → text" 的推断
    # 在 `ANY(...)`/`IS NULL` 组合下并不可靠，靠隐式推断会得到难懂的编程错误。
    # 另外全程避免 `::text` —— SQLAlchemy 的 `:name` 绑定解析会把 `::` 后的
    # `text` 当成绑定参数名，是这套 SQL 最初失败的真正原因。
    bindparam("visible_rights", type_=ARRAY(SAText)),
    bindparam("school_id", type_=SAText),
)


def max_candidates() -> int:
    """候选上限（**语料保护**，不是相关性裁剪）。

    历史教训：早先版本在 SQL 侧用 `ILIKE ANY(前 6 个 token)` 粗筛后再
    `ORDER BY c.id LIMIT 300`。评测立刻暴露问题 —— 命中 >300 条时按 `id`
    截断与相关性**无关**，`余弦定理` 一类查询直接召回为 0。
    正确形态是：SQL 只做**权限/可见性**过滤，相关性完全由 Python 侧打分决定；
    这里的上限只用于挡住语料爆炸，不参与排序。
    """
    return int(os.getenv("RESEARCH_FOUNDATIONAL_MAX_CANDIDATES", "20000"))


def _rights_of(row: Any) -> AccessRights:  # noqa: ANN401
    status = str(row.rights_status or "UNKNOWN").upper()
    return AccessRights(
        rights_status=status,
        rights_evidence_ref=f"textbook_v2.rights_status:{status}",
        redistributable=False,
        license_id="",
        # 教材一律不外传全文：只给 snippet 预览 + direct-chunk locator
        fulltext_allowed=False,
    )


def _snippet_of(row: Any) -> str:  # noqa: ANN401
    title = (row.section_title or "").strip()
    body = " ".join((row.chunk_text or "").split())
    text_out = f"{title} — {body}" if title else body
    return text_out[:SNIPPET_MAX]


class FoundationalMathAdapter(FederatedProvider):
    """Teacher direct-chunk 只读检索。"""

    name = "foundational_math"
    version = "1.0.0"
    evidence_type = EvidenceType.FOUNDATIONAL_MATH

    def __init__(self, engine: Engine | None = None, *, database_url: str | None = None) -> None:
        self._engine = engine
        self._database_url = database_url

    def _engine_or_raise(self) -> Engine:
        if self._engine is not None:
            return self._engine
        return get_readonly_engine(self._database_url)

    def search(self, query: FederatedQuery, scope: ResolvedScope) -> Sequence[ProviderHit]:
        engine = self._engine_or_raise()
        params = {
            "visible_rights": list(VISIBLE_RIGHTS),
            "school_id": scope.school_id,
            "limit": max_candidates(),
        }

        try:
            with engine.connect() as conn:
                rows = list(conn.execute(_SQL, params))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — 连接/查询失败如实上报，不伪装成空结果
            raise ProviderError(f"Teacher direct-chunk 只读查询失败: {exc}") from exc

        if not rows:
            return []

        has_query = bool(query.query_text.strip())
        scored: list[tuple[float, Any]] = []
        for row in rows:
            # 正文 bigram/子串信号 + **章节标题命中的主导权重**。
            # 只算正文会让"标题精确匹配但正文分散"的章节与噪音同分。
            total = lexical_score(query.query_text, row.chunk_text, row.section_title) + (
                title_match_bonus(query.query_text, row.section_title)
            )
            # 有查询词时只保留真正命中的候选；空查询 = 广度浏览（列出可见候选）
            if has_query and total <= 0:
                continue
            scored.append((total if total > 0 else 0.01, row))
        if not scored:
            return []

        # 相关性排序在 **Python 侧**完成，SQL 只负责可见性过滤。
        # 截断发生在精排之后，因此不会像"按 id 取前 N 条"那样丢掉相关章节。
        scored.sort(key=lambda item: (-item[0], str(item[1].chunk_id)))
        scored = scored[:CANDIDATE_LIMIT]

        stamps = [row.section_created_at for _, row in scored if row.section_created_at is not None]
        newest = max(stamps) if stamps else None
        oldest = min(stamps) if stamps else None

        hits: list[ProviderHit] = []
        for score, row in scored:
            hits.append(hit_from_row(row, scope, score=score, newest=newest, oldest=oldest))
        return hits


def hit_from_row(
    row: Any,  # noqa: ANN401 — SQLAlchemy Row / 测试用轻量替身
    scope: ResolvedScope,
    *,
    score: float,
    newest: Any | None = None,  # noqa: ANN401
    oldest: Any | None = None,  # noqa: ANN401
) -> ProviderHit:
    """把一行 Teacher direct-chunk 投影成命中。

    单独成函数是为了让"page_range 被丢弃"这条行为可被**直接**测试：
    本机 Teacher 库里 `page_range` 目前全为 NULL，光靠端到端用例覆盖不到
    `has_page_range=True` 的分支，而它恰恰是教材铁律最关键的一条。
    """
    warnings: list[str] = []
    if getattr(row, "has_page_range", False):
        # Teacher 表里确实有 page_range 字段；本适配器**不**把它带进 locator
        warnings.append(WarnCode.DROPPED_PAGE_RANGE.value)
    warnings.append(WarnCode.NO_RIGHTS_FULLTEXT_BLOCKED.value)
    created = getattr(row, "section_created_at", None)
    return ProviderHit(
        evidence_type=EvidenceType.FOUNDATIONAL_MATH,
        provider=FoundationalMathAdapter.name,
        provider_version=FoundationalMathAdapter.version,
        locator=TextbookLocator(
            source_id=str(row.source_id),
            section_id=str(row.section_id),
            chunk_id=str(row.chunk_id),
        ),
        snippet=_snippet_of(row),
        rights=_rights_of(row),
        provenance=Provenance(
            retrieved_at=created.isoformat() if created else "",
            parser_version=PARSER_VERSION,
            source_hash=row.content_hash or "",
            index_version=str(row.index_version or ""),
        ),
        retrieved_at=created.isoformat() if created else "",
        score_components=ScoreComponents(
            lexical=round(score, 6),
            # 向量分如实为 0：跨库读取 768 维向量会把维度分裂引进来
            vector=0.0,
            rights_penalty=0.0,
            recency=round(recency_component(created, newest=newest, oldest=oldest), 6),
        ),
        scope_meta=HitScope(
            tenant_id=scope.tenant_id,
            # 教材整册入库，不属于任何 project（见 scope.PROJECT_AGNOSTIC_PROVIDERS）
            project_id=None,
            school_id=str(row.school_id) if row.school_id is not None else None,
            visibility="school",
        ),
        warnings=tuple(dict.fromkeys(warnings)),
        labels=("TEXTBOOK", "FOUNDATIONAL_MATH"),
        # 教材不得被用于声称「文献已支持」
        citation_eligible=False,
        record_kind="TEXTBOOK_CHUNK",
    )


__all__ = [
    "FoundationalMathAdapter",
    "foundational_database_url",
    "build_readonly_engine",
    "get_readonly_engine",
    "hit_from_row",
    "PARSER_VERSION",
    "READONLY_CONNECT_TIMEOUT_S",
    "READONLY_STATEMENT_TIMEOUT_MS",
    "VISIBLE_RIGHTS",
]
