"""R08 检索 Provider：locate_sections / list_example_candidates / resolve_source_ref。

设计（对应 r08-handoff）：
- 混合召回：权限/版本过滤 → lexical + vector anchors → 聚合 section candidate；
- rights：检索可见 = STAGED+ACTIVE（visible_scope 语义）；QUARANTINED 永不进结果；
- tenant：school_id 叠加过滤（NULL=平台级种子，对所有租户可见；非 NULL 只对本租户）；
  越权返回空（不抛敏感错误）；
- index_version 版本隔离：只在该版本内检索（回滚 = 切换读取版本，不删向量）；
- 教材事实纯净：只承载教材域数据。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.models import (
    TextbookChunk,
    TextbookProblem,
    TextbookSection,
    TextbookSource,
)
from app.textbook_v2.retrieval.dto import ProblemCandidate, SectionCandidate, SourceView
from app.textbook_v2.retrieval.fake_embedding import FakeEmbedding, cosine_similarity

# 混合召回权重：R08 为 fake embedding（向量是确定性扰动，仅作同分 tiebreak）。
# anchor 主导信号（强→弱）：1) 章节标题命中（TITLE_HIT_W 主导权重）→ 2) chunk 文本
# lexical（子串命中 + 出现次数加权）→ 3) 向量 tiebreak。
# R11 接入真实 embedding 后可将向量权重上调为主信号（语义主导）。
LEXICAL_WEIGHT = 1.0
VECTOR_WEIGHT = 0.001
_TITLE_HIT_W = 10.0  # 标题命中主导权重：章节标题含查询 = 最强锚，必先于非标题命中
_LEX_SUBSTRING = 0.9  # 整串命中基础分
_LEX_OCC = 0.1  # 出现次数加成（更强 anchor → 更高分，确定性区分同词频章节）
_OCC_NORM_DENOM = 4.0  # 每个 query bigram 平均出现 ≥4 次即加成满分
_PREVIEW_LEN = 160  # SourceView 文本预览截断长度（不泄露全文）

# 检索可见 rights（语义见 rights.visible_scope；此处为 SQL 谓词复用）
VISIBLE_RIGHTS = ("STAGED", "ACTIVE")


async def _visible_sources(
    session: AsyncSession,
    *,
    school_id: Any | None,
    grade: str | None = None,
    volume: str | None = None,
    publisher: str | None = None,
) -> list[TextbookSource]:
    """可见 source 列表：rights 可见 + 租户隔离 + 可选 course_context 过滤。"""
    q = select(TextbookSource).where(TextbookSource.rights_status.in_(VISIBLE_RIGHTS))
    if school_id is None:
        q = q.where(TextbookSource.school_id.is_(None))
    else:
        q = q.where(or_(TextbookSource.school_id.is_(None), TextbookSource.school_id == school_id))
    if grade:
        q = q.where(TextbookSource.grade == grade)
    if volume:
        q = q.where(TextbookSource.volume == volume)
    if publisher:
        q = q.where(TextbookSource.publisher == publisher)
    return list((await session.execute(q)).scalars().all())


async def _visible_section(
    session: AsyncSession, section_id: Any, *, school_id: Any | None
) -> tuple[TextbookSection, TextbookSource] | None:
    """加载 section 并断言其 source 可见（rights + tenant）；越权返回 None。"""
    section = (await session.execute(select(TextbookSection).where(TextbookSection.id == section_id))).scalars().first()
    if section is None:
        return None
    source = (await session.execute(select(TextbookSource).where(TextbookSource.id == section.textbook_id))).scalars().first()
    if source is None or source.rights_status not in VISIBLE_RIGHTS:
        return None
    if school_id is None:
        if source.school_id is not None:
            return None  # 平台级查询者看不到租户私有数据
    elif source.school_id is not None and source.school_id != school_id:
        return None  # 租户隔离：非本租户 source 不可见
    return section, source


def _tokenize(text: str) -> set[str]:
    """轻量中文 bigram + 拉丁/数字 token（检索用，非语言学分词）。"""
    toks: set[str] = set()
    for cjk in re.findall(r"[\u4e00-\u9fff]+", text.lower()):
        for i in range(len(cjk) - 1):
            toks.add(cjk[i : i + 2])
    toks.update(re.findall(r"[a-z0-9]+", text.lower()))
    return toks


def _lexical_score(query: str, text: str) -> float:
    """lexical anchor 分数 [0,1]：整串命中=0.9+出现次数加成；否则 recall+加成（<0.9）。

    出现次数加权让重复主题（如教师用书长文）与聚焦章节可确定性区分。
    """
    q = query.strip().lower()
    if not q:
        return 0.0
    qt = _tokenize(q)
    if not qt:
        return 0.0
    tc = (text or "").lower().replace(" ", "")
    occ = sum(tc.count(b) for b in qt)
    occ_norm = min(1.0, occ / (len(qt) * _OCC_NORM_DENOM))
    if q.replace(" ", "") in tc:
        return _LEX_SUBSTRING + _LEX_OCC * occ_norm  # [0.9, 1.0]
    recall = len(qt & _tokenize(text)) / len(qt)
    return _LEX_SUBSTRING * recall + _LEX_OCC * occ_norm  # 一般 < 0.9


def _hybrid_score(query: str, text: str, embedding: list[float] | None, query_vec: list[float]) -> float:
    """混合分数：lexical 主导 + 向量 tiebreak（fake embedding 为确定性扰动）。

    query_vec 由调用方一次性计算（避免每个 chunk 重复 embed）。
    """
    lex = _lexical_score(query, text)
    if embedding:
        cos = cosine_similarity(list(embedding), query_vec)
        vec = (cos + 1.0) / 2.0  # [-1,1] -> [0,1]
    else:
        vec = 0.0
    return LEXICAL_WEIGHT * lex + VECTOR_WEIGHT * (0.5 + vec / 2)


def _source_ref_source(source_id: Any) -> str:
    return f"textbook_v2/source/{source_id}"


def _source_ref_section(source_id: Any, section_id: Any) -> str:
    return f"textbook_v2/source/{source_id}/section/{section_id}"


def _source_ref_unit(source_id: Any, section_id: Any, unit_id: Any) -> str:
    return f"{_source_ref_section(source_id, section_id)}/unit/{unit_id}"


def _source_ref_problem(source_id: Any, section_id: Any, problem_id: Any) -> str:
    return f"{_source_ref_section(source_id, section_id)}/problem/{problem_id}"


def _source_ref_chunk(source_id: Any, section_id: Any, chunk_id: Any) -> str:
    return f"{_source_ref_section(source_id, section_id)}/chunk/{chunk_id}"


async def locate_sections(
    session: AsyncSession,
    query: str,
    course_context: dict[str, Any] | None = None,
    *,
    top_k: int = 5,
    index_version: int | None = None,
) -> list[SectionCandidate]:
    """anchor 召回：混合评分 → top-k chunk → 聚合 section candidate（score=节内最高分）。

    course_context 可选键：school_id / grade / volume / publisher（同名章节跨版本隔离）。
    index_version=None → 自动解析为可见范围内最新版本（避免跨版本向量混比）。
    """
    ctx = course_context or {}
    sources = await _visible_sources(
        session,
        school_id=ctx.get("school_id"),
        grade=ctx.get("grade"),
        volume=ctx.get("volume"),
        publisher=ctx.get("publisher"),
    )
    if not sources:
        return []
    source_ids = [s.id for s in sources]
    source_by_id = {s.id: s for s in sources}

    section_rows = (
        (await session.execute(select(TextbookSection).where(TextbookSection.textbook_id.in_(source_ids))))
        .scalars()
        .all()
    )
    if not section_rows:
        return []
    section_by_id = {s.id: s for s in section_rows}

    chunks = (
        (
            await session.execute(
                select(TextbookChunk).where(TextbookChunk.section_id.in_(list(section_by_id)))
            )
        )
        .scalars()
        .all()
    )
    if index_version is None:
        versions = {c.index_version for c in chunks if c.embedding is not None}
        effective = max(versions) if versions else None
    else:
        effective = index_version

    scored: list[tuple[float, TextbookChunk]] = []
    query_vec = FakeEmbedding().embed(query)
    query_compact = query.strip().lower().replace(" ", "")
    for c in chunks:
        if effective is not None and c.index_version != effective:
            continue  # 版本隔离：只在请求版本内检索
        lex = _lexical_score(query, c.text)
        if lex <= 0:
            continue  # lexical 是 anchor 召回门（fake embedding 向量为扰动，仅用于重排）
        vec = list(c.embedding) if c.embedding is not None else None
        score = _hybrid_score(query, c.text, vec, query_vec)
        sec = section_by_id[c.section_id]
        title_compact = (sec.normalized_title or sec.title or "").lower().replace(" ", "")
        if query_compact and title_compact and query_compact in title_compact:
            score = _TITLE_HIT_W + score  # 章节标题命中 = 最强锚（主导权重）
        scored.append((score, c))

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:top_k]

    by_section: dict[Any, float] = {}
    for score, c in top:
        by_section[c.section_id] = max(by_section.get(c.section_id, 0.0), score)

    candidates: list[SectionCandidate] = []
    # 确定性排序：score 降序 → 标题 → id（同分 tiebreak 不依赖 DB 行序）
    for sec_id, score in sorted(
        by_section.items(), key=lambda kv: (-kv[1], str(section_by_id[kv[0]].title), str(kv[0]))
    ):
        sec = section_by_id[sec_id]
        src = source_by_id[sec.textbook_id]
        candidates.append(
            SectionCandidate(
                section_id=sec.id,
                textbook_id=sec.textbook_id,
                section_path=sec.section_path,
                title=sec.title,
                level=sec.level,
                page_range=[sec.page_start, sec.page_end],
                score=score,
                rights_status=src.rights_status,
                source_volume=src.volume,
            )
        )
    return candidates


async def list_example_candidates(
    session: AsyncSession,
    section_id: Any,
    filters: dict[str, Any] | None = None,
    *,
    include_descendants: bool = True,
    course_context: dict[str, Any] | None = None,
) -> list[ProblemCandidate]:
    """例题/习题/探究候选（复用 R07 textbook_problems，stem/solution 关联保真）。

    filters 可选键：kind（example/exercise/inquiry）、difficulty、knowledge_tags、
    limit；越权/不可见返回空列表。
    """
    ctx = course_context or {}
    visible = await _visible_section(session, section_id, school_id=ctx.get("school_id"))
    if visible is None:
        return []
    section, source = visible

    target_ids = await _descendant_section_ids(session, section, include_descendants)
    q = select(TextbookProblem).where(TextbookProblem.section_id.in_(target_ids))
    problems = list((await session.execute(q)).scalars().all())

    f = filters or {}
    if f.get("kind"):
        problems = [p for p in problems if p.kind == f["kind"]]
    if f.get("difficulty"):
        problems = [p for p in problems if p.difficulty == f["difficulty"]]
    tags = f.get("knowledge_tags")
    if tags:
        wanted = set(tags)
        problems = [p for p in problems if p.knowledge_tags and wanted & set(p.knowledge_tags)]

    problems.sort(key=lambda p: (p.page_ref, p.order))
    if f.get("limit"):
        problems = problems[: int(f["limit"])]

    return [
        ProblemCandidate(
            problem_id=p.id,
            section_id=p.section_id,
            kind=p.kind,
            order=p.order,
            stem_text=p.stem_text,
            solution_text=p.solution_text,
            page_ref=p.page_ref,
            knowledge_tags=p.knowledge_tags or [],
            difficulty=p.difficulty,
            provenance=p.provenance,
            source_ref=_source_ref_problem(source.id, p.section_id, p.id),
        )
        for p in problems
    ]


async def _descendant_section_ids(
    session: AsyncSession, section: TextbookSection, include_descendants: bool
) -> list[Any]:
    """section 自身 + 可选全部后代 section id（BFS，保证覆盖整章/整节）。"""
    ids = [section.id]
    if not include_descendants:
        return ids
    frontier = [section.id]
    while frontier:
        children = (
            (
                await session.execute(select(TextbookSection.id).where(TextbookSection.parent_id.in_(frontier)))
            )
            .scalars()
            .all()
        )
        if not children:
            break
        ids.extend(children)
        frontier = list(children)
    return ids


async def resolve_source_ref(
    session: AsyncSession,
    source_ref: str,
    *,
    course_context: dict[str, Any] | None = None,
) -> SourceView | None:
    """把 source_ref 解析成可读 SourceView（文本截断预览）；越权/不可见返回 None。

    R12 起委托 locator.resolve_source_ref：legacy ref（textbook_v2/source/...）与
    v1 版本化 SourceLocator ref 均解析；legacy 只读，不产生任何新写入。
    """
    from app.textbook_v2.locator import resolve_source_ref as _locator_resolve

    return await _locator_resolve(session, source_ref, course_context=course_context)


def _preview(text: str) -> str:
    """截断预览（默认 160 字符），不泄露全文。"""
    text = (text or "").strip()
    return text if len(text) <= _PREVIEW_LEN else text[:_PREVIEW_LEN] + "…"
